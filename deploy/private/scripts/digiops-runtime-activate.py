#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "run"
HOME = ROOT / ".runtime-home"
PACKAGES = ROOT / "packages" / "runtime"
GATEWAY = ROOT / "scripts" / "cgi-gateway.py"


def emit(payload: dict) -> None:
    print(json.dumps(payload, separators=(",", ":")), flush=True)


def validate_runtime() -> None:
    if not PACKAGES.is_dir():
        raise RuntimeError("Bundled Python runtime packages are missing from the release")
    if not GATEWAY.is_file():
        raise RuntimeError("Python CGI gateway is missing from the release")

    env = os.environ.copy()
    env["HOME"] = str(HOME)
    env["TMPDIR"] = str(RUN / "tmp")
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = os.pathsep.join([str(PACKAGES), str(ROOT)])

    check = subprocess.run(
        [
            sys.executable,
            "-c",
            "import flask, PIL, deepface; from app import app; print('CFAS request runtime ready')",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60,
    )
    if check.returncode != 0:
        raise RuntimeError(check.stdout.strip()[-1200:] or "Bundled Python runtime validation failed")

    cgi_env = env.copy()
    cgi_env.update(
        {
            "REQUEST_METHOD": "GET",
            "QUERY_STRING": "",
            "CONTENT_TYPE": "",
            "CONTENT_LENGTH": "0",
            "SCRIPT_NAME": "/cfas",
            "PATH_INFO": "/api/config",
            "REQUEST_URI": "/cfas/api/config",
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "443",
            "SERVER_PROTOCOL": "HTTP/1.1",
            "SERVER_SOFTWARE": "DigiOps-CFAS-Check",
            "GATEWAY_INTERFACE": "CGI/1.1",
            "REMOTE_ADDR": "127.0.0.1",
            "HTTPS": "on",
            "HTTP_HOST": "localhost",
            "HTTP_X_FORWARDED_HOST": "localhost",
            "HTTP_X_FORWARDED_PROTO": "https",
            "HTTP_X_FORWARDED_PREFIX": "/cfas",
        }
    )
    smoke = subprocess.run(
        [sys.executable, str(GATEWAY)],
        cwd=ROOT,
        env=cgi_env,
        input=b"",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    output = smoke.stdout.decode("utf-8", errors="replace")
    if smoke.returncode != 0 or '"ok":true' not in output.replace(" ", ""):
        detail = smoke.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError((detail or output or "CFAS CGI smoke test failed")[-1200:])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args()

    for directory in (RUN, RUN / "tmp", HOME, ROOT / "data"):
        directory.mkdir(parents=True, exist_ok=True)

    validate_runtime()
    (RUN / "release-commit").write_text(args.expected_commit.strip() + "\n")

    emit(
        {
            "ok": True,
            "running_commit": args.expected_commit.strip(),
            "runtime": "request-driven-php-python-cgi",
            "backend": "no-persistent-daemon",
        }
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        emit({"ok": False, "error": str(exc)[:1200]})
        raise SystemExit(1)
