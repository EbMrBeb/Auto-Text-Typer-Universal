"""Every kind of loss an importer can report needs its own wording in the panel.

The importers and the "What will not be reproduced" panel meet at a string. An
importer says "1 special symbol will be left out"; the panel recognises that with an
anchored pattern and says what it means for the person's document. A kind the panel has
no pattern for is not lost -- it is shown in the importer's own developer wording -- but
nothing pins what it says, and that is how text boxes, tracked changes and symbols went
out unreviewed, while the one test that was meant to catch it built its inputs from a
list of kinds typed out by hand and so never heard about the new ones.

No list of kinds is typed out below. They come from the importers' own
:class:`~auto_essay_typer.importers.notes.NoteKind`, so a kind added tomorrow is in
every one of these tests the moment it exists:

1. the panel has a rule for every kind, and the failure says what to add and where;
2. the importers' wording of every kind, at any count, is recognised as that kind and
   becomes a sentence rather than the fallback;
3. the real importers, fed real files, report every kind between them, and the panel
   recognises everything they say -- and each claim a sentence makes about the
   document is true of the importer that produced it;
4. every kind's sentence is pinned in the wording table, so a reword cannot change it unseen.
"""

from __future__ import annotations

import zipfile
from collections.abc import Collection, Iterable
from io import BytesIO
from pathlib import Path

import pytest

from auto_essay_typer.document import Alignment, ParagraphStyle
from auto_essay_typer.gui import document_warnings
from auto_essay_typer.gui.document_warnings import explain_import_warning, explain_warnings, import_warning_kind
from auto_essay_typer.importers import notes
from auto_essay_typer.importers.docx import parse_docx
from auto_essay_typer.importers.gdocs_html import parse_gdocs_html
from auto_essay_typer.importers.notes import NoteKind
from tests.gui.test_document_warnings import _JARGON, IMPORT_SENTENCES

REPO = Path(__file__).resolve().parents[2]

#: The places a developer is sent to, kept in one spot so the test that checks they exist
#: and the message that names them cannot drift apart.
_RULES_FILE = "auto_essay_typer/gui/document_warnings.py"
_WORDING_FILE = "auto_essay_typer/importers/notes.py"
_WORDING_TEST_FILE = "tests/gui/test_document_warnings.py"
_WORDING_TABLE = "IMPORT_SENTENCES"
_WORDING_TEST = "test_each_importer_message_becomes_a_plain_sentence"
_MODEL_RULE = "_say_footnote"


# --- 1. the panel has a rule for every kind -----------------------------------------------------------


def _require_a_rule_for_every_kind(kinds: Iterable[str], rule_kinds: Collection[str]) -> None:
    """Fail, naming each kind that has no rule and exactly what to write and where."""
    missing = [kind for kind in kinds if kind not in rule_kinds]
    if not missing:
        return
    listed = "\n".join(f"    NoteKind.{kind.upper()}  ({kind!r})" for kind in missing)
    raise AssertionError(
        "\n\nThe importers can report a kind of loss that the 'What will not be reproduced' panel "
        f"has no wording for:\n\n{listed}\n\n"
        f"NoteKind ({_WORDING_FILE}) is the importers' own list of what they can report. A kind with "
        "no rule reaches the Type page in the importer's developer wording (something like "
        "'1 special symbol will be left out') instead of a sentence about the person's document, "
        "and nothing pins what that says.\n\n"
        "To fix it, for each kind above:\n"
        f"  1. In {_RULES_FILE}, write a function that takes the regex match and returns the plain "
        "sentence for it: what will happen to the person's document, with no developer words "
        f"('dropped', 'flattened'). Copy the shape of {_MODEL_RULE}.\n"
        f"  2. Add it to _IMPORT_RULES in the same file, keyed by the NoteKind member, as an "
        f"_ImportRule whose pattern is anchored to the exact message {_WORDING_FILE} renders for "
        "that kind (messages_by_kind).\n"
        f"  3. In {_WORDING_TEST_FILE}, add that message and the sentence you expect as a row of "
        f"{_WORDING_TABLE}, which {_WORDING_TEST} runs. A kind with no row fails "
        "test_every_kind_has_its_sentence_pinned_in_the_wording_table.\n"
        "If the pattern does not match what the importers render, "
        "test_every_message_the_importers_word_is_recognised_as_its_own_kind says which message."
    )


