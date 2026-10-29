"""``DocumentView``: a Document drawn with real Qt formatting, read-only."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QFontInfo, QTextBlock, QTextFormat, QTextListFormat
from PySide6.QtCore import Qt

from auto_essay_typer.document import (
    Alignment,
    Document,
    ListKind,
    Paragraph,
    ParagraphStyle,
    Run,
    TextStyle,
)
from auto_essay_typer.gui.document_view import DocumentView
from auto_essay_typer.importers import load_document

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "gdocs_export_sample.html"


def _view(qtbot, document: Document | None = None) -> DocumentView:
    view = DocumentView()
    qtbot.addWidget(view)
    if document is not None:
        view.set_document(document)
    return view


def _blocks(view: DocumentView) -> list[QTextBlock]:
    blocks = []
    block = view.document().begin()
    while block.isValid():
        blocks.append(block)
        block = block.next()
    return blocks


def _fragments(block: QTextBlock):
    """``(text, char format)`` for each styled stretch of ``block``."""
    found = []
    iterator = block.begin()
    while not iterator.atEnd():
        fragment = iterator.fragment()
        found.append((fragment.text(), fragment.charFormat()))
        iterator += 1
    return found


def _para(text: str, **kwargs) -> Paragraph:
    return Paragraph((Run(text),), **kwargs)


def _pixel_size(view: DocumentView, block: QTextBlock) -> int:
    """The size a block's first fragment is really drawn at, in pixels."""
    char_format = _fragments(block)[0][1]
    if char_format.hasProperty(QTextFormat.Property.FontPixelSize):
        return char_format.intProperty(QTextFormat.Property.FontPixelSize)
    return QFontInfo(view.font()).pixelSize()


# --- the widget -----------------------------------------------------------------------------------------


def test_the_view_is_read_only_and_named_for_screen_readers(qtbot):
    view = _view(qtbot)
    assert view.isReadOnly() is True
    assert view.accessibleName()
    assert view.accessibleDescription()


def test_a_new_view_is_empty_and_holds_no_document(qtbot):
    view = _view(qtbot)
    assert view.toPlainText() == ""
    assert view.shown_document is None


def test_the_view_remembers_the_document_it_was_given_as_the_source_of_truth(qtbot):
    document = Document((_para("hello"),))
    view = _view(qtbot, document)
    assert view.shown_document is document


def test_giving_the_view_nothing_clears_it(qtbot):
    view = _view(qtbot, Document((_para("hello"),)))
    view.set_document(None)
    assert view.toPlainText() == ""
    assert view.shown_document is None


def test_a_new_document_replaces_the_old_one_completely(qtbot):
    view = _view(qtbot, Document((_para("first"), _para("second", list_kind=ListKind.BULLET))))
    view.set_document(Document((_para("only"),)))
    assert view.toPlainText() == "only"
    assert view.document().blockCount() == 1
    assert _blocks(view)[0].textList() is None


def test_an_empty_document_draws_nothing(qtbot):
    assert _view(qtbot, Document(())).toPlainText() == ""


def test_one_block_is_drawn_per_paragraph_blank_ones_included(qtbot):
    document = Document((_para("a"), Paragraph(), _para("b", list_kind=ListKind.BULLET), Paragraph(), _para("c")))
    view = _view(qtbot, document)
    assert view.document().blockCount() == 5
    assert [block.text() for block in _blocks(view)] == ["a", "", "b", "", "c"]


def test_the_text_shown_is_exactly_the_documents_text_with_no_list_markers_in_it(qtbot):
    document = Document(
        (
            _para("Heading", style=ParagraphStyle.HEADING_1),
            _para("one", list_kind=ListKind.BULLET),
            _para("two", list_kind=ListKind.NUMBERED),
            _para("three", list_kind=ListKind.NUMBERED, list_level=1),
        )
    )
    view = _view(qtbot, document)
    # Markers are drawn by the layout, so they are never characters in the widget.
    assert view.toPlainText() == document.plain_text() == "Heading\none\ntwo\nthree"


def test_the_imported_sample_document_is_drawn_in_full(qtbot):
    document, _report = load_document(str(FIXTURE))
    view = _view(qtbot, document)
    assert view.document().blockCount() == len(document.paragraphs)
    assert view.toPlainText() == document.plain_text()


