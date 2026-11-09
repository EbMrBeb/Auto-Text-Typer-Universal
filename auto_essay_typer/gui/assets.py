"""Resolves paths into ``assets/`` -- the app icon, tray icons, etc.

Works both from a source checkout and from inside a PyInstaller bundle:
PyInstaller unpacks bundled data next to ``sys._MEIPASS`` (set for both
onedir and onefile builds), while running from source instead reaches
``assets/`` two directories up from this file. See
``packaging/pyinstaller/auto_text_typer.spec``'s ``datas=`` entry, which is
what puts ``assets/`` there in a build.
"""

from __future__ import annotations

import sys
from pathlib import Path


def assets_dir() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "assets"
    return Path(__file__).resolve().parent.parent.parent / "assets"


def asset_path(name: str) -> Path:
    """Path to one file in ``assets/``, e.g. ``asset_path("icon.png")``."""
    return assets_dir() / name
