# Auto-Text-Typer-Universal

Auto text typer with formatting, for everything from Google Docs to Microsoft
Word to TextEdit, etc. It types text **you supply** into whatever application
has focus, using OS-level keystroke simulation with the timing, hesitation and
self-corrected mistakes of a real typist. macOS, Windows and Linux.

It does not write anything. You give it the text; it reproduces it exactly.
That last part is not a slogan — it is a machine-checked property of the
engine, replayed over thousands of random seeds on every test run. See
[The guarantee](#the-guarantee).

It can also import a formatted document — a Google Docs share link, a `.docx`,
or an HTML export — and retype it with its formatting, by pressing the target
editor's own keyboard shortcuts.

## Install

Download the file for your platform from the repository's Releases page.
`<version>` below is the version in the release tag.

### macOS

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

### Windows

- `AutoTextTyper-<version>-Windows-x64-Setup.exe` — an installer. It installs
  per-user with no administrator prompt, and offers an all-users install if you
  elevate from the wizard's first page. A desktop shortcut is offered but not
  ticked by default.
- `AutoTextTyper-<version>-Windows-x64-portable.zip` — unzip it anywhere and run
  `AutoTextTyper\AutoTextTyper.exe`. Nothing is installed and nothing is written
  outside the folder you unzipped to, apart from the app's own settings file.

Both are x64 only, and neither is code-signed, so SmartScreen will warn the
first time: **More info → Run anyway**.

### Linux

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

### Checking that the install works

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

## Permissions

Typing into another application means injecting keystrokes at the OS level, and
each OS has its own answer to whether that is allowed. The app checks this
before every run and explains what to do; it does not start typing into nothing.

### macOS — Accessibility permission is required

macOS blocks synthetic keystrokes unless the process has Accessibility access.
The app probes for it directly (`AXIsProcessTrusted`) rather than discovering
mid-run that nothing is landing, and reports the state in three places: the
first-run walkthrough, a dialog when you press Start without it, and a status
line at the bottom of the main window.

Grant it to **Auto Text Typer itself**:

1. Open **System Settings → Privacy & Security → Accessibility**.
2. Turn on **Auto Text Typer**.
3. **Quit and reopen Auto Text Typer.** macOS does not apply the change to a
   process that is already running.

The app has an **Open Settings** button that opens that pane directly, and a
**Check again** button, so you do not have to find it by hand.

If you are running from source rather than from a built app, the permission
belongs to whatever *launches* Python — Terminal, iTerm, or your IDE — not to
Python itself, and that launcher must also be quit and relaunched.

Global hotkeys need **Input Monitoring** as well as Accessibility on macOS. If
they cannot start, the main window shows a "Hotkeys unavailable" link to
Settings; typing itself still works.

### Windows — no permission needed

Nothing to grant. One caveat the app cannot check in advance, so it states it as
a note: to type into an application that is running **as administrator**, Auto
Text Typer has to be running as administrator too, or its keystrokes will not
reach it.

### Linux — X11 works, Wayland does not

- **X11 (Xorg):** works, with no permission to grant.
- **Wayland:** blocks one application from sending keystrokes to another
  window, and there is no permission dialog that can grant an exception. The
  app detects this and says so instead of typing nothing. The fix is to use an
  X11 session: log out, pick an X11/Xorg session at the login screen (for
  example "Ubuntu on Xorg", "GNOME on Xorg", "Plasma (X11)"), and log back in.
- **No graphical session at all** (a bare SSH login, a headless box): also
  cannot be typed into, and is reported as such.

Wayland sessions run an XWayland server that still sets `DISPLAY`, so the app
checks the Wayland signals first; otherwise a Wayland session would look like
X11 and fail silently. Under Wayland, global hotkeys may work only while an X11
window has focus.

## Typing plain text

1. Paste or type your text into the box on the **Type** page. You can also drop
   a `.txt` or `.md` file onto it. A counter under the box shows characters,
   words and an estimated typing time.
2. Set the **Quick settings** above the box: which editor you are **Typing
   into**, which **Profile** to use, **Speed**, **Mistakes**, and whether to
   take breaks on long documents.
3. Press **Preview** before committing to a run. It plans the whole run and
   types nothing, showing the duration, effective words per minute, how many
   mistakes, revisions and breaks are coming, a timeline of the notable moments,
   and a line confirming the planned keystrokes replay back to exactly your text.
4. Press **Start typing** (or Ctrl/Cmd+Return). A countdown runs — five seconds
   by default, adjustable from 0 to 30 on the Settings page — during which you
   click into the document or field where the text should go.
5. Keep your hands off the mouse and keyboard while it types.
6. Press **Esc**, or the **Stop** button, to end a run early. **Pause** stops
   it where it is; resuming runs a short three-second countdown first.

While a run is going, a small progress window appears at the bottom right with
the percentage, ETA and Pause/Stop buttons. It never takes keyboard focus, so
your focus stays in the document being typed into. Turn it off under **Settings
→ Show mini progress window while typing**.

## Importing a formatted document

Above the text box is an import row. Paste a **Google Docs link** into it and
press **Import**, or press **Browse…** to pick a file.

Accepted sources:

| Source | Notes |
|---|---|
| Google Docs link | Fetched via the document's own HTML export URL |
| Google Drive link | Only if the file is a Word `.docx` |
| `.docx` | A Word document |
| `.html` / `.htm` | A raw Google Docs HTML export |
| `.zip` | The Google Docs "Web page (.html, zipped)" download |
| `.txt` / `.md` | Plain text, with no formatting to recover |

Google Sheets, Slides and Forms links are rejected by name — there is no
keyboard-typing equivalent of a spreadsheet grid or a slide canvas.

**A Google Doc has to be shared before the app can read it.** There is no
sign-in and no OAuth: the app fetches the document the same way an anonymous
browser would, so its sharing must be set to **Anyone with the link can view**.
Otherwise the import fails with a message telling you to change the sharing, or
to use **File → Download → Microsoft Word (.docx)** and import that file
instead.

On a successful import the text box is replaced by a read-only view of the
document showing its real headings, bold, italic, underline, strikethrough,
alignment and lists. **Back to plain text** drops the formatting and lets you
edit the words again.

What the document model can reproduce: paragraph styles (Normal, Title,
Subtitle, Heading 1 to 6), alignment (left, centre, right, justified), bullet
and numbered lists including nesting, and bold, italic, underline and
strikethrough.

### "Typing into" — the editor picker

Formatting is reproduced by pressing the target editor's own keyboard
shortcuts, so the app has to know which editor is receiving the keystrokes.
That choice is the **Typing into** picker, in Quick settings on the Type page
(and as a default on the Settings page):

| Editor | Available on | Shortcuts checked against the vendor's own reference |
|---|---|---|
| Google Docs | macOS, Windows, Linux | Yes |
| Microsoft Word | macOS, Windows | No |
| LibreOffice Writer | macOS, Windows, Linux | No |
| TextEdit | macOS | No |
| Any app (plain text) | macOS, Windows, Linux | Yes (nothing to get wrong) |

Microsoft Word has no Linux release, and TextEdit is macOS-only, so neither is
offered where it does not exist. The three marked "No" are best-effort
transcriptions that have not been checked against a live install; the app says
so under the picker and points you at the Advanced page, where any individual
shortcut can be corrected.

Each editor also carries its own note, shown under the picker:

- **Google Docs** — on Windows and Linux, justified text is typed left-aligned,
  because the shortcut for it opens the browser's developer tools instead of
  reaching the document.
- **Microsoft Word** — turn off "Automatic bulleted lists" and "Automatic
  numbered lists" under AutoCorrect Options → AutoFormat As You Type. Lists are
  typed as plain text starting with "1." or "•", and Word would otherwise turn
  those into a list of its own.
- **LibreOffice Writer** — turn off Tools → AutoCorrect → While Typing, for the
  same reason.
- **TextEdit** — the document must be in rich text mode (Format → Make Rich
  Text) first. In plain text mode TextEdit ignores formatting shortcuts, so
  nothing would be formatted.
- **Any app (plain text)** — for a web form, a chat box, a terminal. Everything
  is typed as plain text, and each new paragraph presses Enter, which may send a
  message or run a command.

### "What will not be reproduced"

A document can contain things that cannot be produced by typing at all. The
app does not drop those silently. A panel titled **What will not be
reproduced** sits directly above the editor picker and lists every loss the
importer noticed, in plain language — for example that images will be left out
because images cannot be typed, that links will be typed as plain text rather
than as clickable links, that a table will be typed as plain paragraphs one per
cell, or that custom fonts, sizes and colours will not survive.

Images, tables, fonts, colours, links, footnotes, comments and soft line breaks
have no representation in the document model at all, by design; this panel is
how their absence gets reported rather than hidden. The list depends on the
editor you picked and is recalculated the moment you change it. Where a loss is
caused by a missing shortcut rather than by the document, the panel says which
shortcut is missing for which editor — that is the part you can fix on the
Advanced page. When nothing is lost, the panel says so instead.

## Turn off autocorrect in the target application

**This is the single biggest cause of imperfect output, and it is not the app's
fault.** The guarantee below is about the *keystroke stream*: the keys that get
pressed reproduce your text exactly. What the receiving application does with
those keystrokes afterwards is outside the app's control, and it cannot read
back what the target rendered.

Google Docs and Word in particular rewrite text *after* the keystrokes land:
straight quotes become smart quotes, the first letter of something gets
capitalised, `--` becomes an em dash. If that is switched on, the text on screen
can differ from your source even though every keystroke sent was correct. Turn
it off before a real run:

- **Google Docs:** Tools → Preferences → uncheck the substitution options.
- **Microsoft Word:** AutoCorrect settings → uncheck "Replace text as you
  type". Also uncheck "Automatic bulleted lists" and "Automatic numbered
  lists" under AutoFormat As You Type.
- **LibreOffice Writer:** Tools → AutoCorrect → uncheck While Typing.

## Realism settings

Three built-in profiles differ in how often the typist makes mistakes and
second-guesses themselves:

| Preset | Mistakes per 100 characters | Revision chance per word |
|---|---|---|
| `clean` | 0.3 | 1 in 800 |
| `natural` | 1.2 | 1 in 500 |
| `sloppy` | 3.0 | 1 in 300 |

All three start from the same 65 words per minute. The Type page's Quick
settings adjust speed and mistake rate directly without going near a profile:
a speed slider from 20 to 160 wpm, and five mistake stops from None through
Natural to Sloppy.

Underneath, the realism comes from three stacked timing effects: a **lognormal**
base keystroke interval, because real keystroke timing is right-skewed rather
than uniform (uniform jitter is the classic tell of automation and is used
nowhere); **digraph multipliers** derived from QWERTY geometry, so same-finger
pairs are slow and alternating-hand pairs are fast; and a **mean-reverting
burst walk**, which produces stretches of fast and slow typing instead of
per-character noise. Thinking pauses sit on top of those at sentence, clause and
paragraph boundaries, and a fatigue term slows a long session down.

With session breaks on — the default — the planner also tracks simulated
elapsed time and inserts a real break every 7 to 15 minutes of typing, at a
sentence or paragraph boundary only. A break is 20 to 90 seconds, or with 20%
probability a 2 to 8 minute one. Coming back from a break starts with a
re-entry dip rather than an instant speed-up, and partially clears the fatigue
clock. An unbroken 40-minute typing session is itself a tell.

### The Advanced page

Everything above is a view onto one `Profile` object with 27 fields. The
**Advanced** page exposes all of them, in six collapsible groups — Speed,
Pauses, Mistakes, Revisions, Breaks, Formatting — with a label, a help line and
a control for each, carrying that field's usual range. The form is generated
from the model's own metadata, so it cannot drift out of step with the code.

The same page handles profile management: pick a profile, edit it, **Save** or
**Save as…** under a new name, **Delete** (which restores the built-in if you
had customised one), or **Reset to defaults**. A customised built-in shadows the
built-in everywhere.

Also on the Advanced page: a **Keyboard shortcuts** editor, one row per
formatting command per editor, for correcting a shortcut that is wrong in your
version of an application; and a **file locations** panel showing where the
profile and settings files live, with a button to open the folder. Both files
are plain text and can be edited by hand while the app is closed.

## Global hotkeys and the tray

Global hotkeys drive a run from any application, without switching back to the
window. The defaults:

| Action | macOS | Windows and Linux |
|---|---|---|
| Start typing what is on the Type page | `<cmd>+<alt>+<shift>+t` | `<ctrl>+<alt>+<shift>+t` |
| Pause, or resume | `<cmd>+<alt>+<shift>+p` | `<ctrl>+<alt>+<shift>+p` |
| Stop a run that is typing | `<esc>` | `<esc>` |

They can be turned off and rewritten under **Settings → Global hotkeys**. A
hotkey that would fire during ordinary typing is refused with an explanation
rather than saved: a bare letter or digit, or Shift plus one, is rejected
because it would trigger every time you typed it. So is a chord that duplicates
or contains another hotkey, and so is one the app presses itself as a formatting
shortcut. Function keys and `Esc` are allowed on their own.

The tray icon — the menu bar on macOS — carries **Show window**, **Start typing
from clipboard**, **Pause**/**Resume**, **Stop** and **Quit**, so a run can be
controlled while the window is out of the way. Not every Linux desktop has a
tray; where there is none, the app carries on without one. Because starting from
the tray or a hotkey means the text is not in front of you, **Settings → Confirm
before typing from the tray or a hotkey** asks first, and is on by default.

## The command line

A headless entry point for plain text. It types a text file and nothing else —
document import, formatting and the editor picker are GUI-only.

```bash
auto-text-typer essay.txt --preset natural --wpm 70
auto-text-typer essay.txt --dry-run          # preview, no typing
```

From a source checkout, without installing the console script:

```bash
.venv/bin/python -m auto_essay_typer.cli essay.txt --preset natural --wpm 70
```

It takes one positional argument, the path to the text file to type, plus:

| Flag | What it does |
|---|---|
| `--preset PRESET` | Named typing profile: a builtin (`clean`, `natural`, `sloppy`) or a saved user profile. Defaults to `natural` |
| `--profile PROFILE` | Alias for `--preset`; whichever of the two is given wins |
| `--wpm WPM` | Target words per minute |
| `--typo-rate TYPO_RATE` | Mistakes per 100 characters |
| `--set KEY=VALUE` | Override a single profile field, e.g. `--set wpm=90`. Repeatable |
| `--seed SEED` | Reproduce an exact run |
| `--countdown COUNTDOWN` | Seconds to focus the target window. Defaults to 5 |
| `--dry-run` | Print what would be typed and how long it would take, then exit |
| `--list-profiles` | List every available profile, builtin and user-saved, then exit |
| `--show-profile` | Print the fully resolved profile as TOML, then exit |
| `--save-profile NAME` | Save the fully resolved profile under NAME, then exit |
| `--delete-profile NAME` | Delete a previously saved user profile, then exit |
| `--config-path` | Print the path to the user profiles config file, then exit |

Precedence, lowest to highest: `--preset`/`--profile` < `--wpm`/`--typo-rate` <
`--set`. The last five need no `file` argument.

`--set KEY=VALUE` reaches any of the 27 profile fields by name; range fields
take a comma-separated pair, as in `--set burst_range=0.7,1.3`. Saved profiles
are hand-editable TOML, and a malformed file, an unknown field or a wrong-sized
range raises an error naming the file and the bad field, so a typo in the config
is never silently ignored.

```bash
.venv/bin/python -m auto_essay_typer.cli essay.txt --profile natural --set wpm=90 --set session_breaks=false
.venv/bin/python -m auto_essay_typer.cli --preset natural --wpm 80 --save-profile my-fast
.venv/bin/python -m auto_essay_typer.cli --list-profiles
```

Ctrl-C stops a run.

## The guarantee

The text that ends up in your document is character-for-character identical to
the text you supplied. Every mistake the engine injects is provably corrected.

The reason this can be stated rather than hoped for is that planning is a pure
function. `plan(text, profile, seed)` does no I/O, touches no clock and no
keyboard, and returns a list of key-press, backspace, pause and formatting
actions; a separate driver executes that list. So the whole realism engine can
be checked by pure computation:

> `simulate(plan(text, profile, seed)) == text`

`simulate` replays the generated keystroke stream through a virtual text
buffer — appending characters, deleting on backspace, raising if a backspace
would run past the start — and the test suite asserts the result equals the
input. Every typo is constructed as a `(wrong, correct)` pair covering an exact
span of source text, so its correction is pure arithmetic and no typo can
survive into the output. A session break is nothing but an extra pause action,
so it cannot break the invariant either.

`tests/test_invariant.py` exercises that property roughly 21,500 times per run
(104 collected test items): 20 corpus texts across 5 stress profiles at 40 seeds
each, 10,000 random `(text, profile, seed)` combinations, 2,000 randomly
generated strings, 2,000 cases checking nothing ever backspaces past the start,
and 14 adversarial texts at 50 seeds each. The corpus runs from empty and
one-character input through pure whitespace, symbols, digits, accented and
non-Latin characters, an emoji outside the basic plane and tabs.

```bash
.venv/bin/python -m pytest tests/test_invariant.py -q
```

Formatted documents get the same treatment with a second referee:
`simulate_document(plan_document(doc)).normalized() == doc.normalized()`, in
`tests/test_document_pipeline.py`, which runs the whole import → adapt → plan →
drive pipeline end to end.

## Building from source

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

### Running the tests

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

### Packaging

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
[Checking that the install works](#checking-that-the-install-works); CI runs it
on all three platforms.

## Licence

MIT. Copyright (c) 2026 Braxton Gorman. Full text in [LICENSE](LICENSE).
