import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

DEPLOY = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("verify_release", DEPLOY / "verify_release.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReleaseSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def bundle(self, extra=None, link=False):
        archive = self.root / "application.tar.gz"
        files = {"manage.py": b"manage", "pyproject.toml": b"project", "uv.lock": b"lock", "rdbhs_backoffice/settings.py": b"settings"}
        if extra:
            files.update(extra)
        with tarfile.open(archive, "w:gz") as tar:
            for name, content in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(content)
                if link and name == "manage.py":
                    member.type = tarfile.SYMTYPE
                    member.linkname = "/etc/passwd"
                    member.size = 0
                    tar.addfile(member)
                else:
                    tar.addfile(member, io.BytesIO(content))
        manifest = {"schema": 1, "project": "rdbhs-backoffice", "commit": "a" * 40, "archive": archive.name, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "lock_sha256": hashlib.sha256(b"lock").hexdigest()}
        (self.root / "release.json").write_text(json.dumps(manifest))
        return archive

    def test_valid_bundle(self):
        self.bundle()
        manifest, files = module.verify(self.root, "a" * 40)
        self.assertIn("manage.py", files)
        self.assertEqual(manifest["commit"], "a" * 40)

    def test_corruption_is_rejected(self):
        archive = self.bundle()
        archive.write_bytes(archive.read_bytes() + b"corrupt")
        with self.assertRaisesRegex(ValueError, "checksum"):
            module.verify(self.root)

    def test_wrong_commit_is_rejected(self):
        self.bundle()
        with self.assertRaisesRegex(ValueError, "Commit"):
            module.verify(self.root, "b" * 40)

    def test_path_traversal_is_rejected(self):
        self.bundle({"core/../../escape": b"bad"})
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            module.verify(self.root)

    def test_secret_path_is_rejected(self):
        self.bundle({"core/.env": b"synthetic"})
        with self.assertRaisesRegex(ValueError, "Forbidden"):
            module.verify(self.root)

    def test_symlink_is_rejected(self):
        self.bundle(link=True)
        with self.assertRaisesRegex(ValueError, "links"):
            module.verify(self.root)

    def test_worktree_secrets_are_not_exported(self):
        repo = self.root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        for name, value in {"manage.py": "manage", "pyproject.toml": "project", "uv.lock": "lock", "core/__init__.py": "", "rdbhs_backoffice/settings.py": "settings"}.items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Pilot test", "-c", "user.email=pilot@example.invalid", "commit", "-qm", "fixture"], check=True)
        (repo / "entra-secrets.env").write_text("synthetic-secret")
        (repo / "manage.py").write_text("uncommitted-change")
        output = self.root / "built"
        subprocess.run(["python3", str(DEPLOY / "build_release.py"), "--output", str(output)], cwd=repo, check=True, stdout=subprocess.DEVNULL)
        _, files = module.verify(output)
        self.assertEqual(files["manage.py"], b"manage")
        self.assertNotIn("entra-secrets.env", files)


if __name__ == "__main__":
    unittest.main()
