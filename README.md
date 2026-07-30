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

GUI:

```bash
.venv/bin/python -m auto_essay_typer.app
```

Paste your text, choose a preset, then **F9** to start (five seconds to click
into your document), **F10** to pause/resume, **Esc** to abort.

CLI:

```bash
.venv/bin/python -m auto_essay_typer.cli essay.txt --preset natural --wpm 70
.venv/bin/python -m auto_essay_typer.cli essay.txt --dry-run   # preview, no typing
```

Full flag list, confirmed by running `.venv/bin/python -m auto_essay_typer.cli --help`:

```
usage: auto-essay-typer [-h] [--preset {clean,natural,sloppy}] [--wpm WPM]
                        [--typo-rate TYPO_RATE] [--seed SEED]
                        [--countdown COUNTDOWN] [--dry-run]
                        file

Type a text file into the focused application with human-like rhythm.

positional arguments:
  file                  path to the text file to type

options:
  -h, --help            show this help message and exit
  --preset {clean,natural,sloppy}
  --wpm WPM             target words per minute
  --typo-rate TYPO_RATE
                        mistakes per 100 characters
  --seed SEED           reproduce an exact run
  --countdown COUNTDOWN
                        seconds to focus the target window
  --dry-run             print what would be typed and how long it would
                        take, then exit
```

`--wpm` and `--typo-rate` override whatever the chosen `--preset` sets for
those two fields; every other knob (pause lengths, hesitation probability,
revision rate, ...) comes from the preset and is not exposed as a flag.

## Presets

| Preset | Mistakes per 100 chars |
|---|---|
| `clean` | 0.3 |
| `natural` | 1.2 |
| `sloppy` | 3.0 |

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
injected mistake is corrected. This is enforced by a property test that replays
generated action streams through a text buffer:

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
2.88-2.89s; your machine will vary). The original design draft's "12,000+
cases" undersold the collected-item count (104, not 12,000+) while, at the
same time, undercounting the real number of simulated replays (~21,500) —
both real numbers are given above so neither is taken on faith.

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
  appear anywhere in `auto_essay_typer/` (verified by grep). `pynput` itself
  has Windows and Linux backends. "macOS only" describes what has actually
  been built and verified here — the Accessibility/TCC permission flow
  documented above, and the silent-failure behavior below, are macOS
  specifics — not a hard architectural wall. A port would mean re-verifying
  permission behavior on the target OS, not rewriting `planner.py`,
  `timing.py`, `errors.py`, `layout.py`, or `simulator.py`, which are pure
  and platform-agnostic.
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
