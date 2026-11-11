# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Auto Text Typer.

Builds a windowed (no console), onedir application for whichever platform
`pyinstaller` runs on. Two things come from the environment rather than
being hardcoded, so this one spec drives every platform from
build_macos.sh / build_windows.ps1 / build_linux.sh and the release
workflow:

    ATT_ENTRY         Entry-point script, relative to the repo root.
                       Defaults to packaging/launch_gui.py -- the real Qt
                       GUI, via auto_essay_typer.gui.__main__ -- which is
                       what every shipped build packages. It stays
                       overridable so a one-off diagnostic build can be
                       pointed at another script without editing this spec;
                       nothing in CI or the release workflow sets it.
    ATT_TARGET_ARCH    macOS only: forwarded to EXE(target_arch=...).
                       "universal2", "arm64", "x86_64", or unset for the
                       native arch of the machine running PyInstaller.

Usage:

    pyinstaller packaging/pyinstaller/auto_text_typer.spec

(normally via one of the packaging/build_*.{sh,ps1} scripts, which set
ATT_ENTRY/ATT_TARGET_ARCH and post-process the output into an installer).

PyInstaller injects Analysis/PYZ/EXE/COLLECT/BUNDLE and SPECPATH into this
file's execution namespace itself -- see `pyi-makespec`'s own output -- so,
matching every other .spec file in the wild, they are used here without an
`import`.
"""
import os
import sys
import tomllib
from pathlib import Path

# SPECPATH is injected by PyInstaller: the directory containing this file,
# packaging/pyinstaller/. The repo root is two levels up. Resolving the
# root this way (rather than a bare relative path, or a plain __file__,
# which .spec files are not guaranteed to have) keeps every path below
# correct regardless of the directory `pyinstaller` was invoked from.
REPO_ROOT = Path(SPECPATH).resolve().parent.parent  # noqa: F821

with open(REPO_ROOT / "pyproject.toml", "rb") as _f:
    VERSION = tomllib.load(_f)["project"]["version"]

APP_DISPLAY_NAME = "Auto Text Typer"
BUNDLE_IDENTIFIER = "io.github.ebmrbeb.autotexttyper"

ENTRY_SCRIPT = str(REPO_ROOT / os.environ.get("ATT_ENTRY", "packaging/launch_gui.py"))
TARGET_ARCH = os.environ.get("ATT_TARGET_ARCH") or None

# The onedir folder / executable base name. macOS keeps the spaced display
# name because build_macos.sh (and the packaging spec in general) expects
# the bundle's real executable at "Auto Text Typer.app/Contents/MacOS/Auto
# Text Typer". Windows and Linux use conventional unspaced names instead,
# to avoid quoting headaches in shortcuts, .desktop Exec= lines, etc.
if sys.platform == "darwin":
    APP_BASENAME = APP_DISPLAY_NAME
    ICON_PATH = str(REPO_ROOT / "assets" / "icon.icns")
elif sys.platform == "win32":
    APP_BASENAME = "AutoTextTyper"
    ICON_PATH = str(REPO_ROOT / "assets" / "icon.ico")
else:
    APP_BASENAME = "auto-text-typer"
    ICON_PATH = None  # PyInstaller does not support EXE icons on Linux.

# pynput picks its backend at import time via platform sniffing rather than
# a static `import`, so PyInstaller's static analysis can never discover
# these on its own -- each must be a hiddenimport. Verified against the
# pynput 1.8.2 installed in this project's .venv (see packaging README /
# the packaging report for how): only the backend for the platform actually
# building is needed, since the other two backends' native dependencies
# (pywin32, python-xlib) are not installed and must not be required here.
_PYNPUT_HIDDEN_IMPORTS = {
    "darwin": [
        "pynput.keyboard._darwin",
        "pynput.mouse._darwin",
        "pynput._util.darwin",
        "pynput._util.darwin_vks",
    ],
    "win32": [
        "pynput.keyboard._win32",
        "pynput.mouse._win32",
        "pynput._util.win32",
        "pynput._util.win32_vks",
    ],
    "linux": [
        "pynput.keyboard._xorg",
        "pynput.mouse._xorg",
        "pynput._util.xorg",
        "pynput._util.xorg_keysyms",
    ],
}
HIDDEN_IMPORTS = _PYNPUT_HIDDEN_IMPORTS.get(sys.platform, _PYNPUT_HIDDEN_IMPORTS["linux"])

EXCLUDES = [
    # GUI toolkits this project does not use for its Qt-based GUI.
    "tkinter",
    "rumps",
    # Stdlib weight with no runtime purpose in a frozen app.
    "test",
    "unittest",
    "pydoc_data",
    "distutils",
    "lib2to3",
    "ensurepip",
    # PySide6-Essentials pulls in far more of Qt than a single-window
    # typing-assistant GUI with a tray icon needs; trimming these modules
    # keeps PyInstaller from bundling their (sizeable) native Qt libraries.
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets",
    "PySide6.QtNetwork",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtBluetooth",
    "PySide6.QtPositioning",
    "PySide6.QtNfc",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DExtras",
]

# Windows-only: render the PyInstaller version-resource file fresh from
# pyproject.toml on every build (see packaging/windows/version_info.py's
# module docstring for the file format). Not committed -- it lives under
# the gitignored build/ directory, regenerated each time.
WINDOWS_VERSION_FILE = None
if sys.platform == "win32":
    sys.path.insert(0, str(REPO_ROOT / "packaging" / "windows"))
    from version_info import write_version_file  # noqa: E402

    WINDOWS_VERSION_FILE = str(REPO_ROOT / "build" / "windows_version_info.txt")
    write_version_file(VERSION, WINDOWS_VERSION_FILE)

a = Analysis(  # noqa: F821
    [ENTRY_SCRIPT],
    # Pointing pathex at the repo root (in addition to whatever PyInstaller
    # finds via the environment's installed packages) keeps module
    # discovery for `auto_essay_typer` robust even though it is installed
    # editable (`pip install -e .`) rather than as a normal site-packages
    # copy -- editable-install finders vary by setuptools version and
    # config, and are not always something PyInstaller's static analysis
    # follows cleanly, whereas a plain path on disk always is.
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=[(str(REPO_ROOT / "assets"), "assets")],
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_BASENAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed executables trip antivirus/SmartScreen heuristics.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=TARGET_ARCH,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_PATH,
    version=WINDOWS_VERSION_FILE,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_BASENAME,
)

if sys.platform == "darwin":
    app = BUNDLE(  # noqa: F821
        coll,
        name=f"{APP_DISPLAY_NAME}.app",
        icon=ICON_PATH,
        bundle_identifier=BUNDLE_IDENTIFIER,
        version=VERSION,
        info_plist={
            "CFBundleName": APP_DISPLAY_NAME,
            "CFBundleDisplayName": APP_DISPLAY_NAME,
            "CFBundleIdentifier": BUNDLE_IDENTIFIER,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.productivity",
            # Measured, not taken from a wheel tag: the highest minos over
            # every Mach-O in a built app, 15.0 for PySide6-Essentials 6.11.2.
            # Qt's own libraries are built for macOS 13, which is all the
            # wheel's macosx_13_0 tag describes, but PySide6's binding modules
            # (QtCore/QtGui/QtWidgets/QtSvg .abi3.so, libpyside6, Shiboken,
            # libshiboken6) are built for 15.0 and the app imports them on
            # every launch. The main executable's own minos (11.0) is
            # PyInstaller's bootloader and says nothing about the rest.
            # Re-measure after any PySide6 or Python change, and before
            # raising the PySide6-Essentials cap in pyproject.toml:
            #   find "dist/Auto Text Typer.app" -type f -exec vtool -show-build {} \; \
            #     2>/dev/null | awk '/minos/ {print $2}' | sort -V | tail -1
            "LSMinimumSystemVersion": "15.0",
        },
    )
