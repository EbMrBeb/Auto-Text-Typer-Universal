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
    NO_OP,
    adapt_document,
    format_chord,
    parse_chord,
    required_ops,
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


def test_parse_chord_accepts_space_as_a_named_final_key():
    # Needed for microsoft-word's CLEAR_FORMATTING (ctrl+space) -- see
    # targets.py.
    assert parse_chord("space") == ("space",)
    assert parse_chord("ctrl+space") == ("ctrl", "space")


def test_no_op_is_an_empty_chord():
    assert NO_OP == ()


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


# --- adapt_document: CLEAR_FORMATTING is the one unconditional requirement --


def test_adapt_document_raises_when_clear_formatting_is_missing_even_for_a_blank_document():
    # CLEAR_FORMATTING must always be defined (as a real chord, or
    # keymaps.NO_OP when the target genuinely has nothing to clear) -- its
    # absence is a programming error in the keymap, not something any
    # document-level fallback can paper over.
    doc = Document((Paragraph(),))
    with pytest.raises(ValueError, match="clear_formatting"):
        adapt_document(doc, {})


def test_adapt_document_blank_document_needs_nothing_but_clear_formatting():
    # A default (NORMAL, LEFT, no runs, no list) paragraph never needs its
    # own SET_NORMAL/ALIGN_LEFT op -- a blank document already starts there
    # (see required_ops's docstring), so this minimal keymap suffices.
    doc = Document((Paragraph(),))
    adapted, warnings = adapt_document(doc, {FormatOp.CLEAR_FORMATTING: NO_OP})
    assert adapted == doc
    assert warnings == ()


# --- adapt_document: missing alignment op falls back to LEFT ----------------


def test_adapt_document_falls_back_missing_alignment_to_left():
    incomplete = {op: chord for op, chord in GOOGLE_DOCS_MAC.items() if op != FormatOp.ALIGN_RIGHT}
    doc = Document((Paragraph(runs=(Run("x"),), alignment=Alignment.RIGHT),))
    adapted, warnings = adapt_document(doc, incomplete)
    assert adapted.paragraphs[0].alignment == Alignment.LEFT
    assert len(warnings) == 1
    assert "left" in warnings[0].lower()
    assert "Right" in warnings[0]


def test_adapt_document_left_alignment_never_needs_a_fallback_or_warning():
    doc = Document((Paragraph(runs=(Run("x"),), alignment=Alignment.LEFT),))
    adapted, warnings = adapt_document(doc, {FormatOp.CLEAR_FORMATTING: NO_OP})
    assert adapted == doc
    assert warnings == ()


def test_adapt_document_aggregates_multiple_alignment_fallbacks_of_the_same_kind():
    incomplete = {op: chord for op, chord in GOOGLE_DOCS_MAC.items() if op != FormatOp.ALIGN_CENTER}
    doc = Document(
        (
            Paragraph(runs=(Run("a"),), alignment=Alignment.CENTER),
            Paragraph(runs=(Run("b"),), alignment=Alignment.CENTER),
        )
    )
    adapted, warnings = adapt_document(doc, incomplete)
    assert all(p.alignment == Alignment.LEFT for p in adapted.paragraphs)
    assert len(warnings) == 1
    assert "2" in warnings[0]
    assert "Center" in warnings[0]


# --- adapt_document: missing text-style toggle drops that style from the run -


def test_adapt_document_drops_missing_toggle_style_from_run():
    incomplete = {op: chord for op, chord in GOOGLE_DOCS_MAC.items() if op != FormatOp.TOGGLE_UNDERLINE}
    doc = Document((Paragraph(runs=(Run("x", frozenset({TextStyle.UNDERLINE})),)),))
    adapted, warnings = adapt_document(doc, incomplete)
    assert adapted.paragraphs[0].runs[0].styles == frozenset()
    assert adapted.paragraphs[0].runs[0].text == "x"
    assert len(warnings) == 1
    assert "underline" in warnings[0]


