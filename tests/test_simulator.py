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
