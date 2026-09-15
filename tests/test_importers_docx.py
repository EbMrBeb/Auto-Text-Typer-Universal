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

_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _build_docx(body_xml: str, styles_xml: str | None = None, numbering_xml: str | None = None) -> bytes:
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


def test_table_flattened_to_one_paragraph_per_cell_in_reading_order():
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
    assert any("table" in w for w in warnings)


def test_table_between_paragraphs_preserves_reading_order():
    body = (
        "<w:p><w:r><w:t>before</w:t></w:r></w:p>"
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
        "<w:p><w:r><w:t>after</w:t></w:r></w:p>"
    )
    doc, _ = parse_docx(_build_docx(body))
    assert [p.text for p in doc.paragraphs] == ["before", "cell", "after"]


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
