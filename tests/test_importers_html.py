"""Tests for auto_essay_typer.importers.gdocs_html.

The big fixture-driven test (``test_full_fixture_produces_exact_expected_document``)
exercises everything contract section 7 asks the fixture to cover in one
realistic document; the smaller tests around it isolate individual behaviours
(some, like <hr> and the inline footnote/comment anchor, deliberately do NOT
appear in the fixture, to keep that one test's expected Document tractable).
"""

import zipfile
from io import BytesIO
from pathlib import Path

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
from auto_essay_typer.importers.gdocs_html import parse_gdocs_html, parse_gdocs_html_zip

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# --- the full realistic-export fixture -----------------------------------------


def test_full_fixture_produces_exact_expected_document():
    html = (FIXTURES_DIR / "gdocs_export_sample.html").read_text(encoding="utf-8")
    doc, warnings = parse_gdocs_html(html)

    expected = Document(
        (
            Paragraph(
                runs=(Run("Sample Document Title", frozenset({TextStyle.BOLD})),),
                style=ParagraphStyle.TITLE,
            ),
            Paragraph(
                runs=(Run("A subtitle, italicized by its own span", frozenset({TextStyle.ITALIC})),),
                style=ParagraphStyle.SUBTITLE,
            ),
            Paragraph(runs=(Run("Section One"),), style=ParagraphStyle.HEADING_1),
            Paragraph(runs=(Run("Subsection A"),), style=ParagraphStyle.HEADING_2),
            Paragraph(runs=(Run("Detail"),), style=ParagraphStyle.HEADING_3),
            Paragraph(
                runs=(
                    Run("Bold, ", frozenset({TextStyle.BOLD})),
                    Run("italic, ", frozenset({TextStyle.ITALIC})),
                    Run("underlined, ", frozenset({TextStyle.UNDERLINE})),
                    Run("struck through", frozenset({TextStyle.STRIKETHROUGH})),
                    Run(", and plain."),
                ),
            ),
            Paragraph(runs=(Run("This paragraph is centered."),), alignment=Alignment.CENTER),
            Paragraph(runs=(Run("This paragraph is right-aligned."),), alignment=Alignment.RIGHT),
            Paragraph(
                runs=(Run("Top-level bullet item"),),
                list_kind=ListKind.BULLET,
                list_level=0,
            ),
            Paragraph(
                runs=(Run("Nested bullet item"),),
                list_kind=ListKind.BULLET,
                list_level=1,
            ),
            Paragraph(
                runs=(Run("First numbered item"),),
                list_kind=ListKind.NUMBERED,
                list_level=0,
            ),
            Paragraph(
                runs=(
                    Run("Visit "),
                    Run("our page", frozenset({TextStyle.BOLD})),
                    Run(" for more."),
                ),
            ),
            Paragraph(runs=()),  # the image-only paragraph: image dropped, nothing left
            Paragraph(runs=(Run("A1"),)),
            Paragraph(runs=(Run("B1"),)),
            Paragraph(runs=(Run("A2"),)),
            Paragraph(runs=(Run("B2"),)),
            Paragraph(runs=(Run("Non-breaking space right here."),)),
            Paragraph(runs=(Run("Custom font, size, and colour."),)),
        )
    )

    assert doc == expected
    assert warnings == (
        "1 link typed as plain text",
        "1 image not reproduced (dropped)",
        "1 table flattened: each cell typed as one or more paragraphs, row by row, "
        "losing any heading style, alignment or list inside a cell",
        "1 footnote/endnote/comment reference dropped, along with the text of the note",
        "this document uses custom fonts, sizes, or colours, which are not reproduced",
    )


# --- basics: plain text, minimal HTML ------------------------------------------


def test_minimal_fragment_with_no_html_or_body_wrapper():
    doc, warnings = parse_gdocs_html("<p>hello</p>")
    assert doc == Document((Paragraph(runs=(Run("hello"),)),))
    assert warnings == ()


def test_full_page_wrapper_is_found_and_used():
    doc, _ = parse_gdocs_html("<html><head></head><body><p>hi</p></body></html>")
    assert doc.plain_text() == "hi"


def test_multiple_paragraphs_in_order():
    doc, _ = parse_gdocs_html("<p>one</p><p>two</p><p>three</p>")
    assert [p.text for p in doc.paragraphs] == ["one", "two", "three"]


def test_empty_paragraph_has_no_runs():
    doc, _ = parse_gdocs_html("<p></p>")
    assert doc.paragraphs == (Paragraph(),)


