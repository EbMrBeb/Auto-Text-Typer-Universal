# Auto Essay Typer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a macOS tool that types user-supplied text into any focused application with human-like timing, hesitation, and self-corrected mistakes, while guaranteeing the final on-screen text exactly matches the input.

**Architecture:** A strict plan/execute split. `planner.plan(text, profile, seed)` is a pure function returning a list of `Action` values (`KeyPress`, `Backspace`, `Pause`) with no I/O. `driver.Driver` executes that stream against the real keyboard via pynput. Because planning is pure and seeded, the core invariant `simulate(plan(text)) == text` is verifiable by pure computation over thousands of seeds.

**Tech Stack:** Python 3.14, pynput (keystroke injection + global hotkeys), tkinter (GUI, stdlib), pytest + pytest-cov.

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | Package metadata, deps, pytest/coverage config |
| `auto_essay_typer/actions.py` | The `Action` union — `KeyPress`, `Backspace`, `Pause`. No logic. |
| `auto_essay_typer/simulator.py` | Replays an action stream into a text buffer. Used by tests and dry-run. |
| `auto_essay_typer/layout.py` | Static QWERTY geometry: row/col/finger/hand/shift + neighbor lookup |
| `auto_essay_typer/timing.py` | Interval model: lognormal base, digraph multipliers, burst walk, fatigue, thinking pauses |
| `auto_essay_typer/errors.py` | Typo construction (4 classes) + notice-distance policy + correction arithmetic |
| `auto_essay_typer/profiles.py` | `Profile` dataclass and the Clean/Natural/Sloppy presets |
| `auto_essay_typer/planner.py` | The only module that composes timing + errors into an action stream |
| `auto_essay_typer/driver.py` | Executes actions; countdown, pause/resume, abort |
| `auto_essay_typer/cli.py` | Headless entry point |
| `auto_essay_typer/app.py` | Tkinter GUI + global hotkeys |
| `tests/` | One test module per source module, plus `test_invariant.py` |

Dependency direction is strictly one-way: `actions` <- `simulator`, and `layout` <- `timing`, `layout` <- `errors`, and everything <- `planner`. `driver`, `cli`, `app` depend on `planner` but nothing depends on them. No module imports `planner` except the entry points.

---

## Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `auto_essay_typer/__init__.py`
- Create: `tests/__init__.py`
- Create: `.gitignore`

- [ ] **Step 1: Create the virtual environment and install dependencies**

```bash
cd "/Users/braxtongorman/Auto essay typer"
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet pynput pytest pytest-cov
.venv/bin/python -c "import pynput, pytest; print('deps ok')"
```

Expected output: `deps ok`

If pynput fails to build on Python 3.14, fall back to `.venv/bin/pip install "pynput>=1.8"` and report the error rather than silently continuing — every later task depends on it.

- [ ] **Step 2: Create `pyproject.toml`**

```toml
[project]
name = "auto-essay-typer"
version = "0.1.0"
description = "Types supplied text into any application with human-like rhythm and self-corrected mistakes"
requires-python = ">=3.12"
dependencies = ["pynput>=1.8"]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-cov>=5"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["auto_essay_typer*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"

[tool.coverage.report]
fail_under = 80
```

- [ ] **Step 3: Create the package and test package markers**

`auto_essay_typer/__init__.py`:

```python
"""Human-like automated typing for text the user supplies."""

__version__ = "0.1.0"
```

`tests/__init__.py`: empty file.

- [ ] **Step 4: Create `.gitignore`**

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
.coverage
htmlcov/
build/
dist/
*.egg-info/
```

- [ ] **Step 5: Install the package in editable mode and verify pytest runs**

```bash
.venv/bin/pip install --quiet -e .
.venv/bin/pytest
```

Expected: `no tests ran` (exit code 5). That is success at this stage.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore: scaffold project with pyproject, venv deps, and test layout"
```

---

## Task 2: The Action union

**Files:**
- Create: `auto_essay_typer/actions.py`
- Test: `tests/test_actions.py`

These are inert data carriers, so the test only pins down immutability and validation — the behavior that later tasks rely on.

- [ ] **Step 1: Write the failing test**

`tests/test_actions.py`:

```python
import pytest

from auto_essay_typer.actions import Backspace, KeyPress, Pause


def test_keypress_holds_a_single_character():
    assert KeyPress("a").char == "a"


def test_keypress_rejects_multi_character_strings():
    with pytest.raises(ValueError):
        KeyPress("ab")


def test_backspace_rejects_non_positive_counts():
    with pytest.raises(ValueError):
        Backspace(0)


def test_pause_rejects_negative_durations():
    with pytest.raises(ValueError):
        Pause(-1.0)


def test_actions_are_frozen():
    with pytest.raises(Exception):
        KeyPress("a").char = "b"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_actions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'auto_essay_typer.actions'`

- [ ] **Step 3: Write minimal implementation**

`auto_essay_typer/actions.py`:

```python
"""The three primitive actions a typing plan is made of.

A plan is a flat list of these. Delays are carried as explicit ``Pause``
actions rather than as attributes on ``KeyPress`` so that the simulator and
the driver share one uniform interpretation of the stream.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class KeyPress:
    """Emit exactly one character."""

    char: str

    def __post_init__(self) -> None:
        if len(self.char) != 1:
            raise ValueError(f"KeyPress takes exactly one character, got {self.char!r}")


@dataclass(frozen=True, slots=True)
class Backspace:
    """Delete ``count`` characters backwards from the cursor."""

    count: int

    def __post_init__(self) -> None:
        if self.count < 1:
            raise ValueError(f"Backspace count must be >= 1, got {self.count}")


@dataclass(frozen=True, slots=True)
class Pause:
    """Wait ``ms`` milliseconds."""

    ms: float

    def __post_init__(self) -> None:
        if self.ms < 0:
            raise ValueError(f"Pause must be non-negative, got {self.ms}")


Action = KeyPress | Backspace | Pause
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_actions.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add auto_essay_typer/actions.py tests/test_actions.py
git commit -m "feat: add Action union with validation"
```

---

## Task 3: The simulator

This is the referee for the core invariant, so it gets written before anything that produces actions. It must be dead simple and obviously correct.

**Files:**
- Create: `auto_essay_typer/simulator.py`
- Test: `tests/test_simulator.py`

- [ ] **Step 1: Write the failing test**

`tests/test_simulator.py`:

```python
import pytest

from auto_essay_typer.actions import Backspace, KeyPress, Pause
from auto_essay_typer.simulator import simulate, total_duration_ms


def test_keypresses_accumulate_in_order():
    actions = [KeyPress("c"), KeyPress("a"), KeyPress("t")]
    assert simulate(actions) == "cat"


def test_pauses_do_not_affect_text():
    actions = [KeyPress("h"), Pause(500.0), KeyPress("i")]
    assert simulate(actions) == "hi"


def test_backspace_removes_trailing_characters():
    actions = [KeyPress("t"), KeyPress("e"), KeyPress("h"), Backspace(2), KeyPress("h"), KeyPress("e")]
    assert simulate(actions) == "the"


def test_backspace_past_start_is_an_error():
    with pytest.raises(ValueError, match="past the start"):
        simulate([KeyPress("a"), Backspace(3)])


def test_total_duration_sums_pauses_only():
    actions = [Pause(100.0), KeyPress("a"), Pause(250.5), Backspace(1)]
    assert total_duration_ms(actions) == pytest.approx(350.5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_simulator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'auto_essay_typer.simulator'`

- [ ] **Step 3: Write minimal implementation**

`auto_essay_typer/simulator.py`:

```python
"""Replays an action stream into a text buffer.

This is the referee for the core invariant: ``simulate(plan(text)) == text``.
It deliberately contains no cleverness -- if this module is wrong, every
correctness guarantee in the project is worthless.
"""

from collections.abc import Iterable

from auto_essay_typer.actions import Action, Backspace, KeyPress, Pause


def simulate(actions: Iterable[Action]) -> str:
    """Return the text an action stream would leave on screen."""
    buffer: list[str] = []
    for index, action in enumerate(actions):
        match action:
            case KeyPress(char=char):
                buffer.append(char)
            case Backspace(count=count):
                if count > len(buffer):
                    raise ValueError(
                        f"action {index}: Backspace({count}) runs past the start "
                        f"of a {len(buffer)}-character buffer"
                    )
                del buffer[len(buffer) - count :]
            case Pause():
                pass
            case _:
                raise TypeError(f"action {index}: unknown action {action!r}")
    return "".join(buffer)


def total_duration_ms(actions: Iterable[Action]) -> float:
    """Return the total time an action stream would take, in milliseconds."""
    return sum(action.ms for action in actions if isinstance(action, Pause))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_simulator.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add auto_essay_typer/simulator.py tests/test_simulator.py
git commit -m "feat: add action stream simulator for invariant checking"
```

---

## Task 4: QWERTY layout geometry

Physical key positions drive two things: which slips are plausible (adjacent keys) and which digraphs are slow (same finger). Everything downstream reads from here.

**Files:**
- Create: `auto_essay_typer/layout.py`
- Test: `tests/test_layout.py`

- [ ] **Step 1: Write the failing test**

`tests/test_layout.py`:

