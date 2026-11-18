import pytest

from auto_essay_typer.actions import Backspace, Format, FormatOp, KeyPress, Pause


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


# --- Format / FormatOp -----------------------------------------------------


def test_format_holds_an_op():
    assert Format(FormatOp.TOGGLE_BOLD).op == FormatOp.TOGGLE_BOLD


def test_format_is_frozen():
    with pytest.raises(Exception):
        Format(FormatOp.TOGGLE_BOLD).op = FormatOp.TOGGLE_ITALIC


def test_format_op_is_a_str_enum():
    assert isinstance(FormatOp.TOGGLE_BOLD, str)


def test_format_op_values_are_lowercase_names():
    # Spot checks, plus an exhaustive check below covering every member.
    assert FormatOp.CLEAR_FORMATTING == "clear_formatting"
    assert FormatOp.SET_HEADING_3 == "set_heading_3"
    assert FormatOp.ALIGN_JUSTIFIED == "align_justified"
    assert FormatOp.TOGGLE_NUMBERED_LIST == "toggle_numbered_list"
    assert FormatOp.OUTDENT == "outdent"


def test_every_format_op_value_is_its_lowercased_name():
    for op in FormatOp:
        assert op.value == op.name.lower()
