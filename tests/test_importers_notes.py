"""The importers' shared list of what they report, and the one place it is worded.

The panel's half of this -- a rule for every kind -- is ``tests/gui/test_import_note_rules.py``.
These pin the importers' half: what each message says, that a kind cannot be counted without
being worded, and that a loss counted under a name nothing words is an error rather than a
line missing from the report.
"""

from __future__ import annotations

import ast
import inspect
from collections import Counter

import pytest

from auto_essay_typer.importers import docx, gdocs_html, notes
from auto_essay_typer.importers.notes import NoteKind

_EVERYTHING = {
    "text_box_excerpts": ("Important sidebar note", "a second box"),
    "tracked_changes": True,
    "custom_style": True,
}


@pytest.mark.parametrize(
    ("kind", "count", "message"),
    [
        (NoteKind.LINK, 1, "1 link typed as plain text"),
        (NoteKind.LINK, 3, "3 links typed as plain text"),
        (NoteKind.IMAGE, 1, "1 image not reproduced (dropped)"),
        (NoteKind.IMAGE, 2, "2 images not reproduced (dropped)"),
        (NoteKind.RULE, 1, "1 horizontal rule not reproduced (dropped)"),
        (NoteKind.RULE, 4, "4 horizontal rules not reproduced (dropped)"),
        (NoteKind.BREAK, 1, "1 soft line break typed as paragraph break"),
        (NoteKind.BREAK, 5, "5 soft line breaks typed as paragraph breaks"),
        (
            NoteKind.FOOTNOTE,
            1,
            "1 footnote/endnote/comment reference dropped, along with the text of the note",
        ),
        (
            NoteKind.FOOTNOTE,
            2,
            "2 footnote/endnote/comment references dropped, along with the text of the note",
        ),
        (NoteKind.SYMBOL, 1, "1 special symbol will be left out"),
        (NoteKind.SYMBOL, 2, "2 special symbols will be left out"),
        (NoteKind.HEADER_FOOTER, 1, "1 header or footer not reproduced (dropped)"),
        (NoteKind.HEADER_FOOTER, 3, "3 headers or footers not reproduced (dropped)"),
    ],
)
def test_a_counted_kind_is_worded_by_its_count(kind, count, message):
    assert notes.messages_by_kind({kind: count}, keeps_paragraph_formatting=True) == {kind: message}


def test_a_table_is_worded_by_whether_the_reader_keeps_a_cells_formatting():
    kept = notes.messages_by_kind({NoteKind.TABLE: 1}, keeps_paragraph_formatting=True)
    lost = notes.messages_by_kind({NoteKind.TABLE: 1}, keeps_paragraph_formatting=False)
    assert kept == {NoteKind.TABLE: "1 table flattened: each cell typed as one or more paragraphs, row by row"}
    assert lost == {
        NoteKind.TABLE: "1 table flattened: each cell typed as one or more paragraphs, row by row, "
        "losing any heading style, alignment or list inside a cell"
    }
    plural = notes.messages_by_kind({NoteKind.TABLE: 2}, keeps_paragraph_formatting=True)
    assert plural[NoteKind.TABLE].startswith("2 tables flattened: ")


def test_a_table_message_no_longer_claims_one_paragraph_per_cell():
    for keeps in (True, False):
        (message,) = notes.messages_by_kind({NoteKind.TABLE: 1}, keeps_paragraph_formatting=keeps).values()
        assert "one paragraph per cell" not in message


@pytest.mark.parametrize(
    ("excerpts", "message"),
    [
        (("Important sidebar note",), "1 text box's content will not be reproduced: \"Important sidebar note\""),
        (("a", "b"), "2 text boxes' content will not be reproduced: \"a\", \"b\""),
        (("a", ""), "2 text boxes' content will not be reproduced: \"a\""),
        (("",), "1 text box's content will not be reproduced"),
    ],
)
def test_text_boxes_are_worded_with_the_words_found_in_them(excerpts, message):
    messages = notes.messages_by_kind({}, keeps_paragraph_formatting=True, text_box_excerpts=excerpts)
    assert messages == {NoteKind.TEXT_BOX: message}


