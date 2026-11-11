#!/usr/bin/env bash
# Builds the macOS .app with PyInstaller, verifies its code signature and
# architecture(s), then packages it as both a .dmg and a .zip under dist/.
#
# Entry point and target architecture come from the same env vars the
# PyInstaller spec (packaging/pyinstaller/auto_text_typer.spec) reads, so
# this script and the spec always agree on what is being built:
#
#   ATT_ENTRY          Entry script, relative to the repo root.
#                       Default: packaging/launch_gui.py (the real GUI).
#   ATT_TARGET_ARCH     universal2 | arm64 | x86_64 | unset (native).
#
# Usage:
#   packaging/build_macos.sh
#   ATT_TARGET_ARCH=universal2 packaging/build_macos.sh
#
# universal2 requires every bundled native binary (Python itself, every C
# extension, every Qt/pynput shared library) to already be a universal2
# fat binary. If any one of them is not, PyInstaller fails the build with
# an error naming that binary -- there is no partial/best-effort mode. On
# that failure, rerun with ATT_TARGET_ARCH=arm64 (or x86_64).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    PYTHON="${PYTHON:-$REPO_ROOT/.venv/bin/python}"
    PYINSTALLER="${PYINSTALLER:-$REPO_ROOT/.venv/bin/pyinstaller}"
else
    PYTHON="${PYTHON:-python3}"
    PYINSTALLER="${PYINSTALLER:-pyinstaller}"
fi

ENTRY="${ATT_ENTRY:-packaging/launch_gui.py}"
if [ ! -f "$ENTRY" ]; then
    echo "error: ATT_ENTRY script '$ENTRY' not found (relative to $REPO_ROOT)" >&2
    exit 1
fi

VERSION="$("$PYTHON" -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
ARCH="${ATT_TARGET_ARCH:-$(uname -m)}"
APP_NAME="Auto Text Typer"
APP_PATH="dist/${APP_NAME}.app"

echo "==> Building ${APP_NAME} ${VERSION} (${ARCH}) from ${ENTRY}"
rm -rf build "dist/${APP_NAME}.app" "dist/${APP_NAME}"
"$PYINSTALLER" --noconfirm --clean packaging/pyinstaller/auto_text_typer.spec

if [ ! -d "$APP_PATH" ]; then
    echo "error: expected bundle not found at $APP_PATH" >&2
    exit 1
fi

MAIN_EXE="$APP_PATH/Contents/MacOS/${APP_NAME}"
if [ ! -x "$MAIN_EXE" ]; then
    echo "error: expected main executable not found at $MAIN_EXE" >&2
    exit 1
fi

echo "==> Verifying code signature"
if ! codesign --verify --deep --strict "$APP_PATH"; then
    echo "==> ad-hoc verification failed; re-signing ad-hoc (codesign --force --deep --sign -)"
    codesign --force --deep --sign - "$APP_PATH"
    codesign --verify --deep --strict "$APP_PATH"
fi
echo "==> Code signature OK"

echo "==> Architectures in ${MAIN_EXE}:"
lipo -archs "$MAIN_EXE"

DMG_PATH="dist/AutoTextTyper-${VERSION}-macOS-${ARCH}.dmg"
ZIP_PATH="dist/AutoTextTyper-${VERSION}-macOS-${ARCH}.zip"

echo "==> Building ${ZIP_PATH}"
rm -f "$ZIP_PATH"
ditto -c -k --keepParent "$APP_PATH" "$ZIP_PATH"

echo "==> Building ${DMG_PATH}"
rm -f "$DMG_PATH"
STAGE_DIR="$(mktemp -d)"
trap 'rm -rf "$STAGE_DIR"' EXIT
ditto "$APP_PATH" "$STAGE_DIR/${APP_NAME}.app"
ln -s /Applications "$STAGE_DIR/Applications"
hdiutil create -volname "$APP_NAME" -srcfolder "$STAGE_DIR" -format UDZO -ov "$DMG_PATH"

echo "==> Done."
echo "    App: $APP_PATH ($(du -sh "$APP_PATH" | cut -f1))"
echo "    Zip: $ZIP_PATH ($(du -sh "$ZIP_PATH" | cut -f1))"
echo "    Dmg: $DMG_PATH ($(du -sh "$DMG_PATH" | cut -f1))"
