#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import hashlib
import importlib
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
PACKAGES = ROOT / "packages"
RUNTIME_PACKAGES = PACKAGES / "runtime"
FACE_PACKAGES = PACKAGES / "face"
BOOTSTRAP = ROOT / "bootstrap"
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


def pip_wheel() -> Path:
    wheels = sorted(BOOTSTRAP.glob("pip-*.whl"))
    if not wheels:
        raise RuntimeError("Bundled pip wheel is missing from the release")
    return wheels[-1]


def build_env(wheel: Path) -> dict[str, str]:
    env = os.environ.copy()
    parts = [str(wheel), str(FACE_PACKAGES), str(RUNTIME_PACKAGES)]
    existing = env.get("PYTHONPATH", "").strip()
    if existing:
        parts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    return env


def main() -> int:
    RUN.mkdir(parents=True, exist_ok=True)
    FACE_PACKAGES.mkdir(parents=True, exist_ok=True)

    with LOCK.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0

        digest = hashlib.sha256(REQ.read_bytes()).hexdigest()
        current = STAMP.read_text().strip() if STAMP.exists() else ""

        if str(FACE_PACKAGES) not in sys.path:
            sys.path.insert(0, str(FACE_PACKAGES))
        installed = importlib.util.find_spec("deepface") is not None
        if installed and current == digest:
            write_status("ready")
            return 0

        write_status("installing")
        try:
            wheel = pip_wheel()
            env = build_env(wheel)
            subprocess.run(
                [
                    sys.executable,
                    "-m", "pip",
                    "install",
                    "--upgrade",
                    "--target", str(FACE_PACKAGES),
                    "-r", str(REQ),
                ],
                cwd=ROOT,
                env=env,
                check=True,
            )
            importlib.invalidate_caches()
            if str(FACE_PACKAGES) not in sys.path:
                sys.path.insert(0, str(FACE_PACKAGES))
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
