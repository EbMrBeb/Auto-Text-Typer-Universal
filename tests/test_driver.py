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
