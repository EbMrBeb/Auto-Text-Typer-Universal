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
  `ATT_TARGET_ARCH=arm64`. The script signs the bundle, verifies the signature
  and prints which identity it used; see
  [Code signing on macOS](#code-signing-on-macos) below.
- **Windows:** needs Inno Setup's `iscc` on `PATH` or in its usual install
  location, for the installer. `choco install innosetup -y` works.
- **Linux:** PyInstaller's bootloader is not cross-buildable, so the build has
  to run on Linux. Needs `tar` and either a pre-installed `appimagetool` or
  network access to fetch one. The Qt and X11 runtime libraries the build and
  the tests need are listed in `.github/workflows/ci.yml`.

Each built artifact supports `--self-test`, as described under
[Checking that the install works](install.md#checking-that-the-install-works); CI runs it
on all three platforms.

## Code signing on macOS

By default the build signs the app **ad-hoc**. That works, and it passes
`--self-test`, but an ad-hoc signature has no identity. macOS then recognises the
app by a hash of the exact binary (the `CDHash`), which is different on every
build. The consequence is that the Accessibility permission you granted stops
applying after each rebuild, even though **System Settings → Privacy & Security
→ Accessibility** still lists Auto Text Typer with its switch on. The same
happens to anyone who installs an update, if the update is ad-hoc signed.

Sign with a real certificate instead and macOS identifies the app by your
certificate plus its bundle ID (`io.github.ebmrbeb.autotexttyper`), which stays
the same from build to build. Grant the permission once and it keeps working.

Set `ATT_CODESIGN_IDENTITY` to the name of a certificate in your keychain, or to
its SHA-1 hash:

```bash
ATT_CODESIGN_IDENTITY="Auto Text Typer Local Signing" packaging/build_macos.sh
```

Unset, empty or `-` means ad-hoc, which is the same behaviour as before. When
the variable is set the script never falls back to ad-hoc: if `codesign` cannot
use the identity, or the finished bundle is still ad-hoc, the build stops with an
error. Every build ends with a signature report, so you can see what you got:

```
==> Signed: Auto Text Typer Local Signing (team: none)
    CDHash: 5a78...
    Designated requirement: identifier "io.github.ebmrbeb.autotexttyper" and certificate leaf = H"..."
```

An ad-hoc build reports `Signed: AD-HOC` and a designated requirement of
`cdhash H"..."` instead. That `cdhash` is the thing that changes every build.

The script signs after PyInstaller has finished, with `codesign --force --deep`,
rather than through the spec's `codesign_identity`. In PyInstaller 6.22 that
setting turns on the hardened runtime and a secure timestamp for every file in
the bundle. The hardened runtime's library validation rejects the bundled Python
and Qt libraries unless you also supply entitlements, so the app would not start.

### Option 1: a self-signed certificate (free)

This is enough to make permission grants survive rebuilds. It does not change how
other people's Macs treat the app: it is still "unidentified" to them.

1. Open **Keychain Access** (Applications → Utilities).
2. In the menu bar choose **Keychain Access → Certificate Assistant → Create a
   Certificate…**
3. Set **Name** to `Auto Text Typer Local Signing`. Set **Identity Type** to
   **Self Signed Root** and **Certificate Type** to **Code Signing**.
4. Click **Create**, then **Continue**, then **Done**. The certificate is stored
   in your **login** keychain.
5. Check that it is there:

   ```bash
   security find-identity -p codesigning
   ```

   You should see a line containing `"Auto Text Typer Local Signing"`.
6. If the build later says the certificate is not trusted, double-click it in
   Keychain Access, expand **Trust**, set **Code Signing** to **Always Trust**,
   and enter your password when asked.
7. Build with it:

   ```bash
   ATT_CODESIGN_IDENTITY="Auto Text Typer Local Signing" packaging/build_macos.sh
   ```

   The first time, macOS may ask whether `codesign` can use the key. Choose
   **Always Allow**.
8. Grant Accessibility to the newly built app once, as described in
   [Permissions](permissions.md#macos--accessibility-permission-is-required). If
   an older build is already in the list, first follow
   [A permission that stopped working](permissions.md#a-permission-that-stopped-working).

From then on, every build signed with this certificate keeps the grant. Two
things break that: signing with a different certificate, and creating a new
certificate with the same name. A certificate is its key, not its name. Export
yours from Keychain Access (right-click it, **Export**, `.p12` format) and keep
the file and its password somewhere safe.

### Option 2: an Apple Developer ID

A paid Apple Developer Program membership gives you a **Developer ID
Application** certificate. Create it in Xcode (**Settings → Accounts → Manage
Certificates**) or on developer.apple.com, then find its exact name:

```bash
security find-identity -v -p codesigning
```

```bash
ATT_CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" packaging/build_macos.sh
```

This gives permission grants the same stability as a self-signed certificate, and
it identifies you as the developer to other people's Macs. The "unidentified
developer" warning that [Install](install.md#macos) describes goes away only when
the app is also **notarised**, and the build does not do that. Notarisation needs
the hardened runtime, entitlements and a secure timestamp, which this script does
not apply, so a Developer ID build from here is signed but not yet notarisable.

### Signing in the release workflow

`.github/workflows/release.yml` signs the two macOS builds when these repository
secrets exist. These names are this workflow's own convention; create them under
**Settings → Secrets and variables → Actions**:

| Secret | Contents |
| --- | --- |
| `MACOS_CERTIFICATE` | The certificate and its private key as a `.p12` file, base64-encoded: `base64 -i cert.p12 \| pbcopy` |
| `MACOS_CERTIFICATE_PASSWORD` | The password you set when exporting the `.p12` |
| `MACOS_CODESIGN_IDENTITY` | The certificate's name, as you would pass it to `ATT_CODESIGN_IDENTITY` |

The workflow imports the certificate into a temporary keychain, builds with
`ATT_CODESIGN_IDENTITY` set, and deletes the keychain afterwards. If
`MACOS_CERTIFICATE` is missing, which is the case in a fork, the signing steps are
skipped and the build is ad-hoc signed as before. If it is set but
`MACOS_CODESIGN_IDENTITY` is not, or the bundle still comes out ad-hoc, the job
fails rather than publishing an unsigned release. Never commit a certificate or
its password.
