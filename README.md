<div align="center">

# Auto Text Typer

**Types your text into any app, one keystroke at a time, with a human typist's pauses and fixed mistakes.**

<br>

[![CI](https://github.com/EbMrBeb/Auto-Text-Typer-Universal/actions/workflows/ci.yml/badge.svg)](https://github.com/EbMrBeb/Auto-Text-Typer-Universal/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/EbMrBeb/Auto-Text-Typer-Universal)](https://github.com/EbMrBeb/Auto-Text-Typer-Universal/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Platforms](https://img.shields.io/badge/platforms-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)

<br>

<img src="docs/images/screenshot.png" alt="The Type page of Auto Text Typer: an import row, a text box, quick settings for speed and mistakes, and Preview and Start typing buttons" width="720">

<br>

[Download](https://github.com/EbMrBeb/Auto-Text-Typer-Universal/releases/latest) &nbsp;|&nbsp; [Quick start](#quick-start) &nbsp;|&nbsp; [Documentation](#documentation)

</div>

<br>

## Why use it

Some places will not take a paste, and some documents are easier to retype
than to rebuild by hand. Auto Text Typer sends your text to whatever
application has focus (Google Docs, Word, TextEdit, a web form) as real
keystrokes, at a speed you choose. It can also import a Google Doc or a `.docx`
and retype it with its headings, bold, italic and lists.

It does not write anything for you. You supply the text.

## Before you install

> [!IMPORTANT]
> Three things you will hit in the first five minutes.
>
> 1. **macOS 15 (Sequoia) or newer is required.**
> 2. **The macOS app is ad-hoc signed and not notarised.** macOS will refuse to
>    open it the first time. Control-click the app, choose **Open**, then
>    **Open** again; or go to **System Settings → Privacy & Security** and
>    click **Open Anyway**. Details in [Install](docs/install.md#macos).
> 3. **It needs Accessibility permission to type.** Turn on **Auto Text Typer**
>    in **System Settings → Privacy & Security → Accessibility**, then quit and
>    reopen the app. Details in [Permissions](docs/permissions.md).

The Windows build is unsigned too, so SmartScreen warns on first run
(**More info → Run anyway**).

## What it does

| | |
|---|---|
| **Types like a person** | OS-level keystrokes at a speed you set, with natural pauses, occasional mistakes it backspaces and fixes, and breaks on long documents. |
| **Reproduces your text exactly** | A test replays the keystrokes over thousands of random seeds and checks the result equals your text. See [the guarantee](docs/guarantee.md). |
| **Previews first** | **Preview** plans a whole run without typing anything, so you see the duration, the mistakes and the breaks before you start. |
| **Imports formatting** | Takes a Google Docs link, `.docx` or HTML export and types its formatting using the target editor's own shortcuts. What cannot be typed (images, tables, footnotes) is listed before you start, not dropped silently. |
| **Stays in reach** | Global hotkeys, a tray icon, a pause/stop control and a plain-text [command line](docs/cli.md). |

## Install

Download from the [Releases page](https://github.com/EbMrBeb/Auto-Text-Typer-Universal/releases/latest).

| Platform | File |
|---|---|
| macOS, Apple silicon (M1 or newer) | `AutoTextTyper-<version>-macOS-arm64.dmg` |
| macOS, Intel | `AutoTextTyper-<version>-macOS-x86_64.dmg` |
| Windows (x64) | `AutoTextTyper-<version>-Windows-x64-Setup.exe` |

Linux builds (AppImage and tarball) are also published; they work on X11 but
not Wayland. See [Install](docs/install.md) for every file, and for a
self-test that checks a download works.

## Quick start

1. Install and open the app, and grant Accessibility permission (macOS).
2. Paste your text into the box on the **Type** page.
3. Press **Preview** to see what the run will look like.
4. Press **Start typing**. During the countdown, click into the document or
   field where the text should go.
5. Keep your hands off the keyboard and mouse. Press **Esc** to stop.

<details>
<summary><b>Turn off autocorrect in the target application first</b></summary>

<br>

Otherwise the application will rewrite the text after it lands. See
[how to turn it off](docs/importing.md#turn-off-autocorrect-in-the-target-application).

</details>

## Documentation

| Page | What is in it |
|---|---|
| [Install](docs/install.md) | Every download, first-open steps, self-test |
| [Permissions](docs/permissions.md) | macOS Accessibility, Windows, Linux X11/Wayland |
| [Typing, hotkeys and the tray](docs/usage.md) | Running, pausing and stopping |
| [Importing a formatted document](docs/importing.md) | Google Docs, `.docx`, the editor picker, what is not reproduced |
| [Realism settings](docs/realism.md) | Profiles and the Advanced page |
| [Command line](docs/cli.md) | Typing a text file without the GUI |
| [The guarantee](docs/guarantee.md) | Why the typed text equals your text |
| [Building from source](docs/building.md) | Python 3.12+, dev setup |

## Contributing

Bug reports and pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md)
first; it covers setup, the tests and the one invariant a change must keep.

## Licence

MIT. Copyright (c) 2026 Braxton Gorman. See [LICENSE](LICENSE).

<div align="center">
<sub>Typing is done with <a href="https://pypi.org/project/pynput/">pynput</a> and the window with <a href="https://doc.qt.io/qtforpython-6/">Qt for Python (PySide6)</a>.</sub>
</div>