# --- text with no block element of its own ---------------------------------------
#
# A file that is plain text, or a wrapper holding text directly, used to come
# back as an empty Document with nothing reported -- the worst case the
# importers can produce, because the panel then says there is no problem.


def test_a_file_that_is_just_plain_text_is_still_read():
    doc, _ = parse_gdocs_html("plain text with no tags at all")
    assert [p.text for p in doc.paragraphs] == ["plain text with no tags at all"]


def test_text_sitting_directly_in_a_div_is_read():
    doc, _ = parse_gdocs_html("<div>loose text in a div</div>")
    assert [p.text for p in doc.paragraphs] == ["loose text in a div"]


def test_a_span_with_no_paragraph_around_it_is_read():
    html = '<style>.c1{font-weight:700}</style><div><span class="c1">span text</span></div>'
    doc, _ = parse_gdocs_html(html)
    assert doc.paragraphs[0].runs == (Run("span text", frozenset({TextStyle.BOLD})),)


def test_text_either_side_of_a_block_keeps_its_place_and_does_not_fuse():
    doc, _ = parse_gdocs_html("<div>before<p>middle</p>after</div>")
    assert [p.text for p in doc.paragraphs] == ["before", "middle", "after"]


def test_loose_text_in_the_body_is_read_alongside_its_paragraphs():
    doc, _ = parse_gdocs_html("<html><body>loose in body<p>and a para</p></body></html>")
    assert [p.text for p in doc.paragraphs] == ["loose in body", "and a para"]


def test_an_image_with_no_paragraph_around_it_is_still_reported():
    _doc, warnings = parse_gdocs_html('<div><img src="a.png"/></div>')
    assert any("image" in w for w in warnings)


def test_a_stylesheet_is_never_typed_as_text():
    html = "<style>.c1{font-weight:700}</style><p>body</p>"
    doc, _ = parse_gdocs_html(html)
    assert [p.text for p in doc.paragraphs] == ["body"]


def test_the_page_title_is_not_typed_as_body_text():
    html = "<html><head><title>Not body text</title></head><body><p>body</p></body></html>"
    doc, _ = parse_gdocs_html(html)
    assert [p.text for p in doc.paragraphs] == ["body"]


def test_a_stylesheet_inside_the_body_is_not_typed_either():
    html = "<body><style>.c1{font-weight:700}</style><p>body</p></body>"
    doc, _ = parse_gdocs_html(html)
    assert [p.text for p in doc.paragraphs] == ["body"]


@pytest.mark.parametrize(
    "html",
    [
        "<blockquote>quoted text</blockquote>",
        "<section><p>quoted text</p></section>",
        "<li>quoted text</li>",
        "<dl><dt>quoted text</dt></dl>",
    ],
)
def test_a_block_element_with_no_style_of_its_own_still_has_its_text_typed(html):
    doc, _ = parse_gdocs_html(html)
    assert [p.text for p in doc.paragraphs] == ["quoted text"]


def test_text_put_in_a_list_outside_an_item_is_still_typed():
    html = "<ul><li>a real item</li><div>stray text</div></ul>"
    doc, _ = parse_gdocs_html(html)
    assert [p.text for p in doc.paragraphs] == ["a real item", "stray text"]


def test_a_stylesheet_inside_a_list_is_not_typed():
    html = "<ul><style>.c1{font-weight:700}</style><li>a real item</li></ul>"
    doc, _ = parse_gdocs_html(html)
    assert [p.text for p in doc.paragraphs] == ["a real item"]


# --- run styles: classes ---------------------------------------------------------


def test_bold_via_font_weight_700_class():
    html = '<style>.c1{font-weight:700}</style><p><span class="c1">x</span></p>'
    doc, _ = parse_gdocs_html(html)
    assert doc.paragraphs[0].runs[0].styles == frozenset({TextStyle.BOLD})


def test_bold_via_literal_bold_keyword():
    html = '<style>.c1{font-weight:bold}</style><p><span class="c1">x</span></p>'
    doc, _ = parse_gdocs_html(html)
    assert doc.paragraphs[0].runs[0].styles == frozenset({TextStyle.BOLD})


def test_font_weight_600_or_above_is_bold():
    html = '<style>.c1{font-weight:600}</style><p><span class="c1">x</span></p>'
    doc, _ = parse_gdocs_html(html)
    assert doc.paragraphs[0].runs[0].styles == frozenset({TextStyle.BOLD})


def test_font_weight_below_600_is_not_bold():
    html = '<style>.c1{font-weight:400}</style><p><span class="c1">x</span></p>'
    doc, _ = parse_gdocs_html(html)
    assert doc.paragraphs[0].runs[0].styles == frozenset()


