import random

import pytest

from auto_essay_typer.actions import Backspace, Format, FormatOp, KeyPress, Pause
from auto_essay_typer.document import Alignment, ListKind, ParagraphStyle, TextStyle
from auto_essay_typer.simulator import (
    SimulationError,
    simulate,
    simulate_document,
    total_duration_ms,
)


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


def test_unknown_action_type_is_rejected():
    with pytest.raises(TypeError, match="unknown action"):
        simulate([KeyPress("a"), object()])


def test_simulate_document_rejects_an_unknown_action_type():
    with pytest.raises(TypeError, match="unknown action"):
        simulate_document([KeyPress("a"), object()])


def test_simulate_ignores_format_actions():
    actions = [KeyPress("a"), Format(FormatOp.TOGGLE_BOLD), KeyPress("b")]
    assert simulate(actions) == "ab"


def test_total_duration_ms_ignores_format():
    actions = [Pause(100.0), Format(FormatOp.TOGGLE_BOLD), Pause(50.0)]
    assert total_duration_ms(actions) == pytest.approx(150.0)


# === simulate_document(): the Google Docs editing model =====================

# --- initial state and plain typing -----------------------------------------


def test_initial_state_is_one_empty_normal_left_paragraph():
    doc = simulate_document([])
    assert len(doc.paragraphs) == 1
    p = doc.paragraphs[0]
    assert p.runs == ()
    assert p.style == ParagraphStyle.NORMAL
    assert p.alignment == Alignment.LEFT
    assert p.list_kind is None
    assert p.list_level == 0


def test_keypress_appends_to_the_last_paragraph():
    doc = simulate_document([KeyPress("a"), KeyPress("b"), KeyPress("c")])
    assert doc.paragraphs[0].text == "abc"


def test_keypress_newline_starts_a_new_paragraph():
    doc = simulate_document([KeyPress("a"), KeyPress("\n"), KeyPress("b")])
    assert [p.text for p in doc.paragraphs] == ["a", "b"]


def test_enter_after_a_heading_gives_normal():
    actions = [Format(FormatOp.SET_HEADING_1), KeyPress("a"), KeyPress("\n"), KeyPress("b")]
    doc = simulate_document(actions)
    assert doc.paragraphs[0].style == ParagraphStyle.HEADING_1
    assert doc.paragraphs[1].style == ParagraphStyle.NORMAL


def test_enter_after_title_or_subtitle_also_gives_normal():
    for op in (FormatOp.SET_TITLE, FormatOp.SET_SUBTITLE):
        doc = simulate_document([Format(op), KeyPress("a"), KeyPress("\n"), KeyPress("b")])
        assert doc.paragraphs[1].style == ParagraphStyle.NORMAL


def test_enter_inherits_alignment_and_list_from_the_previous_paragraph():
    actions = [
        Format(FormatOp.ALIGN_CENTER),
        Format(FormatOp.TOGGLE_BULLET_LIST),
        Format(FormatOp.INDENT),
        Format(FormatOp.INDENT),
        KeyPress("a"),
        KeyPress("\n"),
        KeyPress("b"),
    ]
    doc = simulate_document(actions)
    first, second = doc.paragraphs
    assert first.alignment == second.alignment == Alignment.CENTER
    assert first.list_kind == second.list_kind == ListKind.BULLET
    assert first.list_level == second.list_level == 2


def test_enter_does_not_carry_insertion_styles_change_of_its_own():
    # Enter itself neither sets nor clears insertion -- whatever styles were
    # active carry straight into the new paragraph's first character.
    actions = [
        Format(FormatOp.TOGGLE_BOLD),
        KeyPress("a"),
        KeyPress("\n"),
        KeyPress("b"),
    ]
    doc = simulate_document(actions)
    assert doc.paragraphs[1].runs[0].styles == frozenset({TextStyle.BOLD})


# --- backspace ----------------------------------------------------------------


def test_backspace_on_the_initial_empty_paragraph_raises():
    with pytest.raises(SimulationError, match="paragraph boundary"):
        simulate_document([Backspace(1)])


def test_backspace_at_start_of_a_new_paragraph_raises_even_after_prior_text():
    # Can't merge back into the previous paragraph by backspacing.
    with pytest.raises(SimulationError, match="paragraph boundary"):
        simulate_document([KeyPress("a"), KeyPress("\n"), Backspace(1)])