def test_adapt_document_drops_only_the_unavailable_style_keeping_others():
    incomplete = {op: chord for op, chord in GOOGLE_DOCS_MAC.items() if op != FormatOp.TOGGLE_UNDERLINE}
    doc = Document(
        (Paragraph(runs=(Run("x", frozenset({TextStyle.UNDERLINE, TextStyle.BOLD})),)),)
    )
    adapted, _ = adapt_document(doc, incomplete)
    assert adapted.paragraphs[0].runs[0].styles == frozenset({TextStyle.BOLD})


def test_adapt_document_reports_two_fallback_kinds_together():
    incomplete = {
        op: chord
        for op, chord in GOOGLE_DOCS_MAC.items()
        if op not in (FormatOp.ALIGN_RIGHT, FormatOp.TOGGLE_BOLD)
    }
    doc = Document(
        (Paragraph(runs=(Run("x", frozenset({TextStyle.BOLD})),), alignment=Alignment.RIGHT),)
    )
    adapted, warnings = adapt_document(doc, incomplete)
    assert adapted.paragraphs[0].alignment == Alignment.LEFT
    assert adapted.paragraphs[0].runs[0].styles == frozenset()
    assert len(warnings) == 2
    joined = " ".join(warnings)
    assert "Right" in joined
    assert "bold" in joined


# --- adapt_document: heading representability fallback -----------------------


def test_adapt_document_falls_back_heading_to_normal_bold_when_bold_available():
    incomplete = {op: chord for op, chord in GOOGLE_DOCS_MAC.items() if op != FormatOp.SET_HEADING_3}
    doc = Document((Paragraph(runs=(Run("Section"),), style=ParagraphStyle.HEADING_3),))
    adapted, warnings = adapt_document(doc, incomplete)
    p = adapted.paragraphs[0]
    assert p.style == ParagraphStyle.NORMAL
    assert p.runs[0].styles == frozenset({TextStyle.BOLD})
    assert p.text == "Section"
    assert len(warnings) == 1
    assert "Heading 3" in warnings[0]
    assert "bold" in warnings[0].lower()


def test_adapt_document_falls_back_heading_to_plain_normal_when_bold_unavailable():
    incomplete = {
        op: chord
        for op, chord in GOOGLE_DOCS_MAC.items()
        if op not in (FormatOp.SET_HEADING_3, FormatOp.TOGGLE_BOLD)
    }
    doc = Document((Paragraph(runs=(Run("Section"),), style=ParagraphStyle.HEADING_3),))
    adapted, warnings = adapt_document(doc, incomplete)
    p = adapted.paragraphs[0]
    assert p.style == ParagraphStyle.NORMAL
    assert p.runs[0].styles == frozenset()
    assert len(warnings) == 1
    assert "Heading 3" in warnings[0]


def test_adapt_document_falls_back_heading_when_set_normal_itself_is_missing():
    # SET_HEADING_1 is present, but there would be no way back to Normal
    # afterwards, so the heading still cannot be represented as itself.
    incomplete = {op: chord for op, chord in GOOGLE_DOCS_MAC.items() if op != FormatOp.SET_NORMAL}
    doc = Document((Paragraph(runs=(Run("Section"),), style=ParagraphStyle.HEADING_1),))
    adapted, warnings = adapt_document(doc, incomplete)
    assert adapted.paragraphs[0].style == ParagraphStyle.NORMAL
    assert len(warnings) == 1


def test_adapt_document_title_that_falls_all_the_way_to_normal_reports_both_steps():
    # Title has no shortcut anywhere, so it substitutes to Heading 1 first;
    # when this keymap can't represent Heading 1 either, it then falls all
    # the way to Normal -- both are real, distinct changes, so both warn.
    incomplete = {op: chord for op, chord in GOOGLE_DOCS_MAC.items() if op != FormatOp.SET_HEADING_1}
    doc = Document((Paragraph(runs=(Run("My Essay"),), style=ParagraphStyle.TITLE),))
    adapted, warnings = adapt_document(doc, incomplete)
    p = adapted.paragraphs[0]
    assert p.style == ParagraphStyle.NORMAL
    assert p.runs[0].styles == frozenset({TextStyle.BOLD})
    assert len(warnings) == 2
    joined = " ".join(warnings)
    assert "Title" in joined and "Heading 1" in joined


