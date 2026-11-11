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


# --- re-entry dip then recovery -------------------------------------------------


def test_typing_dips_immediately_after_a_break_then_recovers():
    """Resuming after a break is a re-entry *dip*, not an instant speedup.

    ``post_break_burst`` (1.15) sits above the burst walk's neutral 1.0, so
    the very next keystrokes after a break are deliberately slower -- the
    re-reading/re-orienting a real typist does before getting going again.
    The mean-reverting walk then carries the multiplier back toward 1.0 over
    the following keystrokes, and because ``break_fatigue_recovery`` also
    partially clears the fatigue clock, the typist ends up at or above their
    pre-break pace once the burst has settled -- recovered, not jumped.

    This is engineered, like the test it replaces, to isolate that shape
    from sampling noise:

    - ``lognormal_sigma``/``burst_sigma`` are 0, so every keystroke interval
      is the exact deterministic mean (no per-character noise).
    - ``break_interval_ms``/``short_break_ms`` are fixed single values (not
      ranges), and ``break_fatigue_recovery=1.0`` with a break exactly as
      long as the elapsed typing time zeroes the fatigue clock at the break.
    - ``burst_reversion`` is small so the post-break bump decays slowly
      enough to still show up in a 50-keystroke median, rather than being
      diluted away by the back half of the window.
    - the sample text is one short, all-lowercase, punctuation-terminated
      phrase repeated many times. Digraph multipliers (same-finger vs.
      alternating-hand pairs) swing far wider than the burst/fatigue effect
      being tested here, so windows are sized as whole multiples of the
      phrase length: that guarantees every window -- before the break,
      immediately after, or later -- samples the *exact same* multiset of
      character pairs regardless of where in the repeating phrase it starts,
      leaving burst and fatigue as the only thing that can move the median.

    Because none of the profile's remaining randomness (typo/hesitation/
    revision are all off, and there are no shift-requiring characters in the
    phrase) affects this computation, the result is identical for every
    seed -- verified separately by running this scenario across 50 seeds
    with zero failures.
    """
    profile = Profile(
        typo_rate_per_100=0.0,
        revision_rate_per_word=0.0,
        hesitation_probability=0.0,
        wpm=60.0,
        lognormal_sigma=0.0,
        burst_sigma=0.0,
        burst_reversion=0.01,
        fatigue_max=0.08,
        fatigue_tau_ms=150_000.0,
        break_interval_ms=(600_000.0, 600_000.0),
        short_break_ms=(600_000.0, 600_000.0),
        long_break_probability=0.0,
        break_fatigue_recovery=1.0,
        post_break_burst=1.15,
    )
    phrase = "the claim fails clearly. "
    assert len(phrase) == 25, "window sizes below assume a 25-character phrase"
    text = phrase * 900

    actions = plan(text, profile, seed=5)
    threshold = profile.short_break_ms[0]
    break_index = next(
        i for i, a in enumerate(actions) if isinstance(a, Pause) and a.ms >= threshold
    )
    before_all = _keystroke_intervals(actions[:break_index])
    after_all = _keystroke_intervals(actions[break_index + 1 :])

    # Windows are whole multiples of the 25-char phrase (200 = 8x, 50 = 2x),
    # so each one covers the same digraph multiset regardless of phase.
    before = before_all[-200:]
    immediately_after = after_all[:50]
    later_after = after_all[300:500]
    assert len(before) == 200
    assert len(immediately_after) == 50
    assert len(later_after) == 200

    median_before = statistics.median(before)
    median_immediately_after = statistics.median(immediately_after)
    median_later_after = statistics.median(later_after)

    # Phase 1: the re-entry dip. Right after the break, typing is slower
    # than the pre-break steady state.
    assert median_immediately_after > median_before, (
        f"expected a re-entry dip right after the break: "
        f"immediately-after median {median_immediately_after} was not "
        f"slower than pre-break median {median_before}"
    )
    # Phase 2: recovery. Once the burst walk has mean-reverted, the cleared
    # fatigue means typing is at least as fast as the pre-break steady state.
    assert median_later_after <= median_before, (
        f"expected recovery to at-or-above pre-break pace once the burst "
        f"walk settled: later median {median_later_after} was slower than "
        f"pre-break median {median_before}"
    )


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