```python
from auto_essay_typer.layout import Hand, key_info, needs_shift, neighbors, same_finger, same_hand


def test_home_row_keys_have_expected_hands():
    assert key_info("a").hand is Hand.LEFT
    assert key_info("l").hand is Hand.RIGHT


def test_uppercase_maps_to_base_key_and_requires_shift():
    info = key_info("A")
    assert info.row == key_info("a").row
    assert info.finger == key_info("a").finger
    assert needs_shift("A") is True
    assert needs_shift("a") is False


def test_shifted_symbols_map_to_their_base_key():
    assert key_info("!").finger == key_info("1").finger
    assert needs_shift("!") is True


def test_neighbors_are_physically_adjacent():
    result = set(neighbors("s"))
    assert {"a", "d", "w", "x"} <= result
    assert "p" not in result


def test_neighbors_preserve_shift_state():
    assert all(c.isupper() for c in neighbors("S"))
    assert "D" in neighbors("S")


def test_same_finger_detects_vertical_pairs():
    assert same_finger("e", "d") is True
    assert same_finger("e", "k") is False


def test_same_hand():
    assert same_hand("q", "z") is True
    assert same_hand("q", "p") is False


def test_unknown_characters_degrade_gracefully():
    assert key_info("é") is None
    assert neighbors("é") == []
    assert same_finger("é", "a") is False


def test_space_is_a_thumb_key():
    assert key_info(" ").finger == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_layout.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'auto_essay_typer.layout'`

- [ ] **Step 3: Write minimal implementation**

`auto_essay_typer/layout.py`:

```python
"""Static QWERTY geometry.

Fingers are numbered 0-9 left pinky to right pinky, with 5 reserved for the
thumbs (space). Columns carry the physical row stagger so that Euclidean
distance between two keys approximates real finger travel.

Characters outside this layout (accented letters, unicode punctuation) return
``None`` from :func:`key_info` and are treated as neutral everywhere: no shift
penalty, no neighbours, never chosen as a typo site.
"""

from dataclasses import dataclass
from enum import Enum
from functools import cache


class Hand(Enum):
    LEFT = "L"
    RIGHT = "R"


@dataclass(frozen=True, slots=True)
class KeyInfo:
    char: str
    row: int
    x: float
    finger: int
    hand: Hand


_ROWS: dict[int, str] = {
    0: "1234567890-=",
    1: "qwertyuiop[]\\",
    2: "asdfghjkl;'",
    3: "zxcvbnm,./",
}

# Horizontal offset of each row, in key widths, approximating the stagger.
_ROW_OFFSET: dict[int, float] = {0: 0.0, 1: 0.5, 2: 0.75, 3: 1.25}

_FINGER: dict[str, int] = {}
for _finger, _keys in {
    0: "1qaz`",
    1: "2wsx",
    2: "3edc",
    3: "45rfvtgb",
    6: "67yhnujm",
    7: "8ik,",
    8: "9ol.",
    9: "0p;/-=[]\\'",
}.items():
    for _key in _keys:
        _FINGER[_key] = _finger

_SHIFTED: dict[str, str] = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7",
    "*": "8", "(": "9", ")": "0", "_": "-", "+": "=", "{": "[", "}": "]",
    "|": "\\", ":": ";", '"': "'", "<": ",", ">": ".", "?": "/", "~": "`",
}

_SPACE = KeyInfo(char=" ", row=4, x=5.0, finger=5, hand=Hand.LEFT)

_BASE: dict[str, KeyInfo] = {" ": _SPACE}
for _row, _chars in _ROWS.items():
    for _col, _char in enumerate(_chars):
        _finger = _FINGER.get(_char, 3)
        _BASE[_char] = KeyInfo(
            char=_char,
            row=_row,
            x=_col + _ROW_OFFSET[_row],
            finger=_finger,
            hand=Hand.LEFT if _finger <= 4 else Hand.RIGHT,
        )

# Maximum centre-to-centre distance still counted as "adjacent".
_ADJACENCY_RADIUS = 1.2


def _base_char(char: str) -> str | None:
    """Return the unshifted key that produces ``char``, or None if unmodelled."""
    if char in _BASE:
        return char
    if char in _SHIFTED:
        return _SHIFTED[char]
    lowered = char.lower()
    if lowered != char and lowered in _BASE:
        return lowered
    return None


def key_info(char: str) -> KeyInfo | None:
    """Return the physical key that produces ``char``, or None if unmodelled."""
    base = _base_char(char)
    return _BASE[base] if base is not None else None


def needs_shift(char: str) -> bool:
    """True if producing ``char`` requires holding shift."""
    if char in _SHIFTED:
        return True
    return char.isalpha() and char.isupper() and _base_char(char) is not None


@cache
def neighbors(char: str) -> list[str]:
    """Physically adjacent keys, in the same shift state as ``char``.

    Adjacency is Euclidean distance on the staggered grid, so it includes
    diagonals. Shift state is preserved because a slip while holding shift
    produces another shifted character.
    """
    info = key_info(char)
    if info is None or info.row == 4:
        return []
    shifted = needs_shift(char)
    result: list[str] = []
    for other in _BASE.values():
        if other.char == info.char or other.row == 4:
            continue
        distance = ((other.row - info.row) ** 2 + (other.x - info.x) ** 2) ** 0.5
        if distance <= _ADJACENCY_RADIUS:
            result.append(other.char.upper() if shifted else other.char)
    return result


def same_finger(first: str, second: str) -> bool:
    """True if both characters are struck by the same finger on different keys."""
    a, b = key_info(first), key_info(second)
    if a is None or b is None:
        return False
    return a.finger == b.finger and a.char != b.char


def same_hand(first: str, second: str) -> bool:
    """True if both characters are struck by the same hand."""
    a, b = key_info(first), key_info(second)
    if a is None or b is None:
        return False
    return a.hand is b.hand
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_layout.py -v`
Expected: 9 passed

If `test_neighbors_are_physically_adjacent` fails because `a`/`d`/`w`/`x` are not all within range, adjust `_ADJACENCY_RADIUS` upward in 0.05 increments until it passes, then confirm `p` is still excluded. Do not exceed 1.4 — beyond that, slips stop being plausible.

- [ ] **Step 5: Commit**

```bash
git add auto_essay_typer/layout.py tests/test_layout.py
git commit -m "feat: add QWERTY geometry with neighbour and finger lookups"
```

---

## Task 5: Profiles and presets

A single frozen config object threaded through timing, errors, and the planner. Defining it before the models that read it keeps parameter names consistent.

**Files:**
- Create: `auto_essay_typer/profiles.py`
- Test: `tests/test_profiles.py`

- [ ] **Step 1: Write the failing test**

`tests/test_profiles.py`:

```python
import pytest

from auto_essay_typer.profiles import PRESETS, Profile, preset


def test_default_profile_is_valid():
    assert Profile().wpm > 0


def test_presets_are_ordered_by_typo_rate():
    rates = [PRESETS[name].typo_rate_per_100 for name in ("clean", "natural", "sloppy")]
    assert rates == sorted(rates)


def test_preset_lookup_is_case_insensitive():
    assert preset("Natural") == PRESETS["natural"]


def test_unknown_preset_names_are_rejected():
    with pytest.raises(KeyError, match="nonexistent"):
        preset("nonexistent")


def test_wpm_must_be_positive():
    with pytest.raises(ValueError):
        Profile(wpm=0)


def test_typo_rate_cannot_be_negative():
    with pytest.raises(ValueError):
        Profile(typo_rate_per_100=-1.0)


def test_with_overrides_returns_a_modified_copy():
    base = preset("natural")
    fast = base.with_overrides(wpm=120)
    assert fast.wpm == 120
    assert fast.typo_rate_per_100 == base.typo_rate_per_100
    assert base.wpm != 120
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_profiles.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'auto_essay_typer.profiles'`

- [ ] **Step 3: Write minimal implementation**

`auto_essay_typer/profiles.py`:

```python
"""Tunable parameters for the typing model, plus three presets."""

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class Profile:
    """All knobs for the typing model.

    Timing values are milliseconds. Ranges are inclusive ``(low, high)`` pairs
    sampled uniformly unless stated otherwise.
    """

    # --- speed -----------------------------------------------------------
    wpm: float = 65.0
    lognormal_sigma: float = 0.35
    burst_sigma: float = 0.06
    burst_reversion: float = 0.08
    burst_range: tuple[float, float] = (0.8, 1.25)
    fatigue_max: float = 0.15
    fatigue_tau_ms: float = 1_200_000.0

    # --- thinking pauses -------------------------------------------------
    sentence_pause_ms: tuple[float, float] = (400.0, 1200.0)
    clause_pause_ms: tuple[float, float] = (120.0, 350.0)
    paragraph_pause_ms: tuple[float, float] = (1500.0, 5000.0)
    hesitation_probability: float = 0.02
    hesitation_ms: tuple[float, float] = (300.0, 1400.0)
    long_word_threshold: int = 9

    # --- mistakes --------------------------------------------------------
    typo_rate_per_100: float = 1.2
    oh_moment_ms: tuple[float, float] = (150.0, 400.0)
    backspace_ms: tuple[float, float] = (40.0, 60.0)
    retype_care_factor: float = 1.15

    # --- revision --------------------------------------------------------
    revision_rate_per_word: float = 1.0 / 500.0
    revision_span_words: tuple[int, int] = (2, 5)

    def __post_init__(self) -> None:
        if self.wpm <= 0:
            raise ValueError(f"wpm must be positive, got {self.wpm}")
        if self.typo_rate_per_100 < 0:
            raise ValueError(
                f"typo_rate_per_100 must be non-negative, got {self.typo_rate_per_100}"
            )
        if not 0.0 <= self.hesitation_probability <= 1.0:
            raise ValueError(
                f"hesitation_probability must be in [0, 1], got {self.hesitation_probability}"
            )

    def with_overrides(self, **changes: object) -> "Profile":
        """Return a copy with the given fields replaced."""
        return replace(self, **changes)