def test_backspace_removes_trailing_characters_within_the_paragraph():
    doc = simulate_document([KeyPress("a"), KeyPress("b"), KeyPress("c"), Backspace(2)])
    assert doc.paragraphs[0].text == "a"


def test_backspace_that_runs_past_the_paragraphs_own_text_raises():
    with pytest.raises(SimulationError, match="paragraph boundary"):
        simulate_document([KeyPress("a"), Backspace(2)])


def test_insertion_adopts_the_preceding_characters_styles_after_backspace():
    actions = [
        KeyPress("a"),  # unstyled
        Format(FormatOp.TOGGLE_BOLD),
        KeyPress("b"),  # bold
        Backspace(1),  # removes "b"; "a" (unstyled) is now last
        KeyPress("c"),  # must NOT be bold
    ]
    doc = simulate_document(actions)
    (run,) = doc.paragraphs[0].runs
    assert run.text == "ac"
    assert run.styles == frozenset()


def test_backspace_that_empties_the_paragraph_leaves_insertion_unchanged():
    actions = [
        Format(FormatOp.TOGGLE_BOLD),
        KeyPress("a"),  # bold "a"
        Backspace(1),  # paragraph now empty; insertion must still be {BOLD}
        KeyPress("b"),  # must still be bold
    ]
    doc = simulate_document(actions)
    (run,) = doc.paragraphs[0].runs
    assert run.text == "b"
    assert run.styles == frozenset({TextStyle.BOLD})


# --- Format: CLEAR_FORMATTING and style toggles --------------------------------


def test_clear_formatting_resets_insertion_to_no_styles():
    actions = [
        Format(FormatOp.TOGGLE_BOLD),
        Format(FormatOp.TOGGLE_ITALIC),
        Format(FormatOp.CLEAR_FORMATTING),
        KeyPress("a"),
    ]
    doc = simulate_document(actions)
    assert doc.paragraphs[0].runs[0].styles == frozenset()


def test_clear_formatting_does_not_touch_paragraph_style_or_list():
    actions = [
        Format(FormatOp.SET_HEADING_3),
        Format(FormatOp.TOGGLE_BULLET_LIST),
        Format(FormatOp.CLEAR_FORMATTING),
    ]
    doc = simulate_document(actions)
    p = doc.paragraphs[0]
    assert p.style == ParagraphStyle.HEADING_3
    assert p.list_kind == ListKind.BULLET


@pytest.mark.parametrize(
    "op,style",
    [
        (FormatOp.TOGGLE_BOLD, TextStyle.BOLD),
        (FormatOp.TOGGLE_ITALIC, TextStyle.ITALIC),
        (FormatOp.TOGGLE_UNDERLINE, TextStyle.UNDERLINE),
        (FormatOp.TOGGLE_STRIKETHROUGH, TextStyle.STRIKETHROUGH),
    ],
)
def test_toggle_style_applies_it_then_toggling_again_removes_it(op, style):
    on = simulate_document([Format(op), KeyPress("a")])
    assert on.paragraphs[0].runs[0].styles == frozenset({style})

    off = simulate_document([Format(op), Format(op), KeyPress("a")])
    assert off.paragraphs[0].runs[0].styles == frozenset()


def test_toggling_two_different_styles_combines_them():
    actions = [Format(FormatOp.TOGGLE_BOLD), Format(FormatOp.TOGGLE_ITALIC), KeyPress("a")]
    doc = simulate_document(actions)
    assert doc.paragraphs[0].runs[0].styles == frozenset({TextStyle.BOLD, TextStyle.ITALIC})


# --- Format: SET_* / ALIGN_* ----------------------------------------------------


@pytest.mark.parametrize(
    "op,style",
    [
        (FormatOp.SET_NORMAL, ParagraphStyle.NORMAL),
        (FormatOp.SET_TITLE, ParagraphStyle.TITLE),
        (FormatOp.SET_SUBTITLE, ParagraphStyle.SUBTITLE),
        (FormatOp.SET_HEADING_1, ParagraphStyle.HEADING_1),
        (FormatOp.SET_HEADING_2, ParagraphStyle.HEADING_2),
        (FormatOp.SET_HEADING_3, ParagraphStyle.HEADING_3),
        (FormatOp.SET_HEADING_4, ParagraphStyle.HEADING_4),
        (FormatOp.SET_HEADING_5, ParagraphStyle.HEADING_5),
        (FormatOp.SET_HEADING_6, ParagraphStyle.HEADING_6),
    ],
)
def test_set_style_ops_set_the_last_paragraphs_style(op, style):
    assert simulate_document([Format(op)]).paragraphs[0].style == style


