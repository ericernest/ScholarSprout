"""ScholarSprout console-script bootstrap.

Editable installs may resolve a third-party top-level ``auth`` package before
ScholarSprout's own source tree.  Put the repository root at the front of
``sys.path`` before importing the existing Typer application so the standard
``scholarsprout`` console entry point behaves like running from the repo root.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
try:
    sys.path.remove(_PROJECT_ROOT)
except ValueError:
    pass
sys.path.insert(0, _PROJECT_ROOT)

from cli.main import app  # noqa: E402

__all__ = ["app"]
