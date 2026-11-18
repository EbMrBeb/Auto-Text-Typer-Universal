#!/usr/bin/env bash
# Builds the Linux .tar.gz and .AppImage for Auto Text Typer.
#
# Entry point comes from the same ATT_ENTRY env var the PyInstaller spec
# (packaging/pyinstaller/auto_text_typer.spec) and the macOS/Windows build
# scripts read, so all three always agree on what is being built:
#
#   ATT_ENTRY   Entry script, relative to the repo root.
#               Default: packaging/launch_gui.py (the real GUI).
#
# Usage:
#   packaging/build_linux.sh
#
# Requires PyInstaller to have been run on Linux (its bootloader is not
# cross-buildable), plus a desktop 'tar' and either a pre-installed
# 'appimagetool' or network access to fetch the pinned continuous build.
# When $CI is set (GitHub Actions sets it for every run), appimagetool is
# invoked with --appimage-extract-and-run, since FUSE -- which AppImages
# normally use to mount themselves -- is not available on CI runners.
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
APP_NAME="auto-text-typer"

echo "==> Building ${APP_NAME} ${VERSION} from ${ENTRY}"
rm -rf build "dist/${APP_NAME}"
"$PYINSTALLER" --noconfirm --clean packaging/pyinstaller/auto_text_typer.spec

ONEDIR="dist/${APP_NAME}"
if [ ! -d "$ONEDIR" ]; then
    echo "error: expected onedir build not found at $ONEDIR" >&2
    exit 1
fi
MAIN_EXE="$ONEDIR/${APP_NAME}"
if [ ! -x "$MAIN_EXE" ]; then
    echo "error: expected main executable not found at $MAIN_EXE" >&2
    exit 1
fi

DESKTOP_SRC="packaging/linux/${APP_NAME}.desktop"
echo "==> Validating ${DESKTOP_SRC}"
if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$DESKTOP_SRC"
else
    echo "    desktop-file-validate not on PATH; checking required keys manually"
    for key in "Type=Application" "Name=" "Exec=" "Icon=" "Categories="; do
        grep -q "^${key}" "$DESKTOP_SRC" || {
            echo "error: $DESKTOP_SRC missing required key matching '^${key}'" >&2
            exit 1
        }
    done
fi

TAR_PATH="dist/AutoTextTyper-${VERSION}-Linux-x86_64.tar.gz"
echo "==> Building ${TAR_PATH}"
rm -f "$TAR_PATH"
tar -czf "$TAR_PATH" -C dist "${APP_NAME}"

echo "==> Assembling AppDir"
APPDIR="build/${APP_NAME}.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib/${APP_NAME}"

# The onedir build becomes the AppDir's payload verbatim; AppRun below execs
# straight into it, so nothing here needs to be an FHS-style usr/bin split.
cp -a "$ONEDIR/." "$APPDIR/usr/lib/${APP_NAME}/"
cp "$DESKTOP_SRC" "$APPDIR/${APP_NAME}.desktop"
cp "assets/icon.png" "$APPDIR/${APP_NAME}.png"

# 'auto-text-typer' is hardcoded rather than variable-substituted: it is a
# fixed constant of this project (== $APP_NAME above), not something that
# varies per build, so there is nothing to template here.
cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
# AppImage entry point: resolves its own location (so this works whether
# launched directly, from a file manager, or through the AppImage's own
# fuse mount) and execs straight into the bundled onedir payload,
# forwarding every argument untouched (e.g. --self-test).
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec "$HERE/usr/lib/auto-text-typer/auto-text-typer" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

echo "==> Locating appimagetool"
APPIMAGETOOL="$(command -v appimagetool || true)"
if [ -z "$APPIMAGETOOL" ]; then
    APPIMAGETOOL="build/appimagetool-x86_64.AppImage"
    if [ ! -x "$APPIMAGETOOL" ]; then
        echo "    downloading pinned continuous-release appimagetool"
        curl -fsSL -o "$APPIMAGETOOL" \
            "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
        chmod +x "$APPIMAGETOOL"
    fi
fi

APPIMAGE_PATH="dist/AutoTextTyper-${VERSION}-Linux-x86_64.AppImage"
echo "==> Building ${APPIMAGE_PATH}"
rm -f "$APPIMAGE_PATH"
APPIMAGETOOL_ARGS=()
if [ -n "${CI:-}" ]; then
    echo "    \$CI is set; running appimagetool --appimage-extract-and-run (no FUSE on CI runners)"
    APPIMAGETOOL_ARGS+=(--appimage-extract-and-run)
fi
ARCH=x86_64 "$APPIMAGETOOL" "${APPIMAGETOOL_ARGS[@]}" "$APPDIR" "$APPIMAGE_PATH"

echo "==> Done."
echo "    Onedir:   $ONEDIR"
echo "    Tarball:  $TAR_PATH ($(du -sh "$TAR_PATH" | cut -f1))"
echo "    AppImage: $APPIMAGE_PATH ($(du -sh "$APPIMAGE_PATH" | cut -f1))"