@pytest.mark.parametrize(
    "op,alignment",
    [
        (FormatOp.ALIGN_LEFT, Alignment.LEFT),
        (FormatOp.ALIGN_CENTER, Alignment.CENTER),
        (FormatOp.ALIGN_RIGHT, Alignment.RIGHT),
        (FormatOp.ALIGN_JUSTIFIED, Alignment.JUSTIFIED),
    ],
)
def test_align_ops_set_the_last_paragraphs_alignment(op, alignment):
    assert simulate_document([Format(op)]).paragraphs[0].alignment == alignment


# --- Format: list toggles, every transition -------------------------------------


def test_toggle_bullet_from_no_list_starts_bullet_at_level_zero():
    p = simulate_document([Format(FormatOp.TOGGLE_BULLET_LIST)]).paragraphs[0]
    assert (p.list_kind, p.list_level) == (ListKind.BULLET, 0)


def test_toggle_bullet_from_bullet_ends_the_list():
    actions = [Format(FormatOp.TOGGLE_BULLET_LIST), Format(FormatOp.TOGGLE_BULLET_LIST)]
    p = simulate_document(actions).paragraphs[0]
    assert (p.list_kind, p.list_level) == (None, 0)


def test_toggle_bullet_from_numbered_switches_kind_and_keeps_level():
    actions = [
        Format(FormatOp.TOGGLE_NUMBERED_LIST),
        Format(FormatOp.INDENT),
        Format(FormatOp.INDENT),
        Format(FormatOp.TOGGLE_BULLET_LIST),
    ]
    p = simulate_document(actions).paragraphs[0]
    assert (p.list_kind, p.list_level) == (ListKind.BULLET, 2)


def test_toggle_numbered_from_no_list_starts_numbered_at_level_zero():
    p = simulate_document([Format(FormatOp.TOGGLE_NUMBERED_LIST)]).paragraphs[0]
    assert (p.list_kind, p.list_level) == (ListKind.NUMBERED, 0)


def test_toggle_numbered_from_numbered_ends_the_list():
    actions = [Format(FormatOp.TOGGLE_NUMBERED_LIST), Format(FormatOp.TOGGLE_NUMBERED_LIST)]
    p = simulate_document(actions).paragraphs[0]
    assert (p.list_kind, p.list_level) == (None, 0)


def test_toggle_numbered_from_bullet_switches_kind_and_keeps_level():
    actions = [
        Format(FormatOp.TOGGLE_BULLET_LIST),
        Format(FormatOp.INDENT),
        Format(FormatOp.TOGGLE_NUMBERED_LIST),
    ]
    p = simulate_document(actions).paragraphs[0]
    assert (p.list_kind, p.list_level) == (ListKind.NUMBERED, 1)


# --- Format: INDENT / OUTDENT --------------------------------------------------


def test_indent_on_a_non_list_paragraph_raises():
    with pytest.raises(SimulationError):
        simulate_document([Format(FormatOp.INDENT)])


def test_outdent_on_a_non_list_paragraph_raises():
    with pytest.raises(SimulationError):
        simulate_document([Format(FormatOp.OUTDENT)])


def test_indent_level_clamps_at_eight():
    actions = [Format(FormatOp.TOGGLE_BULLET_LIST)] + [Format(FormatOp.INDENT)] * 12
    assert simulate_document(actions).paragraphs[0].list_level == 8


def test_outdent_level_clamps_at_zero():
    actions = [
        Format(FormatOp.TOGGLE_BULLET_LIST),
        Format(FormatOp.INDENT),
        *([Format(FormatOp.OUTDENT)] * 5),
    ]
    assert simulate_document(actions).paragraphs[0].list_level == 0


def test_outdent_on_a_list_that_has_just_ended_raises():
    # Toggling the list off first means the paragraph is no longer a list
    # item, even though it was one a moment ago.
    actions = [
        Format(FormatOp.TOGGLE_BULLET_LIST),
        Format(FormatOp.TOGGLE_BULLET_LIST),
        Format(FormatOp.OUTDENT),
    ]
    with pytest.raises(SimulationError):
        simulate_document(actions)