def test_italic_via_class():
    html = '<style>.c1{font-style:italic}</style><p><span class="c1">x</span></p>'
    doc, _ = parse_gdocs_html(html)
    assert doc.paragraphs[0].runs[0].styles == frozenset({TextStyle.ITALIC})


def test_underline_via_class():
    html = '<style>.c1{text-decoration:underline}</style><p><span class="c1">x</span></p>'
    doc, _ = parse_gdocs_html(html)
    assert doc.paragraphs[0].runs[0].styles == frozenset({TextStyle.UNDERLINE})


def test_strikethrough_via_class():
    html = '<style>.c1{text-decoration:line-through}</style><p><span class="c1">x</span></p>'
    doc, _ = parse_gdocs_html(html)
    assert doc.paragraphs[0].runs[0].styles == frozenset({TextStyle.STRIKETHROUGH})


def test_all_four_styles_combine():
    html = (
        "<style>.c1{font-weight:700;font-style:italic;"
        "text-decoration:underline line-through}</style>"
        '<p><span class="c1">x</span></p>'
    )
    doc, _ = parse_gdocs_html(html)
    assert doc.paragraphs[0].runs[0].styles == frozenset(
        {TextStyle.BOLD, TextStyle.ITALIC, TextStyle.UNDERLINE, TextStyle.STRIKETHROUGH}
    )


def test_unstyled_span_has_no_styles():
    doc, _ = parse_gdocs_html("<p><span>plain</span></p>")
    assert doc.paragraphs[0].runs[0].styles == frozenset()


def test_adjacent_same_style_spans_merge_into_one_run():
    html = '<style>.c1{font-weight:700}</style><p><span class="c1">ab</span><span class="c1">cd</span></p>'
    doc, _ = parse_gdocs_html(html)
    assert doc.paragraphs[0].runs == (Run("abcd", frozenset({TextStyle.BOLD})),)


# --- run styles: inline style="" attribute --------------------------------------


def test_bold_via_inline_style_attribute():
    doc, _ = parse_gdocs_html('<p><span style="font-weight:700">x</span></p>')
    assert doc.paragraphs[0].runs[0].styles == frozenset({TextStyle.BOLD})


def test_inline_style_attribute_overrides_class():
    # class says italic, inline style says not-italic (normal) -- inline wins.
    html = (
        '<style>.c1{font-style:italic}</style>'
        '<p><span class="c1" style="font-style:normal">x</span></p>'
    )
    doc, _ = parse_gdocs_html(html)
    assert doc.paragraphs[0].runs[0].styles == frozenset()


# --- paragraph style: headings, title, subtitle ---------------------------------


def test_headings_h1_through_h6():
    html = "".join(f"<h{n}>heading {n}</h{n}>" for n in range(1, 7))
    doc, _ = parse_gdocs_html(html)
    expected_styles = [
        ParagraphStyle.HEADING_1,
        ParagraphStyle.HEADING_2,
        ParagraphStyle.HEADING_3,
        ParagraphStyle.HEADING_4,
        ParagraphStyle.HEADING_5,
        ParagraphStyle.HEADING_6,
    ]
    assert [p.style for p in doc.paragraphs] == expected_styles


def test_p_with_title_class_is_title_style():
    doc, _ = parse_gdocs_html('<p class="title">My Title</p>')
    assert doc.paragraphs[0].style == ParagraphStyle.TITLE


def test_p_with_subtitle_class_is_subtitle_style():
    doc, _ = parse_gdocs_html('<p class="subtitle">My Subtitle</p>')
    assert doc.paragraphs[0].style == ParagraphStyle.SUBTITLE


def test_p_with_other_classes_is_normal_style():
    doc, _ = parse_gdocs_html('<p class="c7 c8">just text</p>')
    assert doc.paragraphs[0].style == ParagraphStyle.NORMAL


# --- alignment --------------------------------------------------------------------


def test_center_alignment_from_paragraph_class():
    html = '<style>.c1{text-align:center}</style><p class="c1">x</p>'
    doc, _ = parse_gdocs_html(html)
    assert doc.paragraphs[0].alignment == Alignment.CENTER


def test_right_alignment_from_paragraph_class():
    html = '<style>.c1{text-align:right}</style><p class="c1">x</p>'
    doc, _ = parse_gdocs_html(html)
    assert doc.paragraphs[0].alignment == Alignment.RIGHT


def test_justified_alignment_from_paragraph_class():
    html = '<style>.c1{text-align:justify}</style><p class="c1">x</p>'
    doc, _ = parse_gdocs_html(html)
    assert doc.paragraphs[0].alignment == Alignment.JUSTIFIED