PRESETS: dict[str, Profile] = {
    "clean": Profile(typo_rate_per_100=0.3, revision_rate_per_word=1.0 / 800.0),
    "natural": Profile(typo_rate_per_100=1.2, revision_rate_per_word=1.0 / 500.0),
    "sloppy": Profile(typo_rate_per_100=3.0, revision_rate_per_word=1.0 / 300.0),
}


def preset(name: str) -> Profile:
    """Look up a preset by name, case-insensitively."""
    key = name.strip().lower()
    if key not in PRESETS:
        raise KeyError(f"unknown preset {name!r}; choose one of {sorted(PRESETS)}")
    return PRESETS[key]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_profiles.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add auto_essay_typer/profiles.py tests/test_profiles.py
git commit -m "feat: add Profile config with clean/natural/sloppy presets"
```

---

## Task 6: Timing model

The single biggest difference between "obviously a bot" and "plausibly a person". Three effects stack: a right-skewed base distribution, per-digraph multipliers, and a slow burst walk that creates stretches of fast and slow typing.

**Files:**
- Create: `auto_essay_typer/timing.py`
- Test: `tests/test_timing.py`

- [ ] **Step 1: Write the failing test**

`tests/test_timing.py`:

```python
import random
import statistics

import pytest

from auto_essay_typer.profiles import Profile
from auto_essay_typer.timing import TimingModel


def model(**overrides) -> TimingModel:
    return TimingModel(Profile(**overrides), random.Random(1234))


def test_base_interval_follows_five_characters_per_word():
    # 60 wpm == 300 chars/min == 200 ms/char
    assert model(wpm=60).base_interval_ms() == pytest.approx(200.0)


def test_first_keystroke_has_no_digraph_penalty():
    assert model().digraph_multiplier(None, "a") == pytest.approx(1.0)


def test_same_finger_pairs_are_slower_than_alternating_hands():
    m = model()
    same_finger = m.digraph_multiplier("e", "d")
    alternating = m.digraph_multiplier("t", "h")
    assert same_finger > 1.0 > alternating


def test_repeated_key_is_fast():
    assert model().digraph_multiplier("l", "l") < 1.0


def test_number_row_is_slower_than_home_row():
    m = model()
    assert m.digraph_multiplier("a", "4") > m.digraph_multiplier("a", "s")


def test_shift_penalty_applies_only_on_entry():
    m = model()
    assert m.shift_penalty_ms("a", "S") > 0
    assert m.shift_penalty_ms("A", "S") == 0
    assert m.shift_penalty_ms("a", "s") == 0


def test_unmodelled_characters_are_neutral():
    m = model()
    assert m.digraph_multiplier("a", "é") == pytest.approx(1.0)
    assert m.shift_penalty_ms("a", "é") == 0


def test_intervals_are_positive_and_right_skewed():
    m = model(wpm=60)
    samples = [m.keystroke_interval_ms("a", "s") for _ in range(4000)]
    assert all(s > 0 for s in samples)
    # Lognormal: mean sits above the median. Uniform noise would not do this.
    assert statistics.mean(samples) > statistics.median(samples) * 1.03


def test_median_interval_tracks_requested_wpm():
    m = model(wpm=60, burst_sigma=0.0, fatigue_max=0.0)
    samples = [m.keystroke_interval_ms("a", "l") for _ in range(4000)]
    # 'a' -> 'l' alternates hands (x0.85), so expect ~170 ms median.
    assert 150 < statistics.median(samples) < 195


def test_burst_multiplier_stays_within_configured_range():
    m = model()
    low, high = m.profile.burst_range
    for _ in range(5000):
        m.keystroke_interval_ms("a", "s")
        assert low <= m.burst <= high


def test_fatigue_slows_typing_over_a_long_session():
    m = model(wpm=60, burst_sigma=0.0, lognormal_sigma=0.0)
    early = statistics.median([m.keystroke_interval_ms("a", "s") for _ in range(200)])
    m.advance_ms(20 * 60 * 1000)
    late = statistics.median([m.keystroke_interval_ms("a", "s") for _ in range(200)])
    assert late > early


def test_sentence_end_produces_a_long_pause():
    m = model()
    low, high = m.profile.sentence_pause_ms
    assert low <= m.thinking_pause_ms(".", "N") <= high


def test_paragraph_break_pauses_longer_than_a_sentence():
    m = model()
    assert m.thinking_pause_ms("\n", "N") > m.profile.sentence_pause_ms[1]


def test_ordinary_characters_get_no_thinking_pause():
    assert model().thinking_pause_ms("a", "b") == 0.0


def test_backspace_bursts_are_fast_and_tight():
    m = model()
    low, high = m.profile.backspace_ms
    samples = [m.backspace_interval_ms() for _ in range(200)]
    assert all(low <= s <= high for s in samples)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_timing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'auto_essay_typer.timing'`

- [ ] **Step 3: Write minimal implementation**

`auto_essay_typer/timing.py`:

```python
"""Inter-keystroke interval model.

Three effects stack to produce the timing signature of a real typist:

1. A **lognormal** base distribution, which has the long right tail that real
   keystroke intervals show. Uniform jitter is the classic tell of automation
   and is used nowhere in this module.
2. **Digraph multipliers** from the physical layout -- same-finger pairs are
   slow, alternating-hand pairs are fast.
3. A **mean-reverting burst walk**, which produces stretches of fast and slow
   typing rather than per-character white noise.

On top of those sit thinking pauses at sentence, clause, and paragraph
boundaries.
"""

import math
import random

from auto_essay_typer import layout
from auto_essay_typer.profiles import Profile

_SENTENCE_ENDS = frozenset(".?!")
_CLAUSE_ENDS = frozenset(",;:")

# Fingers 0-4 and 6-9 exclude the thumbs; row 0 is the number/symbol row.
_SAME_FINGER = 1.6
_SAME_HAND = 1.05
_ALTERNATING = 0.85
_REPEATED_KEY = 0.9
_SPACE = 0.9
_NUMBER_ROW = 1.4


class TimingModel:
    """Stateful sampler for keystroke intervals and pauses.

    Stateful because burst and fatigue both depend on history. Construct one
    per plan and never share across plans -- doing so would leak state between
    runs and break reproducibility.
    """

    def __init__(self, profile: Profile, rng: random.Random) -> None:
        self.profile = profile
        self._rng = rng
        self.burst = 1.0
        self._elapsed_ms = 0.0

    # -- base -------------------------------------------------------------

    def base_interval_ms(self) -> float:
        """Mean milliseconds per character implied by the configured WPM."""
        return 60_000.0 / (self.profile.wpm * 5.0)

    def digraph_multiplier(self, previous: str | None, current: str) -> float:
        """Speed multiplier for typing ``current`` right after ``previous``."""
        info = layout.key_info(current)
        if info is None:
            return 1.0

        if previous is None:
            multiplier = 1.0
        elif layout.key_info(previous) is None:
            multiplier = 1.0
        elif previous.lower() == current.lower():
            multiplier = _REPEATED_KEY
        elif layout.same_finger(previous, current):
            multiplier = _SAME_FINGER
        elif not layout.same_hand(previous, current):
            multiplier = _ALTERNATING
        else:
            multiplier = _SAME_HAND

        if current == " ":
            multiplier *= _SPACE
        elif info.row == 0:
            multiplier *= _NUMBER_ROW
        return multiplier

    def shift_penalty_ms(self, previous: str | None, current: str) -> float:
        """Cost of reaching for shift, charged only when entering shift state.

        Typing ``AB`` holds shift down once, so only the ``A`` pays.
        """
        if layout.key_info(current) is None or not layout.needs_shift(current):
            return 0.0
        if previous is not None and layout.needs_shift(previous):
            return 0.0
        return self._rng.uniform(40.0, 90.0)

    # -- state ------------------------------------------------------------

    def advance_ms(self, milliseconds: float) -> None:
        """Advance the fatigue clock, e.g. across a long thinking pause."""
        self._elapsed_ms += milliseconds

    @property
    def fatigue_multiplier(self) -> float:
        """Slowdown from sustained typing, saturating at ``fatigue_max``."""
        if self.profile.fatigue_max == 0.0:
            return 1.0
        saturation = 1.0 - math.exp(-self._elapsed_ms / self.profile.fatigue_tau_ms)
        return 1.0 + self.profile.fatigue_max * saturation

    def _step_burst(self) -> float:
        """Advance the mean-reverting random walk and return the multiplier."""
        low, high = self.profile.burst_range
        drift = self.profile.burst_reversion * (1.0 - self.burst)
        shock = self._rng.gauss(0.0, self.profile.burst_sigma)
        self.burst = min(high, max(low, self.burst + drift + shock))
        return self.burst

    # -- sampling ---------------------------------------------------------

    def keystroke_interval_ms(self, previous: str | None, current: str) -> float:
        """Sample the delay before typing ``current``, advancing all state."""
        mean = (
            self.base_interval_ms()
            * self.digraph_multiplier(previous, current)
            * self._step_burst()
            * self.fatigue_multiplier
        )
        sigma = self.profile.lognormal_sigma
        if sigma > 0.0:
            # Shift mu so that the lognormal's mean is the value computed above.
            mu = math.log(mean) - sigma**2 / 2.0
            interval = self._rng.lognormvariate(mu, sigma)
        else:
            interval = mean
        interval += self.shift_penalty_ms(previous, current)
        self.advance_ms(interval)
        return interval

    def backspace_interval_ms(self) -> float:
        """Sample one step of a correction burst -- fast, and tighter than typing."""
        low, high = self.profile.backspace_ms
        interval = self._rng.uniform(low, high)
        self.advance_ms(interval)
        return interval

    def thinking_pause_ms(self, previous: str | None, upcoming_word: str) -> float:
        """Extra pause after ``previous``, before ``upcoming_word``.

        Returns 0.0 when no boundary or hesitation applies. Recovery from
        fatigue during long pauses is modelled by *not* advancing the fatigue
        clock here -- resting does not tire you further.
        """
        if previous is None:
            return 0.0
        if previous == "\n":
            return self._rng.uniform(*self.profile.paragraph_pause_ms)
        if previous in _SENTENCE_ENDS:
            return self._rng.uniform(*self.profile.sentence_pause_ms)
        if previous in _CLAUSE_ENDS:
            return self._rng.uniform(*self.profile.clause_pause_ms)
        if (
            len(upcoming_word) >= self.profile.long_word_threshold
            and self._rng.random() < self.profile.hesitation_probability
        ):
            return self._rng.uniform(*self.profile.hesitation_ms)
        return 0.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_timing.py -v`
Expected: 14 passed

These are statistical tests with a fixed seed, so they are deterministic. If `test_median_interval_tracks_requested_wpm` fails, check the mu correction in `keystroke_interval_ms` first — forgetting `- sigma**2 / 2` inflates the mean by roughly 6% at sigma 0.35.

- [ ] **Step 5: Commit**

```bash
git add auto_essay_typer/timing.py tests/test_timing.py
git commit -m "feat: add lognormal timing model with digraph, burst, and fatigue effects"
```

---

## Task 7: Mistake construction

A typo is a pair `(wrong, correct)` where `correct` is the exact source span it consumes. Correction is then arithmetic that cannot drift: backspace `len(wrong) + len(tail)`, retype `correct + tail`. The invariant holds by construction; the property test in Task 10 proves the construction is right.

**Files:**
- Create: `auto_essay_typer/errors.py`
- Test: `tests/test_errors.py`

- [ ] **Step 1: Write the failing test**

`tests/test_errors.py`:

```python
import random