# --- Pause has no effect; run grouping ------------------------------------------


def test_pause_has_no_effect_on_the_model():
    doc = simulate_document([KeyPress("a"), Pause(5_000.0), KeyPress("b")])
    assert doc.paragraphs[0].text == "ab"


def test_consecutive_same_style_characters_group_into_one_run():
    actions = [Format(FormatOp.TOGGLE_BOLD), KeyPress("a"), KeyPress("b"), KeyPress("c")]
    doc = simulate_document(actions)
    assert len(doc.paragraphs[0].runs) == 1
    assert doc.paragraphs[0].runs[0].text == "abc"


def test_a_style_change_starts_a_new_run():
    actions = [KeyPress("a"), Format(FormatOp.TOGGLE_BOLD), KeyPress("b")]
    doc = simulate_document(actions)
    runs = doc.paragraphs[0].runs
    assert [(r.text, r.styles) for r in runs] == [
        ("a", frozenset()),
        ("b", frozenset({TextStyle.BOLD})),
    ]


def test_paragraph_with_no_characters_has_no_runs():
    doc = simulate_document([KeyPress("a"), KeyPress("\n")])
    assert doc.paragraphs[1].runs == ()


# --- simulate() vs simulate_document(): agreement on a hand-built stream -------


def test_simulate_equals_simulate_document_plain_text_for_a_hand_built_stream():
    actions = [
        Format(FormatOp.CLEAR_FORMATTING),
        Format(FormatOp.SET_HEADING_1),
        *[KeyPress(c) for c in "Title"],
        KeyPress("\n"),
        Format(FormatOp.SET_NORMAL),
        Format(FormatOp.TOGGLE_BULLET_LIST),
        *[KeyPress(c) for c in "one"],
        KeyPress("\n"),
        Format(FormatOp.INDENT),
        *[KeyPress(c) for c in "two"],
        Backspace(1),
        Format(FormatOp.TOGGLE_ITALIC),
        KeyPress("o"),
    ]
    assert simulate(actions) == simulate_document(actions).plain_text()


# --- property test: simulate() and simulate_document() always agree ------------


def _generate_valid_action_stream(rng: random.Random, steps: int) -> list:
    """Build a random but always-VALID action stream.

    Mirrors just enough of the simulate_document state machine to never
    generate an action that would raise SimulationError: never backspaces
    more characters than the current paragraph holds, and never
    indents/outdents a paragraph that isn't a list item. "Valid" is the only
    goal here -- the per-rule tests above already pin down exact behaviour,
    so this only needs to be a faithful-enough legality check.
    """
    actions: list = []
    current_length = 0
    current_list_kind: str | None = None  # mirrors ListKind | None, loosely
    alphabet = "abc XYZ.,!?\t"
    format_ops = list(FormatOp)

    while len(actions) < steps:
        weights = [4, 2 if current_length else 0, 1, 3]
        kind = rng.choices(["char", "backspace", "newline", "format"], weights=weights)[0]

        if kind == "char":
            actions.append(KeyPress(rng.choice(alphabet)))
            current_length += 1
        elif kind == "backspace":
            count = rng.randint(1, current_length)
            actions.append(Backspace(count))
            current_length -= count
        elif kind == "newline":
            actions.append(KeyPress("\n"))
            current_length = 0
            # list_kind/alignment/style are inherited automatically by the
            # model itself; current_list_kind (this generator's own legality
            # tracker) simply carries over unchanged too.
        else:  # format
            op = rng.choice(format_ops)
            if op in (FormatOp.INDENT, FormatOp.OUTDENT):
                if current_list_kind is None:
                    continue  # would raise -- skip this step, try again
            elif op is FormatOp.TOGGLE_BULLET_LIST:
                current_list_kind = None if current_list_kind == "bullet" else "bullet"
            elif op is FormatOp.TOGGLE_NUMBERED_LIST:
                current_list_kind = None if current_list_kind == "numbered" else "numbered"
            actions.append(Format(op))

    return actions


def test_simulate_matches_simulate_document_over_2000_random_valid_streams():
    rng = random.Random(20250915)
    for _ in range(2000):
        steps = rng.randint(5, 60)
        actions = _generate_valid_action_stream(rng, steps)
        # Must not raise, and the two simulators must always agree.
        assert simulate(actions) == simulate_document(actions).plain_text()
