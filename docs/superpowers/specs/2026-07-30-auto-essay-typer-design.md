# Auto Essay Typer — Design

**Date:** 2026-07-30
**Status:** Approved

## Status Note — Partly Superseded

**Added 2026-11-09.** This is a dated design document, not living documentation.
It records the design as approved on 2026-07-30, and the project has since
outgrown it. **Where this document and the code disagree, the code is
authoritative.**

What changed:

- **Platforms.** No longer macOS-only. macOS, Windows and Linux are all
  supported; `platforms.py` resolves the config directory, the primary modifier
  key, and the input-permission check per OS. "Non-macOS platforms" under *Out
  of Scope* below no longer holds.
- **GUI toolkit.** There is no Tkinter `app.py`. The interface is a Qt
  (PySide6) GUI under `auto_essay_typer/gui/`, with a Type page, a Settings
  page, an Advanced page, a tray icon and a first-run walkthrough.
- **Document import.** Formatted documents can be imported (`importers/`:
  .docx, Google Docs, exported HTML, plain text) and typed with their headings,
  emphasis, alignment and lists reproduced — on top of the plain-text typing
  this document describes. `document.py` models what is reproducible that way.
- **Target editors.** Formatting is not Google-Docs-only. `targets.py` holds a
  keymap per (editor, OS) for Google Docs, Microsoft Word, LibreOffice Writer,
  TextEdit, and a plain-text fallback; `keymaps.adapt_document` fits a document
  to whatever its target can actually express. Google Docs remains the
  reference editor the document model was designed against.
- **Smaller drift.** The `typer_core/` subpackage in the module layout below is
  flat under `auto_essay_typer/`, and the global hotkeys are no longer
  `F9`/`F10` (see `app_settings.default_hotkeys`, which also explains why).

What held: the core invariant, the plan/execute split, and the pure, seeded
planner are all intact, and `simulator.py` is still the referee.

Everything below is the original document, unchanged.

## Purpose

A macOS desktop tool that types a body of text into any focused application using
OS-level keystroke simulation, with timing, hesitation, and error-correction
behavior modeled on human typists.

The tool does not generate text. It reproduces text the user supplies.

## Core Invariant

**The final text on screen must be character-for-character identical to the input.**

Every error the engine injects must be provably corrected. This is enforced as a
property test: replay the generated action stream through a virtual text buffer and
assert `buffer == input_text`, over at least 10,000 random seeds spanning varied
inputs (unicode, long words, punctuation runs, empty lines).

No typos are left uncorrected. This was an explicit product decision, not a default.

## Architecture

A strict plan/execute split:

```
text ──► Planner ──► [Action] stream ──► Driver ──► real keystrokes
         (pure, seeded, zero I/O)        (pynput)
```

The planner is a pure function: `(text, profile, seed) -> list[Action]`.
It performs no I/O, reads no clock, and touches no keyboard. Given the same
`(text, profile, seed)` it returns an identical stream.

`Action` is a closed union of three variants:

- `KeyPress(char: str)` — emit one character
- `Backspace(count: int)` — delete `count` characters
- `Pause(ms: float)` — wait

Delays between keystrokes are carried as explicit `Pause` actions rather than
attached to `KeyPress`, so the simulator and driver share one uniform interpretation
of the stream.

### Why this split

1. The entire realism engine is unit-testable with no keyboard involvement.
2. The core invariant is checkable by pure computation (`simulator.py`).
3. `--dry-run` can print exactly what would be typed, typos and all, before the
   user trusts it with a real document.
4. A seed makes any observed behavior reproducible for debugging.

### Module layout

```
auto_essay_typer/
├── typer_core/
│   ├── layout.py      # QWERTY geometry: char -> (row, col, finger, hand, shift)
│   ├── timing.py      # digraph delays, fatigue, burst walk, thinking pauses
│   ├── errors.py      # typo injection + correction planning
│   ├── planner.py     # text -> Action stream (orchestrates timing + errors)
│   ├── simulator.py   # Action stream -> text (invariant verification)
│   └── driver.py      # executes stream; pause/abort/countdown
├── app.py             # Tkinter GUI + global hotkeys
├── cli.py             # headless entry point
└── tests/
```

Each module has one responsibility and a narrow interface. `layout.py` knows
nothing about timing. `timing.py` knows nothing about errors. `planner.py` is the
only module that composes them.

## Component: layout.py

Static QWERTY model. For each character: physical row, column, assigned finger,
hand, and whether shift is required. Derived lookups:

- `neighbors(char) -> list[str]` — physically adjacent keys, for plausible slips
- `same_finger(a, b) -> bool`
- `same_hand(a, b) -> bool`
- `needs_shift(char) -> bool`

Characters outside the modeled layout (accented letters, em dashes, unicode
punctuation) fall back to neutral defaults: no shift penalty, no neighbors, average
finger assignment. They are typed correctly and never selected as typo sites.

## Component: timing.py

Inter-keystroke intervals are right-skewed and digraph-dependent. Uniform random
delay is the primary tell of automated typing, so it is not used anywhere.

**Base interval.** WPM converts to a mean interval using the standard 5-characters-
per-word convention (60 WPM = 300 chars/min = 200 ms/char). Each interval is sampled
from a **lognormal** distribution around the adjusted mean, producing the natural
long right tail of real typing.