import pytest

from auto_essay_typer.errors import (
    TypoKind,
    make_typo,
    plan_correction,
    sample_notice_distance,
)


def rng() -> random.Random:
    return random.Random(7)


def build(kind: TypoKind, text: str, index: int):
    return make_typo(text, index, rng(), forced_kind=kind)


def test_transposition_swaps_two_adjacent_characters():
    typo = build(TypoKind.TRANSPOSITION, "the cat", 0)
    assert typo.correct == "th"
    assert typo.wrong == "ht"


def test_substitution_uses_a_physically_adjacent_key():
    typo = build(TypoKind.SUBSTITUTION, "something", 5)
    assert typo.correct == "h"
    assert typo.wrong != "h"
    assert len(typo.wrong) == 1


def test_doubling_repeats_one_character():
    typo = build(TypoKind.DOUBLING, "the", 0)
    assert typo.correct == "t"
    assert typo.wrong == "tt"


def test_omission_types_nothing():
    typo = build(TypoKind.OMISSION, "the", 0)
    assert typo.correct == "t"
    assert typo.wrong == ""


def test_typos_are_never_placed_on_whitespace():
    for index, char in enumerate("a b\nc"):
        typo = make_typo("a b\nc", index, rng())
        if char.isspace():
            assert typo is None


def test_transposition_is_skipped_for_identical_neighbours():
    assert build(TypoKind.TRANSPOSITION, "aa", 0) is None


def test_unmodelled_characters_are_never_typo_sites():
    assert make_typo("éa", 0, rng()) is None


def test_typo_at_end_of_text_is_rejected():
    # Nothing follows, so there is no realistic way to notice it late.
    assert make_typo("a", 0, rng()) is None


def test_correction_backspaces_wrong_text_plus_tail():
    typo = build(TypoKind.DOUBLING, "the", 0)
    backspaces, retype = plan_correction(typo, tail="he")
    assert backspaces == len("tt") + len("he")
    assert retype == "the"


def test_correction_of_an_omission_backspaces_only_the_tail():
    typo = build(TypoKind.OMISSION, "the", 0)
    backspaces, retype = plan_correction(typo, tail="he")
    assert backspaces == 2
    assert retype == "the"


def test_correction_with_no_tail_is_immediate():
    typo = build(TypoKind.SUBSTITUTION, "the", 0)
    backspaces, retype = plan_correction(typo, tail="")
    assert backspaces == 1
    assert retype == "t"


def test_notice_distance_is_non_negative_and_usually_short():
    r = random.Random(99)
    samples = [sample_notice_distance(r) for _ in range(2000)]
    assert all(s >= 0 for s in samples)
    assert sum(1 for s in samples if s <= 4) / len(samples) > 0.7


def test_every_kind_is_reachable_without_forcing():
    r = random.Random(3)
    kinds = {make_typo("something clever", i % 8, r).kind for i in range(400)}
    assert kinds == set(TypoKind)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_errors.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'auto_essay_typer.errors'`

- [ ] **Step 3: Write minimal implementation**

`auto_essay_typer/errors.py`:

```python
"""Typo construction and correction planning.

Every typo is a ``(wrong, correct)`` pair where ``correct`` is the exact span
of source text the typo consumes. Correction is therefore pure arithmetic:
backspace ``len(wrong) + len(tail)`` characters and retype ``correct + tail``.
No typo can survive into the output, because the planner always emits the
correction alongside the mistake.
"""

import random
from dataclasses import dataclass
from enum import StrEnum

from auto_essay_typer import layout


class TypoKind(StrEnum):
    TRANSPOSITION = "transposition"
    SUBSTITUTION = "substitution"
    DOUBLING = "doubling"
    OMISSION = "omission"


# Weights approximate observed real-world typo frequencies.
_KIND_WEIGHTS: dict[TypoKind, float] = {
    TypoKind.TRANSPOSITION: 0.35,
    TypoKind.SUBSTITUTION: 0.30,
    TypoKind.DOUBLING: 0.20,
    TypoKind.OMISSION: 0.15,
}


@dataclass(frozen=True, slots=True)
class Typo:
    """A mistake, and the source span it stands in for."""

    kind: TypoKind
    wrong: str
    correct: str


def _is_typo_site(text: str, index: int) -> bool:
    """True if a typo may be placed at ``index``.

    Whitespace is excluded because slips on the space bar read as unnatural,
    and the final character is excluded because nothing follows it to make a
    delayed notice plausible.
    """
    if index >= len(text) - 1:
        return False
    char = text[index]
    return not char.isspace() and layout.key_info(char) is not None


def _build(kind: TypoKind, text: str, index: int, rng: random.Random) -> Typo | None:
    """Construct one typo of the given kind, or None if infeasible here."""
    char = text[index]
    match kind:
        case TypoKind.TRANSPOSITION:
            nxt = text[index + 1]
            if nxt.isspace() or nxt == char or layout.key_info(nxt) is None:
                return None
            return Typo(kind, wrong=nxt + char, correct=char + nxt)
        case TypoKind.SUBSTITUTION:
            options = layout.neighbors(char)
            if not options:
                return None
            return Typo(kind, wrong=rng.choice(options), correct=char)
        case TypoKind.DOUBLING:
            return Typo(kind, wrong=char * 2, correct=char)
        case TypoKind.OMISSION:
            return Typo(kind, wrong="", correct=char)
    return None


def make_typo(
    text: str,
    index: int,
    rng: random.Random,
    forced_kind: TypoKind | None = None,
) -> Typo | None:
    """Build a typo at ``index``, or None if no kind is feasible there.

    When ``forced_kind`` is given only that kind is attempted, which keeps
    tests deterministic. Otherwise kinds are tried in weighted random order so
    that an infeasible first choice degrades to a plausible alternative rather
    than skipping the mistake entirely.
    """
    if not _is_typo_site(text, index):
        return None
    if forced_kind is not None:
        return _build(forced_kind, text, index, rng)

    remaining = dict(_KIND_WEIGHTS)
    while remaining:
        kinds = list(remaining)
        chosen = rng.choices(kinds, weights=[remaining[k] for k in kinds], k=1)[0]
        typo = _build(chosen, text, index, rng)
        if typo is not None:
            return typo
        del remaining[chosen]
    return None


def sample_notice_distance(rng: random.Random) -> int:
    """How many more source characters get typed before the error is spotted.

    This distribution is what makes correction read as human rather than
    scripted: a bot fixes errors instantly, a person types on for a beat first.
    Weighted toward short distances -- most slips are caught within the word --
    with a thin tail for errors noticed a phrase later.

    Because correction is unconditional, this affects only *how much* text gets
    backspaced, never whether the mistake survives.
    """
    roll = rng.random()
    if roll < 0.45:
        return 0
    if roll < 0.80:
        return rng.randint(1, 4)
    if roll < 0.95:
        return rng.randint(5, 10)
    return rng.randint(11, 25)


