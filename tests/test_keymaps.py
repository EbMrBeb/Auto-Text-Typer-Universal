import pytest

from auto_essay_typer.actions import FormatOp
from auto_essay_typer.document import (
    Alignment,
    Document,
    ListKind,
    Paragraph,
    ParagraphStyle,
    Run,
    TextStyle,
)
from auto_essay_typer.keymaps import (
    GOOGLE_DOCS_MAC,
    KEYMAPS,
    adapt_document,
    format_chord,
    parse_chord,
)

# --- parse_chord / format_chord --------------------------------------------


def test_parse_chord_splits_modifiers_from_the_final_key():
    assert parse_chord("cmd+shift+8") == ("cmd", "shift", "8")


def test_parse_chord_with_no_modifiers():
    assert parse_chord("b") == ("b",)


def test_parse_chord_accepts_named_final_keys():
    assert parse_chord("tab") == ("tab",)
    assert parse_chord("shift+tab") == ("shift", "tab")
    assert parse_chord("enter") == ("enter",)


def test_format_chord_joins_with_plus():
    assert format_chord(("cmd", "shift", "8")) == "cmd+shift+8"


def test_format_chord_of_a_single_key():
    assert format_chord(("tab",)) == "tab"


@pytest.mark.parametrize(
    "text",
    ["cmd+shift+8", "b", "tab", "shift+tab", "cmd+\\", "cmd+alt+1", "enter"],
)
def test_parse_and_format_chord_round_trip(text):
    assert format_chord(parse_chord(text)) == text


def test_parse_chord_rejects_empty_string():
    with pytest.raises(ValueError):
        parse_chord("")


def test_parse_chord_rejects_unknown_modifier():
    with pytest.raises(ValueError, match="modifier"):
        parse_chord("foo+a")


def test_parse_chord_rejects_trailing_plus_with_no_final_key():
    with pytest.raises(ValueError):
        parse_chord("cmd+")


def test_parse_chord_rejects_a_lone_modifier_name_as_final_key():
    # "shift" alone isn't a single character or a named key, so it cannot be
    # a final key -- and there is nothing else to be one either.
    with pytest.raises(ValueError):
        parse_chord("shift")


def test_parse_chord_rejects_multiple_non_modifier_tokens():
    with pytest.raises(ValueError):
        parse_chord("cmd+a+b")


def test_parse_chord_rejects_a_multi_character_final_key_that_is_not_named():
    with pytest.raises(ValueError):
        parse_chord("cmd+ab")


# --- GOOGLE_DOCS_MAC coverage -----------------------------------------------


def test_google_docs_mac_covers_every_format_op_except_title_and_subtitle():
    missing = set(FormatOp) - set(GOOGLE_DOCS_MAC)
    assert missing == {FormatOp.SET_TITLE, FormatOp.SET_SUBTITLE}


def test_keymaps_registry_contains_google_docs_mac():
    assert KEYMAPS["google-docs-mac"] is GOOGLE_DOCS_MAC


def test_indent_and_outdent_use_tab_not_a_bracket_shortcut():
    # Deliberately not cmd+]/cmd+[ -- see keymaps.py module docstring.
    assert GOOGLE_DOCS_MAC[FormatOp.INDENT] == ("tab",)
    assert GOOGLE_DOCS_MAC[FormatOp.OUTDENT] == ("shift", "tab")


# --- adapt_document: Title / Subtitle substitution --------------------------


def test_adapt_document_leaves_a_fully_supported_document_unchanged():
    doc = Document(
        (
            Paragraph(runs=(Run("hi"),), style=ParagraphStyle.HEADING_1),
            Paragraph(runs=(Run("body", frozenset({TextStyle.BOLD})),)),
        )
    )
    adapted, warnings = adapt_document(doc, GOOGLE_DOCS_MAC)
    assert adapted == doc
    assert warnings == ()


def test_adapt_document_substitutes_title_with_heading_1():
    doc = Document((Paragraph(runs=(Run("My Essay"),), style=ParagraphStyle.TITLE),))
    adapted, warnings = adapt_document(doc, GOOGLE_DOCS_MAC)
    assert adapted.paragraphs[0].style == ParagraphStyle.HEADING_1
    assert adapted.paragraphs[0].text == "My Essay"
    assert len(warnings) == 1
    assert "Title" in warnings[0]
    assert "Heading 1" in warnings[0]
    assert "1" in warnings[0]


def test_adapt_document_substitutes_subtitle_with_heading_2():
    doc = Document((Paragraph(runs=(Run("by me"),), style=ParagraphStyle.SUBTITLE),))
    adapted, warnings = adapt_document(doc, GOOGLE_DOCS_MAC)
    assert adapted.paragraphs[0].style == ParagraphStyle.HEADING_2
    assert len(warnings) == 1
    assert "Subtitle" in warnings[0]
    assert "Heading 2" in warnings[0]


