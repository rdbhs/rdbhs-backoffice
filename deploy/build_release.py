#!/usr/bin/env python3
"""Export only application files from an exact Git commit, never a worktree."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOTS = ("core", "rdbhs_backoffice", "templates", "manage.py", "pyproject.toml", "uv.lock", "README.md", ".python-version")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    sha = subprocess.check_output(["git", "rev-parse", "--verify", f"{args.revision}^{{commit}}"], text=True).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise SystemExit("Expected a full Git commit")
    roots = subprocess.check_output(["git", "ls-tree", "--name-only", sha], text=True).splitlines()
    required = {"core", "rdbhs_backoffice", "manage.py", "pyproject.toml", "uv.lock"}
    if not required.issubset(roots):
        raise SystemExit("Incomplete application source")
    args.output.mkdir(parents=True, exist_ok=False)
    archive = args.output / "application.tar.gz"
    subprocess.run(["git", "archive", "--format=tar.gz", f"--output={archive.resolve()}", sha, "--", *[p for p in ROOTS if p in roots]], check=True)
    lock = subprocess.check_output(["git", "show", f"{sha}:uv.lock"])
    manifest = {
        "schema": 1, "project": "rdbhs-backoffice", "commit": sha,
        "archive": archive.name, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "lock_sha256": hashlib.sha256(lock).hexdigest(),
    }
    (args.output / "release.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