def test_the_view_scrolls_back_to_the_top_for_a_new_document(qtbot):
    long_document = Document(tuple(_para(f"line {n}") for n in range(300)))
    view = _view(qtbot, long_document)
    view.resize(300, 120)
    view.show()
    qtbot.waitExposed(view)
    view.verticalScrollBar().setValue(view.verticalScrollBar().maximum())
    assert view.verticalScrollBar().value() > 0

    view.set_document(Document(tuple(_para(f"a different line {n}") for n in range(300))))

    assert view.verticalScrollBar().value() == 0


def test_a_document_equal_to_the_one_shown_is_not_drawn_again_so_the_reader_keeps_their_place(qtbot):
    document = Document(tuple(_para(f"line {n}") for n in range(300)))
    view = _view(qtbot, document)
    view.resize(300, 120)
    view.show()
    qtbot.waitExposed(view)
    view.verticalScrollBar().setValue(view.verticalScrollBar().maximum())
    place = view.verticalScrollBar().value()

    same_again = Document(tuple(_para(f"line {n}") for n in range(300)))
    assert same_again == document and same_again is not document
    view.set_document(same_again)

    assert view.verticalScrollBar().value() == place
    assert view.shown_document is same_again, "the view still holds the object it was given"


def test_a_long_document_is_drawn_in_full(qtbot):
    document = Document(
        tuple(_para(f"paragraph number {n} with some words in it", list_kind=ListKind.BULLET if n % 3 == 0 else None) for n in range(2000))
    )
    view = _view(qtbot, document)
    assert view.document().blockCount() == 2000


# --- paragraph styles -------------------------------------------------------------------------------------


def test_headings_are_drawn_larger_the_higher_their_level(qtbot):
    document = Document(
        (
            _para("title", style=ParagraphStyle.TITLE),
            _para("h1", style=ParagraphStyle.HEADING_1),
            _para("h2", style=ParagraphStyle.HEADING_2),
            _para("h3", style=ParagraphStyle.HEADING_3),
            _para("h4", style=ParagraphStyle.HEADING_4),
            _para("body"),
        )
    )
    view = _view(qtbot, document)
    sizes = [_pixel_size(view, block) for block in _blocks(view)]
    assert sizes == sorted(sizes, reverse=True)
    assert len(set(sizes[:5])) == 5, "every level should look different"
    assert sizes[4] > sizes[5], "even the smallest heading is larger than body text"


def test_every_heading_level_has_its_own_size_and_a_heading_level_for_assistive_tech(qtbot):
    styles = [
        ParagraphStyle.HEADING_1,
        ParagraphStyle.HEADING_2,
        ParagraphStyle.HEADING_3,
        ParagraphStyle.HEADING_4,
        ParagraphStyle.HEADING_5,
        ParagraphStyle.HEADING_6,
    ]
    view = _view(qtbot, Document(tuple(_para(f"h{n}", style=style) for n, style in enumerate(styles, 1))))
    assert [block.blockFormat().headingLevel() for block in _blocks(view)] == [1, 2, 3, 4, 5, 6]


def test_a_title_and_a_subtitle_are_display_text_but_not_headings(qtbot):
    view = _view(
        qtbot,
        Document(
            (
                _para("title", style=ParagraphStyle.TITLE),
                _para("subtitle", style=ParagraphStyle.SUBTITLE),
                _para("body"),
            )
        ),
    )
    title, subtitle, body = _blocks(view)
    assert title.blockFormat().headingLevel() == 0
    assert subtitle.blockFormat().headingLevel() == 0
    assert _pixel_size(view, title) > _pixel_size(view, subtitle) > _pixel_size(view, body)


def test_headings_are_heavier_than_body_text_but_a_subtitle_is_not(qtbot):
    view = _view(
        qtbot,
        Document(
            (
                _para("h", style=ParagraphStyle.HEADING_2),
                _para("sub", style=ParagraphStyle.SUBTITLE),
                _para("body"),
            )
        ),
    )
    heading, subtitle, body = (_fragments(block)[0][1].fontWeight() for block in _blocks(view))
    assert heading > body
    assert subtitle == body