def test_adapt_document_aggregates_multiple_title_paragraphs_into_one_warning():
    doc = Document(
        (
            Paragraph(runs=(Run("a"),), style=ParagraphStyle.TITLE),
            Paragraph(runs=(Run("b"),), style=ParagraphStyle.TITLE),
            Paragraph(runs=(Run("c"),), style=ParagraphStyle.TITLE),
        )
    )
    adapted, warnings = adapt_document(doc, GOOGLE_DOCS_MAC)
    assert all(p.style == ParagraphStyle.HEADING_1 for p in adapted.paragraphs)
    assert len(warnings) == 1
    assert "3" in warnings[0]
    assert "paragraphs" in warnings[0]  # plural


def test_adapt_document_reports_title_and_subtitle_as_separate_warnings():
    doc = Document(
        (
            Paragraph(runs=(Run("a"),), style=ParagraphStyle.TITLE),
            Paragraph(runs=(Run("b"),), style=ParagraphStyle.SUBTITLE),
        )
    )
    _, warnings = adapt_document(doc, GOOGLE_DOCS_MAC)
    assert len(warnings) == 2
    joined = " ".join(warnings)
    assert "Title" in joined and "Subtitle" in joined


def test_adapt_document_does_not_mutate_other_paragraph_attributes():
    doc = Document(
        (
            Paragraph(
                runs=(Run("x", frozenset({TextStyle.ITALIC})),),
                style=ParagraphStyle.TITLE,
                alignment=Alignment.CENTER,
                list_kind=ListKind.BULLET,
                list_level=2,
            ),
        )
    )
    adapted, _ = adapt_document(doc, GOOGLE_DOCS_MAC)
    p = adapted.paragraphs[0]
    assert p.alignment == Alignment.CENTER
    assert p.list_kind == ListKind.BULLET
    assert p.list_level == 2
    assert p.runs[0].styles == frozenset({TextStyle.ITALIC})


# --- adapt_document: missing ops raise --------------------------------------


def test_adapt_document_raises_naming_a_missing_alignment_op():
    incomplete = {op: chord for op, chord in GOOGLE_DOCS_MAC.items() if op != FormatOp.ALIGN_RIGHT}
    doc = Document((Paragraph(runs=(Run("x"),), alignment=Alignment.RIGHT),))
    with pytest.raises(ValueError, match="align_right"):
        adapt_document(doc, incomplete)


def test_adapt_document_raises_naming_a_missing_toggle_style_op():
    incomplete = {op: chord for op, chord in GOOGLE_DOCS_MAC.items() if op != FormatOp.TOGGLE_UNDERLINE}
    doc = Document((Paragraph(runs=(Run("x", frozenset({TextStyle.UNDERLINE})),)),))
    with pytest.raises(ValueError, match="toggle_underline"):
        adapt_document(doc, incomplete)


def test_adapt_document_raises_naming_a_missing_list_toggle_op():
    incomplete = {
        op: chord for op, chord in GOOGLE_DOCS_MAC.items() if op != FormatOp.TOGGLE_NUMBERED_LIST
    }
    doc = Document((Paragraph(runs=(Run("x"),), list_kind=ListKind.NUMBERED),))
    with pytest.raises(ValueError, match="toggle_numbered_list"):
        adapt_document(doc, incomplete)


def test_adapt_document_raises_naming_a_missing_indent_op():
    incomplete = {op: chord for op, chord in GOOGLE_DOCS_MAC.items() if op != FormatOp.INDENT}
    doc = Document((Paragraph(runs=(Run("x"),), list_kind=ListKind.BULLET, list_level=1),))
    with pytest.raises(ValueError, match="indent"):
        adapt_document(doc, incomplete)


def test_adapt_document_reports_all_missing_ops_at_once():
    incomplete = {
        op: chord
        for op, chord in GOOGLE_DOCS_MAC.items()
        if op not in (FormatOp.ALIGN_RIGHT, FormatOp.TOGGLE_BOLD)
    }
    doc = Document(
        (Paragraph(runs=(Run("x", frozenset({TextStyle.BOLD})),), alignment=Alignment.RIGHT),)
    )
    with pytest.raises(ValueError) as exc_info:
        adapt_document(doc, incomplete)
    message = str(exc_info.value)
    assert "align_right" in message
    assert "toggle_bold" in message


def test_adapt_document_with_empty_keymap_raises_even_for_a_blank_document():
    # A single default (NORMAL, LEFT, no list, no runs) paragraph still needs
    # SET_NORMAL and ALIGN_LEFT -- an empty keymap has neither. NORMAL has no
    # substitution fallback (only TITLE/SUBTITLE do), so this must raise.
    doc = Document((Paragraph(),))
    with pytest.raises(ValueError, match="set_normal"):
        adapt_document(doc, {})