def test_default_alignment_is_left():
    doc, _ = parse_gdocs_html("<p>x</p>")
    assert doc.paragraphs[0].alignment == Alignment.LEFT


def test_alignment_via_inline_style():
    doc, _ = parse_gdocs_html('<p style="text-align:right">x</p>')
    assert doc.paragraphs[0].alignment == Alignment.RIGHT


# --- lists ---------------------------------------------------------------------


def test_bullet_list_single_item_level_zero():
    html = '<ul class="lst-kix_a-0"><li><span>item</span></li></ul>'
    doc, _ = parse_gdocs_html(html)
    p = doc.paragraphs[0]
    assert p.list_kind == ListKind.BULLET
    assert p.list_level == 0
    assert p.text == "item"


def test_numbered_list_single_item():
    html = '<ol class="lst-kix_a-0"><li><span>item</span></li></ol>'
    doc, _ = parse_gdocs_html(html)
    assert doc.paragraphs[0].list_kind == ListKind.NUMBERED


def test_nested_level_read_from_class_suffix():
    html = (
        '<ul class="lst-kix_a-0"><li><span>top</span></li></ul>'
        '<ul class="lst-kix_a-1"><li><span>nested</span></li></ul>'
        '<ul class="lst-kix_a-2"><li><span>double nested</span></li></ul>'
    )
    doc, _ = parse_gdocs_html(html)
    assert [p.list_level for p in doc.paragraphs] == [0, 1, 2]
    assert [p.list_kind for p in doc.paragraphs] == [ListKind.BULLET] * 3


def test_multiple_items_in_one_list_element():
    html = '<ul class="lst-kix_a-0"><li><span>one</span></li><li><span>two</span></li></ul>'
    doc, _ = parse_gdocs_html(html)
    assert [p.text for p in doc.paragraphs] == ["one", "two"]
    assert all(p.list_kind == ListKind.BULLET for p in doc.paragraphs)


def test_list_items_keep_their_own_run_styles():
    html = (
        '<style>.c1{font-weight:700}</style>'
        '<ul class="lst-kix_a-0"><li><span class="c1">bold item</span></li></ul>'
    )
    doc, _ = parse_gdocs_html(html)
    assert doc.paragraphs[0].runs[0].styles == frozenset({TextStyle.BOLD})


def test_paragraph_outside_any_list_is_not_a_list_item():
    doc, _ = parse_gdocs_html("<p>not a list</p>")
    assert doc.paragraphs[0].list_kind is None
    assert doc.paragraphs[0].list_level == 0


def test_list_element_with_no_lst_kix_class_defaults_to_level_zero():
    doc, _ = parse_gdocs_html("<ul><li>item with no recognisable class</li></ul>")
    assert doc.paragraphs[0].list_kind == ListKind.BULLET
    assert doc.paragraphs[0].list_level == 0


# --- links, images ------------------------------------------------------------------


def test_link_text_is_kept_with_a_links_warning():
    doc, warnings = parse_gdocs_html('<p>see <a href="https://x.example/">this link</a></p>')
    assert doc.paragraphs[0].text == "see this link"
    assert any("link" in w for w in warnings)


def test_link_keeps_its_own_formatting():
    html = '<style>.c1{text-decoration:underline}</style><p><a href="https://x.example/" class="c1">link</a></p>'
    doc, _ = parse_gdocs_html(html)
    assert doc.paragraphs[0].runs[0].styles == frozenset({TextStyle.UNDERLINE})


def test_multiple_links_warning_is_aggregated_with_a_count():
    html = '<p><a href="https://a/">a</a> and <a href="https://b/">b</a></p>'
    doc, warnings = parse_gdocs_html(html)
    link_warnings = [w for w in warnings if "link" in w]
    assert len(link_warnings) == 1
    assert "2" in link_warnings[0]


def test_image_dropped_with_a_warning():
    doc, warnings = parse_gdocs_html('<p><img src="a.png"/>caption</p>')
    assert doc.paragraphs[0].text == "caption"
    assert any("image" in w for w in warnings)


def test_multiple_images_warning_is_aggregated_with_a_count():
    doc, warnings = parse_gdocs_html('<p><img src="a.png"/><img src="b.png"/></p>')
    image_warnings = [w for w in warnings if "image" in w]
    assert len(image_warnings) == 1
    assert "2" in image_warnings[0]


# --- tables --------------------------------------------------------------------------