def test_adapt_document_normal_paragraph_never_needs_set_normal():
    doc = Document((Paragraph(runs=(Run("plain"),)),))
    adapted, warnings = adapt_document(doc, {FormatOp.CLEAR_FORMATTING: NO_OP})
    assert adapted == doc
    assert warnings == ()


# --- adapt_document: list fallback ---------------------------------------------


def test_adapt_document_flattens_bullet_list_when_toggle_missing():
    incomplete = {op: chord for op, chord in GOOGLE_DOCS_MAC.items() if op != FormatOp.TOGGLE_BULLET_LIST}
    doc = Document((Paragraph(runs=(Run("first item"),), list_kind=ListKind.BULLET),))
    adapted, warnings = adapt_document(doc, incomplete)
    p = adapted.paragraphs[0]
    assert p.list_kind is None
    assert p.list_level == 0
    assert p.style == ParagraphStyle.NORMAL
    assert p.text == "• first item"
    assert p.runs[0].styles == frozenset()  # the marker run is unstyled
    assert len(warnings) == 1
    assert "bullet" in warnings[0]


def test_adapt_document_flattens_numbered_list_when_indent_missing_even_at_level_zero():
    # Spec: fallback is decided once per list kind for the whole document,
    # not per paragraph -- so a level-0 item falls back too when nesting
    # can't be supported at all, rather than mixing real and typed items
    # within what should read as one consistent list.
    incomplete = {op: chord for op, chord in GOOGLE_DOCS_MAC.items() if op != FormatOp.INDENT}
    doc = Document(
        (
            Paragraph(runs=(Run("one"),), list_kind=ListKind.NUMBERED, list_level=0),
            Paragraph(runs=(Run("two"),), list_kind=ListKind.NUMBERED, list_level=0),
        )
    )
    adapted, warnings = adapt_document(doc, incomplete)
    assert adapted.paragraphs[0].text == "1. one"
    assert adapted.paragraphs[1].text == "2. two"
    assert all(p.list_kind is None for p in adapted.paragraphs)
    assert len(warnings) == 1
    assert "numbered" in warnings[0]


def test_adapt_document_flattens_list_when_outdent_missing():
    incomplete = {op: chord for op, chord in GOOGLE_DOCS_MAC.items() if op != FormatOp.OUTDENT}
    doc = Document((Paragraph(runs=(Run("x"),), list_kind=ListKind.BULLET),))
    adapted, _ = adapt_document(doc, incomplete)
    assert adapted.paragraphs[0].list_kind is None
    assert adapted.paragraphs[0].text == "• x"


def test_adapt_document_list_marker_is_its_own_unstyled_run_before_the_original_runs():
    incomplete = {op: chord for op, chord in GOOGLE_DOCS_MAC.items() if op != FormatOp.TOGGLE_BULLET_LIST}
    doc = Document(
        (Paragraph(runs=(Run("bold text", frozenset({TextStyle.BOLD})),), list_kind=ListKind.BULLET),)
    )
    adapted, _ = adapt_document(doc, incomplete)
    runs = adapted.paragraphs[0].runs
    assert runs[0] == Run("• ", frozenset())
    assert runs[1] == Run("bold text", frozenset({TextStyle.BOLD}))


def test_adapt_document_nested_list_marker_is_indented_four_spaces_per_level():
    incomplete = {op: chord for op, chord in GOOGLE_DOCS_MAC.items() if op != FormatOp.TOGGLE_BULLET_LIST}
    doc = Document((Paragraph(runs=(Run("x"),), list_kind=ListKind.BULLET, list_level=2),))
    adapted, _ = adapt_document(doc, incomplete)
    assert adapted.paragraphs[0].text == "        • x"  # 4 spaces * level 2