def test_a_headings_empty_line_still_takes_the_headings_height(qtbot):
    view = _view(qtbot, Document((Paragraph(style=ParagraphStyle.HEADING_1), _para("body"))))
    empty_heading = _blocks(view)[0]
    assert empty_heading.charFormat().intProperty(QTextFormat.Property.FontPixelSize) > QFontInfo(view.font()).pixelSize()


# --- alignment ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("alignment", "flag"),
    [
        (Alignment.LEFT, Qt.AlignmentFlag.AlignLeft),
        (Alignment.CENTER, Qt.AlignmentFlag.AlignHCenter),
        (Alignment.RIGHT, Qt.AlignmentFlag.AlignRight),
        (Alignment.JUSTIFIED, Qt.AlignmentFlag.AlignJustify),
    ],
)
def test_each_alignment_is_applied_to_its_paragraph(qtbot, alignment, flag):
    view = _view(qtbot, Document((_para("a"), _para("b", alignment=alignment), _para("c"))))
    block = _blocks(view)[1]
    assert block.blockFormat().alignment() & flag
    # and it does not spill onto its neighbours
    assert _blocks(view)[2].blockFormat().alignment() & Qt.AlignmentFlag.AlignLeft


# --- character styles ------------------------------------------------------------------------------------------


def test_each_character_style_is_drawn(qtbot):
    document = Document(
        (
            Paragraph(
                (
                    Run("plain "),
                    Run("bold ", frozenset({TextStyle.BOLD})),
                    Run("italic ", frozenset({TextStyle.ITALIC})),
                    Run("underline ", frozenset({TextStyle.UNDERLINE})),
                    Run("strike ", frozenset({TextStyle.STRIKETHROUGH})),
                    Run("all", frozenset(TextStyle)),
                )
            ),
        )
    )
    view = _view(qtbot, document)
    plain, bold, italic, underline, strike, everything = (fmt for _text, fmt in _fragments(_blocks(view)[0]))

    assert plain.fontWeight() < 600 and not (plain.fontItalic() or plain.fontUnderline() or plain.fontStrikeOut())
    assert bold.fontWeight() >= 700 and not bold.fontItalic()
    assert italic.fontItalic() and italic.fontWeight() < 600
    assert underline.fontUnderline() and not underline.fontStrikeOut()
    assert strike.fontStrikeOut() and not strike.fontUnderline()
    assert everything.fontWeight() >= 700
    assert everything.fontItalic() and everything.fontUnderline() and everything.fontStrikeOut()


def test_a_run_styles_do_not_leak_into_the_next_run_or_paragraph(qtbot):
    document = Document(
        (
            Paragraph((Run("bold", frozenset({TextStyle.BOLD})), Run(" plain"))),
            _para("next paragraph"),
        )
    )
    view = _view(qtbot, document)
    first, second = _blocks(view)
    assert _fragments(first)[1][1].fontWeight() < 600
    assert _fragments(second)[0][1].fontWeight() < 600


def test_a_bold_run_inside_a_heading_is_bolder_than_the_heading_alone(qtbot):
    document = Document(
        (Paragraph((Run("plain "), Run("bold", frozenset({TextStyle.BOLD}))), style=ParagraphStyle.HEADING_1),)
    )
    view = _view(qtbot, document)
    plain, bold = (fmt for _text, fmt in _fragments(_blocks(view)[0]))
    assert bold.fontWeight() > plain.fontWeight()
    assert bold.intProperty(QTextFormat.Property.FontPixelSize) == plain.intProperty(QTextFormat.Property.FontPixelSize)


# --- lists ---------------------------------------------------------------------------------------------------------


def _list_info(block: QTextBlock):
    text_list = block.textList()
    if text_list is None:
        return None
    return (text_list.format().style(), text_list.format().indent(), text_list.itemNumber(block))


