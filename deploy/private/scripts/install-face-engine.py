#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "run"
REQ = ROOT / "requirements-face.txt"
STATUS = RUN / "face-engine-status.json"
LOCK = RUN / "face-engine-install.lock"
STAMP = RUN / "face-requirements.sha256"


def write_status(state: str, detail: str = "") -> None:
    RUN.mkdir(parents=True, exist_ok=True)
    payload = {
        "state": state,
        "detail": detail[:1200],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp = STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(STATUS)


def main() -> int:
    RUN.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0

        digest = hashlib.sha256(REQ.read_bytes()).hexdigest()
        installed = importlib.util.find_spec("deepface") is not None
        current = STAMP.read_text().strip() if STAMP.exists() else ""
        if installed and current == digest:
            write_status("ready")
            return 0

        write_status("installing")
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-r", str(REQ)],
                cwd=ROOT,
                check=True,
            )
            importlib.invalidate_caches()
            if importlib.util.find_spec("deepface") is None:
                raise RuntimeError("DeepFace package is still unavailable after installation")
            STAMP.write_text(digest)
            write_status("ready")
            return 0
        except Exception as exc:
            write_status("failed", str(exc))
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