def test_adapt_document_numbered_list_fallback_counts_up_within_one_level():
    incomplete = {op: chord for op, chord in GOOGLE_DOCS_MAC.items() if op != FormatOp.TOGGLE_NUMBERED_LIST}
    doc = Document(
        (
            Paragraph(runs=(Run("a"),), list_kind=ListKind.NUMBERED),
            Paragraph(runs=(Run("b"),), list_kind=ListKind.NUMBERED),
            Paragraph(runs=(Run("c"),), list_kind=ListKind.NUMBERED),
        )
    )
    adapted, _ = adapt_document(doc, incomplete)
    assert [p.text for p in adapted.paragraphs] == ["1. a", "2. b", "3. c"]


def test_adapt_document_numbered_list_counter_resets_after_a_non_list_paragraph():
    incomplete = {op: chord for op, chord in GOOGLE_DOCS_MAC.items() if op != FormatOp.TOGGLE_NUMBERED_LIST}
    doc = Document(
        (
            Paragraph(runs=(Run("a"),), list_kind=ListKind.NUMBERED),
            Paragraph(runs=(Run("break"),)),
            Paragraph(runs=(Run("b"),), list_kind=ListKind.NUMBERED),
        )
    )
    adapted, _ = adapt_document(doc, incomplete)
    assert [p.text for p in adapted.paragraphs] == ["1. a", "break", "1. b"]


def test_adapt_document_numbered_list_nested_levels_reset_when_a_shallower_level_resumes():
    incomplete = {op: chord for op, chord in GOOGLE_DOCS_MAC.items() if op != FormatOp.TOGGLE_NUMBERED_LIST}
    doc = Document(
        (
            Paragraph(runs=(Run("one"),), list_kind=ListKind.NUMBERED, list_level=0),
            Paragraph(runs=(Run("nested-a"),), list_kind=ListKind.NUMBERED, list_level=1),
            Paragraph(runs=(Run("nested-b"),), list_kind=ListKind.NUMBERED, list_level=1),
            Paragraph(runs=(Run("two"),), list_kind=ListKind.NUMBERED, list_level=0),
            Paragraph(runs=(Run("nested-c"),), list_kind=ListKind.NUMBERED, list_level=1),
        )
    )
    adapted, _ = adapt_document(doc, incomplete)
    assert [p.text for p in adapted.paragraphs] == [
        "1. one",
        "    1. nested-a",
        "    2. nested-b",
        "2. two",
        "    1. nested-c",
    ]


def test_adapt_document_bullet_paragraph_does_not_reset_an_in_progress_numbered_counter():
    # Spec: a numbered run resets on a *non-list* paragraph -- a bullet item
    # (still a list paragraph, just a different kind) does not break it.
    incomplete = {
        op: chord
        for op, chord in GOOGLE_DOCS_MAC.items()
        if op not in (FormatOp.TOGGLE_NUMBERED_LIST, FormatOp.TOGGLE_BULLET_LIST)
    }
    doc = Document(
        (
            Paragraph(runs=(Run("one"),), list_kind=ListKind.NUMBERED),
            Paragraph(runs=(Run("aside"),), list_kind=ListKind.BULLET),
            Paragraph(runs=(Run("two"),), list_kind=ListKind.NUMBERED),
        )
    )
    adapted, warnings = adapt_document(doc, incomplete)
    assert [p.text for p in adapted.paragraphs] == ["1. one", "• aside", "2. two"]
    assert len(warnings) == 2  # one aggregated warning per list kind


def test_adapt_document_bullet_list_needs_no_fallback_when_fully_supported():
    doc = Document((Paragraph(runs=(Run("x"),), list_kind=ListKind.BULLET, list_level=1),))
    adapted, warnings = adapt_document(doc, GOOGLE_DOCS_MAC)
    assert adapted == doc
    assert warnings == ()


# --- adapt_document: idempotency ----------------------------------------------