def test_bulleted_items_are_real_list_items_indented_by_level(qtbot):
    document = Document(
        (
            _para("top", list_kind=ListKind.BULLET),
            _para("nested", list_kind=ListKind.BULLET, list_level=1),
            _para("deeper", list_kind=ListKind.BULLET, list_level=2),
            _para("top again", list_kind=ListKind.BULLET),
        )
    )
    view = _view(qtbot, document)
    top, nested, deeper, again = (_list_info(block) for block in _blocks(view))

    assert top[:2] == (QTextListFormat.Style.ListDisc, 1)
    assert nested[:2] == (QTextListFormat.Style.ListCircle, 2)
    assert deeper[:2] == (QTextListFormat.Style.ListSquare, 3)
    assert again[:2] == (QTextListFormat.Style.ListDisc, 1)
    assert top[2] == 0 and again[2] == 1  # the same top-level list, carried on


def test_numbered_items_are_numbered_and_nested_levels_use_letters_then_numerals(qtbot):
    document = Document(
        (
            _para("one", list_kind=ListKind.NUMBERED),
            _para("a", list_kind=ListKind.NUMBERED, list_level=1),
            _para("i", list_kind=ListKind.NUMBERED, list_level=2),
            _para("two", list_kind=ListKind.NUMBERED),
        )
    )
    view = _view(qtbot, document)
    one, a, i, two = (_list_info(block) for block in _blocks(view))

    assert one[:2] == (QTextListFormat.Style.ListDecimal, 1)
    assert a[:2] == (QTextListFormat.Style.ListLowerAlpha, 2)
    assert i[:2] == (QTextListFormat.Style.ListLowerRoman, 3)
    assert two[:2] == (QTextListFormat.Style.ListDecimal, 1)


def test_numbering_carries_on_across_nested_items_and_restarts_after_a_non_list_paragraph(qtbot):
    document = Document(
        (
            _para("one", list_kind=ListKind.NUMBERED),
            _para("nested", list_kind=ListKind.NUMBERED, list_level=1),
            _para("two", list_kind=ListKind.NUMBERED),
            _para("a break in the list"),
            _para("one again", list_kind=ListKind.NUMBERED),
        )
    )
    view = _view(qtbot, document)
    numbers = [(_list_info(block) or (None, None, None))[2] for block in _blocks(view)]
    assert numbers == [0, 0, 1, None, 0]


def test_a_sub_list_starts_again_from_one_after_its_parent_moves_on(qtbot):
    document = Document(
        (
            _para("one", list_kind=ListKind.NUMBERED),
            _para("one-a", list_kind=ListKind.NUMBERED, list_level=1),
            _para("one-b", list_kind=ListKind.NUMBERED, list_level=1),
            _para("two", list_kind=ListKind.NUMBERED),
            _para("two-a", list_kind=ListKind.NUMBERED, list_level=1),
        )
    )
    view = _view(qtbot, document)
    numbers = [_list_info(block)[2] for block in _blocks(view)]
    assert numbers == [0, 0, 1, 1, 0]


def test_changing_list_kind_at_the_same_level_starts_a_new_list(qtbot):
    document = Document(
        (
            _para("bullet", list_kind=ListKind.BULLET),
            _para("number", list_kind=ListKind.NUMBERED),
            _para("number two", list_kind=ListKind.NUMBERED),
        )
    )
    view = _view(qtbot, document)
    bullet, number, number_two = (_list_info(block) for block in _blocks(view))
    assert bullet[0] == QTextListFormat.Style.ListDisc
    assert number[0] == QTextListFormat.Style.ListDecimal and number[2] == 0
    assert number_two[2] == 1


def test_a_list_item_keeps_its_own_alignment_and_run_styles(qtbot):
    document = Document(
        (
            Paragraph(
                (Run("styled", frozenset({TextStyle.ITALIC})),),
                list_kind=ListKind.BULLET,
                alignment=Alignment.RIGHT,
            ),
        )
    )
    view = _view(qtbot, document)
    block = _blocks(view)[0]
    assert block.textList() is not None
    assert block.blockFormat().alignment() & Qt.AlignmentFlag.AlignRight
    assert _fragments(block)[0][1].fontItalic()


def test_a_list_that_begins_nested_is_still_indented_by_its_level(qtbot):
    view = _view(qtbot, Document((_para("deep", list_kind=ListKind.BULLET, list_level=2),)))
    assert _list_info(_blocks(view)[0])[1] == 3