def test_table_flattened_with_its_cells_in_reading_order():
    html = (
        "<table><tbody>"
        "<tr><td>A1</td><td>B1</td></tr>"
        "<tr><td>A2</td><td>B2</td></tr>"
        "</tbody></table>"
    )
    doc, warnings = parse_gdocs_html(html)
    assert [p.text for p in doc.paragraphs] == ["A1", "B1", "A2", "B2"]
    (table_warning,) = [w for w in warnings if "table" in w]
    # A cell is one or more paragraphs. "One paragraph per cell" stopped being true when a
    # cell's own paragraphs and line breaks were kept apart (tests below).
    assert "one or more paragraphs" in table_warning
    assert "one paragraph per cell" not in table_warning


def test_table_without_tbody_wrapper_still_flattens():
    html = "<table><tr><td>x</td></tr></table>"
    doc, _ = parse_gdocs_html(html)
    assert doc.paragraphs[0].text == "x"


def test_table_between_paragraphs_preserves_reading_order():
    html = "<p>before</p><table><tr><td>cell</td></tr></table><p>after</p>"
    doc, _ = parse_gdocs_html(html)
    assert [p.text for p in doc.paragraphs] == ["before", "cell", "after"]


def test_table_cell_keeps_its_run_styles():
    html = '<style>.c1{font-weight:700}</style><table><tr><td><span class="c1">bold cell</span></td></tr></table>'
    doc, _ = parse_gdocs_html(html)
    assert doc.paragraphs[0].runs[0].styles == frozenset({TextStyle.BOLD})


def test_two_paragraphs_in_one_cell_stay_two_paragraphs():
    # Google writes one <p> per paragraph inside a cell. Running them together
    # would fuse the end of one onto the start of the next.
    html = "<table><tr><td><p>first para</p><p>second para</p></td></tr></table>"
    doc, _ = parse_gdocs_html(html)
    assert [p.text for p in doc.paragraphs] == ["first para", "second para"]


def test_br_inside_a_cell_splits_the_cell_text_and_is_counted():
    html = "<table><tr><td>cell line one<br>cell line two</td></tr></table>"
    doc, warnings = parse_gdocs_html(html)
    assert [p.text for p in doc.paragraphs] == ["cell line one", "cell line two"]
    assert any("soft line break" in w for w in warnings)


def test_a_nested_table_inside_a_cell_keeps_its_cells_apart():
    html = (
        "<table><tr><td>outer"
        "<table><tr><td>inner A</td><td>inner B</td></tr></table>"
        "</td></tr></table>"
    )
    doc, warnings = parse_gdocs_html(html)
    assert [p.text for p in doc.paragraphs] == ["outer", "inner A", "inner B"]
    table_warnings = [w for w in warnings if "table" in w]
    assert len(table_warnings) == 1
    assert "2" in table_warnings[0]


def test_a_heading_a_list_and_alignment_inside_a_cell_are_typed_as_ordinary_text_and_the_message_says_so():
    """This reader types a cell's content from its inline text alone, so those are lost.

    The message says so (``keeps_paragraph_formatting=False``). If this reader is ever
    taught to keep them, this test fails; flip the flag in ``_render_warnings`` with it.
    """
    html = (
        "<table><tr><td><h2>Cell heading</h2>"
        '<p style="text-align:center">centered</p><ul><li>bullet</li></ul></td></tr></table>'
    )
    doc, warnings = parse_gdocs_html(html)
    assert [p.text for p in doc.paragraphs] == ["Cell heading", "centered", "bullet"]
    for paragraph in doc.paragraphs:
        assert paragraph.style is ParagraphStyle.NORMAL
        assert paragraph.alignment is Alignment.LEFT
        assert paragraph.list_kind is None
    (table_warning,) = [w for w in warnings if "table" in w]
    assert "losing any heading style, alignment or list inside a cell" in table_warning


def test_an_empty_cell_still_produces_a_paragraph():
    html = "<table><tr><td>filled</td><td></td></tr></table>"
    doc, _ = parse_gdocs_html(html)
    assert [p.text for p in doc.paragraphs] == ["filled", ""]


def test_a_table_caption_is_typed_before_the_cells():
    html = "<table><caption>Table 1: results</caption><tr><td>cell</td></tr></table>"
    doc, _ = parse_gdocs_html(html)
    assert [p.text for p in doc.paragraphs] == ["Table 1: results", "cell"]


# --- block elements nested inside inline content -------------------------------------


def test_block_elements_nested_in_a_paragraph_are_not_fused():
    html = "<p>wrapper<div><p>inner one</p><p>inner two</p></div></p>"
    doc, _ = parse_gdocs_html(html)
    assert [p.text for p in doc.paragraphs] == ["wrapper", "inner one", "inner two"]


