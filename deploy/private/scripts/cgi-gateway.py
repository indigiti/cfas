#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path
from wsgiref.handlers import CGIHandler

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "packages" / "runtime"

if not PACKAGES.is_dir():
    raise SystemExit("CFAS bundled Python packages are missing")

sys.path.insert(0, str(PACKAGES))
sys.path.insert(0, str(ROOT))

home = ROOT / ".runtime-home"
tmp = ROOT / "run" / "tmp"
home.mkdir(parents=True, exist_ok=True)
tmp.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("HOME", str(home))
os.environ.setdefault("TMPDIR", str(tmp))

from app import app as application  # noqa: E402

CGIHandler().run(application)