@pytest.mark.parametrize(
    "doc",
    [
        Document((Paragraph(),)),
        Document((Paragraph(runs=(Run("hi"),), style=ParagraphStyle.TITLE),)),
        Document((Paragraph(runs=(Run("x", frozenset({TextStyle.UNDERLINE})),)),)),
        Document((Paragraph(runs=(Run("x"),), alignment=Alignment.CENTER),)),
        Document(
            (
                Paragraph(runs=(Run("one"),), list_kind=ListKind.NUMBERED, list_level=0),
                Paragraph(runs=(Run("nested"),), list_kind=ListKind.NUMBERED, list_level=1),
            )
        ),
    ],
)
def test_adapting_an_already_adapted_document_is_idempotent(doc):
    incomplete = {
        op: chord
        for op, chord in GOOGLE_DOCS_MAC.items()
        if op
        not in (
            FormatOp.SET_HEADING_1,
            FormatOp.SET_TITLE,
            FormatOp.TOGGLE_UNDERLINE,
            FormatOp.ALIGN_CENTER,
            FormatOp.TOGGLE_NUMBERED_LIST,
        )
    }
    adapted_once, warnings_once = adapt_document(doc, incomplete)
    adapted_twice, warnings_twice = adapt_document(adapted_once, incomplete)
    assert adapted_twice == adapted_once
    assert warnings_twice == ()


# --- required_ops --------------------------------------------------------------


def test_required_ops_of_a_blank_document_is_just_clear_formatting():
    doc = Document((Paragraph(),))
    assert required_ops(doc) == frozenset({FormatOp.CLEAR_FORMATTING})


def test_required_ops_excludes_set_normal_for_an_all_normal_document():
    doc = Document((Paragraph(runs=(Run("a"),)), Paragraph(runs=(Run("b"),))))
    assert FormatOp.SET_NORMAL not in required_ops(doc)


def test_required_ops_includes_set_normal_when_a_non_normal_style_is_used():
    doc = Document((Paragraph(runs=(Run("a"),), style=ParagraphStyle.HEADING_2),))
    ops = required_ops(doc)
    assert FormatOp.SET_NORMAL in ops
    assert FormatOp.SET_HEADING_2 in ops


def test_required_ops_excludes_align_left():
    doc = Document((Paragraph(runs=(Run("a"),), alignment=Alignment.LEFT),))
    assert FormatOp.ALIGN_LEFT not in required_ops(doc)


def test_required_ops_includes_non_left_alignment():
    doc = Document((Paragraph(runs=(Run("a"),), alignment=Alignment.CENTER),))
    assert FormatOp.ALIGN_CENTER in required_ops(doc)


def test_required_ops_includes_run_text_styles():
    doc = Document((Paragraph(runs=(Run("a", frozenset({TextStyle.ITALIC})),)),))
    assert FormatOp.TOGGLE_ITALIC in required_ops(doc)


def test_required_ops_includes_list_toggle_without_indent_outdent_at_level_zero():
    doc = Document((Paragraph(runs=(Run("a"),), list_kind=ListKind.BULLET),))
    ops = required_ops(doc)
    assert FormatOp.TOGGLE_BULLET_LIST in ops
    assert FormatOp.INDENT not in ops
    assert FormatOp.OUTDENT not in ops


def test_required_ops_includes_indent_and_outdent_when_level_is_nonzero():
    doc = Document((Paragraph(runs=(Run("a"),), list_kind=ListKind.NUMBERED, list_level=1),))
    ops = required_ops(doc)
    assert FormatOp.TOGGLE_NUMBERED_LIST in ops
    assert FormatOp.INDENT in ops
    assert FormatOp.OUTDENT in ops


def test_required_ops_of_a_fully_supported_document_is_subset_of_google_docs_mac():
    doc = Document(
        (
            Paragraph(runs=(Run("hi"),), style=ParagraphStyle.HEADING_1),
            Paragraph(runs=(Run("body", frozenset({TextStyle.BOLD})),)),
        )
    )
    assert required_ops(doc) <= set(GOOGLE_DOCS_MAC)