def test_every_kind_of_loss_an_importer_can_report_has_a_rule_in_the_panel():
    _require_a_rule_for_every_kind(
        (kind.value for kind in NoteKind), {kind.value for kind in document_warnings.import_rule_kinds()}
    )


def test_a_kind_with_no_rule_fails_with_what_to_add_and_where():
    """The failure is part of the deliverable, so it is tested: a hypothetical new kind trips it."""
    with pytest.raises(AssertionError) as failure:
        _require_a_rule_for_every_kind(["symbol", "field"], {"symbol"})
    text = str(failure.value)
    assert "NoteKind.FIELD" in text
    assert "NoteKind.SYMBOL" not in text, "a kind that has its rule must not be named"
    for place in (_RULES_FILE, _WORDING_FILE, _WORDING_TEST_FILE, "_IMPORT_RULES", _MODEL_RULE, _WORDING_TEST):
        assert place in text


def _require_every_kind_pinned(kinds: Iterable[str], pinned_kinds: Collection[str]) -> None:
    """Fail, naming each kind whose sentence no row of the wording table pins."""
    unpinned = [kind for kind in kinds if kind not in pinned_kinds]
    if not unpinned:
        return
    listed = "\n".join(f"    NoteKind.{kind.upper()}  ({kind!r})" for kind in unpinned)
    raise AssertionError(
        f"\n\nThese kinds of loss have a rule in the panel but no row in {_WORDING_TABLE}:\n\n{listed}\n\n"
        "A sentence no test pins is one a reword can change without anyone seeing it.\n"
        f"Add a row to {_WORDING_TABLE} in {_WORDING_TEST_FILE}: the exact message "
        f"{_WORDING_FILE} renders for the kind, and the exact sentence the panel should show for it. "
        f"{_WORDING_TEST} then runs it."
    )


def test_every_kind_has_its_sentence_pinned_in_the_wording_table():
    pinned = {import_warning_kind(message) for message, _sentence in IMPORT_SENTENCES}
    _require_every_kind_pinned((kind.value for kind in NoteKind), {kind.value for kind in pinned if kind})


def test_a_kind_with_no_pinned_sentence_fails_with_what_to_add_and_where():
    with pytest.raises(AssertionError) as failure:
        _require_every_kind_pinned(["symbol", "field"], {"symbol"})
    text = str(failure.value)
    assert "NoteKind.FIELD" in text
    assert "NoteKind.SYMBOL" not in text
    for place in (_WORDING_TABLE, _WORDING_TEST_FILE, _WORDING_FILE, _WORDING_TEST):
        assert place in text


def test_the_places_the_failure_points_to_exist():
    """Guidance that points at a renamed function is worse than none, so it is checked."""
    for place in (_RULES_FILE, _WORDING_FILE, _WORDING_TEST_FILE):
        assert (REPO / place).is_file(), place
    assert hasattr(document_warnings, "_IMPORT_RULES")
    assert hasattr(document_warnings, "_ImportRule")
    assert callable(getattr(document_warnings, _MODEL_RULE))
    assert hasattr(notes, "messages_by_kind")
    wording_tests = (REPO / _WORDING_TEST_FILE).read_text(encoding="utf-8")
    assert f"def {_WORDING_TEST}" in wording_tests
    assert f"{_WORDING_TABLE}: list" in wording_tests
    assert IMPORT_SENTENCES, "the wording table should not be empty"


