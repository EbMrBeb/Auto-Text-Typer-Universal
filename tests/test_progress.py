import pytest

from auto_essay_typer.actions import Backspace, KeyPress, Pause
from auto_essay_typer.progress import (
    cumulative_pause_ms_by_keypress,
    eta_seconds,
    format_duration_words,
    format_mmss,
)


def test_cumulative_pause_ms_by_keypress_basic():
    actions = [Pause(100.0), KeyPress("a"), Pause(50.0), Pause(25.0), KeyPress("b")]
    assert cumulative_pause_ms_by_keypress(actions) == [0.0, 100.0, 175.0]


def test_cumulative_pause_ms_by_keypress_no_keypresses():
    actions = [Pause(100.0), Pause(50.0)]
    assert cumulative_pause_ms_by_keypress(actions) == [0.0]


def test_cumulative_pause_ms_by_keypress_ignores_backspace():
    actions = [Pause(10.0), KeyPress("a"), Pause(10.0), Backspace(1), Pause(10.0), KeyPress("b")]
    assert cumulative_pause_ms_by_keypress(actions) == [0.0, 10.0, 30.0]


def test_cumulative_pause_ms_by_keypress_empty():
    assert cumulative_pause_ms_by_keypress([]) == [0.0]


def test_eta_seconds_at_start_equals_total():
    cumulative = [0.0, 100.0, 300.0]
    assert eta_seconds(0, 300.0, cumulative) == pytest.approx(0.3)


def test_eta_seconds_partway_through():
    cumulative = [0.0, 100.0, 300.0]
    assert eta_seconds(1, 300.0, cumulative) == pytest.approx(0.2)


def test_eta_seconds_at_end_is_zero():
    cumulative = [0.0, 100.0, 300.0]
    assert eta_seconds(2, 300.0, cumulative) == pytest.approx(0.0)


def test_eta_seconds_never_negative_past_the_table():
    cumulative = [0.0, 100.0, 300.0]
    assert eta_seconds(99, 300.0, cumulative) == 0.0


def test_eta_seconds_negative_done_does_not_raise_and_assumes_no_time_left():
    cumulative = [0.0, 100.0, 300.0]
    assert eta_seconds(-1, 300.0, cumulative) == 0.0


def test_format_mmss_zero():
    assert format_mmss(0) == "0:00"


def test_format_mmss_rounds_down_to_the_second():
    assert format_mmss(61_999) == "1:01"


def test_format_mmss_negative_floors_at_zero():
    assert format_mmss(-500) == "0:00"


def test_format_mmss_pads_seconds():
    assert format_mmss(5_000) == "0:05"


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "~0 s"),
        (12, "~12 s"),
        (59, "~59 s"),
        (60, "~1 min"),
        (61, "~1 min 1 s"),
        (220, "~3 min 40 s"),
        (300, "~5 min"),
    ],
)
def test_format_duration_words(seconds, expected):
    assert format_duration_words(seconds) == expected


def test_format_duration_words_negative_floors_at_zero():
    assert format_duration_words(-5) == "~0 s"