def test_a_paragraph_with_typed_markers_shows_them_as_ordinary_text(qtbot):
    # What an editor with no list shortcuts is given (see keymaps.adapt_document): the marker
    # is a character that will really be typed, so it belongs in the view.
    document = Document((Paragraph((Run("• "), Run("item"))),))
    view = _view(qtbot, document)
    assert view.toPlainText() == "• item"
    assert _blocks(view)[0].textList() is None


# --- sizes, odd characters, empty items, font changes ---------------------------------------------------------------------------


@pytest.mark.parametrize("body", [9, 10, 11, 12, 13, 14, 15, 16, 18, 20, 24])
def test_every_display_style_is_strictly_larger_than_the_next_one_down_at_any_body_size(body):
    from auto_essay_typer.gui.document_view import _SIZE_RANK, display_sizes

    sizes = display_sizes(body)
    ranked = [sizes[style] for style in _SIZE_RANK]
    assert ranked == sorted(set(ranked), reverse=True), f"collision at body size {body}: {ranked}"
    assert min(ranked) > body


def test_google_docs_ordering_is_kept_with_a_subtitle_between_heading_2_and_heading_3():
    from auto_essay_typer.gui.document_view import display_sizes

    sizes = display_sizes(13)
    assert sizes[ParagraphStyle.HEADING_2] > sizes[ParagraphStyle.SUBTITLE] > sizes[ParagraphStyle.HEADING_3]


def test_all_eight_display_styles_are_drawn_in_distinct_decreasing_sizes(qtbot):
    from auto_essay_typer.gui.document_view import _SIZE_RANK

    view = _view(qtbot, Document(tuple(_para(style.value, style=style) for style in _SIZE_RANK) + (_para("body"),)))
    sizes = [_pixel_size(view, block) for block in _blocks(view)]
    assert sizes == sorted(set(sizes), reverse=True)
    assert len(sizes) == 9


def test_characters_a_text_layout_would_act_on_do_not_split_a_paragraph_or_its_list_item(qtbot):
    document = Document(
        (
            Paragraph((Run("carriage\rreturn"),), list_kind=ListKind.BULLET),
            Paragraph((Run("line separator and paragraph separator"),), list_kind=ListKind.BULLET),
            _para("plain"),
        )
    )
    view = _view(qtbot, document)
    assert view.document().blockCount() == 3
    first, second, third = _blocks(view)
    assert first.textList() is not None and second.textList() is not None
    assert third.textList() is None
    # The view cannot be the source of what is typed; the document still holds the real text.
    assert view.shown_document.paragraphs[0].text == "carriage\rreturn"


def test_qts_own_placeholder_characters_are_replaced_for_the_drawing(qtbot):
    view = _view(qtbot, Document((_para("a﷐b﷑c"),)))
    assert view.toPlainText() == "a�b�c"


def test_a_list_item_with_no_text_draws_no_marker_because_no_real_document_has_one(qtbot):
    document = Document((_para("one", list_kind=ListKind.BULLET), Paragraph(list_kind=ListKind.BULLET), _para("two")))
    view = _view(qtbot, document)
    assert [block.textList() is not None for block in _blocks(view)] == [True, False, False]
    assert view.shown_document is document, "the view still holds what it was given"


def test_the_indent_leaves_room_for_a_three_digit_number_marker(qtbot):
    view = _view(qtbot, Document((_para("x", list_kind=ListKind.NUMBERED),)))
    assert view.fontMetrics().horizontalAdvance("100.") < view.document().indentWidth()


def test_a_change_of_font_redraws_with_the_new_sizes_and_keeps_the_readers_place(qtbot):
    document = Document(
        (_para("Heading", style=ParagraphStyle.HEADING_1),) + tuple(_para(f"line {n}") for n in range(300))
    )
    view = _view(qtbot, document)
    view.resize(300, 120)
    view.show()
    qtbot.waitExposed(view)
    before = _pixel_size(view, _blocks(view)[0])
    view.verticalScrollBar().setValue(400)
    place = view.verticalScrollBar().value()
    assert place > 0

    bigger = view.font()
    bigger.setPixelSize(QFontInfo(view.font()).pixelSize() + 8)
    view.setFont(bigger)

    assert _pixel_size(view, _blocks(view)[0]) > before
    assert view.verticalScrollBar().value() > 0