# --- 2. the importers' wording of every kind is recognised as that kind ---------------------------------------


def _every_kind_found(
    count: int, *, keeps_paragraph_formatting: bool, excerpts=("Important sidebar note", "a second box")
):
    """The importers' own wording of every kind at once, built from the registry's COUNTED_KINDS."""
    return notes.messages_by_kind(
        {kind: count for kind in notes.COUNTED_KINDS},
        keeps_paragraph_formatting=keeps_paragraph_formatting,
        text_box_excerpts=excerpts,
        tracked_changes=True,
        custom_style=True,
    )


@pytest.mark.parametrize("keeps_paragraph_formatting", [True, False], ids=["word-reader", "google-reader"])
@pytest.mark.parametrize("count", [1, 2, 5])
def test_every_message_the_importers_word_is_recognised_as_its_own_kind(count, keeps_paragraph_formatting):
    messages = _every_kind_found(count, keeps_paragraph_formatting=keeps_paragraph_formatting)
    assert set(messages) == set(NoteKind), "the importers' wording should reach every kind"
    for kind, message in messages.items():
        assert import_warning_kind(message) is kind, f"{kind!r}: the panel does not recognise {message!r} as it"
        plain = explain_import_warning(message)
        assert plain[0].isupper() or plain[0].isdigit(), plain
        assert plain.endswith("."), plain
        assert plain.lower().rstrip(".") != message.lower(), f"{kind!r} is shown as the importer's own wording"
        assert not any(word in plain for word in _JARGON), plain


@pytest.mark.parametrize("excerpts", [(), ("",), ("one box",), ("one box", ""), ("one box", "another box", "a third")])
def test_a_text_box_message_is_recognised_whatever_it_quotes(excerpts):
    messages = notes.messages_by_kind(
        {}, keeps_paragraph_formatting=True, text_box_excerpts=excerpts
    )
    if not excerpts:
        assert NoteKind.TEXT_BOX not in messages
        return
    (message,) = messages.values()
    assert import_warning_kind(message) is NoteKind.TEXT_BOX
    plain = explain_import_warning(message)
    assert plain.startswith(f"{len(excerpts)} text box")
    for excerpt in filter(None, excerpts):
        assert excerpt in plain, "every quoted excerpt reaches the panel"


def test_the_panel_has_no_rule_for_a_kind_the_importers_do_not_have():
    assert set(document_warnings.import_rule_kinds()) <= set(NoteKind)


# --- 3. the real importers ---------------------------------------------------------------------------------------

_W = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:v="urn:schemas-microsoft-com:vml" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
)


