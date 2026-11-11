# Install

Download the file for your platform from the repository's Releases page.
`<version>` below is the version in the release tag.

## macOS

There is one download for each kind of Mac, so pick the one that matches yours:

- `AutoTextTyper-<version>-macOS-arm64.dmg` for a Mac with Apple silicon (the M1
  chip or newer).
- `AutoTextTyper-<version>-macOS-x86_64.dmg` for an Intel Mac.

Not sure which one you have? Open the Apple menu and choose **About This Mac**. A
line that says **Chip** followed by a name starting with "Apple" means Apple
silicon. A line that says **Processor** and mentions Intel means an Intel Mac.
The arm64 download does not run on an Intel Mac.

Both need **macOS 15 (Sequoia) or newer**. A `.zip` of the same `.app` is
published alongside each `.dmg`. Open the `.dmg` and drag **Auto Text Typer** to
Applications.

The app is **not signed with an Apple Developer ID and is not notarised** — the
build only applies an ad-hoc signature. macOS will therefore refuse to open it
on the first attempt. To allow it, once per installed copy:

- Control-click (right-click) the app in Applications and choose **Open**, then
  **Open** again in the dialog; or
- if macOS refuses outright with no Open button, launch it once, then go to
  **System Settings → Privacy & Security**, find the blocked-app notice near the
  bottom, and click **Open Anyway**.

## Windows

- `AutoTextTyper-<version>-Windows-x64-Setup.exe` — an installer. It installs
  per-user with no administrator prompt, and offers an all-users install if you
  elevate from the wizard's first page. A desktop shortcut is offered but not
  ticked by default.
- `AutoTextTyper-<version>-Windows-x64-portable.zip` — unzip it anywhere and run
  `AutoTextTyper\AutoTextTyper.exe`. Nothing is installed and nothing is written
  outside the folder you unzipped to, apart from the app's own settings file.

Both are x64 only, and neither is code-signed, so SmartScreen will warn the
first time: **More info → Run anyway**.

## Linux

- `AutoTextTyper-<version>-Linux-x86_64.AppImage` — a single file. A download
  does not keep its executable bit, so set it before running it:

  ```bash
  chmod +x AutoTextTyper-<version>-Linux-x86_64.AppImage
  ./AutoTextTyper-<version>-Linux-x86_64.AppImage
  ```

  An AppImage mounts itself with FUSE. On a system without `libfuse2`, run it
  without mounting instead:

  ```bash
  ./AutoTextTyper-<version>-Linux-x86_64.AppImage --appimage-extract-and-run
  ```

- `AutoTextTyper-<version>-Linux-x86_64.tar.gz` — extracts to an
  `auto-text-typer/` directory; run `./auto-text-typer/auto-text-typer`.

Both are x86_64 only. They are built on an older-glibc runner on purpose, so a
binary built there keeps working on newer distributions.

## Checking that the install works

Every build carries a headless self-test that plans a sample sentence, types it
into an in-memory buffer through the app's own machinery, and checks the result
matches character for character. It needs no permissions and opens no window.
The release workflow runs it on each artifact before publishing, and you can run
it yourself:

```bash
# macOS
"/Applications/Auto Text Typer.app/Contents/MacOS/Auto Text Typer" --self-test

# Linux (AppImage)
./AutoTextTyper-<version>-Linux-x86_64.AppImage --self-test
```

```powershell
# Windows
& "$env:LOCALAPPDATA\Programs\Auto Text Typer\AutoTextTyper.exe" --self-test
```

It prints a line starting `SELFTEST OK` on success, or `SELFTEST FAILED` with
the reason. `--version` prints the version and exits.