def test_a_nested_list_inside_a_list_item_is_not_fused():
    html = "<ul><li>outer item<ul><li>inner item</li></ul></li></ul>"
    doc, _ = parse_gdocs_html(html)
    assert [p.text for p in doc.paragraphs] == ["outer item", "inner item"]


def test_a_wrapper_div_does_not_add_an_empty_paragraph():
    html = "<p><div>only text</div></p>"
    doc, _ = parse_gdocs_html(html)
    assert [p.text for p in doc.paragraphs] == ["only text"]


def test_a_nested_block_boundary_is_not_counted_as_a_line_break():
    html = "<p>a<div>b</div></p>"
    _doc, warnings = parse_gdocs_html(html)
    assert not any("soft line break" in w for w in warnings)


def test_a_nested_block_keeps_the_styles_it_inherits_and_its_own():
    html = (
        "<style>.c1{font-weight:700}.c2{font-style:italic}</style>"
        '<p class="x"><span class="c1">bold <div class="c2">and italic</div></span></p>'
    )
    doc, _ = parse_gdocs_html(html)
    assert [p.runs for p in doc.paragraphs] == [
        (Run("bold ", frozenset({TextStyle.BOLD})),),
        (Run("and italic", frozenset({TextStyle.BOLD, TextStyle.ITALIC})),),
    ]


def test_whitespace_between_table_rows_is_ignored_not_treated_as_a_cell():
    html = "<table>\n  <tr><td>A1</td></tr>\n  <tr><td>A2</td></tr>\n</table>"
    doc, _ = parse_gdocs_html(html)
    assert [p.text for p in doc.paragraphs] == ["A1", "A2"]


# --- <hr> ------------------------------------------------------------------------------


def test_hr_is_skipped_with_a_warning_and_produces_no_paragraph():
    doc, warnings = parse_gdocs_html("<p>before</p><hr><p>after</p>")
    assert [p.text for p in doc.paragraphs] == ["before", "after"]
    assert any("horizontal rule" in w for w in warnings)


def test_hr_appearing_inside_a_paragraph_is_also_skipped_with_a_warning():
    doc, warnings = parse_gdocs_html("<p>before<hr/>after</p>")
    assert doc.paragraphs[0].text == "beforeafter"
    assert any("horizontal rule" in w for w in warnings)


# --- <br>: soft line breaks split a paragraph -------------------------------------------


def test_br_splits_one_element_into_two_paragraphs_with_a_warning():
    doc, warnings = parse_gdocs_html("<p>line one<br>line two</p>")
    assert [p.text for p in doc.paragraphs] == ["line one", "line two"]
    assert any("soft line break" in w for w in warnings)


def test_br_split_paragraphs_inherit_alignment():
    html = '<style>.c1{text-align:center}</style><p class="c1">line one<br>line two</p>'
    doc, _ = parse_gdocs_html(html)
    assert [p.alignment for p in doc.paragraphs] == [Alignment.CENTER, Alignment.CENTER]


def test_br_split_inside_list_item_keeps_both_halves_in_the_list():
    html = '<ul class="lst-kix_a-0"><li><span>line one<br>line two</span></li></ul>'
    doc, _ = parse_gdocs_html(html)
    assert [p.text for p in doc.paragraphs] == ["line one", "line two"]
    assert all(p.list_kind == ListKind.BULLET and p.list_level == 0 for p in doc.paragraphs)


def test_multiple_br_warnings_are_aggregated_with_a_count():
    doc, warnings = parse_gdocs_html("<p>a<br>b<br>c</p>")
    assert [p.text for p in doc.paragraphs] == ["a", "b", "c"]
    break_warnings = [w for w in warnings if "soft line break" in w]
    assert len(break_warnings) == 1
    assert "2" in break_warnings[0]


# --- whitespace: nbsp and embedded newlines -----------------------------------------------


def test_nbsp_entity_becomes_a_regular_space():
    doc, _ = parse_gdocs_html("<p>a&nbsp;b</p>")
    assert doc.paragraphs[0].text == "a b"


def test_literal_nbsp_character_becomes_a_regular_space():
    doc, _ = parse_gdocs_html("<p>a b</p>")
    assert doc.paragraphs[0].text == "a b"


def test_newline_inside_text_node_becomes_a_space():
    doc, _ = parse_gdocs_html("<p>a\nb</p>")
    assert doc.paragraphs[0].text == "a b"


