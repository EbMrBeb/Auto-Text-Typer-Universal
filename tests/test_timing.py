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