def plan_correction(typo: Typo, tail: str) -> tuple[int, str]:
    """Return ``(backspace_count, retype_text)`` to repair ``typo``.

    ``tail`` is the correctly typed source text that followed the mistake
    before it was noticed. Everything from the cursor back through the error is
    deleted, then the whole span is retyped correctly.
    """
    return len(typo.wrong) + len(tail), typo.correct + tail
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_errors.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add auto_essay_typer/errors.py tests/test_errors.py
git commit -m "feat: add typo construction with provably-correct correction planning"
```

---

## Task 8: The planner

The only module that composes timing and errors. Pure and seeded: same `(text, profile, seed)` always yields the same stream.

**Files:**
- Create: `auto_essay_typer/planner.py`
- Test: `tests/test_planner.py`

- [ ] **Step 1: Write the failing test**

`tests/test_planner.py`:

```python
import pytest

from auto_essay_typer.actions import Backspace, KeyPress, Pause
from auto_essay_typer.planner import plan
from auto_essay_typer.profiles import Profile
from auto_essay_typer.simulator import simulate, total_duration_ms

CLEAN = Profile(typo_rate_per_100=0.0, revision_rate_per_word=0.0)


def test_clean_profile_types_text_verbatim():
    actions = plan("hello world", CLEAN, seed=1)
    assert simulate(actions) == "hello world"


def test_clean_profile_never_backspaces():
    actions = plan("hello world", CLEAN, seed=1)
    assert not any(isinstance(a, Backspace) for a in actions)


def test_every_keypress_is_preceded_by_a_pause():
    actions = plan("abc", CLEAN, seed=1)
    for index, action in enumerate(actions):
        if isinstance(action, KeyPress):
            assert index > 0 and isinstance(actions[index - 1], Pause)


def test_planning_is_deterministic_for_a_seed():
    assert plan("the quick brown fox", seed=42) == plan("the quick brown fox", seed=42)


def test_different_seeds_produce_different_plans():
    text = "the quick brown fox jumps over the lazy dog"
    assert plan(text, seed=1) != plan(text, seed=2)


def test_typos_are_always_corrected():
    text = "the quick brown fox jumps over the lazy dog. " * 10
    noisy = Profile(typo_rate_per_100=8.0, revision_rate_per_word=0.0)
    actions = plan(text, noisy, seed=5)
    assert any(isinstance(a, Backspace) for a in actions)
    assert simulate(actions) == text


def test_empty_text_produces_no_actions():
    assert plan("", CLEAN, seed=1) == []


def test_sentence_boundary_inserts_a_long_pause():
    actions = plan("Hi. Go", CLEAN, seed=3)
    assert any(isinstance(a, Pause) and a.ms >= 400 for a in actions)


def test_duration_is_broadly_consistent_with_wpm():
    text = "the quick brown fox jumps over the lazy dog " * 12
    profile = Profile(wpm=60, typo_rate_per_100=0.0, revision_rate_per_word=0.0)
    minutes = total_duration_ms(plan(text, profile, seed=9)) / 60_000.0
    observed_wpm = (len(text) / 5.0) / minutes
    assert 45 < observed_wpm < 75


def test_newlines_and_tabs_survive():
    text = "one\n\ttwo\nthree"
    assert simulate(plan(text, CLEAN, seed=4)) == text


def test_unmodelled_characters_survive():
    text = "café — naïve résumé"
    assert simulate(plan(text, CLEAN, seed=4)) == text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_planner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'auto_essay_typer.planner'`

- [ ] **Step 3: Write minimal implementation**

`auto_essay_typer/planner.py`:

```python
"""Turns text into an action stream.

Pure and seeded: no I/O, no clock, no keyboard. Given the same
``(text, profile, seed)`` this returns an identical list of actions, which is
what makes the core invariant checkable by pure computation and makes any
observed behaviour reproducible for debugging.
"""

import random

from auto_essay_typer.actions import Action, Backspace, KeyPress, Pause
from auto_essay_typer.errors import make_typo, plan_correction, sample_notice_distance
from auto_essay_typer.profiles import Profile
from auto_essay_typer.timing import TimingModel


def _word_at(text: str, index: int) -> str:
    """The whole word starting at ``index``, or '' if mid-word.

    Used to decide whether an upcoming word is long enough to hesitate before.
    """
    if index > 0 and not text[index - 1].isspace():
        return ""
    end = index
    while end < len(text) and not text[end].isspace():
        end += 1
    return text[index:end]


def plan(
    text: str,
    profile: Profile | None = None,
    seed: int | None = None,
) -> list[Action]:
    """Return the action stream that types ``text`` in a human-like way."""
    profile = profile or Profile()
    rng = random.Random(seed)
    timing = TimingModel(profile, rng)
    actions: list[Action] = []

    previous: str | None = None
    typo_probability = profile.typo_rate_per_100 / 100.0

    def emit(char: str, care: float = 1.0) -> None:
        """Append the pause-then-keypress pair for one character."""
        nonlocal previous
        interval = timing.keystroke_interval_ms(previous, char) * care
        actions.append(Pause(interval))
        actions.append(KeyPress(char))
        previous = char

    index = 0
    while index < len(text):
        thinking = timing.thinking_pause_ms(previous, _word_at(text, index))
        if thinking > 0.0:
            actions.append(Pause(thinking))

        typo = None
        if rng.random() < typo_probability:
            typo = make_typo(text, index, rng)

        if typo is None:
            emit(text[index])
            index += 1
            continue

        for char in typo.wrong:
            emit(char)

        # Keep typing correctly until the mistake is noticed. The tail stops at
        # a line break -- nobody starts a new paragraph without re-reading.
        consumed = len(typo.correct)
        distance = sample_notice_distance(rng)
        tail = text[index + consumed : index + consumed + distance]
        if "\n" in tail:
            tail = tail[: tail.index("\n")]
        for char in tail:
            emit(char)

        actions.append(Pause(rng.uniform(*profile.oh_moment_ms)))

        backspaces, retype = plan_correction(typo, tail)
        for _ in range(backspaces):
            actions.append(Pause(timing.backspace_interval_ms()))
            actions.append(Backspace(1))

        # Backspacing resets the digraph context: the last key struck was not a
        # character key.
        previous = None
        for char in retype:
            emit(char, care=profile.retype_care_factor)

        index += consumed + len(tail)

    return actions
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_planner.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add auto_essay_typer/planner.py tests/test_planner.py
git commit -m "feat: add pure seeded planner composing timing and mistakes"
```

---

## Task 9: Revision behaviour

Rarely, at a sentence boundary, back up over the last few words and retype them. The retyped span is **identical** — the tool never alters wording. The realism is in the process.

**Files:**
- Modify: `auto_essay_typer/planner.py`
- Test: `tests/test_revision.py`

- [ ] **Step 1: Write the failing test**

`tests/test_revision.py`:

```python
from auto_essay_typer.actions import Backspace
from auto_essay_typer.planner import plan
from auto_essay_typer.profiles import Profile
from auto_essay_typer.simulator import simulate

TEXT = "The argument rests on a single premise. That premise is contested. " * 8


def test_revision_produces_backspaces_without_any_typos():
    profile = Profile(typo_rate_per_100=0.0, revision_rate_per_word=1.0)
    actions = plan(TEXT, profile, seed=11)
    assert any(isinstance(a, Backspace) for a in actions)


def test_revision_preserves_the_text_exactly():
    profile = Profile(typo_rate_per_100=0.0, revision_rate_per_word=1.0)
    assert simulate(plan(TEXT, profile, seed=11)) == TEXT


def test_revision_can_be_disabled():
    profile = Profile(typo_rate_per_100=0.0, revision_rate_per_word=0.0)
    actions = plan(TEXT, profile, seed=11)
    assert not any(isinstance(a, Backspace) for a in actions)


def test_revision_span_respects_configured_word_count():
    profile = Profile(
        typo_rate_per_100=0.0, revision_rate_per_word=1.0, revision_span_words=(2, 2)
    )
    actions = plan(TEXT, profile, seed=11)
    runs = _backspace_run_lengths(actions)
    assert runs, "expected at least one revision"
    # Two words plus their separating space; generous upper bound for long words.
    assert all(3 <= run <= 40 for run in runs)


def _backspace_run_lengths(actions) -> list[int]:
    runs, current = [], 0
    for action in actions:
        if isinstance(action, Backspace):
            current += action.count
        elif current:
            runs.append(current)
            current = 0
    if current:
        runs.append(current)
    return runs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_revision.py -v`
Expected: FAIL — `test_revision_produces_backspaces_without_any_typos` fails because no revision logic exists yet.

- [ ] **Step 3: Add revision to the planner**

In `auto_essay_typer/planner.py`, add this helper directly below `_word_at`:

```python
def _revision_span_start(text: str, end: int, words: int) -> int:
    """Index of the start of the last ``words`` words ending at ``end``.

    Walks backwards over word/space pairs. Never crosses a line break, so a
    revision stays within the paragraph being written.
    """
    index = end
    for _ in range(words):
        while index > 0 and text[index - 1].isspace():
            if text[index - 1] == "\n":
                return index
            index -= 1
        while index > 0 and not text[index - 1].isspace():
            index -= 1
    return index