**Digraph multipliers**, applied to the mean before sampling:

| Condition | Multiplier |
|---|---|
| Same finger, different key (`ed`, `un`) | x1.6 |
| Same hand, adjacent finger | x1.05 |
| Alternating hands (`th`, `an`) | x0.85 |
| Same key repeated | x0.9 |
| Space | x0.9 |
| Number / symbol row | x1.4 |
| Shift required | +40-90 ms additive |

**Burst walk.** A mean-reverting (Ornstein-Uhlenbeck style) random walk clamped to
[0.8, 1.25] multiplies the interval. This produces *stretches* of fast and slow
typing rather than per-character white noise, and is the largest realism
contributor after digraph modeling.

**Fatigue.** The mean interval drifts upward by up to ~15% over ~20 minutes of
continuous typing, recovering partially after long pauses.

## Component: thinking pauses

Layered on top of the base timing, as explicit `Pause` actions:

| Trigger | Duration |
|---|---|
| Sentence end (`.`, `?`, `!`) | 400-1200 ms |
| Comma, semicolon, colon | 120-350 ms |
| Paragraph break | 1.5-5 s |
| Before a long or uncommon word | occasional word-retrieval hesitation |
| Mid-sentence stall | rare |

## Component: errors.py

### Typo classes

Weighted by observed real-world frequency:

| Type | Example | Weight |
|---|---|---|
| Transposition | `teh` <- `the` | 35% |
| Adjacent-key substitution | `sonething` <- `something` | 30% |
| Doubling / insertion | `tthe` <- `the` | 20% |
| Omission | `th` <- `the` | 15% |

Substitutions draw from `layout.neighbors()`, so slips land on physically
plausible keys rather than arbitrary letters.

### Notice delay

Humans do not catch errors instantly, and this delay is what makes correction
behavior read as human rather than scripted. After a typo commits, the engine
samples a *notice distance* in characters. The distribution over that distance is
the key behavioral parameter and is defined in `errors.py::sample_notice_distance`.

Once noticed:

1. Pause 150-400 ms (the "oh" moment).
2. Backspace from the cursor back through the error, in a fast rhythmic burst
   (~40-60 ms per key, lower variance than normal typing).
3. Retype the affected span correctly, at slightly elevated care (marginally slower,
   lower typo probability for the immediate retype).

Because correction is unconditional, the notice distance affects *only* how much
text is backspaced — never whether the error survives.

### Typo rate

Expressed as errors per 100 characters, exposed as a slider. Presets:

- **Clean** — 0.3 / 100 chars
- **Natural** — 1.2 / 100 chars
- **Sloppy** — 3.0 / 100 chars

## Component: revision behavior

Rarely (~1 per 300-800 words), at a clause or sentence boundary: pause, backspace
over the last 2-5 words, then retype them.

**The retyped span is identical to the original.** The tool never alters the user's
wording. The realism lies in the process, not in a different result. This also keeps
revision behavior compatible with the core invariant.

## Component: driver.py

Executes an action stream against the real keyboard via `pynput`.

- 5-second countdown before the first keystroke, so the user can focus the target
  window.
- `F10` pause/resume — checked between actions, so a pause takes effect within one
  keystroke.
- `Esc` abort — immediate, checked on the same boundary. Abort leaves partial text;
  it does not attempt to undo.
- Long `Pause` actions are slept in short slices so pause/abort stay responsive.

## Interface

**Tkinter GUI** (`app.py`): text area for input, WPM slider, realism preset selector
(Clean / Natural / Sloppy), typo-rate slider, seed field, and a **Dry Run** button
that renders the planned output without touching the keyboard.

**Global hotkeys:** `F9` start · `F10` pause/resume · `Esc` abort.

**CLI** (`cli.py`): `python -m auto_essay_typer.cli essay.txt --wpm 65 --preset natural
--seed 42 [--dry-run]`.

## Testing

| Level | Coverage |
|---|---|
| Property | `simulate(plan(text, seed)) == text` over 10,000+ seeds and varied inputs |
| Unit | `layout` lookups; `timing` multiplier selection; each typo class' correction plan |
| Statistical | Mean WPM lands within tolerance of the requested value; typo rate matches configured rate; interval distribution is right-skewed, not uniform |
| Integration | Driver against a captured virtual sink rather than the real keyboard |

Target coverage: 95%+ on `typer_core` (this is the critical path), 80%+ overall.

## Known Constraints

1. **macOS Accessibility permission** must be granted to the process' host
   application (Terminal.app, or the packaged app). Without it, `pynput` silently
   emits nothing. The app detects this on start and shows an actionable message
   rather than failing silently.

2. **Target-application autocorrect fights the invariant.** Google Docs and Word
   apply smart quotes, autocapitalization, and `--` to em-dash substitution *after*
   our keystrokes land, which can break exactness through no fault of the engine.
   The README instructs users to disable autocorrect in the target application. The
   engine does not attempt to detect or compensate for this.

3. **Layout is QWERTY-only.** Other layouts type correctly but get neutral timing
   and no adjacent-key slips.

## Out of Scope

- Essay or content generation of any kind
- Non-macOS platforms (the plan/execute split leaves the door open; only `driver.py`
  is platform-specific)
- Mouse movement or window management
- Altering the user's wording in any way