def test_meaningful_spaces_across_span_boundaries_are_preserved():
    html = '<style>.c1{font-weight:700}</style><p><span class="c1">bold </span><span>plain</span></p>'
    doc, _ = parse_gdocs_html(html)
    assert doc.paragraphs[0].text == "bold plain"


# --- footnotes / comments ---------------------------------------------------------------


def test_ordinary_content_wrapped_in_a_plain_div_is_still_parsed():
    # Google sometimes wraps the whole body in a plain content <div> that is
    # not a footnote/comment section -- its children must still be walked,
    # not silently dropped along with the (separately handled) special divs.
    html = '<div class="doc-content"><p>first</p><p>second</p></div>'
    doc, _ = parse_gdocs_html(html)
    assert [p.text for p in doc.paragraphs] == ["first", "second"]


def test_trailing_footnotes_section_is_skipped_with_a_warning():
    html = (
        "<p>body text</p>"
        '<div id="footnotes"><p class="footnote">'
        '<a id="ftnt1" href="#ftnt_ref1">1</a> a footnote</p></div>'
    )
    doc, warnings = parse_gdocs_html(html)
    assert [p.text for p in doc.paragraphs] == ["body text"]
    assert any("footnote" in w for w in warnings)


def test_trailing_comments_section_is_skipped_with_a_warning():
    html = '<p>body text</p><div id="comments"><p>a review comment</p></div>'
    doc, warnings = parse_gdocs_html(html)
    assert [p.text for p in doc.paragraphs] == ["body text"]
    assert any("comment" in w for w in warnings)


def test_inline_footnote_reference_anchor_is_dropped_from_body_text():
    html = '<p>a claim<a id="ftnt_ref1" href="#ftnt1">[1]</a> continues</p>'
    doc, warnings = parse_gdocs_html(html)
    assert doc.paragraphs[0].text == "a claim continues"
    assert any("footnote" in w for w in warnings)
    # An inline footnote reference is not a real hyperlink -- it must not
    # also show up counted as one.
    assert not any("typed as plain text" in w for w in warnings)


def test_a_footnotes_block_shaped_as_google_writes_it_is_not_typed_as_body_text():
    # Google marks neither the wrapping <div> nor each note's <p>: the only
    # sign is the back-link at the start of the note. Read as ordinary content,
    # the notes are typed as the closing paragraphs of the essay.
    html = (
        '<p>a claim<sup><a href="#ftnt1" id="ftnt_ref1">[1]</a></sup> continues</p>'
        '<div><p><a href="#ftnt_ref1" id="ftnt1">[1]</a>'
        "<span> Smith, A Study of Things, p. 14.</span></p></div>"
    )
    doc, warnings = parse_gdocs_html(html)
    assert [p.text for p in doc.paragraphs] == ["a claim continues"]
    assert any("footnote" in w for w in warnings)


def test_the_footnote_message_says_the_notes_own_text_is_dropped_with_its_marker():
    html = (
        '<p>a claim<sup><a href="#ftnt1" id="ftnt_ref1">[1]</a></sup> continues</p>'
        '<div><p><a href="#ftnt_ref1" id="ftnt1">[1]</a><span> Smith, p. 14.</span></p></div>'
    )
    doc, warnings = parse_gdocs_html(html)
    assert "Smith" not in doc.plain_text()
    (footnote_warning,) = [w for w in warnings if "footnote" in w]
    assert "along with the text of the note" in footnote_warning


def test_one_footnote_is_reported_once_not_once_per_end_of_it():
    # The marker in the text and the note itself are two halves of one
    # footnote, so counting both says "2 footnotes" about a document with one.
    html = (
        '<p>a claim<sup><a href="#ftnt1" id="ftnt_ref1">[1]</a></sup> continues</p>'
        '<div><p><a href="#ftnt_ref1" id="ftnt1">[1]</a><span> the note</span></p></div>'
    )
    _doc, warnings = parse_gdocs_html(html)
    (footnote_warning,) = [w for w in warnings if "footnote" in w]
    assert footnote_warning.startswith("1 ")


def test_two_footnotes_are_reported_as_two():
    html = (
        '<p>first<sup><a href="#ftnt1" id="ftnt_ref1">[1]</a></sup> and '
        'second<sup><a href="#ftnt2" id="ftnt_ref2">[2]</a></sup></p>'
        '<div><p><a href="#ftnt_ref1" id="ftnt1">[1]</a><span> note one</span></p>'
        '<p><a href="#ftnt_ref2" id="ftnt2">[2]</a><span> note two</span></p></div>'
    )
    _doc, warnings = parse_gdocs_html(html)
    (footnote_warning,) = [w for w in warnings if "footnote" in w]
    assert footnote_warning.startswith("2 ")


