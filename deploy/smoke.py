#!/usr/bin/env python3
"""Start the extracted application on a private Unix socket and check login."""
import argparse
import os
from pathlib import Path
import subprocess
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("release", type=Path)
    parser.add_argument("socket", type=Path)
    args = parser.parse_args()
    log_path = args.socket.with_suffix(".log")
    with log_path.open("wb") as log:
        process = subprocess.Popen([
            str(args.release / ".venv/bin/gunicorn"), "--workers", "1",
            "--bind", f"unix:{args.socket}", "--access-logfile", "-",
            "rdbhs_backoffice.wsgi:application",
        ], cwd=args.release, stdout=log, stderr=log, start_new_session=True)
        try:
            for _ in range(60):
                if process.poll() is not None:
                    raise RuntimeError("Gunicorn exited; inspect private pilot log")
                result = subprocess.run([
                    "curl", "--silent", "--output", "/dev/null", "--write-out", "%{http_code}",
                    "--max-time", "2", "--unix-socket", str(args.socket),
                    "--header", "X-Forwarded-Proto: https", "http://localhost/accounts/login/",
                ], capture_output=True, text=True)
                if result.returncode == 0 and result.stdout == "200":
                    print("Gunicorn login: HTTP 200 over private Unix socket")
                    return
                time.sleep(0.25)
            raise RuntimeError("Login health check timed out")
        finally:
            os.killpg(process.pid, 15) if process.poll() is None else None
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, 9)
                process.wait()


if __name__ == "__main__":
    main()
