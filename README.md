# Auto Essay Typer

Types text you supply into any focused macOS application, with the timing,
hesitation, and self-corrected mistakes of a real typist.

It does not write essays. You provide the text; it reproduces it exactly.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

### Grant Accessibility permission (required)

macOS blocks synthetic keystrokes by default. Grant permission to the app that
*launches* the script — Terminal, iTerm, or your IDE — not to Python itself:

**System Settings → Privacy & Security → Accessibility**

Quit and relaunch that app afterwards. macOS does not apply the change to
already-running processes. Without this, nothing is typed and no error is
raised — see [Limitations](#limitations).

## Use

### GUI

```bash
.venv/bin/python -m auto_essay_typer.app
```

Paste your text, choose a preset, then **F9** to start (five seconds to click
into your document), **F10** to pause/resume, **Esc** to abort.

Beyond the original three controls, the window now has:

- A **character/word counter** above the text box, live as you type or paste.
- A **profile dropdown** (built-ins are labelled `[builtin]`) with **Save
  As...**, **Delete**, and **Reload** buttons for managing your own saved
  profiles from `~/.config/auto-essay-typer/profiles.toml` without touching
  the CLI.
- A collapsible **Advanced settings** panel (click "▸ Advanced settings")
  editing all 26 `Profile` fields, grouped into the same five sections used
  throughout this README: Speed, Thinking pauses, Mistakes, Revision, Session
  breaks. Its field list is generated from the `Profile` dataclass itself, so
  it never drifts out of sync with `profiles.py`.
- A **Preview** pane. **Dry Run** now renders a colour-coded, line-by-line
  trace of the plan (typos in red, revisions in blue, breaks in purple,
  ordinary pauses in grey) followed by a summary: character count, estimated
  duration, effective WPM, typo/revision/break counts, the longest single
  pause, and a `output matches input exactly` / `MISMATCH` line.
- A **progress bar** with percentage, ETA, and elapsed time, shown once a
  real (non-dry-run) run is in progress.

### CLI

```bash
.venv/bin/python -m auto_essay_typer.cli essay.txt --preset natural --wpm 70
.venv/bin/python -m auto_essay_typer.cli essay.txt --dry-run   # preview, no typing
```

Full flag list, confirmed by running `.venv/bin/python -m auto_essay_typer.cli --help`:

```
usage: auto-essay-typer [-h] [--preset PRESET] [--profile PROFILE] [--wpm WPM]
                        [--typo-rate TYPO_RATE] [--set KEY=VALUE]
                        [--seed SEED] [--countdown COUNTDOWN] [--dry-run]
                        [--list-profiles] [--show-profile]
                        [--save-profile NAME] [--delete-profile NAME]
                        [--config-path] [--menubar]
                        [file]

Type a text file into the focused application with human-like rhythm.

positional arguments:
  file                  path to the text file to type

options:
  -h, --help            show this help message and exit
  --preset PRESET       named typing profile: a builtin (clean, natural,
                        sloppy) or a saved user profile; see --list-profiles
  --profile PROFILE     alias for --preset; whichever of the two is given wins
  --wpm WPM             target words per minute
  --typo-rate TYPO_RATE
                        mistakes per 100 characters
  --set KEY=VALUE       override a single profile field, e.g. --set wpm=90
                        (repeatable; applied after --preset/--profile and
                        --wpm/--typo-rate, so --set always wins)
  --seed SEED           reproduce an exact run
  --countdown COUNTDOWN
                        seconds to focus the target window
  --dry-run             print what would be typed and how long it would
                        take, then exit
  --list-profiles       list every available profile (builtin and user-saved)
                        and exit
  --show-profile        print the fully resolved profile as TOML to stdout,
                        then exit
  --save-profile NAME   save the fully resolved profile under NAME for later
                        reuse via --profile, then exit
  --delete-profile NAME
                        delete a previously saved user profile, then exit
  --config-path         print the path to the user profiles config file, then
                        exit
  --menubar             launch the macOS menu-bar app instead of the CLI
                        (needs the 'menubar' extra)

precedence (lowest to highest): --preset/--profile  <  --wpm/--typo-rate  <  --set

examples:
  auto-essay-typer essay.txt --preset sloppy --dry-run
  auto-essay-typer essay.txt --profile natural --set wpm=90 --set session_breaks=false
  auto-essay-typer essay.txt --preset natural --wpm 80 --save-profile my-fast
  auto-essay-typer essay.txt --profile my-fast

management flags (no "file" argument needed):
  auto-essay-typer --list-profiles
  auto-essay-typer --show-profile --profile my-fast
  auto-essay-typer --config-path
```

`--wpm` and `--typo-rate` override whatever the chosen `--preset`/`--profile`
sets for those two fields; `--set KEY=VALUE` (repeatable) overrides any of the
26 fields individually and always wins. See [Customizing](#customizing) for
the full field list and the config file format.

### Menu-bar mode

An optional macOS menu-bar app, for typing without a terminal window open.
It needs the `menubar` extra (`rumps`, currently pinned as `rumps>=0.4` in
`pyproject.toml`):

```bash
.venv/bin/pip install -e ".[menubar]"
.venv/bin/python -m auto_essay_typer.menubar
# or:
.venv/bin/python -m auto_essay_typer.cli --menubar
```

Running it without the extra installed fails with a clear message telling you
to install `auto-essay-typer[menubar]`, rather than a bare
`ModuleNotFoundError`.

The menu bar icon shows the current state (idle, counting down, typing,
paused, done) and its menu offers:

- **Type from Clipboard** — reads the current clipboard via `pbpaste` and
  types it. This is the only clipboard-input path in the project; the CLI and
  GUI both take text from a file or the pasted-in text box, not the
  clipboard.
- **Type from File…** — a native file-open panel.
- **Profile** — a submenu listing every builtin and saved profile, with a
  checkmark on the active one.
- **Pause/Resume** and **Abort** — mirror F10/Esc, greyed out when nothing is
  running.
- **Open Full Window…** — launches the Tkinter GUI (`app.py`) as a separate
  process, since Tk and `rumps` both need the main thread and can't share one.
- **Quit**.

## Customizing

Every knob in `Profile` (26 fields; verify yourself with
`.venv/bin/python -c "from auto_essay_typer.profiles import field_names; print(len(field_names()))"`)
is reachable without editing code, three ways:

1. **Presets/profiles** — `--preset natural` (or its alias `--profile
   natural`) picks a full set of defaults. `clean`, `natural`, and `sloppy`
   are always available; anything else is looked up in your saved profiles.
2. **`--wpm` / `--typo-rate`** — the two most common overrides get their own
   flags, applied on top of the chosen preset/profile.
3. **`--set KEY=VALUE`** (repeatable) — overrides any single field by name.
   Tuple fields take a comma-separated pair, e.g. `--set burst_range=0.7,1.3`.
   `--set` always wins over everything else.

```bash
.venv/bin/python -m auto_essay_typer.cli essay.txt --profile natural --set wpm=90 --set session_breaks=false
```

### The config file

Saved profiles live in a hand-editable TOML file. Its location honours (in
order) `$AUTO_ESSAY_TYPER_CONFIG` (an explicit full-path override), then
`$XDG_CONFIG_HOME`, then falls back to `~/.config/auto-essay-typer/profiles.toml`.
Print the resolved path for your own shell with:

```bash
.venv/bin/python -m auto_essay_typer.cli --config-path
```

A profile you save under an existing preset's name (e.g. `natural`) shadows
that builtin everywhere — `--preset natural` then resolves to your version.

### A real worked example

Resolve a profile, layer overrides on top, and save it — this is genuine
output from running the commands below, not hand-written TOML:

```bash
.venv/bin/python -m auto_essay_typer.cli --preset natural --set wpm=90 --set session_breaks=false --save-profile my-fast
```

```
saved profile 'my-fast' to /Users/you/.config/auto-essay-typer/profiles.toml
```

`--show-profile --profile my-fast` (or opening the file directly) shows the
result, and it round-trips cleanly back through `Profile.from_dict`:

```toml
["my-fast"]
backspace_ms = [40.0, 60.0]
break_fatigue_recovery = 0.6
break_interval_ms = [420000.0, 900000.0]
burst_range = [0.8, 1.25]
burst_reversion = 0.08
burst_sigma = 0.06
clause_pause_ms = [120.0, 350.0]
fatigue_max = 0.15
fatigue_tau_ms = 1200000.0
hesitation_ms = [300.0, 1400.0]
hesitation_probability = 0.02
lognormal_sigma = 0.35
long_break_ms = [120000.0, 480000.0]
long_break_probability = 0.2
long_word_threshold = 9
oh_moment_ms = [150.0, 400.0]
paragraph_pause_ms = [1500.0, 5000.0]
post_break_burst = 1.15
retype_care_factor = 1.15
revision_rate_per_word = 0.002
revision_span_words = [2, 5]
sentence_pause_ms = [400.0, 1200.0]
session_breaks = false
short_break_ms = [20000.0, 90000.0]
typo_rate_per_100 = 1.2
wpm = 90.0
```

`--list-profiles` shows built-ins and saved profiles side by side:

```
clean                builtin  wpm=65.0 typo_rate_per_100=0.3
my-fast              user     wpm=90.0 typo_rate_per_100=1.2
natural              builtin  wpm=65.0 typo_rate_per_100=1.2
sloppy               builtin  wpm=65.0 typo_rate_per_100=3.0
```

A malformed file, an unknown field, or a wrong-sized tuple in the TOML raises
an error naming the file and the bad field — a typo'd config is never
silently ignored.

### All 26 fields

Defaults below are read directly from the `Profile` dataclass in
`auto_essay_typer/profiles.py`, not from memory. Timing values are
milliseconds; two-value fields are inclusive `(low, high)` ranges sampled
uniformly unless noted otherwise.

**Speed**

| Field | Default | What it does |
|---|---|---|
| `wpm` | `65.0` | Target words per minute; sets the base time between keystrokes. |
| `lognormal_sigma` | `0.35` | Spread of the lognormal keystroke-interval distribution — higher is more erratic timing. |
| `burst_sigma` | `0.06` | Size of the random nudge, applied each keystroke, driving the mean-reverting "burst" walk. |
| `burst_reversion` | `0.08` | How strongly the burst walk is pulled back toward its neutral value (1.0) each keystroke. |
| `burst_range` | `(0.8, 1.25)` | Floor/ceiling clamp on the burst multiplier. |
| `fatigue_max` | `0.15` | Maximum slowdown, as a fraction, that sustained typing can add to the keystroke interval. |
| `fatigue_tau_ms` | `1200000.0` (20 min) | Time constant for how quickly fatigue climbs toward `fatigue_max`. |

**Thinking pauses**

| Field | Default | What it does |
|---|---|---|
| `sentence_pause_ms` | `(400.0, 1200.0)` | Pause after sentence-ending punctuation (`.`, `?`, `!`). |
| `clause_pause_ms` | `(120.0, 350.0)` | Pause after clause-ending punctuation (`,`, `;`, `:`). |
| `paragraph_pause_ms` | `(1500.0, 5000.0)` | Pause after a newline. |
| `hesitation_probability` | `0.02` | Chance of an extra hesitation pause before a long upcoming word. |
| `hesitation_ms` | `(300.0, 1400.0)` | Duration of that extra hesitation pause. |
| `long_word_threshold` | `9` | Word length, in characters, at or above which the hesitation check applies. |

**Mistakes**

| Field | Default | What it does |
|---|---|---|
| `typo_rate_per_100` | `1.2` | Mistakes injected per 100 characters typed. |
| `oh_moment_ms` | `(150.0, 400.0)` | Pause after a mistake is noticed, before backspacing starts. |
| `backspace_ms` | `(40.0, 60.0)` | Per-keystroke interval while backspacing during a correction. |
| `retype_care_factor` | `1.15` | Multiplier slowing keystrokes while retyping a correction or a revision (typed more carefully). |

**Revision**

| Field | Default | What it does |
|---|---|---|
| `revision_rate_per_word` | `0.002` (1/500) | Chance, checked at each sentence boundary, of backing up and rewriting text already typed. |
| `revision_span_words` | `(2, 5)` | How many trailing words a revision rewrites. |

**Session breaks**

| Field | Default | What it does |
|---|---|---|
| `session_breaks` | `true` | Whether the typist takes breaks during a long session. |
| `break_interval_ms` | `(420000.0, 900000.0)` (7–15 min) | Simulated typing time between breaks. |
| `short_break_ms` | `(20000.0, 90000.0)` (20–90 s) | Duration of an ordinary break. |
| `long_break_probability` | `0.2` | Chance that a given break is a long one instead of a short one. |
| `long_break_ms` | `(120000.0, 480000.0)` (2–8 min) | Duration of a long break. |
| `post_break_burst` | `1.15` | Burst-walk value set on return from a break. This multiplies the keystroke interval directly, so **values above 1.0 are slower, below 1.0 faster** — the default sits above the walk's neutral 1.0 so the next stretch of typing starts with a re-entry dip and eases back to normal. Clamped into `burst_range` when applied. |
| `break_fatigue_recovery` | `0.6` | Fraction of the accumulated fatigue clock that a break clears. |

## Presets

| Preset | Mistakes per 100 chars |
|---|---|
| `clean` | 0.3 |
| `natural` | 1.2 |
| `sloppy` | 3.0 |

These are just three built-in profiles; save your own with `--save-profile`
(see [Customizing](#customizing)) and use it the same way via `--preset`/`--profile`.

Pass `--seed N` to reproduce an exact run.

## Turn off autocorrect in the target application

This is the one thing that can corrupt output. Google Docs and Word rewrite
text *after* our keystrokes land — smart quotes, autocapitalisation, `--` to em
dash. Disable it before a real run:

- **Google Docs:** Tools → Preferences → uncheck the substitution options
- **Word:** Settings → AutoCorrect → uncheck "Replace text as you type"

See [Limitations](#limitations): this is outside the tool's control by design.

## Guarantee

The final text is character-for-character identical to your input. Every
injected mistake is corrected, and a session break is nothing more than an
extra `Pause` action, so it cannot break this guarantee either. This is
enforced by a property test that replays generated action streams through a
text buffer:

```bash
.venv/bin/pytest tests/test_invariant.py -q
```

`tests/test_invariant.py` defines 5 test functions. One of them is
parametrized over 20 corpus texts and 5 stress profiles (100 combinations,
40 seeds each), which is why `pytest` reports **104 passed** rather than 5
— that's the collected-item count, not the number of `simulate()` calls
actually made. Counting the loops inside every test function (100 × 40,
plus `range(10_000)`, `range(2_000)` twice, and 14 adversarial texts × 5
profiles × 50 seeds), the suite actually exercises the invariant around
21,500 times per run. The whole file completes in about 2.9 seconds on the
machine used to verify this README (measured with `time`, three runs,
2.87-2.96s; your machine will vary). The original design draft's "12,000+
cases" undersold the collected-item count (104, not 12,000+) while, at the
same time, undercounting the real number of simulated replays (~21,500) —
both real numbers are given above so neither is taken on faith.

The full suite — including config, session-break, and GUI/menu-bar-controller
tests — is 341 tests, all passing at the time of writing:

```bash
.venv/bin/pytest -q
```

## How it works

`planner.plan(text, profile, seed)` is a pure function returning a list of
`KeyPress` / `Backspace` / `Pause` actions — no I/O, no clock, no keyboard.
`Driver` executes that stream. The split is what makes the realism engine
testable and the guarantee provable.

Realism comes from three stacked effects: a **lognormal** base interval (real
keystroke timing is right-skewed, not uniform), **digraph multipliers** from
QWERTY geometry (same-finger pairs slow, alternating hands fast), and a
**mean-reverting burst walk** producing stretches of fast and slow typing.
Mistakes are noticed after a sampled delay, then backspaced and retyped.

### Session breaks

`session_breaks` is on by default. `planner.plan` tracks simulated elapsed
typing time and, once 7–15 minutes (`break_interval_ms`) have passed *and*
the planner is sitting at a sentence or paragraph boundary, it inserts a
break: a single `Pause` of 20–90 seconds (`short_break_ms`), or, with 20%
probability (`long_break_probability`), a longer 2–8 minute break
(`long_break_ms`). A break can only land where
`_at_sentence_or_paragraph_boundary` in `planner.py` says it may — never
mid-word — so it stays purely an extra `Pause` and can never disturb the
`simulate(plan(text)) == text` invariant.

A break also partially recovers fatigue (`break_fatigue_recovery`, 60% of the
accumulated fatigue clock by default) and resets the burst-walk multiplier to
`post_break_burst` (1.15 by default, clamped into `burst_range` — above the
walk's neutral value of 1.0, since the burst multiplier scales the keystroke
interval directly and so a value above 1.0 is *slower*). Concretely, this
means the stretch right after a break resumes at a *dip*: a real typist
re-reads the last sentence and re-orients before getting going again, rather
than instantly speeding up. The burst walk then mean-reverts back toward 1.0
over the following keystrokes, and because fatigue was also partially
cleared, the typist ends up at or above their pre-break pace once the dip
has worn off. `tests/test_session_breaks.py` verifies this directly by
comparing median keystroke intervals immediately after a break (slower) and
further after the break, once the burst walk has settled (at least as fast).
This is why an unbroken 40-minute typing session is
itself a tell: the whole point of `session_breaks` is that real attention
wanders and real hands get tired, so a long stretch with no pause longer than
a sentence-thinking pause reads as mechanical regardless of how realistic the
per-keystroke timing is.

Set `--set session_breaks=false` (CLI/config) or uncheck it in the GUI's
Advanced panel to turn breaks off.

### Substitution slips stay within the same character class

When a substitution typo is chosen, the candidate replacement keys come from
`layout.neighbors(char)` — physically adjacent keys, including the number
row above the top letter row, because that is geometrically real. But a real
typist very rarely produces a digit in the middle of a word or a letter in
the middle of a number; that reach is longer and struck far less often
during prose typing, and a stray digit reads as a machine artefact rather
than a human slip. `errors._plausible_slips` therefore filters neighbours so
letters only slip to letters and digits only slip to digits. Punctuation and
symbol keys are left unfiltered, since those genuinely sit beside a mix of
letters, digits, and other punctuation on a real keyboard.

## Verifying it works for real

The automated suite proves the *planning* logic — that whatever gets typed
and backspaced always nets out to your exact input. It cannot prove that real
keystrokes actually land in a real application, because that requires macOS
Accessibility permission and a focused window, neither of which exists in a
test run. To check that for yourself:

1. Grant Accessibility permission as described above, and make sure the
   terminal/IDE you're running from has been relaunched since granting it.
2. Create a sample file:

   ```bash
   printf 'The argument rests on a single premise. That premise is contested.\n\nIt does not follow.\n' > /tmp/sample.txt
   ```

3. Open TextEdit and create a new blank document.
4. Run:

   ```bash
   .venv/bin/python -m auto_essay_typer.cli /tmp/sample.txt --preset sloppy --seed 7
   ```

5. During the five-second countdown printed in the terminal, click into the
   TextEdit window so it has focus.
6. Watch the text appear. With `--preset sloppy` you should see visible
   typos being typed and then backspaced/corrected as it goes — this is the
   behaviour the invariant test suite proves happens exactly, not just
   approximately.
7. When it finishes, compare the TextEdit document against
   `/tmp/sample.txt` — they should be identical.
8. Run it again and press **Esc** partway through to confirm the abort path
   leaves whatever was typed so far in place and stops without error.

If nothing gets typed and no error appears anywhere, this is almost always
the Accessibility permission not being granted to the process actually
running Python (see [Limitations](#limitations)) — not a bug in the planner.

## Limitations

- **QWERTY only.** The layout geometry in `layout.py` only maps standard
  US-QWERTY key positions. Characters and physical layouts it doesn't
  recognize aren't rejected — `layout.key_info` returns `None` for them, and
  the code treats that as neutral: no digraph speed-up/slow-down, no
  adjacent-key substitution slips, no doubling/transposition typos sited
  there. So text typed on or containing non-QWERTY layout assumptions
  (AZERTY punctuation, Dvorak, non-Latin scripts) is still typed
  character-for-character correctly, and the invariant still holds — it
  just types with flatter, less characterful timing and without the typo
  realism that QWERTY text gets.
- **macOS only, but the core is not.** Only two modules import `pynput`
  directly: `driver.py` (keystroke injection via `PynputKeyboard`) and
  `app.py` (the F9/F10/Esc global hotkey listener). Neither contains
  macOS-specific code — no `Quartz`, `AppKit`, or `sys.platform` checks
  appear anywhere in `auto_essay_typer/` (verified by grep), aside from the
  menu-bar mode below. `pynput` itself has Windows and Linux backends.
  "macOS only" describes what has actually been built and verified here —
  the Accessibility/TCC permission flow documented above, and the
  silent-failure behavior below, are macOS specifics — not a hard
  architectural wall. A port would mean re-verifying permission behavior on
  the target OS, not rewriting `planner.py`, `timing.py`, `errors.py`,
  `layout.py`, or `simulator.py`, which are pure and platform-agnostic.
- **Menu-bar mode is macOS-only and needs an optional dependency.**
  `menubar.py` imports `rumps` (and, for its file-open dialog, `AppKit`)
  directly, with no cross-platform fallback. It is not installed by
  `pip install -e ".[dev]"` — you need `pip install -e ".[menubar]"`
  separately, and importing `auto_essay_typer.menubar` without it raises a
  `RuntimeError` with install instructions rather than typing nothing
  silently.
- **Clipboard input only exists in menu-bar mode.** "Type from Clipboard"
  reads via `pbpaste` inside `menubar.py`; the CLI and GUI both take text
  from a file path or the pasted-in text box, never from the clipboard
  directly.
- **Target-application autocorrect happens after we're done.** The tool's
  guarantee is that the *keystroke stream* reproduces your input exactly.
  It has no visibility into what the target application does with those
  keystrokes afterward. Google Docs and Word, in particular, rewrite text
  post-hoc (smart quotes, autocapitalization, `--` → em dash). If that's
  enabled, the on-screen result can differ from your source text even
  though every keystroke we sent was correct. This is turned off in the
  target application (see above), not something this tool can detect or
  prevent — it has no way to read back what the target app rendered.
- **Accessibility permission fails silently.** If macOS has not granted
  Accessibility access to the process running Python, `pynput`'s
  `Controller.type`/`tap` calls do not raise an exception and do not type
  anything. There is no error message, no return code, nothing in this
  tool's control to detect or surface — the CLI will print "Typing N
  characters..." and "done" as if it worked. The only symptom is that no
  characters appear in the target application. If a run produces no visible
  typing, check Accessibility permission first, before assuming a bug in
  the planner or driver.