def test_a_comment_section_with_no_per_note_links_is_still_reported():
    html = '<p>body text</p><div id="comments"><p>a review comment</p></div>'
    _doc, warnings = parse_gdocs_html(html)
    (comment_warning,) = [w for w in warnings if "comment" in w]
    assert comment_warning.startswith("1 ")


def test_a_paragraph_that_merely_starts_with_a_footnote_marker_is_still_body_text():
    html = '<p><sup><a href="#ftnt1" id="ftnt_ref1">[1]</a></sup> says otherwise.</p>'
    doc, _ = parse_gdocs_html(html)
    assert [p.text for p in doc.paragraphs] == [" says otherwise."]


def test_the_footnoted_export_fixture_types_the_body_and_reports_the_notes():
    html = (FIXTURES_DIR / "gdocs_export_footnotes.html").read_text(encoding="utf-8")
    doc, warnings = parse_gdocs_html(html)

    assert doc == Document(
        (
            Paragraph(runs=(Run("The first claim rests on one source."),)),
            Paragraph(runs=(Run("The second claim rests on another."),)),
        )
    )
    assert warnings == ("2 footnote/endnote/comment references dropped, along with the text of the note",)


# --- non-default fonts / sizes / colours: one aggregated warning ------------------------


def test_non_default_font_family_triggers_one_aggregated_warning():
    html = '<style>.c1{font-family:"Courier New"}</style><p><span class="c1">x</span></p>'
    doc, warnings = parse_gdocs_html(html)
    assert any("font" in w.lower() for w in warnings)


def test_non_default_font_size_triggers_the_warning():
    html = '<style>.c1{font-size:18pt}</style><p><span class="c1">x</span></p>'
    doc, warnings = parse_gdocs_html(html)
    assert any("not reproduced" in w for w in warnings)


def test_non_default_color_triggers_the_warning():
    html = '<style>.c1{color:#ff0000}</style><p><span class="c1">x</span></p>'
    doc, warnings = parse_gdocs_html(html)
    assert any("colour" in w or "color" in w for w in warnings)


def test_default_arial_11pt_black_does_not_trigger_the_warning():
    html = (
        '<style>.c1{font-family:Arial;font-size:11pt;color:#000000;font-weight:700}</style>'
        '<p><span class="c1">x</span></p>'
    )
    doc, warnings = parse_gdocs_html(html)
    assert not any("not reproduced" in w and "font" in w for w in warnings)


def test_plain_document_with_no_styling_has_no_warnings_at_all():
    doc, warnings = parse_gdocs_html("<p>just plain text</p>")
    assert warnings == ()


# --- the .zip "Web page, zipped" download variant -----------------------------------------


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_zip_with_single_html_file_is_parsed():
    data = _zip_bytes({"Sample Document.html": b"<p>from zip</p>"})
    doc, warnings = parse_gdocs_html_zip(data)
    assert doc.plain_text() == "from zip"
    assert warnings == ()


def test_zip_ignores_the_images_folder_and_finds_the_html():
    data = _zip_bytes(
        {
            "images/image1.png": b"\x89PNG\r\n\x1a\n",
            "Sample Document.html": b"<p>text next to an images folder</p>",
        }
    )
    doc, _ = parse_gdocs_html_zip(data)
    assert doc.plain_text() == "text next to an images folder"


def test_zip_with_no_html_file_raises_value_error():
    data = _zip_bytes({"readme.txt": b"nothing useful here"})
    with pytest.raises(ValueError):
        parse_gdocs_html_zip(data)


def test_zip_not_a_zip_raises_value_error():
    with pytest.raises(ValueError):
        parse_gdocs_html_zip(b"not a zip file")


# --- CSS parsing robustness ------------------------------------------------------------


def test_css_rule_with_empty_declaration_block_is_harmlessly_ignored():
    html = '<style>.c9{}</style><p class="c9">plain text</p>'
    doc, warnings = parse_gdocs_html(html)
    assert doc.paragraphs[0].text == "plain text"
    assert warnings == ()


def test_css_property_with_no_use_to_this_module_is_harmlessly_ignored():
    # A pure list-bullet-glyph rule like this carries no property this
    # module ever reads; it must not blow up or leak a stray style.
    html = '<style>.lst-kix_a-0>li:before{content:""}</style><p>plain text</p>'
    doc, warnings = parse_gdocs_html(html)
    assert doc.paragraphs[0].text == "plain text"
    assert warnings == ()
