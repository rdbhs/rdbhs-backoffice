#!/usr/bin/env python3
"""Exercise Ansible deployment and rollback using synthetic local data only."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.parse


def run(command, **kwargs):
    return subprocess.run([str(p) for p in command], check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--external-pilot-database", action="store_true", help="Use the CI PostgreSQL service, never production")
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    bundle = args.bundle.resolve()
    run(["python3", here / "verify_release.py", bundle, "--expected-commit", args.expected_commit])
    root = Path(tempfile.mkdtemp(prefix="rdbhs-cicd-", dir="/tmp"))
    env = os.environ.copy()
    # Do not inherit Entra or application connection settings from the workstation.
    for key in list(env):
        if key.startswith(("ENTRA_", "PG")) or key in {"DATABASE_URL", "DJANGO_SETTINGS_MODULE", "STATIC_ROOT", "PYTHONPATH", "UV_PROJECT_ENVIRONMENT"}:
            env.pop(key, None)
    env.update(SECRET_KEY="pilot-only-not-a-production-secret-" + os.urandom(24).hex(), DEBUG="false", SSO_ENABLED="false", ALLOWED_HOSTS="localhost,testserver", CSRF_TRUSTED_ORIGINS="https://localhost", PYTHONDONTWRITEBYTECODE="1")
    env["ANSIBLE_LOCAL_TEMP"] = str(root / "ansible-local")
    pg = None
    report = {"commit": args.expected_commit, "production_touched": False, "result": "failed"}
    try:
        if args.external_pilot_database:
            url = os.environ.get("DATABASE_URL", "")
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme != "postgresql" or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.username != "rdbhs_pilot" or parsed.path != "/rdbhs_cicd_pilot":
                raise ValueError("Only the explicitly named local CI pilot database is accepted")
            env["DATABASE_URL"] = url
        else:
            pg = Path(os.environ.get("PILOT_PG_BINDIR", "/usr/lib/postgresql/17/bin"))
            socket = root / "pgsocket"
            socket.mkdir(mode=0o700)
            run([pg / "initdb", "-D", root / "pgdata", "-U", "rdbhs_pilot", "--auth-local=trust", "--auth-host=reject", "--no-locale", "--encoding=UTF8"], env=env, stdout=subprocess.DEVNULL)
            run([pg / "pg_ctl", "-D", root / "pgdata", "-l", root / "postgres.log", "-o", f"-c listen_addresses='' -k {socket}", "-w", "start"], env=env)
            run([pg / "createdb", "-h", socket, "-U", "rdbhs_pilot", "rdbhs_cicd_pilot"], env=env)
            env["DATABASE_URL"] = f"postgresql://rdbhs_pilot:pilot@/rdbhs_cicd_pilot?host={socket}"
        common = {
            "pilot_root": str(root), "bundle_dir": str(bundle),
            "expected_commit": args.expected_commit,
            "uv_binary": shutil.which("uv"),
            "runtime_python": shutil.which("python3.12") or shutil.which("python3"),
        }
        if not common["uv_binary"]:
            raise RuntimeError("uv is required")
        play = ["ansible-playbook", "-i", "localhost,", here / "ansible/rehearse.yml", "-e"]
        run([*play, json.dumps({**common, "release_slot": "baseline"})], env=env)
        baseline = (root / "current").resolve()
        # Run the complete existing suite against PostgreSQL from the installed artifact.
        # Existing view tests use plain HTTP. Keep production-style HTTPS checks
        # for Gunicorn, but use the application's development setting in the suite.
        run([baseline / ".venv/bin/python", "manage.py", "test", "core", "--noinput"], cwd=baseline, env={**env, "DEBUG": "true"})
        report["application_tests"] = "passed"
        # Deploy the identical verified artifact again to exercise activation mechanics.
        # This is a controlled failure, not proof of arbitrary schema rollback.
        failed = subprocess.run([str(p) for p in [*play, json.dumps({**common, "release_slot": "candidate", "inject_failure": True})]], env=env)
        if failed.returncode == 0 or (root / "current").resolve() != baseline:
            raise RuntimeError("Expected failure did not restore the baseline link")
        run(["python3", here / "smoke.py", root / "current", root / "restored.sock"], env=env)
        report.update(result="passed", installation="passed", activation="passed", synthetic_failure="detected", rollback="passed", restored_login="HTTP 200", database_rollback="not performed", artifact_rebuilt=False)
    finally:
        stopped = True
        if pg and (root / "pgdata/postmaster.pid").exists():
            stopped = subprocess.run([str(pg / "pg_ctl"), "-D", str(root / "pgdata"), "-m", "fast", "-w", "stop"], env=env).returncode == 0
        args.report.parent.mkdir(parents=True, exist_ok=True)
        if stopped:
            shutil.rmtree(root)
            report["temporary_environment_removed"] = True
        else:
            report["temporary_environment_removed"] = False
            report["diagnostic_path"] = str(root)
            report["result"] = "failed"
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        if not stopped:
            raise RuntimeError("Could not stop the disposable database; retained its directory")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
