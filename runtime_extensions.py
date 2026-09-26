from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

from flask import jsonify

from app import admin_required, api_error, app, public_config

ROOT = Path(__file__).resolve().parent


def runtime_root() -> Path:
    explicit = os.environ.get("CFAS_RUNTIME_ROOT", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    if ROOT.parent.name == "private_html":
        return ROOT.parent / f".{ROOT.name}-runtime"
    return ROOT / ".runtime"


RUNTIME_ROOT = runtime_root()
RUN = RUNTIME_ROOT / "run"
STATUS = RUN / "face-engine-status.json"
READY = RUN / "face-engine.ready"
FACE_PACKAGES = RUNTIME_ROOT / "packages" / "face"
HOME = RUNTIME_ROOT / ".runtime-home"
TMP = RUN / "tmp"
INSTALLER = ROOT / "scripts" / "install-face-engine.py"


def read_status() -> dict:
    if READY.is_file():
        return {"state": "ready", "detail": "Face engine is installed."}
    try:
        payload = json.loads(STATUS.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except (OSError, json.JSONDecodeError):
        pass
    return {"state": "not_installed", "detail": "Face engine has not been installed yet."}


@app.get("/api/admin/face-engine/status")
@admin_required
def face_engine_status():
    return jsonify({"ok": True, "face_engine": read_status(), "config": public_config()})


@app.post("/api/admin/face-engine/install")
@admin_required
def install_face_engine():
    if READY.is_file():
        return jsonify({
            "ok": True,
            "message": "Face engine is already installed.",
            "face_engine": read_status(),
            "config": public_config(),
        })

    if not INSTALLER.is_file():
        return api_error("Face engine installer is missing from this release.", 500)

    for directory in (RUN, HOME, TMP, FACE_PACKAGES):
        directory.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["CFAS_RUNTIME_ROOT"] = str(RUNTIME_ROOT)
    env["HOME"] = str(HOME)
    env["TMPDIR"] = str(TMP)
    env["PYTHONUNBUFFERED"] = "1"

    try:
        result = subprocess.run(
            [sys.executable, str(INSTALLER)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=1500,
        )
    except subprocess.TimeoutExpired:
        return jsonify({
            "ok": False,
            "message": "Face engine installation timed out. You can retry from this page.",
            "face_engine": read_status(),
        }), 504

    status = read_status()
    if result.returncode != 0 or status.get("state") != "ready":
        detail = str(status.get("detail") or "").strip()
        if not detail:
            detail = result.stdout.strip()[-1200:]
        return jsonify({
            "ok": False,
            "message": "Face engine installation did not complete successfully.",
            "detail": detail[:1200],
            "face_engine": status,
        }), 500

    if str(FACE_PACKAGES) not in sys.path:
        sys.path.insert(0, str(FACE_PACKAGES))
    importlib.invalidate_caches()

    return jsonify({
        "ok": True,
        "message": "Face engine installed successfully. Enrollment and verification are now enabled.",
        "face_engine": status,
        "config": public_config(),
    })