def _docx(body: str, parts: dict[str, str] | None = None) -> bytes:
    """A minimal .docx built in memory, with any extra parts (footnotes, comments) a test names.

    Mirrors the helper in tests/test_importers_docx.py -- no binary fixture is committed.
    """
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>',
        )
        zf.writestr(
            "word/document.xml",
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {_W}><w:body>{body}</w:body></w:document>',
        )
        for name, xml in (parts or {}).items():
            zf.writestr(name, f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>{xml}')
    return buf.getvalue()


_TEXT_BOX = (
    "<w:pict><v:shape><v:textbox><w:txbxContent>"
    "<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
    "</w:txbxContent></v:textbox></v:shape></w:pict>"
)

#: One Word file with a little of everything an importer has to leave out, and one Google
#: Docs export likewise. Between them they must make the importers report every NoteKind;
#: when a new kind is added, one of these needs the construct that triggers it.
_WORD_FILE_WITH_EVERY_LOSS = _docx(
    "<w:p><w:hyperlink><w:r><w:t>a link</w:t></w:r></w:hyperlink>"
    "<w:r><w:drawing/></w:r>"
    '<w:r><w:t>one</w:t><w:br/><w:t>two</w:t><w:footnoteReference w:id="2"/><w:sym w:font="Symbol" w:char="F0B7"/></w:r>'
    "<w:ins><w:r><w:t>added</w:t></w:r></w:ins>"
    f"<w:r>{_TEXT_BOX.format(text='words in a box')}</w:r></w:p>"
    "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
)

_GOOGLE_EXPORT_WITH_EVERY_LOSS = (
    '<style>.c1{font-family:"Courier New";font-size:16pt;color:#ff0000}</style>'
    '<p><a href="https://example.com/">a link</a><img src="a.png"/>one<br>two<span class="c1">styled</span>'
    '<sup><a href="#ftnt1" id="ftnt_ref1">[1]</a></sup></p><hr>'
    "<table><tr><td>cell</td></tr></table>"
    '<div><p><a href="#ftnt_ref1" id="ftnt1">[1]</a><span> the note</span></p></div>'
)


def _what_the_real_importers_say() -> list[str]:
    return [*parse_docx(_WORD_FILE_WITH_EVERY_LOSS)[1], *parse_gdocs_html(_GOOGLE_EXPORT_WITH_EVERY_LOSS)[1]]


def test_the_real_importers_report_every_kind_and_the_panel_words_each_message():
    messages = _what_the_real_importers_say()

    unrecognised = [m for m in messages if import_warning_kind(m) is None]
    assert not unrecognised, (
        f"an importer said {unrecognised} and the panel has no rule that recognises it. "
        f"Its pattern in {_RULES_FILE} is out of step with what {_WORDING_FILE} renders."
    )

    reported = {import_warning_kind(m) for m in messages}
    never = sorted(kind.value for kind in set(NoteKind) - reported)
    assert not never, (
        f"no input in this file makes an importer report {never}. Add the construct that triggers it to "
        "_WORD_FILE_WITH_EVERY_LOSS or _GOOGLE_EXPORT_WITH_EVERY_LOSS, so the kind is exercised end to end "
        "and not only through its wording."
    )


def test_a_word_file_imported_through_the_page_shows_a_line_for_each_kind_it_loses(qtbot, type_page_rig, tmp_path):
    """The whole way: a real .docx, the Import button, the panel the person reads."""
    path = tmp_path / "everything.docx"
    path.write_bytes(_WORD_FILE_WITH_EVERY_LOSS)
    page = type_page_rig().page

    page.import_bar.set_source(str(path))
    with qtbot.waitSignal(page.importFinished, timeout=10000) as imported:
        page.import_bar.import_button.click()
    assert imported.args[0] is True

    lines = page.warning_lines()
    for beginning in (
        "1 link will be typed as plain text",
        "1 image will be left out",
        "1 table will be typed without its rows and columns",
        "1 line break inside a paragraph will be typed as a new paragraph",
        "1 footnote, endnote or comment will be left out, both the marker and the text of the note",
        "1 symbol from a symbol font",
        '1 text box will be left out, with the text in it: "words in a box"',
        "This document has tracked changes.",
    ):
        assert any(line.startswith(beginning) for line in lines), (beginning, lines)
    assert not any("(dropped)" in line or "not reproduced" in line for line in lines), lines


def test_every_line_the_panel_shows_for_the_real_importers_is_a_sentence_about_the_document():
    for line in explain_warnings(_what_the_real_importers_say(), [], "Google Docs"):
        assert line[0].isupper() or line[0].isdigit(), line
        assert line.endswith("."), line
        assert not any(word in line for word in _JARGON), line


# --- each claim a sentence makes is true of the importer that produced it ------------------------------------------

_NOTE_TEXT = "Smith, A Study of Things, p. 14."

_NOTE_PARTS = {
    "footnote": (
        '<w:footnoteReference w:id="2"/>',
        "word/footnotes.xml",
        f'<w:footnotes {_W}><w:footnote w:id="2"><w:p><w:r><w:footnoteRef/></w:r>'
        f'<w:r><w:t xml:space="preserve"> {_NOTE_TEXT}</w:t></w:r></w:p></w:footnote></w:footnotes>',
    ),
    "endnote": (
        '<w:endnoteReference w:id="2"/>',
        "word/endnotes.xml",
        f'<w:endnotes {_W}><w:endnote w:id="2"><w:p><w:r><w:endnoteRef/></w:r>'
        f'<w:r><w:t xml:space="preserve"> {_NOTE_TEXT}</w:t></w:r></w:p></w:endnote></w:endnotes>',
    ),
    "comment": (
        '<w:commentReference w:id="2"/>',
        "word/comments.xml",
        f'<w:comments {_W}><w:comment w:id="2" w:author="A Reviewer"><w:p>'
        f"<w:r><w:t>{_NOTE_TEXT}</w:t></w:r></w:p></w:comment></w:comments>",
    ),
}


def _all_typed(document) -> str:
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


@pytest.mark.parametrize("kind", sorted(_NOTE_PARTS))
def test_a_word_notes_own_text_is_left_out_and_the_panel_says_so(kind):
    reference, part_name, part_xml = _NOTE_PARTS[kind]
    body = f"<w:p><w:r><w:t>The first claim</w:t></w:r><w:r>{reference}</w:r><w:r><w:t> rests on one source.</w:t></w:r></w:p>"
    document, warnings = parse_docx(_docx(body, {part_name: part_xml}))

    assert _all_typed(document) == "The first claim rests on one source."
    assert _NOTE_TEXT not in _all_typed(document), "the note's own text is not typed anywhere"

    (line,) = explain_warnings(warnings, [], "Google Docs")
    assert "marker" in line
    assert "the text of the note" in line, line
    assert "add it back by hand" in line


def test_a_google_notes_own_text_is_left_out_and_the_panel_says_so():
    html = (
        '<p>The first claim<sup><a href="#ftnt1" id="ftnt_ref1">[1]</a></sup> rests on one source.</p>'
        f'<div><p><a href="#ftnt_ref1" id="ftnt1">[1]</a><span> {_NOTE_TEXT}</span></p></div>'
    )
    document, warnings = parse_gdocs_html(html)

    assert _all_typed(document) == "The first claim rests on one source."
    assert _NOTE_TEXT not in _all_typed(document), "the note's own text is not typed anywhere"

    (line,) = explain_warnings(warnings, [], "Google Docs")
    assert "marker" in line
    assert "the text of the note" in line, line


def test_several_notes_are_worded_in_the_plural():
    reference, part_name, part_xml = _NOTE_PARTS["footnote"]
    body = f"<w:p><w:r>{reference}</w:r><w:r>{reference}</w:r><w:r>{reference}</w:r></w:p>"
    _document, warnings = parse_docx(_docx(body, {part_name: part_xml}))
    (line,) = explain_warnings(warnings, [], "Google Docs")
    assert line.startswith("3 footnotes, endnotes or comments will be left out")
    assert "the text of each note" in line
    assert "add them back by hand" in line


def test_a_word_table_keeps_each_cells_paragraphs_apart_and_their_formatting():
    body = (
        "<w:tbl><w:tr><w:tc>"
        '<w:p><w:pPr><w:pStyle w:val="Heading2"/><w:jc w:val="center"/></w:pPr><w:r><w:t>Cell heading</w:t></w:r></w:p>'
        "<w:p><w:r><w:t>second paragraph</w:t></w:r></w:p>"
        "</w:tc><w:tc/></w:tr></w:tbl>"
    )
    document, warnings = parse_docx(_docx(body))

    # Not "one paragraph per cell": two paragraphs in one cell stay two, and an empty cell is still a line.
    assert [p.text for p in document.paragraphs] == ["Cell heading", "second paragraph", ""]
    assert document.paragraphs[0].style is ParagraphStyle.HEADING_2
    assert document.paragraphs[0].alignment is Alignment.CENTER

    (line,) = explain_warnings(warnings, [], "Google Docs")
    assert line == (
        "1 table will be typed without its rows and columns: each cell becomes one or more paragraphs, row by row."
    )


def test_a_google_table_keeps_each_cells_paragraphs_apart_but_not_their_formatting():
    """The Google Docs reader types a cell's paragraphs apart but as ordinary text.

    If this ever starts failing because the reader keeps headings, lists and alignment
    inside a cell, switch gdocs_html to ``keeps_paragraph_formatting=True``: the panel then
    stops saying they are lost.
    """
    html = (
        "<table><tr><td><h2>Cell heading</h2>"
        '<p style="text-align:center">centered</p><ul><li>bullet</li></ul></td><td></td></tr></table>'
    )
    document, warnings = parse_gdocs_html(html)

    assert [p.text for p in document.paragraphs] == ["Cell heading", "centered", "bullet", ""]
    for paragraph in document.paragraphs:
        assert paragraph.style is ParagraphStyle.NORMAL
        assert paragraph.alignment is Alignment.LEFT
        assert paragraph.list_kind is None

    (line,) = explain_warnings(warnings, [], "Google Docs")
    assert line == (
        "1 table will be typed without its rows and columns: each cell becomes one or more paragraphs, "
        "row by row. Headings, lists and alignment inside a cell are not kept."
    )


def test_no_table_sentence_still_says_one_per_cell_or_plain_paragraphs():
    for importer, source in (
        (parse_docx, _docx("<w:tbl><w:tr><w:tc><w:p><w:r><w:t>a</w:t></w:r></w:p></w:tc></w:tr></w:tbl>")),
        (parse_gdocs_html, "<table><tr><td>a</td></tr></table>"),
    ):
        (line,) = explain_warnings(importer(source)[1], [], "Google Docs")
        assert "one per cell" not in line
        assert "plain paragraphs" not in line
        assert "one paragraph per cell" not in line


def test_a_text_box_is_left_out_with_its_words_and_the_panel_quotes_them():
    body = (
        "<w:p><w:r><w:t>body text</w:t></w:r></w:p>"
        f"<w:p><w:r>{_TEXT_BOX.format(text='Important sidebar note')}</w:r></w:p>"
    )
    document, warnings = parse_docx(_docx(body))

    assert "Important sidebar note" not in _all_typed(document), "a text box's words are not typed anywhere"

    (line,) = explain_warnings(warnings, [], "Google Docs")
    assert line == (
        '1 text box will be left out, with the text in it: "Important sidebar note". '
        "If you need that text, add it by hand."
    )


def test_a_symbol_is_left_out_and_the_panel_says_what_kind_of_symbol():
    body = '<w:p><w:r><w:t>tick </w:t><w:sym w:font="Wingdings" w:char="F0FC"/><w:t>done</w:t></w:r></w:p>'
    document, warnings = parse_docx(_docx(body))

    assert _all_typed(document) == "tick done", "the symbol is left out, with nothing typed in its place"

    (line,) = explain_warnings(warnings, [], "Google Docs")
    assert line == "1 symbol from a symbol font, such as Wingdings, will be left out, because it can't be typed."


def test_tracked_changes_are_typed_as_the_final_version_and_the_panel_says_a_choice_was_made():
    body = (
        "<w:p><w:r><w:t>keep </w:t></w:r>"
        "<w:ins><w:r><w:t>the added words</w:t></w:r></w:ins>"
        "<w:del><w:r><w:delText> and the deleted ones</w:delText></w:r></w:del></w:p>"
    )
    document, warnings = parse_docx(_docx(body))

    assert _all_typed(document) == "keep the added words", "insertions are kept and deletions left out"

    (line,) = explain_warnings(warnings, [], "Google Docs")
    assert line == (
        "This document has tracked changes. It will be typed as the final version: added text is kept "
        "and deleted text is left out. To type a different version, accept or reject the changes in Word first."
    )

