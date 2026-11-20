"""Bundled package assets.

Exposes the ExceptionHub icon to consumers (e.g. to render it in a
dashboard, CLI header, or docs generated from the package).
"""

from __future__ import annotations

from pathlib import Path

#: Directory that holds package data files (sibling of ``__init__.py``).
_PACKAGE_DIR = Path(__file__).resolve().parent.parent


def icon_path() -> Path:
    """Return the filesystem path to the bundled ExceptionHub icon (PNG)."""
    return _PACKAGE_DIR / "icon.png"


def icon_bytes() -> bytes:
    """Return the raw PNG bytes of the bundled icon."""
    return icon_path().read_bytes()
