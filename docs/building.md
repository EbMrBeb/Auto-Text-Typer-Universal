# Building from source

Python 3.12 or newer.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,gui]"
```

`dev` is the test tooling (pytest, pytest-cov, pytest-qt). `gui` is PySide6,
which the application itself needs. **Install the two together.** pytest-qt
refuses to start pytest without a Qt binding, so with `.[dev]` alone every test
run stops before collecting a single test:

```
ERROR: pytest-qt requires either PySide6, PyQt5 or PyQt6 installed.
```

Run the GUI from the checkout with:

```bash
.venv/bin/python -m auto_essay_typer.gui
```

## Running the tests

```bash
.venv/bin/python -m pytest
```

The GUI tests set `QT_QPA_PLATFORM=offscreen` themselves, so no display is
needed on macOS or Windows. On Linux, pynput's X11 backend does not honour that
variable at all, so run the suite under Xvfb:

```bash
xvfb-run -a .venv/bin/python -m pytest
```

Nothing in the suite can type for real or listen to your keyboard: fixtures make
building a real pynput controller and starting a real global hotkey listener
impossible, and point the profile and settings files at temporary directories.
One test is skipped by default because it makes a real network request; opt in
with `ATT_NETWORK_TESTS=1`.

## Packaging

```bash
.venv/bin/pip install -e ".[build,gui]"
```

`build` adds PyInstaller, which the build scripts run; Pillow, for
`packaging/make_icons.py`; and PyYAML, used only to validate the GitHub Actions
workflow files during development. None of them is needed at runtime. They can
share the `.venv` above: the build scripts use that `.venv`'s `pyinstaller` when
it exists, and whichever is on `PATH` otherwise.

All three scripts read the same `ATT_ENTRY` environment variable the PyInstaller
spec reads, so script and spec always agree on what is being built. It defaults
to `packaging/launch_gui.py`, the real application.

```bash
packaging/build_macos.sh       # -> dist/*.app, *.dmg, *.zip
packaging/build_linux.sh       # -> dist/*.tar.gz, *.AppImage
```

```powershell
packaging/build_windows.ps1    # -> dist/*portable.zip, *Setup.exe
```

Per-platform requirements:

- **macOS:** `ATT_TARGET_ARCH` selects `universal2`, `arm64`, `x86_64`, or the
  native architecture if unset. universal2 requires *every* bundled native
  binary to already be a universal fat binary; if one is not, PyInstaller fails
  the build naming it, and there is no partial mode — rerun with
  `ATT_TARGET_ARCH=arm64`. The script verifies the code signature and re-signs
  ad-hoc if verification fails.
- **Windows:** needs Inno Setup's `iscc` on `PATH` or in its usual install
  location, for the installer. `choco install innosetup -y` works.
- **Linux:** PyInstaller's bootloader is not cross-buildable, so the build has
  to run on Linux. Needs `tar` and either a pre-installed `appimagetool` or
  network access to fetch one. The Qt and X11 runtime libraries the build and
  the tests need are listed in `.github/workflows/ci.yml`.

Each built artifact supports `--self-test`, as described under
[Checking that the install works](install.md#checking-that-the-install-works); CI runs it
on all three platforms.
