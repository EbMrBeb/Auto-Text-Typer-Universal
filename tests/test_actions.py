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
