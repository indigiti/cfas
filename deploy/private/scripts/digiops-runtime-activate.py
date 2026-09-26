#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENV = ROOT / ".venv"
RUN = ROOT / "run"
LOGS = ROOT / "logs"
HOME = ROOT / ".runtime-home"
CORE_REQ = ROOT / "requirements-runtime.txt"
FACE_REQ = ROOT / "requirements-face.txt"
PORT = 8765


def emit(payload: dict) -> None:
    print(json.dumps(payload, separators=(",", ":")), flush=True)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(cmd: list[str], timeout: int = 90, env: dict[str, str] | None = None) -> None:
    subprocess.run(cmd, cwd=ROOT, env=env, check=True, timeout=timeout)


def stop_previous() -> None:
    pid_file = RUN / "gunicorn.pid"
    if not pid_file.exists():
        return
    try:
        pid = int(pid_file.read_text().strip())
    except Exception:
        pid_file.unlink(missing_ok=True)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pid_file.unlink(missing_ok=True)
        return
    deadline = time.time() + 6
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pid_file.unlink(missing_ok=True)
            return
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    pid_file.unlink(missing_ok=True)


def wait_ready() -> dict:
    deadline = time.time() + 22
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/config", timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if response.status == 200 and payload.get("ok") is True:
                    return payload
        except Exception as exc:
            last = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"CFAS runtime did not become ready: {last}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args()

    for directory in (RUN, LOGS, HOME, RUN / "tmp"):
        directory.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["HOME"] = str(HOME)
    env["TMPDIR"] = str(RUN / "tmp")
    env["PYTHONUNBUFFERED"] = "1"

    python = VENV / "bin" / "python"
    if not python.exists():
        run([sys.executable, "-m", "venv", str(VENV)], timeout=45, env=env)

    core_stamp = RUN / "runtime-requirements.sha256"
    required = sha256(CORE_REQ)
    current = core_stamp.read_text().strip() if core_stamp.exists() else ""
    if current != required:
        run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(CORE_REQ)], timeout=85, env=env)
        core_stamp.write_text(required)

    stop_previous()
    gunicorn = VENV / "bin" / "gunicorn"
    run(
        [
            str(gunicorn),
            "--daemon",
            "--bind", f"127.0.0.1:{PORT}",
            "--workers", "1",
            "--threads", "4",
            "--timeout", "120",
            "--pid", str(RUN / "gunicorn.pid"),
            "--access-logfile", str(LOGS / "access.log"),
            "--error-logfile", str(LOGS / "error.log"),
            "wsgi:application",
        ],
        timeout=15,
        env=env,
    )

    config = wait_ready()

    # DeepFace is intentionally installed outside the DigiOps activation timeout.
    # The UI reports FACE ENGINE UNAVAILABLE until this background task completes.
    face_installer = Path(__file__).with_name("install-face-engine.py")
    face_log = (LOGS / "face-engine-install.log").open("ab", buffering=0)
    subprocess.Popen(
        [str(python), str(face_installer)],
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=face_log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )

    (RUN / "release-commit").write_text(args.expected_commit.strip() + "\n")
    emit({
        "ok": True,
        "running_commit": args.expected_commit.strip(),
        "backend": f"127.0.0.1:{PORT}",
        "face_engine_ready": bool(config.get("face_engine_ready")),
    })
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        emit({"ok": False, "error": str(exc)[:1200]})
        raise SystemExit(1)
