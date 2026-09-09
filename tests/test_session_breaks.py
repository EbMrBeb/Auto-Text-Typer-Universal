"""Tests for session-break scheduling.

A real typist writing a long piece does not type continuously for forty
minutes straight -- they get up, they check their phone, they refill a
coffee. These tests cover the break-scheduling behaviour added to
``planner.plan``: breaks only ever add ``Pause`` actions (never touching
``KeyPress``/``Backspace``), so the core ``simulate(plan(...)) == text``
invariant is expected to hold throughout -- ``test_invariant_holds_with_breaks_enabled``
below re-proves that directly rather than assuming it.
"""

import statistics

from auto_essay_typer.actions import KeyPress, Pause
from auto_essay_typer.planner import plan
from auto_essay_typer.profiles import Profile
from auto_essay_typer.simulator import simulate, total_duration_ms

SHORT_TEXT = "Hello there. How are you today?"

# ~7,200 characters -- long enough to blow past even the top of the default
# break_interval_ms range (900,000 ms) at a slow, low-wpm typing speed.
LONG_TEXT = "The premise is contested. It does not follow. " * 150

# A shorter long-text used for the invariant sweep, so 200+ seeds stays fast.
INVARIANT_TEXT = "The premise is contested. It does not follow. " * 40

DEFAULT_BREAK_THRESHOLD = Profile().short_break_ms[0]


def _keystroke_intervals(actions: list) -> list[float]:
    """The Pause.ms of every Pause that sits directly before a KeyPress.

    ``emit()`` in the planner always appends its keystroke-interval Pause
    immediately before the KeyPress it belongs to, so this reliably isolates
    per-character intervals from thinking pauses and break pauses, both of
    which are separate, standalone Pause actions.
    """
    intervals = []
    for i, action in enumerate(actions):
        if isinstance(action, KeyPress) and i > 0 and isinstance(actions[i - 1], Pause):
            intervals.append(actions[i - 1].ms)
    return intervals


def _preceding_char(actions: list, index: int) -> str | None:
    """The char of the nearest KeyPress before ``actions[index]``, or None."""
    for action in reversed(actions[:index]):
        if isinstance(action, KeyPress):
            return action.char
    return None


# --- presence / absence of break pauses -------------------------------------


def test_short_document_produces_no_break_length_pause():
    actions = plan(SHORT_TEXT, Profile(), seed=3)
    assert not any(
        isinstance(a, Pause) and a.ms >= DEFAULT_BREAK_THRESHOLD for a in actions
    )


def test_long_document_produces_at_least_one_break_length_pause():
    profile = Profile(wpm=20.0)
    actions = plan(LONG_TEXT, profile, seed=3)
    assert any(
        isinstance(a, Pause) and a.ms > profile.short_break_ms[0] for a in actions
    )


def test_session_breaks_disabled_produces_none_at_any_length():
    profile = Profile(wpm=20.0, session_breaks=False)
    actions = plan(LONG_TEXT, profile, seed=3)
    assert not any(
        isinstance(a, Pause) and a.ms >= DEFAULT_BREAK_THRESHOLD for a in actions
    )


# --- invariant ---------------------------------------------------------------


def test_invariant_holds_with_breaks_enabled():
    profile = Profile(wpm=20.0, break_interval_ms=(5_000.0, 8_000.0))
    for seed in range(220):
        assert simulate(plan(INVARIANT_TEXT, profile, seed)) == INVARIANT_TEXT, (
            f"invariant broken with breaks enabled: seed={seed}"
        )


# --- boundary placement -------------------------------------------------------


def test_breaks_land_only_at_sentence_or_paragraph_boundaries():
    text = (
        "The argument is simple. It rests on one premise. That premise fails. "
        "Consider the alternative. It fares no better.\n\n"
        "A new paragraph begins here. It continues the thought. The conclusion follows. "
    ) * 5
    profile = Profile(
        typo_rate_per_100=0.0,
        revision_rate_per_word=0.0,
        break_interval_ms=(5_000.0, 8_000.0),
    )
    actions = plan(text, profile, seed=7)
    threshold = profile.short_break_ms[0]
    break_indices = [
        i for i, a in enumerate(actions) if isinstance(a, Pause) and a.ms >= threshold
    ]
    assert break_indices, "expected at least one break at this interval/text length"
    for i in break_indices:
        char = _preceding_char(actions, i)
        assert char is not None, "break occurred before any character was typed"
        assert char in ".?!" or char.isspace(), (
            f"break landed mid-word: preceding char was {char!r}"
        )


# --- fatigue recovery ----------------------------------------------------------


def test_fatigue_recovers_shortly_after_a_break():
    # Engineered so the fatigue clock exactly resets to 0 right at the break
    # (break_fatigue_recovery=1.0, recovery amount == elapsed at break time),
    # and burst/lognormal noise are silenced so the before/after contrast is
    # driven purely by fatigue + post_break_burst, not sampling variance.
    profile = Profile(
        typo_rate_per_100=0.0,
        revision_rate_per_word=0.0,
        hesitation_probability=0.0,
        wpm=200.0,
        lognormal_sigma=0.0,
        burst_sigma=0.0,
        fatigue_max=1.0,
        fatigue_tau_ms=40_000.0,
        break_interval_ms=(40_000.0, 40_000.0),
        short_break_ms=(40_000.0, 40_000.0),
        long_break_probability=0.0,
        break_fatigue_recovery=1.0,
        post_break_burst=0.85,
    )
    text = "The quick brown fox jumps over the lazy dog. " * 80
    actions = plan(text, profile, seed=5)
    threshold = profile.short_break_ms[0]
    break_index = next(
        i for i, a in enumerate(actions) if isinstance(a, Pause) and a.ms >= threshold
    )

    before = _keystroke_intervals(actions[:break_index])[-15:]
    after = _keystroke_intervals(actions[break_index + 1 :])[:15]
    assert len(before) == 15 and len(after) == 15
    assert statistics.median(after) < statistics.median(before)


# --- duration and determinism --------------------------------------------------


def test_total_duration_grows_when_breaks_enabled():
    seed = 11
    without = Profile(wpm=20.0, session_breaks=False)
    with_breaks = Profile(wpm=20.0, session_breaks=True)
    duration_without = total_duration_ms(plan(LONG_TEXT, without, seed))
    duration_with = total_duration_ms(plan(LONG_TEXT, with_breaks, seed))
    assert duration_with > duration_without


def test_same_seed_produces_identical_plan_with_breaks_enabled():
    profile = Profile(wpm=20.0)
    assert plan(LONG_TEXT, profile, seed=99) == plan(LONG_TEXT, profile, seed=99)
