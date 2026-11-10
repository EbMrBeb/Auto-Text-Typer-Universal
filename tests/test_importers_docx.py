"""Tests for auto_essay_typer.importers.docx.

Every fixture is a minimal .docx built in-memory with zipfile -- no binary
files are committed to the repo.
"""

import zipfile
from io import BytesIO

import pytest

from auto_essay_typer.document import (
    Alignment,
    Document,
    ListKind,
    Paragraph,
    ParagraphStyle,
    Run,
    TextStyle,
)
from auto_essay_typer.importers.docx import parse_docx

#: Every namespace a fixture body below needs declared on the root element:
#: WordprocessingML itself, plus the three Word uses for shapes (``v`` for the
#: old VML picture syntax, ``wps`` for the modern one, ``mc`` for the wrapper
#: holding both).
_W = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:v="urn:schemas-microsoft-com:vml" '
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
)


def _build_docx(
    body_xml: str,
    styles_xml: str | None = None,
    numbering_xml: str | None = None,
    parts: dict[str, str] | None = None,
) -> bytes:
    """``parts`` are further files in the package (``word/footnotes.xml``...) by name."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
            'package/2006/content-types"></Types>',
        )
        zf.writestr(
            "word/document.xml",
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f"<w:document {_W}><w:body>{body_xml}</w:body></w:document>",
        )
        if styles_xml is not None:
            zf.writestr(
                "word/styles.xml",
                f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f"<w:styles {_W}>{styles_xml}</w:styles>",
            )
        if numbering_xml is not None:
            zf.writestr(
                "word/numbering.xml",
                f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f"<w:numbering {_W}>{numbering_xml}</w:numbering>",
            )
        for name, xml in (parts or {}).items():
            zf.writestr(name, f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>{xml}')
    return buf.getvalue()


# --- plain text / basic structure --------------------------------------------


def test_plain_paragraph_text():
    body = "<w:p><w:r><w:t>Hello world</w:t></w:r></w:p>"
    doc, warnings = parse_docx(_build_docx(body))
    assert doc == Document((Paragraph(runs=(Run("Hello world"),)),))
    assert warnings == ()


def test_multiple_paragraphs_in_order():
    body = "<w:p><w:r><w:t>one</w:t></w:r></w:p><w:p><w:r><w:t>two</w:t></w:r></w:p>"
    doc, _ = parse_docx(_build_docx(body))
    assert [p.text for p in doc.paragraphs] == ["one", "two"]


def test_empty_paragraph_has_no_runs():
    body = "<w:p/>"
    doc, _ = parse_docx(_build_docx(body))
    assert doc.paragraphs == (Paragraph(),)


def test_xml_space_preserve_keeps_spaces():
    body = '<w:p><w:r><w:t xml:space="preserve">  leading and trailing  </w:t></w:r></w:p>'
    doc, _ = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].text == "  leading and trailing  "


# --- run formatting: direct w:rPr only ----------------------------------------


def test_bold_italic_underline_strikethrough_all_on():
    body = (
        "<w:p><w:r><w:rPr><w:b/><w:i/><w:u w:val=\"single\"/><w:strike/></w:rPr>"
        "<w:t>styled</w:t></w:r></w:p>"
    )
    doc, _ = parse_docx(_build_docx(body))
    (run,) = doc.paragraphs[0].runs
    assert run.text == "styled"
    assert run.styles == frozenset(
        {TextStyle.BOLD, TextStyle.ITALIC, TextStyle.UNDERLINE, TextStyle.STRIKETHROUGH}
    )


def test_bold_with_no_val_attribute_means_on():
    body = "<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>x</w:t></w:r></w:p>"
    doc, _ = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].runs[0].styles == frozenset({TextStyle.BOLD})


@pytest.mark.parametrize("off_val", ["0", "false", "off"])
def test_bold_italic_strike_explicit_off_values(off_val):
    body = (
        f'<w:p><w:r><w:rPr><w:b w:val="{off_val}"/><w:i w:val="{off_val}"/>'
        f'<w:strike w:val="{off_val}"/></w:rPr><w:t>plain</w:t></w:r></w:p>'
    )
    doc, _ = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].runs[0].styles == frozenset()


def test_underline_val_none_is_off():
    body = '<w:p><w:r><w:rPr><w:u w:val="none"/></w:rPr><w:t>x</w:t></w:r></w:p>'
    doc, _ = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].runs[0].styles == frozenset()


def test_underline_any_other_val_is_on():
    body = '<w:p><w:r><w:rPr><w:u w:val="double"/></w:rPr><w:t>x</w:t></w:r></w:p>'
    doc, _ = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].runs[0].styles == frozenset({TextStyle.UNDERLINE})


def test_unformatted_run_has_no_styles():
    body = "<w:p><w:r><w:t>plain</w:t></w:r></w:p>"
    doc, _ = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].runs[0].styles == frozenset()


def test_adjacent_runs_with_same_styles_merge():
    body = (
        "<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>ab</w:t></w:r>"
        "<w:r><w:rPr><w:b/></w:rPr><w:t>cd</w:t></w:r></w:p>"
    )
    doc, _ = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].runs == (Run("abcd", frozenset({TextStyle.BOLD})),)


# --- paragraph style resolution ------------------------------------------------


def test_paragraph_style_resolved_via_styles_xml_name():
    styles = (
        '<w:style w:type="paragraph" w:styleId="MyHeading1"><w:name w:val="heading 1"/></w:style>'
        '<w:style w:type="paragraph" w:styleId="MyTitle"><w:name w:val="Title"/></w:style>'
        '<w:style w:type="paragraph" w:styleId="MySubtitle"><w:name w:val="Subtitle"/></w:style>'
    )
    body = (
        '<w:p><w:pPr><w:pStyle w:val="MyHeading1"/></w:pPr><w:r><w:t>H1</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:pStyle w:val="MyTitle"/></w:pPr><w:r><w:t>T</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:pStyle w:val="MySubtitle"/></w:pPr><w:r><w:t>S</w:t></w:r></w:p>'
    )
    doc, _ = parse_docx(_build_docx(body, styles_xml=styles))
    assert [p.style for p in doc.paragraphs] == [
        ParagraphStyle.HEADING_1,
        ParagraphStyle.TITLE,
        ParagraphStyle.SUBTITLE,
    ]


@pytest.mark.parametrize(
    "style_id,expected",
    [
        ("Heading1", ParagraphStyle.HEADING_1),
        ("Heading2", ParagraphStyle.HEADING_2),
        ("Heading6", ParagraphStyle.HEADING_6),
        ("Title", ParagraphStyle.TITLE),
        ("Subtitle", ParagraphStyle.SUBTITLE),
    ],
)
def test_paragraph_style_falls_back_to_raw_style_id_with_no_styles_xml(style_id, expected):
    body = f'<w:p><w:pPr><w:pStyle w:val="{style_id}"/></w:pPr><w:r><w:t>x</w:t></w:r></w:p>'
    doc, _ = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].style == expected


def test_unknown_style_id_is_normal():
    body = '<w:p><w:pPr><w:pStyle w:val="SomeCustomStyle"/></w:pPr><w:r><w:t>x</w:t></w:r></w:p>'
    doc, _ = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].style == ParagraphStyle.NORMAL


def test_no_pstyle_is_normal():
    body = "<w:p><w:r><w:t>x</w:t></w:r></w:p>"
    doc, _ = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].style == ParagraphStyle.NORMAL


# --- alignment ------------------------------------------------------------------


@pytest.mark.parametrize(
    "jc,expected",
    [
        ("left", Alignment.LEFT),
        ("start", Alignment.LEFT),
        ("center", Alignment.CENTER),
        ("right", Alignment.RIGHT),
        ("end", Alignment.RIGHT),
        ("both", Alignment.JUSTIFIED),
        ("distribute", Alignment.JUSTIFIED),
    ],
)
def test_alignment_from_jc(jc, expected):
    body = f'<w:p><w:pPr><w:jc w:val="{jc}"/></w:pPr><w:r><w:t>x</w:t></w:r></w:p>'
    doc, _ = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].alignment == expected


def test_default_alignment_is_left():
    body = "<w:p><w:r><w:t>x</w:t></w:r></w:p>"
    doc, _ = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].alignment == Alignment.LEFT


# --- lists ------------------------------------------------------------------------

_BULLET_NUMBERING = (
    '<w:abstractNum w:abstractNumId="0">'
    '<w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/></w:lvl>'
    '<w:lvl w:ilvl="1"><w:numFmt w:val="bullet"/></w:lvl>'
    "</w:abstractNum>"
    '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
)

_DECIMAL_NUMBERING = (
    '<w:abstractNum w:abstractNumId="5">'
    '<w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/></w:lvl>'
    '<w:lvl w:ilvl="1"><w:numFmt w:val="decimal"/></w:lvl>'
    "</w:abstractNum>"
    '<w:num w:numId="9"><w:abstractNumId w:val="5"/></w:num>'
)


def test_bullet_list_at_level_zero():
    body = (
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>'
        "<w:r><w:t>item</w:t></w:r></w:p>"
    )
    doc, _ = parse_docx(_build_docx(body, numbering_xml=_BULLET_NUMBERING))
    p = doc.paragraphs[0]
    assert p.list_kind == ListKind.BULLET
    assert p.list_level == 0


def test_numbered_list_at_nested_level():
    body = (
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="9"/></w:numPr></w:pPr>'
        "<w:r><w:t>nested</w:t></w:r></w:p>"
    )
    doc, _ = parse_docx(_build_docx(body, numbering_xml=_DECIMAL_NUMBERING))
    p = doc.paragraphs[0]
    assert p.list_kind == ListKind.NUMBERED
    assert p.list_level == 1


def test_paragraph_with_no_numpr_is_not_a_list_item():
    body = "<w:p><w:r><w:t>x</w:t></w:r></w:p>"
    doc, _ = parse_docx(_build_docx(body, numbering_xml=_BULLET_NUMBERING))
    assert doc.paragraphs[0].list_kind is None
    assert doc.paragraphs[0].list_level == 0


# --- tab / br -----------------------------------------------------------------


def test_tab_becomes_tab_character():
    body = "<w:p><w:r><w:t>a</w:t></w:r><w:r><w:tab/></w:r><w:r><w:t>b</w:t></w:r></w:p>"
    doc, _ = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].text == "a\tb"


def test_br_splits_paragraph_and_warns():
    body = (
        "<w:p><w:r><w:t>line one</w:t></w:r><w:r><w:br/></w:r>"
        "<w:r><w:t>line two</w:t></w:r></w:p>"
    )
    doc, warnings = parse_docx(_build_docx(body))
    assert [p.text for p in doc.paragraphs] == ["line one", "line two"]
    assert any("soft line break" in w for w in warnings)


def test_multiple_br_warnings_are_aggregated_with_a_count():
    body = (
        "<w:p><w:r><w:t>a</w:t></w:r><w:r><w:br/></w:r><w:r><w:t>b</w:t></w:r>"
        "<w:r><w:br/></w:r><w:r><w:t>c</w:t></w:r></w:p>"
    )
    doc, warnings = parse_docx(_build_docx(body))
    assert [p.text for p in doc.paragraphs] == ["a", "b", "c"]
    break_warnings = [w for w in warnings if "soft line break" in w]
    assert len(break_warnings) == 1
    assert "2" in break_warnings[0]


def test_br_inside_a_single_run_splits_the_paragraph_too():
    # Word is free to put the break and the text either side of it in one run.
    # The words on either side are unrelated, so running them together ("end
    # of linenext line") invents a word that is in no document -- and the
    # report would still claim the break had been typed as a paragraph break.
    body = "<w:p><w:r><w:t>end of line</w:t><w:br/><w:t>next line</w:t></w:r></w:p>"
    doc, warnings = parse_docx(_build_docx(body))
    assert [p.text for p in doc.paragraphs] == ["end of line", "next line"]
    assert any("soft line break" in w for w in warnings)


def test_cr_splits_the_paragraph_like_a_br_does():
    body = "<w:p><w:r><w:t>one</w:t><w:cr/><w:t>two</w:t></w:r></w:p>"
    doc, warnings = parse_docx(_build_docx(body))
    assert [p.text for p in doc.paragraphs] == ["one", "two"]
    assert any("soft line break" in w for w in warnings)


def test_two_breaks_in_a_row_leave_an_empty_paragraph_between_them():
    body = "<w:p><w:r><w:t>a</w:t><w:br/><w:br/><w:t>b</w:t></w:r></w:p>"
    doc, _ = parse_docx(_build_docx(body))
    assert [p.text for p in doc.paragraphs] == ["a", "", "b"]


def test_break_keeps_the_formatting_of_the_run_it_split():
    body = (
        '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>first</w:t><w:br/><w:t>second</w:t></w:r></w:p>'
    )
    doc, _ = parse_docx(_build_docx(body))
    assert [p.runs for p in doc.paragraphs] == [
        (Run("first", frozenset({TextStyle.BOLD})),),
        (Run("second", frozenset({TextStyle.BOLD})),),
    ]


def test_position_tab_becomes_a_tab_character():
    body = (
        '<w:p><w:r><w:t>a</w:t>'
        '<w:ptab w:alignment="left" w:relativeTo="margin" w:leader="none"/>'
        "<w:t>b</w:t></w:r></w:p>"
    )
    doc, _ = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].text == "a\tb"


def test_non_breaking_hyphen_is_kept_as_a_hyphen():
    body = "<w:p><w:r><w:t>co</w:t><w:noBreakHyphen/><w:t>operate</w:t></w:r></w:p>"
    doc, _ = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].text == "co\u2011operate"


def test_soft_hyphen_is_kept_as_a_soft_hyphen():
    body = "<w:p><w:r><w:t>co</w:t><w:softHyphen/><w:t>operate</w:t></w:r></w:p>"
    doc, _ = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].text == "co\u00adoperate"


# --- tracked changes: typed as Word's final view --------------------------------


def test_tracked_insertion_text_is_kept():
    body = (
        "<w:p><w:r><w:t>keep </w:t></w:r>"
        "<w:ins><w:r><w:t>the inserted words</w:t></w:r></w:ins>"
        "<w:r><w:t> too</w:t></w:r></w:p>"
    )
    doc, _ = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].text == "keep the inserted words too"


def test_tracked_insertion_keeps_its_own_formatting():
    body = (
        "<w:p><w:ins><w:r><w:rPr><w:i/></w:rPr><w:t>added</w:t></w:r></w:ins></w:p>"
    )
    doc, _ = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].runs == (Run("added", frozenset({TextStyle.ITALIC})),)


def test_tracked_deletion_text_is_left_out():
    body = (
        "<w:p><w:r><w:t>keep </w:t></w:r>"
        "<w:del><w:r><w:delText>struck out</w:delText></w:r></w:del>"
        "<w:r><w:t>this</w:t></w:r></w:p>"
    )
    doc, _ = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].text == "keep this"


def test_deleted_text_with_no_del_wrapper_is_still_left_out():
    body = (
        "<w:p><w:r><w:delText>struck out</w:delText></w:r>"
        "<w:r><w:t>kept</w:t></w:r></w:p>"
    )
    doc, warnings = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].text == "kept"
    assert any("tracked changes" in w for w in warnings)


def test_an_insertion_that_was_then_deleted_is_left_out():
    body = (
        "<w:p><w:ins><w:del><w:r><w:delText>gone</w:delText></w:r></w:del></w:ins>"
        "<w:r><w:t>kept</w:t></w:r></w:p>"
    )
    doc, _ = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].text == "kept"


def test_a_moved_passage_is_typed_only_where_it_moved_to():
    body = (
        "<w:p><w:moveFrom><w:r><w:delText>moved away</w:delText></w:r></w:moveFrom>"
        "<w:r><w:t>middle </w:t></w:r>"
        "<w:moveTo><w:r><w:t>moved here</w:t></w:r></w:moveTo></w:p>"
    )
    doc, _ = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].text == "middle moved here"


def test_an_insertion_inside_a_hyperlink_is_kept():
    body = (
        "<w:p><w:hyperlink><w:ins><w:r><w:t>linked insertion</w:t></w:r></w:ins>"
        "</w:hyperlink></w:p>"
    )
    doc, _ = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].text == "linked insertion"


@pytest.mark.parametrize(
    "body",
    [
        "<w:p><w:ins><w:r><w:t>added</w:t></w:r></w:ins></w:p>",
        "<w:p><w:del><w:r><w:delText>removed</w:delText></w:r></w:del></w:p>",
        '<w:p><w:pPr><w:rPr><w:ins w:id="1"/></w:rPr></w:pPr><w:r><w:t>split here</w:t></w:r></w:p>',
    ],
)
def test_tracked_changes_are_reported_as_flattened_to_the_final_version(body):
    _doc, warnings = parse_docx(_build_docx(body))
    tracked = [w for w in warnings if "tracked changes" in w]
    assert len(tracked) == 1
    assert "final version" in tracked[0]


def test_a_document_with_no_revisions_says_nothing_about_tracked_changes():
    body = "<w:p><w:r><w:t>never edited</w:t></w:r></w:p>"
    _doc, warnings = parse_docx(_build_docx(body))
    assert not any("tracked changes" in w for w in warnings)


# --- content controls -----------------------------------------------------------


def test_content_control_run_text_is_kept():
    body = (
        "<w:p><w:r><w:t>before </w:t></w:r>"
        "<w:sdt><w:sdtPr/><w:sdtContent><w:r><w:t>inside the control</w:t></w:r>"
        "</w:sdtContent></w:sdt>"
        "<w:r><w:t> after</w:t></w:r></w:p>"
    )
    doc, warnings = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].text == "before inside the control after"
    assert warnings == ()


def test_block_level_content_control_paragraphs_are_kept():
    body = (
        "<w:sdt><w:sdtPr/><w:sdtContent>"
        "<w:p><w:r><w:t>first in the control</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>second in the control</w:t></w:r></w:p>"
        "</w:sdtContent></w:sdt>"
    )
    doc, _ = parse_docx(_build_docx(body))
    assert [p.text for p in doc.paragraphs] == [
        "first in the control",
        "second in the control",
    ]


def test_content_control_keeps_the_paragraph_style_inside_it():
    body = (
        "<w:sdt><w:sdtContent>"
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Controlled</w:t></w:r></w:p>'
        "</w:sdtContent></w:sdt>"
    )
    doc, _ = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].style == ParagraphStyle.HEADING_1


def test_content_control_inside_a_table_cell_is_kept():
    body = (
        "<w:tbl><w:tr><w:tc>"
        "<w:sdt><w:sdtContent><w:p><w:r><w:t>controlled cell</w:t></w:r></w:p>"
        "</w:sdtContent></w:sdt>"
        "</w:tc></w:tr></w:tbl>"
    )
    doc, _ = parse_docx(_build_docx(body))
    assert [p.text for p in doc.paragraphs] == ["controlled cell"]


def test_a_content_control_holding_a_whole_table_is_kept():
    body = (
        "<w:sdt><w:sdtContent>"
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>controlled table</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
        "</w:sdtContent></w:sdt>"
    )
    doc, _ = parse_docx(_build_docx(body))
    assert [p.text for p in doc.paragraphs] == ["controlled table"]


# --- text boxes: reported, because typing cannot put them where they belong ------

_TEXT_BOX_PICT = (
    "<w:pict><v:shape><v:textbox><w:txbxContent>"
    "<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
    "</w:txbxContent></v:textbox></v:shape></w:pict>"
)


def test_text_box_content_is_reported_with_enough_of_its_text_to_recognize():
    body = (
        "<w:p><w:r><w:t>body text</w:t></w:r></w:p>"
        f"<w:p><w:r>{_TEXT_BOX_PICT.format(text='Important sidebar note')}</w:r></w:p>"
    )
    doc, warnings = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].text == "body text"
    box_warnings = [w for w in warnings if "text box" in w]
    assert len(box_warnings) == 1
    assert "will not be reproduced" in box_warnings[0]
    assert "Important sidebar note" in box_warnings[0]


def test_a_text_box_is_not_reported_as_an_image():
    body = f"<w:p><w:r>{_TEXT_BOX_PICT.format(text='sidebar')}</w:r></w:p>"
    _doc, warnings = parse_docx(_build_docx(body))
    assert not any("image" in w for w in warnings)


def test_every_paragraph_of_a_text_box_reaches_the_excerpt():
    body = (
        "<w:p><w:r><w:pict><v:shape><v:textbox><w:txbxContent>"
        "<w:p><w:r><w:t>first line</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>second line</w:t></w:r></w:p>"
        "</w:txbxContent></v:textbox></v:shape></w:pict></w:r></w:p>"
    )
    _doc, warnings = parse_docx(_build_docx(body))
    (box_warning,) = [w for w in warnings if "text box" in w]
    assert "first line second line" in box_warning


def test_a_long_text_box_excerpt_is_shortened():
    long_text = "word " * 40
    body = f"<w:p><w:r>{_TEXT_BOX_PICT.format(text=long_text.strip())}</w:r></w:p>"
    _doc, warnings = parse_docx(_build_docx(body))
    (box_warning,) = [w for w in warnings if "text box" in w]
    assert len(box_warning) < 150
    assert box_warning.endswith('…"')


def test_two_text_boxes_are_both_named():
    body = (
        f"<w:p><w:r>{_TEXT_BOX_PICT.format(text='first box')}</w:r>"
        f"<w:r>{_TEXT_BOX_PICT.format(text='second box')}</w:r></w:p>"
    )
    _doc, warnings = parse_docx(_build_docx(body))
    (box_warning,) = [w for w in warnings if "text box" in w]
    assert "2 text boxes" in box_warning
    assert "first box" in box_warning
    assert "second box" in box_warning


def test_a_modern_shape_text_box_is_reported():
    body = (
        "<w:p><w:r><w:drawing><wps:txbx><w:txbxContent>"
        "<w:p><w:r><w:t>shape text</w:t></w:r></w:p>"
        "</w:txbxContent></wps:txbx></w:drawing></w:r></w:p>"
    )
    _doc, warnings = parse_docx(_build_docx(body))
    assert any("shape text" in w for w in warnings)


def test_a_text_box_offered_in_two_formats_is_reported_once():
    # Word writes the same shape twice -- a modern description and a fallback
    # for older versions -- so reading both would report one text box as two.
    body = (
        "<w:p><w:r><mc:AlternateContent>"
        "<mc:Choice><w:drawing><wps:txbx><w:txbxContent>"
        "<w:p><w:r><w:t>only once</w:t></w:r></w:p>"
        "</w:txbxContent></wps:txbx></w:drawing></mc:Choice>"
        f"<mc:Fallback>{_TEXT_BOX_PICT.format(text='only once')}</mc:Fallback>"
        "</mc:AlternateContent></w:r></w:p>"
    )
    _doc, warnings = parse_docx(_build_docx(body))
    (box_warning,) = [w for w in warnings if "text box" in w]
    assert "1 text box" in box_warning


def test_a_shape_with_no_text_in_it_is_still_just_an_image():
    body = "<w:p><w:r><w:drawing><wps:txbx/></w:drawing></w:r></w:p>"
    _doc, warnings = parse_docx(_build_docx(body))
    assert any("image" in w for w in warnings)
    assert not any("text box" in w for w in warnings)


# --- other run content that is reachable but easy to skip -----------------------


def test_smart_tag_run_text_is_kept():
    body = (
        "<w:p><w:r><w:t>I live in </w:t></w:r>"
        "<w:smartTag><w:r><w:t>Paris</w:t></w:r></w:smartTag>"
        "<w:r><w:t> now.</w:t></w:r></w:p>"
    )
    doc, _ = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].text == "I live in Paris now."


def test_simple_field_result_text_is_kept():
    body = (
        "<w:p><w:r><w:t>Page </w:t></w:r>"
        '<w:fldSimple w:instr=" PAGE "><w:r><w:t>7</w:t></w:r></w:fldSimple></w:p>'
    )
    doc, _ = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].text == "Page 7"


def test_a_field_instruction_is_not_typed_but_its_result_is():
    body = (
        '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        "<w:r><w:instrText> PAGE </w:instrText></w:r>"
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        "<w:r><w:t>7</w:t></w:r>"
        '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
    )
    doc, _ = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].text == "7"


@pytest.mark.parametrize(
    "reference",
    ['<w:footnoteReference w:id="2"/>', '<w:endnoteReference w:id="2"/>',
     '<w:commentReference w:id="2"/>'],
)
def test_a_note_reference_is_reported_rather_than_dropped_in_silence(reference):
    body = (
        "<w:p><w:r><w:t>a claim</w:t></w:r>"
        f"<w:r>{reference}</w:r>"
        "<w:r><w:t> continues</w:t></w:r></w:p>"
    )
    doc, warnings = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].text == "a claim continues"
    assert any("footnote" in w for w in warnings)


@pytest.mark.parametrize(
    ("reference", "part_name", "part_xml"),
    [
        (
            '<w:footnoteReference w:id="2"/>',
            "word/footnotes.xml",
            f'<w:footnotes {_W}><w:footnote w:id="2"><w:p><w:r><w:t>Smith, p. 14.</w:t></w:r></w:p>'
            "</w:footnote></w:footnotes>",
        ),
        (
            '<w:endnoteReference w:id="2"/>',
            "word/endnotes.xml",
            f'<w:endnotes {_W}><w:endnote w:id="2"><w:p><w:r><w:t>Smith, p. 14.</w:t></w:r></w:p>'
            "</w:endnote></w:endnotes>",
        ),
        (
            '<w:commentReference w:id="2"/>',
            "word/comments.xml",
            f'<w:comments {_W}><w:comment w:id="2"><w:p><w:r><w:t>Smith, p. 14.</w:t></w:r></w:p>'
            "</w:comment></w:comments>",
        ),
    ],
    ids=["footnote", "endnote", "comment"],
)
def test_a_notes_own_text_is_dropped_with_its_marker_and_the_message_says_so(reference, part_name, part_xml):
    # The note lives in a part of the package this reader never opens, so its words are
    # not typed anywhere -- not only the marker. The report has to say both.
    body = f"<w:p><w:r><w:t>a claim</w:t></w:r><w:r>{reference}</w:r></w:p>"
    doc, warnings = parse_docx(_build_docx(body, parts={part_name: part_xml}))
    assert "Smith" not in doc.plain_text()
    (note_warning,) = [w for w in warnings if "footnote" in w]
    assert "along with the text of the note" in note_warning


def test_a_symbol_is_reported_rather_than_dropped_in_silence():
    body = '<w:p><w:r><w:t>bullet </w:t><w:sym w:font="Symbol" w:char="F0B7"/></w:r></w:p>'
    doc, warnings = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].text == "bullet "
    assert any("symbol" in w for w in warnings)


def test_a_bookmark_around_text_does_not_disturb_it():
    body = (
        '<w:p><w:bookmarkStart w:id="0" w:name="here"/>'
        "<w:r><w:t>bookmarked</w:t></w:r>"
        '<w:bookmarkEnd w:id="0"/></w:p>'
    )
    doc, warnings = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].text == "bookmarked"
    assert warnings == ()


# --- hyperlinks -----------------------------------------------------------------


def test_hyperlink_run_text_is_kept_with_a_links_warning():
    body = (
        "<w:p><w:r><w:t>see </w:t></w:r>"
        "<w:hyperlink><w:r><w:t>this link</w:t></w:r></w:hyperlink></w:p>"
    )
    doc, warnings = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].text == "see this link"
    assert any("link" in w for w in warnings)


def test_hyperlink_run_keeps_its_own_formatting():
    body = (
        "<w:p><w:hyperlink><w:r><w:rPr><w:u w:val=\"single\"/></w:rPr>"
        "<w:t>link</w:t></w:r></w:hyperlink></w:p>"
    )
    doc, _ = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].runs[0].styles == frozenset({TextStyle.UNDERLINE})


# --- tables -----------------------------------------------------------------------


def test_table_flattened_with_its_cells_in_reading_order():
    body = (
        "<w:tbl>"
        "<w:tr><w:tc><w:p><w:r><w:t>A1</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>B1</w:t></w:r></w:p></w:tc></w:tr>"
        "<w:tr><w:tc><w:p><w:r><w:t>A2</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>B2</w:t></w:r></w:p></w:tc></w:tr>"
        "</w:tbl>"
    )
    doc, warnings = parse_docx(_build_docx(body))
    assert [p.text for p in doc.paragraphs] == ["A1", "B1", "A2", "B2"]
    (table_warning,) = [w for w in warnings if "table" in w]
    # A cell is one or more paragraphs. "One paragraph per cell" stopped being true when a
    # cell's own paragraphs and line breaks were kept apart (tests below).
    assert "one or more paragraphs" in table_warning
    assert "one paragraph per cell" not in table_warning


def test_table_between_paragraphs_preserves_reading_order():
    body = (
        "<w:p><w:r><w:t>before</w:t></w:r></w:p>"
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
        "<w:p><w:r><w:t>after</w:t></w:r></w:p>"
    )
    doc, _ = parse_docx(_build_docx(body))
    assert [p.text for p in doc.paragraphs] == ["before", "cell", "after"]


def test_two_paragraphs_in_one_cell_stay_two_paragraphs():
    # Running them together would fuse the end of one sentence onto the start
    # of the next ("first para" + "second para" -> "first parasecond para").
    body = (
        "<w:tbl><w:tr><w:tc>"
        "<w:p><w:r><w:t>first para</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>second para</w:t></w:r></w:p>"
        "</w:tc></w:tr></w:tbl>"
    )
    doc, _ = parse_docx(_build_docx(body))
    assert [p.text for p in doc.paragraphs] == ["first para", "second para"]


def test_soft_break_inside_a_cell_splits_the_cell_text():
    body = (
        "<w:tbl><w:tr><w:tc><w:p>"
        "<w:r><w:t>cell line one</w:t></w:r><w:r><w:br/></w:r>"
        "<w:r><w:t>cell line two</w:t></w:r>"
        "</w:p></w:tc></w:tr></w:tbl>"
    )
    doc, warnings = parse_docx(_build_docx(body))
    assert [p.text for p in doc.paragraphs] == ["cell line one", "cell line two"]
    assert any("soft line break" in w for w in warnings)


def test_a_nested_table_inside_a_cell_keeps_its_text():
    body = (
        "<w:tbl><w:tr><w:tc>"
        "<w:p><w:r><w:t>outer cell</w:t></w:r></w:p>"
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>inner cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
        "</w:tc></w:tr></w:tbl>"
    )
    doc, warnings = parse_docx(_build_docx(body))
    assert [p.text for p in doc.paragraphs] == ["outer cell", "inner cell"]
    table_warnings = [w for w in warnings if "table" in w]
    assert len(table_warnings) == 1
    assert "2" in table_warnings[0]


def test_an_empty_cell_still_produces_a_paragraph():
    body = (
        "<w:tbl><w:tr>"
        "<w:tc><w:p><w:r><w:t>filled</w:t></w:r></w:p></w:tc>"
        "<w:tc/>"
        "</w:tr></w:tbl>"
    )
    doc, _ = parse_docx(_build_docx(body))
    assert [p.text for p in doc.paragraphs] == ["filled", ""]


def test_a_cell_keeps_the_paragraph_style_and_alignment_inside_it():
    body = (
        "<w:tbl><w:tr><w:tc>"
        '<w:p><w:pPr><w:pStyle w:val="Heading2"/><w:jc w:val="center"/></w:pPr>'
        "<w:r><w:t>Cell heading</w:t></w:r></w:p>"
        "</w:tc></w:tr></w:tbl>"
    )
    doc, warnings = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].style == ParagraphStyle.HEADING_2
    assert doc.paragraphs[0].alignment == Alignment.CENTER
    # So the message does not say the formatting is lost (this reader sets
    # ``keeps_paragraph_formatting=True``); the Google Docs reader's does.
    (table_warning,) = [w for w in warnings if "table" in w]
    assert "losing" not in table_warning


def test_a_cell_whose_rows_sit_in_a_content_control_is_kept():
    body = (
        "<w:tbl><w:sdt><w:sdtContent>"
        "<w:tr><w:tc><w:p><w:r><w:t>controlled row</w:t></w:r></w:p></w:tc></w:tr>"
        "</w:sdtContent></w:sdt></w:tbl>"
    )
    doc, _ = parse_docx(_build_docx(body))
    assert [p.text for p in doc.paragraphs] == ["controlled row"]


# --- the whole contract: nothing is lost in silence -------------------------------


def test_every_piece_of_source_text_is_either_typed_or_named_in_the_report():
    """One document holding every construct that used to lose text quietly.

    The contract this parser owes the user is that each piece of the source
    is either reproduced in the Document or named in the report -- never
    dropped with "no problems found" on the screen.
    """
    body = (
        "<w:p><w:r><w:t>plain body text</w:t></w:r></w:p>"
        "<w:p><w:ins><w:r><w:t>an accepted insertion</w:t></w:r></w:ins></w:p>"
        "<w:p><w:del><w:r><w:delText>a rejected deletion</w:delText></w:r></w:del></w:p>"
        "<w:sdt><w:sdtContent><w:p><w:r><w:t>text in a content control</w:t></w:r></w:p>"
        "</w:sdtContent></w:sdt>"
        "<w:p><w:smartTag><w:r><w:t>text in a smart tag</w:t></w:r></w:smartTag></w:p>"
        '<w:p><w:fldSimple w:instr=" PAGE "><w:r><w:t>text in a field</w:t></w:r></w:fldSimple></w:p>'
        f"<w:p><w:r>{_TEXT_BOX_PICT.format(text='text in a text box')}</w:r></w:p>"
        "<w:tbl><w:tr><w:tc>"
        "<w:p><w:r><w:t>cell paragraph one</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>cell paragraph two</w:t></w:r></w:p>"
        "</w:tc></w:tr></w:tbl>"
    )
    doc, warnings = parse_docx(_build_docx(body))
    typed = doc.plain_text()
    reported = " ".join(warnings)

    for phrase in (
        "plain body text",
        "an accepted insertion",
        "text in a content control",
        "text in a smart tag",
        "text in a field",
        "cell paragraph one",
        "cell paragraph two",
    ):
        assert phrase in typed, phrase

    # The two that typing genuinely cannot place are named instead.
    assert "a rejected deletion" not in typed
    assert "tracked changes" in reported
    assert "text in a text box" not in typed
    assert "text in a text box" in reported

    # And nothing was fused onto its neighbour.
    assert "cell paragraph onecell paragraph two" not in typed


# --- drawings / images ------------------------------------------------------------


def test_drawing_is_skipped_with_an_image_warning():
    body = "<w:p><w:r><w:drawing/></w:r><w:r><w:t>caption</w:t></w:r></w:p>"
    doc, warnings = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].text == "caption"
    assert any("image" in w for w in warnings)


def test_pict_is_skipped_with_an_image_warning():
    body = "<w:p><w:r><w:pict/></w:r></w:p>"
    doc, warnings = parse_docx(_build_docx(body))
    assert doc.paragraphs[0].text == ""
    assert any("image" in w for w in warnings)


# --- malformed input --------------------------------------------------------------


def test_not_a_zip_raises_value_error():
    with pytest.raises(ValueError):
        parse_docx(b"this is not a zip file at all")


def test_missing_document_xml_raises_value_error():
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
    with pytest.raises(ValueError):
        parse_docx(buf.getvalue())


def test_doctype_declaration_is_rejected():
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><!DOCTYPE w:document [<!ENTITY x "y">]>'
            f"<w:document {_W}><w:body><w:p><w:r><w:t>&x;</w:t></w:r></w:p></w:body></w:document>",
        )
    with pytest.raises(ValueError):
        parse_docx(buf.getvalue())
