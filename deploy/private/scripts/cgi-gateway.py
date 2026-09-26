#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path
from wsgiref.handlers import CGIHandler

ROOT = Path(__file__).resolve().parents[1]
CORE_PACKAGES = ROOT / "packages" / "runtime"


def runtime_root() -> Path:
    explicit = os.environ.get("CFAS_RUNTIME_ROOT", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    if ROOT.parent.name == "private_html":
        return ROOT.parent / f".{ROOT.name}-runtime"
    return ROOT / ".runtime"


RUNTIME_ROOT = runtime_root()
FACE_PACKAGES = RUNTIME_ROOT / "packages" / "face"
READY = RUNTIME_ROOT / "run" / "face-engine.ready"
HOME = RUNTIME_ROOT / ".runtime-home"
TMP = RUNTIME_ROOT / "run" / "tmp"

if not CORE_PACKAGES.is_dir():
    raise SystemExit("CFAS bundled Python core packages are missing")

if READY.is_file() and FACE_PACKAGES.is_dir():
    sys.path.insert(0, str(FACE_PACKAGES))
sys.path.insert(0, str(CORE_PACKAGES))
sys.path.insert(0, str(ROOT))

HOME.mkdir(parents=True, exist_ok=True)
TMP.mkdir(parents=True, exist_ok=True)
os.environ["CFAS_RUNTIME_ROOT"] = str(RUNTIME_ROOT)
os.environ["HOME"] = str(HOME)
os.environ["TMPDIR"] = str(TMP)

from app import app as application  # noqa: E402
import runtime_extensions  # noqa: E402,F401

CGIHandler().run(application)