def test_tracked_changes_and_custom_styles_are_each_said_once():
    messages = notes.messages_by_kind(
        {}, keeps_paragraph_formatting=True, tracked_changes=True, custom_style=True
    )
    assert messages == {
        NoteKind.TRACKED_CHANGES: "this document had tracked changes; it is typed as the final version, "
        "with the edits accepted and the deleted text left out",
        NoteKind.STYLE: "this document uses custom fonts, sizes, or colours, which are not reproduced",
    }


def test_nothing_found_says_nothing():
    assert notes.render({}, keeps_paragraph_formatting=True) == ()
    assert notes.render(Counter({NoteKind.IMAGE: 0}), keeps_paragraph_formatting=False) == ()


def test_every_kind_has_importer_wording_whether_or_not_a_document_had_one():
    """A member added to NoteKind with no wording fails here, not in front of the first document."""
    assert notes.messages_by_kind({}, keeps_paragraph_formatting=True) == {}


def test_messages_come_out_in_note_kind_order_whatever_order_they_were_counted_in():
    counts = {kind: 1 for kind in reversed(notes.COUNTED_KINDS)}
    messages = notes.messages_by_kind(counts, keeps_paragraph_formatting=True, **_EVERYTHING)
    assert list(messages) == list(NoteKind)


def test_the_counted_kinds_are_every_kind_but_the_three_that_need_more_than_a_count():
    assert set(notes.COUNTED_KINDS) == set(NoteKind) - {
        NoteKind.TEXT_BOX,
        NoteKind.TRACKED_CHANGES,
        NoteKind.STYLE,
    }


def test_a_loss_counted_under_a_name_nothing_words_is_an_error_not_a_missing_line():
    with pytest.raises(ValueError) as failure:
        notes.render(Counter({NoteKind.IMAGE: 1, "widget": 3}), keeps_paragraph_formatting=True)
    text = str(failure.value)
    assert "widget" in text
    assert "NoteKind" in text
    assert "auto_essay_typer/importers/notes.py" in text
    assert "auto_essay_typer/gui/document_warnings.py" in text


@pytest.mark.parametrize("kind", [NoteKind.TEXT_BOX, NoteKind.TRACKED_CHANGES, NoteKind.STYLE])
def test_a_kind_that_needs_more_than_a_count_is_not_accepted_as_one(kind):
    with pytest.raises(ValueError):
        notes.render(Counter({kind: 1}), keeps_paragraph_formatting=True)


@pytest.mark.parametrize("function", [notes.messages_by_kind, notes.render])
def test_an_importer_has_to_say_whether_a_cells_paragraphs_keep_their_formatting(function):
    parameter = inspect.signature(function).parameters["keeps_paragraph_formatting"]
    assert parameter.default is inspect.Parameter.empty, "no default: no importer may leave it unanswered"
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


def test_a_plain_string_that_names_a_kind_is_counted_as_that_kind():
    """The enum is a str, so the importers' ``Counter`` keys and the wording agree on one spelling."""
    assert notes.render({"image": 2}, keeps_paragraph_formatting=True) == ("2 images not reproduced (dropped)",)


@pytest.mark.parametrize("module", [docx, gdocs_html], ids=["docx", "gdocs_html"])
def test_the_importers_count_only_under_note_kind_members(module):
    """Every ``warnings[...] += 1`` names a ``NoteKind`` member, not a string.

    A string that is not a member raises when the loss is counted, but only on a document that
    has it. Reading the source finds the typo or the invented name on a path no test happens to
    reach, which is exactly where a loss counted under a name nothing words would go unreported.
    """
    tree = ast.parse(inspect.getsource(module))
    counted = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "warnings"
    ]
    assert counted, f"{module.__name__} should count something under .warnings"
    for node in counted:
        key = node.slice
        assert (
            isinstance(key, ast.Attribute)
            and isinstance(key.value, ast.Name)
            and key.value.id == "NoteKind"
            and key.attr in NoteKind.__members__
        ), f"{module.__name__}:{node.lineno} counts under {ast.unparse(key)}; count under a NoteKind member"