```

Then, inside `plan`, insert this block immediately after the `thinking` pause is appended and **before** the `typo = None` line:

```python
        # A revision fires only at a sentence boundary, once enough text exists
        # to back over. The span is retyped identically -- wording never changes.
        if (
            previous in (" ", None)
            and index > 0
            and text[index - 2 : index - 1] in (".", "?", "!")
            and rng.random() < profile.revision_rate_per_word
        ):
            words = rng.randint(*profile.revision_span_words)
            start = _revision_span_start(text, index, words)
            span = text[start:index]
            if span.strip():
                actions.append(Pause(rng.uniform(*profile.sentence_pause_ms)))
                for _ in range(len(span)):
                    actions.append(Pause(timing.backspace_interval_ms()))
                    actions.append(Backspace(1))
                previous = None
                for char in span:
                    emit(char, care=profile.retype_care_factor)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_revision.py tests/test_planner.py -v`
Expected: 15 passed. Both files must pass — the planner tests guard against revision breaking the clean path.

- [ ] **Step 5: Commit**

```bash
git add auto_essay_typer/planner.py tests/test_revision.py
git commit -m "feat: add sentence-boundary revision behaviour"
```

---

## Task 10: The core invariant

Everything above is only trustworthy if this passes. `simulate(plan(text)) == text`, across thousands of seeds, profiles, and awkward inputs.

**Files:**
- Create: `tests/test_invariant.py`

- [ ] **Step 1: Write the test**

`tests/test_invariant.py`:

```python
import random

import pytest

from auto_essay_typer.planner import plan
from auto_essay_typer.profiles import PRESETS, Profile
from auto_essay_typer.simulator import simulate

CORPUS = [
    "",
    "a",
    "ab",
    "the",
    "aaa aaa aaa",
    "The quick brown fox jumps over the lazy dog.",
    "Hello, world! How are you? I am fine; thanks.",
    "one\ntwo\nthree",
    "para one.\n\npara two.\n\npara three.",
    "\n\n\n",
    "   leading and trailing   ",
    "MiXeD CaSe WiTh CAPS",
    "symbols: !@#$%^&*()_+-={}[]|\\:;\"'<>,.?/~`",
    "digits 0123456789 and 42 and 3.14159",
    "café — naïve résumé; 日本語 mixed in",
    "hyphenated-words and some_snake_case and CamelCase",
    "a" * 200,
    "supercalifragilisticexpialidocious antidisestablishmentarianism",
    "Tabs\there\tand\tthere.",
    "The argument rests on a single premise. That premise is contested. " * 5,
]

# The most punishing settings we ever expose, plus a deliberately absurd one.
STRESS_PROFILES = [
    *PRESETS.values(),
    Profile(typo_rate_per_100=25.0, revision_rate_per_word=0.1, wpm=140),
    Profile(typo_rate_per_100=0.0, revision_rate_per_word=0.0, wpm=20),
]


@pytest.mark.parametrize("text", CORPUS)
@pytest.mark.parametrize("profile", STRESS_PROFILES, ids=lambda p: f"typo{p.typo_rate_per_100}")
def test_plan_reproduces_text_exactly(text, profile):
    for seed in range(40):
        assert simulate(plan(text, profile, seed)) == text, (
            f"invariant broken: seed={seed} profile={profile} text={text!r}"
        )


def test_invariant_holds_over_ten_thousand_random_cases():
    rng = random.Random(0)
    for iteration in range(10_000):
        text = rng.choice(CORPUS)
        profile = rng.choice(STRESS_PROFILES)
        seed = rng.randrange(1_000_000)
        assert simulate(plan(text, profile, seed)) == text, (
            f"invariant broken at iteration {iteration}: seed={seed} text={text!r}"
        )


def test_invariant_holds_on_randomly_generated_text():
    rng = random.Random(1)
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFG 123.,!?;:'\"-\n\t"
    for _ in range(2000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 120)))
        profile = rng.choice(STRESS_PROFILES)
        assert simulate(plan(text, profile, rng.randrange(10_000))) == text


def test_no_plan_ever_backspaces_past_the_start():
    # simulate() raises on underflow, so reaching the end without an exception
    # is the assertion. Text starting mid-word is the risky case.
    rng = random.Random(2)
    for _ in range(2000):
        text = rng.choice(["x", "xy", "the cat", "a. b. c.", "\nx"])
        simulate(plan(text, rng.choice(STRESS_PROFILES), rng.randrange(10_000)))
```

- [ ] **Step 2: Run the invariant suite**

Run: `.venv/bin/pytest tests/test_invariant.py -v`
Expected: all passed. This is the slowest suite in the project; allow up to a minute.

If any case fails, the assertion message prints the exact seed, profile, and text — reproduce it with `plan(text, profile, seed)` and fix the planner. Do **not** weaken the test.

- [ ] **Step 3: Run the whole suite with coverage**

Run: `.venv/bin/pytest --cov=auto_essay_typer --cov-report=term-missing`
Expected: all tests pass; the core modules (`layout`, `timing`, `errors`, `planner`, `simulator`) at 95%+.

Note: the spec sketched these under a `typer_core/` subpackage. The plan keeps them flat in `auto_essay_typer/` — the package is small enough that a nested subpackage would add import noise without adding a real boundary. Dependency direction is enforced by review, not by directory structure.

If any of those five is below 95%, add tests for the uncovered lines before moving on. This is the critical path.

- [ ] **Step 4: Commit**

```bash
git add tests/test_invariant.py
git commit -m "test: prove plan/simulate invariant over 12k+ generated cases"
```

---

## Task 11: The driver

Executes an action stream against the real keyboard. The keyboard is injected behind a small protocol so the driver is testable without typing into the developer's own terminal.

**Files:**
- Create: `auto_essay_typer/driver.py`
- Test: `tests/test_driver.py`

- [ ] **Step 1: Write the failing test**

`tests/test_driver.py`:

```python
import pytest

from auto_essay_typer.actions import Backspace, KeyPress, Pause
from auto_essay_typer.driver import Driver, RecordingKeyboard, RunResult
from auto_essay_typer.planner import plan
from auto_essay_typer.profiles import Profile


def driver(**kwargs):
    """Countdown defaults to 0 here so it does not pollute sleep assertions."""
    kwargs.setdefault("countdown_seconds", 0)
    keyboard = RecordingKeyboard()
    slept: list[float] = []
    return Driver(keyboard=keyboard, sleep=slept.append, **kwargs), keyboard, slept


def test_driver_reproduces_planned_text():
    d, keyboard, _ = driver()
    text = "The quick brown fox. " * 5
    d.run(plan(text, Profile(typo_rate_per_100=6.0), seed=3))
    assert keyboard.text == text


def test_driver_reports_completion():
    d, _, _ = driver()
    assert d.run([KeyPress("a")]) is RunResult.COMPLETED


def test_driver_sleeps_for_pauses():
    d, _, slept = driver()
    d.run([Pause(120.0), KeyPress("a")])
    assert sum(slept) == pytest.approx(0.120, abs=1e-6)


def test_long_pauses_are_sliced_for_responsiveness():
    d, _, slept = driver()
    d.run([Pause(1000.0)])
    assert len(slept) > 1
    assert max(slept) <= 0.051


def test_abort_stops_mid_stream():
    keyboard = RecordingKeyboard()
    # Countdown must be 0, otherwise the abort fires during the countdown and
    # nothing is ever typed.
    d = Driver(keyboard=keyboard, sleep=lambda _: None, countdown_seconds=0)
    d._sleep = lambda _: d.abort()
    result = d.run([KeyPress("a"), Pause(50.0), KeyPress("b")])
    assert result is RunResult.ABORTED
    assert keyboard.text == "a"


def test_countdown_invokes_the_callback_each_second():
    d, _, _ = driver(countdown_seconds=3)
    ticks: list[int] = []
    d.run([KeyPress("a")], on_countdown=ticks.append)
    assert ticks == [3, 2, 1]


def test_progress_callback_reports_characters_typed():
    d, _, _ = driver()
    seen: list[tuple[int, int]] = []
    d.run([KeyPress("a"), KeyPress("b")], on_progress=lambda done, total: seen.append((done, total)))
    assert seen[-1] == (2, 2)


