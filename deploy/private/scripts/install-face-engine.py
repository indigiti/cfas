#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQ = ROOT / "requirements-face.txt"
CORE_PACKAGES = ROOT / "packages" / "runtime"
BOOTSTRAP = ROOT / "bootstrap"


def runtime_root() -> Path:
    explicit = os.environ.get("CFAS_RUNTIME_ROOT", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    if ROOT.parent.name == "private_html":
        return ROOT.parent / f".{ROOT.name}-runtime"
    return ROOT / ".runtime"


RUNTIME_ROOT = runtime_root()
FACE_PACKAGES = RUNTIME_ROOT / "packages" / "face"
HOME = RUNTIME_ROOT / ".runtime-home"
RUN = RUNTIME_ROOT / "run"
TMP = RUN / "tmp"
STATUS = RUN / "face-engine-status.json"
READY = RUN / "face-engine.ready"
LOCK = RUN / "face-engine-install.lock"
STAMP = RUN / "face-requirements.sha256"
MODEL = HOME / ".deepface" / "weights" / "arcface_weights.h5"


def write_status(state: str, detail: str = "", phase: str = "") -> None:
    RUN.mkdir(parents=True, exist_ok=True)
    payload = {
        "state": state,
        "phase": phase,
        "detail": detail[:1200],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp = STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(STATUS)


def pip_wheel() -> Path:
    wheels = sorted(BOOTSTRAP.glob("pip-*.whl"))
    if not wheels:
        raise RuntimeError("Bundled pip bootstrap is missing from this release")
    return wheels[-1]


def build_env(wheel: Path) -> dict[str, str]:
    env = os.environ.copy()
    parts = [str(wheel), str(FACE_PACKAGES), str(CORE_PACKAGES), str(ROOT)]
    existing = env.get("PYTHONPATH", "").strip()
    if existing:
        parts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env["CFAS_RUNTIME_ROOT"] = str(RUNTIME_ROOT)
    env["HOME"] = str(HOME)
    env["TMPDIR"] = str(TMP)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_CACHE_DIR"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def main() -> int:
    for directory in (RUN, TMP, HOME, FACE_PACKAGES):
        directory.mkdir(parents=True, exist_ok=True)

    with LOCK.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            write_status("installing", "Another installation request is already running.", "locked")
            return 2

        digest = hashlib.sha256(REQ.read_bytes()).hexdigest()
        current = STAMP.read_text(encoding="utf-8").strip() if STAMP.exists() else ""
        if READY.is_file() and current == digest and MODEL.is_file():
            write_status("ready", "Face engine is installed.", "complete")
            return 0

        READY.unlink(missing_ok=True)
        wheel = pip_wheel()
        env = build_env(wheel)

        try:
            write_status(
                "installing",
                "Downloading and installing DeepFace, TensorFlow and supporting packages.",
                "packages",
            )
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

            write_status(
                "installing",
                "Downloading and validating the ArcFace model.",
                "model",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-S",
                    "-c",
                    "from deepface import DeepFace; DeepFace.build_model('ArcFace'); print('ArcFace ready')",
                ],
                cwd=ROOT,
                env=env,
                check=True,
            )

            if not MODEL.is_file():
                raise RuntimeError("ArcFace model file was not created after model initialization")

            STAMP.write_text(digest + "\n", encoding="utf-8")
            READY.write_text(datetime.now(timezone.utc).isoformat() + "\n", encoding="utf-8")
            write_status("ready", "DeepFace, TensorFlow and ArcFace are installed.", "complete")
            return 0
        except Exception as exc:
            READY.unlink(missing_ok=True)
            write_status("failed", str(exc), "failed")
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