def test_recording_keyboard_rejects_backspace_underflow():
    keyboard = RecordingKeyboard()
    with pytest.raises(ValueError):
        keyboard.backspace(1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_driver.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'auto_essay_typer.driver'`

- [ ] **Step 3: Write minimal implementation**

`auto_essay_typer/driver.py`:

```python
"""Executes an action stream against a keyboard.

The keyboard is injected behind :class:`Keyboard` so the driver can be tested
against a recording sink instead of typing into the developer's own terminal.
``PynputKeyboard`` is only imported lazily, so the rest of the package -- and
the whole test suite -- works without accessibility permission.
"""

import threading
import time
from enum import Enum, auto
from typing import Callable, Iterable, Protocol

from auto_essay_typer.actions import Action, Backspace, KeyPress, Pause

# Long pauses are slept in slices no longer than this, so that pause and abort
# take effect promptly instead of after a five-second paragraph break.
_SLICE_SECONDS = 0.05


class RunResult(Enum):
    COMPLETED = auto()
    ABORTED = auto()


class Keyboard(Protocol):
    def press_char(self, char: str) -> None: ...
    def backspace(self, count: int) -> None: ...


class RecordingKeyboard:
    """In-memory keyboard for tests and dry runs."""

    def __init__(self) -> None:
        self._buffer: list[str] = []

    @property
    def text(self) -> str:
        return "".join(self._buffer)

    def press_char(self, char: str) -> None:
        self._buffer.append(char)

    def backspace(self, count: int) -> None:
        if count > len(self._buffer):
            raise ValueError(f"backspace({count}) past start of {len(self._buffer)} chars")
        del self._buffer[len(self._buffer) - count :]


class PynputKeyboard:
    """Real OS-level keystroke injection.

    Newline and tab are sent as named keys rather than typed characters,
    because ``Controller.type`` does not reliably produce Return in every
    application.
    """

    def __init__(self) -> None:
        from pynput.keyboard import Controller, Key

        self._controller = Controller()
        self._named = {"\n": Key.enter, "\t": Key.tab}
        self._backspace = Key.backspace

    def press_char(self, char: str) -> None:
        named = self._named.get(char)
        if named is not None:
            self._controller.tap(named)
        else:
            self._controller.type(char)

    def backspace(self, count: int) -> None:
        for _ in range(count):
            self._controller.tap(self._backspace)


class Driver:
    """Runs an action stream, with countdown, pause/resume, and abort."""

    def __init__(
        self,
        keyboard: Keyboard | None = None,
        sleep: Callable[[float], None] = time.sleep,
        countdown_seconds: int = 5,
    ) -> None:
        self._keyboard = keyboard if keyboard is not None else PynputKeyboard()
        self._sleep = sleep
        self._countdown_seconds = countdown_seconds
        self._abort = threading.Event()
        self._resumed = threading.Event()
        self._resumed.set()

    # -- controls ---------------------------------------------------------

    def abort(self) -> None:
        """Stop as soon as possible. Partial text is left in place."""
        self._abort.set()
        self._resumed.set()

    def pause(self) -> None:
        self._resumed.clear()

    def resume(self) -> None:
        self._resumed.set()

    @property
    def is_paused(self) -> bool:
        return not self._resumed.is_set()

    # -- execution --------------------------------------------------------

    def _wait(self, seconds: float) -> bool:
        """Sleep in slices. Returns False if aborted during the wait."""
        remaining = seconds
        while remaining > 0:
            if self._abort.is_set():
                return False
            slice_seconds = min(_SLICE_SECONDS, remaining)
            self._sleep(slice_seconds)
            remaining -= slice_seconds
        return not self._abort.is_set()

    def _await_resume(self) -> bool:
        while not self._resumed.is_set():
            if self._abort.is_set():
                return False
            self._sleep(_SLICE_SECONDS)
        return not self._abort.is_set()

    def run(
        self,
        actions: Iterable[Action],
        on_progress: Callable[[int, int], None] | None = None,
        on_countdown: Callable[[int], None] | None = None,
    ) -> RunResult:
        """Execute ``actions``. Returns COMPLETED or ABORTED."""
        actions = list(actions)
        self._abort.clear()
        self._resumed.set()

        for remaining in range(self._countdown_seconds, 0, -1):
            if on_countdown is not None:
                on_countdown(remaining)
            if not self._wait(1.0):
                return RunResult.ABORTED

        total = sum(1 for a in actions if isinstance(a, KeyPress))
        done = 0

        for action in actions:
            if not self._await_resume():
                return RunResult.ABORTED
            match action:
                case Pause(ms=ms):
                    if not self._wait(ms / 1000.0):
                        return RunResult.ABORTED
                case KeyPress(char=char):
                    self._keyboard.press_char(char)
                    done += 1
                    if on_progress is not None:
                        on_progress(done, total)
                case Backspace(count=count):
                    self._keyboard.backspace(count)
            if self._abort.is_set():
                return RunResult.ABORTED

        return RunResult.COMPLETED
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_driver.py -v`
Expected: 8 passed

Note `test_countdown_invokes_the_callback_each_second` relies on `sleep` being a no-op collector, so the countdown costs no real time in tests.

- [ ] **Step 5: Commit**

```bash
git add auto_essay_typer/driver.py tests/test_driver.py
git commit -m "feat: add driver with injectable keyboard, countdown, pause and abort"
```

---

## Task 12: Command-line interface

**Files:**
- Create: `auto_essay_typer/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:

```python
import pytest

from auto_essay_typer.cli import build_profile, main, parse_args


def test_preset_sets_the_typo_rate():
    profile = build_profile(parse_args(["f.txt", "--preset", "sloppy"]))
    assert profile.typo_rate_per_100 == 3.0


def test_explicit_flags_override_the_preset():
    profile = build_profile(parse_args(["f.txt", "--preset", "sloppy", "--typo-rate", "0.1"]))
    assert profile.typo_rate_per_100 == 0.1


def test_wpm_flag_is_applied():
    assert build_profile(parse_args(["f.txt", "--wpm", "90"])).wpm == 90.0


def test_dry_run_prints_the_text_without_typing(tmp_path, capsys):
    essay = tmp_path / "essay.txt"
    essay.write_text("The quick brown fox.")
    assert main([str(essay), "--dry-run", "--seed", "1"]) == 0
    out = capsys.readouterr().out
    assert "The quick brown fox." in out
    assert "estimated" in out.lower()


def test_dry_run_output_matches_the_input_exactly(tmp_path, capsys):
    text = "Hello, world! Second sentence here."
    essay = tmp_path / "essay.txt"
    essay.write_text(text)
    main([str(essay), "--dry-run", "--seed", "2", "--preset", "sloppy"])
    printed = capsys.readouterr().out
    assert text in printed


def test_missing_file_reports_an_error(capsys):
    assert main(["definitely-not-here.txt"]) == 1
    assert "not found" in capsys.readouterr().err.lower()


def test_empty_file_is_rejected(tmp_path, capsys):
    essay = tmp_path / "empty.txt"
    essay.write_text("   \n  ")
    assert main([str(essay)]) == 1
    assert "empty" in capsys.readouterr().err.lower()


def test_run_uses_the_injected_keyboard(tmp_path):
    from auto_essay_typer.driver import RecordingKeyboard

    text = "Typed via injection."
    essay = tmp_path / "essay.txt"
    essay.write_text(text)
    keyboard = RecordingKeyboard()
    assert main([str(essay), "--seed", "3", "--countdown", "0"], keyboard=keyboard, sleep=lambda _: None) == 0
    assert keyboard.text == text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'auto_essay_typer.cli'`

- [ ] **Step 3: Write minimal implementation**

`auto_essay_typer/cli.py`:

```python
"""Headless entry point.

    python -m auto_essay_typer.cli essay.txt --wpm 65 --preset natural --dry-run
"""

import argparse
import sys
from pathlib import Path
from typing import Callable, Sequence

from auto_essay_typer.driver import Driver, Keyboard, RunResult
from auto_essay_typer.planner import plan
from auto_essay_typer.profiles import PRESETS, Profile, preset
from auto_essay_typer.simulator import simulate, total_duration_ms


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="auto-essay-typer",
        description="Type a text file into the focused application with human-like rhythm.",
    )
    parser.add_argument("file", help="path to the text file to type")
    parser.add_argument("--preset", choices=sorted(PRESETS), default="natural")
    parser.add_argument("--wpm", type=float, default=None, help="target words per minute")
    parser.add_argument(
        "--typo-rate", type=float, default=None, help="mistakes per 100 characters"
    )
    parser.add_argument("--seed", type=int, default=None, help="reproduce an exact run")
    parser.add_argument(
        "--countdown", type=int, default=5, help="seconds to focus the target window"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be typed and how long it would take, then exit",
    )
    return parser.parse_args(list(argv))


def build_profile(args: argparse.Namespace) -> Profile:
    """Preset first, then explicit flags override it."""
    profile = preset(args.preset)
    overrides: dict[str, object] = {}
    if args.wpm is not None:
        overrides["wpm"] = args.wpm
    if args.typo_rate is not None:
        overrides["typo_rate_per_100"] = args.typo_rate
    return profile.with_overrides(**overrides) if overrides else profile


def _format_duration(milliseconds: float) -> str:
    seconds = int(milliseconds / 1000)
    return f"{seconds // 60}m {seconds % 60}s"


def main(
    argv: Sequence[str] | None = None,
    keyboard: Keyboard | None = None,
    sleep: Callable[[float], None] | None = None,
) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    path = Path(args.file)
    if not path.is_file():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 1
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        print(f"error: file is empty: {path}", file=sys.stderr)
        return 1

    actions = plan(text, build_profile(args), args.seed)
    estimate = _format_duration(total_duration_ms(actions))

    if args.dry_run:
        print(simulate(actions))
        print(f"\n-- estimated typing time: {estimate}", file=sys.stdout)
        return 0

    driver_kwargs: dict[str, object] = {"countdown_seconds": args.countdown}
    if keyboard is not None:
        driver_kwargs["keyboard"] = keyboard
    if sleep is not None:
        driver_kwargs["sleep"] = sleep
    driver = Driver(**driver_kwargs)

    print(f"Typing {len(text)} characters, estimated {estimate}. Ctrl-C to stop.")
    try:
        result = driver.run(
            actions, on_countdown=lambda n: print(f"  starting in {n}...", flush=True)
        )
    except KeyboardInterrupt:
        driver.abort()
        print("\naborted", file=sys.stderr)
        return 130
    print("done" if result is RunResult.COMPLETED else "aborted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add auto_essay_typer/cli.py tests/test_cli.py
git commit -m "feat: add CLI with presets, dry-run, and reproducible seeds"
```

---

## Task 13: Tkinter GUI with global hotkeys

Typing runs on a worker thread so the Tk event loop stays responsive; hotkeys come from a pynput listener on its own thread. All widget updates are marshalled back to the main thread with `root.after`, because Tk is not thread-safe.

**Files:**
- Create: `auto_essay_typer/app.py`
- Test: `tests/test_app.py`

- [ ] **Step 1: Write the failing test**

Tk cannot be exercised headlessly in CI, so the test covers the non-widget logic that carries real risk.

`tests/test_app.py`:

```python
import pytest

from auto_essay_typer.app import accessibility_hint, profile_from_settings


def test_profile_from_settings_applies_preset_and_overrides():
    profile = profile_from_settings(preset_name="sloppy", wpm=95.0, typo_rate=0.5)
    assert profile.wpm == 95.0
    assert profile.typo_rate_per_100 == 0.5


def test_profile_from_settings_keeps_preset_defaults_when_not_overridden():
    profile = profile_from_settings(preset_name="clean", wpm=60.0, typo_rate=0.3)
    assert profile.revision_rate_per_word == pytest.approx(1.0 / 800.0)


def test_accessibility_hint_names_the_settings_pane():
    hint = accessibility_hint()
    assert "Accessibility" in hint
    assert "Privacy" in hint
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'auto_essay_typer.app'`

- [ ] **Step 3: Write minimal implementation**

`auto_essay_typer/app.py`:

```python
"""Tkinter front end with global hotkeys.

Threading model: Tk owns the main thread. Typing runs on a worker thread so the
window stays responsive, and the pynput hotkey listener runs on a third. Widget
updates from either background thread are marshalled back through
``root.after``, because Tk is not thread-safe.
"""

import threading
import tkinter as tk
from tkinter import ttk

from auto_essay_typer.driver import Driver, RunResult
from auto_essay_typer.planner import plan
from auto_essay_typer.profiles import PRESETS, Profile, preset
from auto_essay_typer.simulator import simulate, total_duration_ms

HOTKEYS = {"<f9>": "start", "<f10>": "pause", "<esc>": "abort"}


def accessibility_hint() -> str:
    return (
        "Keystrokes are being blocked. Grant Accessibility permission to the app "
        "that launched this (Terminal, iTerm, or your IDE) in:\n\n"
        "System Settings > Privacy & Security > Accessibility\n\n"
        "Then quit and relaunch that app -- macOS does not apply the change to "
        "already-running processes."
    )


def profile_from_settings(preset_name: str, wpm: float, typo_rate: float) -> Profile:
    """Build a Profile from the three values the GUI exposes."""
    return preset(preset_name).with_overrides(wpm=wpm, typo_rate_per_100=typo_rate)


class TyperApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.driver: Driver | None = None
        self.worker: threading.Thread | None = None
        root.title("Auto Essay Typer")
        root.geometry("720x560")

        self.preset_var = tk.StringVar(value="natural")
        self.wpm_var = tk.DoubleVar(value=65.0)
        self.typo_var = tk.DoubleVar(value=1.2)
        self.seed_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Paste text, then press F9.")

        self._build_widgets()
        self._start_hotkey_listener()

    # -- layout -----------------------------------------------------------

    def _build_widgets(self) -> None:
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill="both", expand=True)

        self.text = tk.Text(frame, wrap="word", height=18, undo=True)
        self.text.pack(fill="both", expand=True, pady=(0, 10))

        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=(0, 8))

        ttk.Label(controls, text="Preset").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            controls,
            textvariable=self.preset_var,
            values=sorted(PRESETS),
            state="readonly",
            width=10,
        ).grid(row=0, column=1, padx=(4, 16))

        ttk.Label(controls, text="WPM").grid(row=0, column=2, sticky="w")
        ttk.Scale(controls, from_=20, to=140, variable=self.wpm_var, length=140).grid(
            row=0, column=3, padx=(4, 4)
        )
        ttk.Label(controls, textvariable=self.wpm_var, width=5).grid(row=0, column=4, padx=(0, 16))

        ttk.Label(controls, text="Typos/100").grid(row=0, column=5, sticky="w")
        ttk.Scale(controls, from_=0, to=6, variable=self.typo_var, length=110).grid(
            row=0, column=6, padx=(4, 16)
        )

        ttk.Label(controls, text="Seed").grid(row=0, column=7, sticky="w")
        ttk.Entry(controls, textvariable=self.seed_var, width=8).grid(row=0, column=8, padx=(4, 0))

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Dry Run", command=self.dry_run).pack(side="left")
        ttk.Button(buttons, text="Start (F9)", command=self.start).pack(side="left", padx=6)
        ttk.Button(buttons, text="Abort (Esc)", command=self.abort).pack(side="left")

        ttk.Label(frame, textvariable=self.status_var, foreground="#555").pack(
            anchor="w", pady=(10, 0)
        )

    # -- helpers ----------------------------------------------------------

    def _set_status(self, message: str) -> None:
        self.root.after(0, self.status_var.set, message)

    def _current_plan(self):
        text = self.text.get("1.0", "end-1c")
        if not text.strip():
            return None, None
        profile = profile_from_settings(
            self.preset_var.get(), round(self.wpm_var.get()), self.typo_var.get()
        )
        seed = int(self.seed_var.get()) if self.seed_var.get().strip().isdigit() else None
        return text, plan(text, profile, seed)

    # -- actions ----------------------------------------------------------

    def dry_run(self) -> None:
        text, actions = self._current_plan()
        if actions is None:
            self._set_status("Nothing to type.")
            return
        seconds = int(total_duration_ms(actions) / 1000)
        exact = simulate(actions) == text
        self._set_status(
            f"Dry run: {len(text)} chars, ~{seconds // 60}m {seconds % 60}s, "
            f"output {'matches input exactly' if exact else 'MISMATCH -- do not use'}."
        )

    def start(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            return
        text, actions = self._current_plan()
        if actions is None:
            self._set_status("Nothing to type.")
            return

        self.driver = Driver()

        def work() -> None:
            try:
                result = self.driver.run(
                    actions,
                    on_progress=lambda done, total: self._set_status(
                        f"Typing {done}/{total}...  F10 pause, Esc abort"
                    ),
                    on_countdown=lambda n: self._set_status(
                        f"Click into your document -- starting in {n}..."
                    ),
                )
            except Exception as error:  # noqa: BLE001 - surfaced to the user
                self._set_status(f"Error: {error}\n\n{accessibility_hint()}")
                return
            self._set_status("Done." if result is RunResult.COMPLETED else "Aborted.")

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def toggle_pause(self) -> None:
        if self.driver is None:
            return
        if self.driver.is_paused:
            self.driver.resume()
            self._set_status("Resumed.")
        else:
            self.driver.pause()
            self._set_status("Paused. F10 to resume.")

    def abort(self) -> None:
        if self.driver is not None:
            self.driver.abort()
            self._set_status("Aborted.")

    # -- hotkeys ----------------------------------------------------------

    def _start_hotkey_listener(self) -> None:
        try:
            from pynput import keyboard
        except Exception:  # noqa: BLE001 - GUI still usable via buttons
            self._set_status("Global hotkeys unavailable; use the buttons.")
            return
        listener = keyboard.GlobalHotKeys(
            {
                "<f9>": lambda: self.root.after(0, self.start),
                "<f10>": lambda: self.root.after(0, self.toggle_pause),
                "<esc>": lambda: self.root.after(0, self.abort),
            }
        )
        listener.daemon = True
        listener.start()


def main() -> int:
    root = tk.Tk()
    TyperApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_app.py -v`
Expected: 3 passed

- [ ] **Step 5: Verify the GUI actually launches**

Run: `.venv/bin/python -m auto_essay_typer.app`
Expected: a 720x560 window titled "Auto Essay Typer" with a text area, preset dropdown, two sliders, seed field, and three buttons. Close it to continue.

- [ ] **Step 6: Commit**

```bash
git add auto_essay_typer/app.py tests/test_app.py
git commit -m "feat: add Tkinter GUI with global hotkeys and dry-run verification"
```

---

## Task 14: README and end-to-end verification

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write `README.md`**

````markdown
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
raised.

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

## Guarantee

The final text is character-for-character identical to your input. Every
injected mistake is corrected. This is enforced by a property test that replays
generated action streams through a text buffer across 12,000+ cases spanning
punctuation, unicode, tabs, newlines, and deliberately extreme typo rates:

```bash
.venv/bin/pytest tests/test_invariant.py
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
````

- [ ] **Step 2: Run the full suite with coverage**

```bash
.venv/bin/pytest --cov=auto_essay_typer --cov-report=term-missing
```

Expected: all tests pass. `layout`, `timing`, `errors`, `planner`, `simulator` at 95%+; total at 80%+.

- [ ] **Step 3: End-to-end dry run**

```bash
printf 'The argument rests on a single premise. That premise is contested.\n\nIt does not follow.\n' > /tmp/sample.txt
.venv/bin/python -m auto_essay_typer.cli /tmp/sample.txt --preset sloppy --seed 7 --dry-run
```

Expected: the sample text printed back **exactly**, followed by an estimated time. Any difference is an invariant failure — stop and fix before shipping.

- [ ] **Step 4: Manual live verification**

Open TextEdit, run `.venv/bin/python -m auto_essay_typer.cli /tmp/sample.txt --preset sloppy --seed 7`, and click into the TextEdit window during the countdown. Watch for visible typos being backspaced and corrected. Confirm the final text matches the file, and that Esc aborts mid-run.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: add README with setup, permissions, and guarantees"
```
