import random

import pytest

from auto_essay_typer.document import (
    Alignment,
    Document,
    ImportReport,
    ListKind,
    Paragraph,
    ParagraphStyle,
    Run,
    TextStyle,
)

# --- Run --------------------------------------------------------------------


def test_run_rejects_empty_text():
    with pytest.raises(ValueError):
        Run("")


def test_run_rejects_embedded_newline():
    with pytest.raises(ValueError):
        Run("a\nb")


def test_run_default_styles_is_empty():
    assert Run("x").styles == frozenset()


def test_run_holds_the_given_styles():
    styles = frozenset({TextStyle.BOLD, TextStyle.ITALIC})
    assert Run("x", styles).styles == styles


def test_run_is_frozen():
    with pytest.raises(Exception):
        Run("x").text = "y"


# --- Paragraph ----------------------------------------------------------------


def test_paragraph_rejects_list_level_below_zero():
    with pytest.raises(ValueError):
        Paragraph(list_kind=ListKind.BULLET, list_level=-1)


def test_paragraph_rejects_list_level_above_eight():
    with pytest.raises(ValueError):
        Paragraph(list_kind=ListKind.BULLET, list_level=9)


def test_paragraph_accepts_list_level_at_the_boundaries():
    Paragraph(list_kind=ListKind.BULLET, list_level=0)
    Paragraph(list_kind=ListKind.BULLET, list_level=8)


def test_paragraph_rejects_nonzero_list_level_without_a_list_kind():
    with pytest.raises(ValueError):
        Paragraph(list_level=1)


def test_paragraph_allows_zero_list_level_without_a_list_kind():
    Paragraph(list_kind=None, list_level=0)  # must not raise


def test_paragraph_defaults_are_normal_left_no_list_no_text():
    p = Paragraph()
    assert p.runs == ()
    assert p.style == ParagraphStyle.NORMAL
    assert p.alignment == Alignment.LEFT
    assert p.list_kind is None
    assert p.list_level == 0
    assert p.text == ""


def test_paragraph_text_joins_run_text_in_order():
    p = Paragraph(runs=(Run("ab"), Run("cd"), Run("ef")))
    assert p.text == "abcdef"


def test_paragraph_is_frozen():
    with pytest.raises(Exception):
        Paragraph().style = ParagraphStyle.TITLE


# --- Document.plain_text ------------------------------------------------------


def test_plain_text_joins_paragraphs_with_newlines():
    doc = Document((Paragraph(runs=(Run("one"),)), Paragraph(runs=(Run("two"),))))
    assert doc.plain_text() == "one\ntwo"


def test_plain_text_of_a_single_empty_paragraph_is_empty_string():
    assert Document((Paragraph(),)).plain_text() == ""


def test_plain_text_of_no_paragraphs_is_empty_string():
    assert Document(()).plain_text() == ""


# --- Document.normalized: run merging -----------------------------------------


def test_normalized_merges_adjacent_runs_with_identical_styles():
    bold = frozenset({TextStyle.BOLD})
    p = Paragraph(runs=(Run("ab", bold), Run("cd", bold)))
    doc = Document((p,)).normalized()
    assert doc.paragraphs[0].runs == (Run("abcd", bold),)


def test_normalized_merges_more_than_two_adjacent_runs():
    doc = Document((Paragraph(runs=(Run("a"), Run("b"), Run("c"))),)).normalized()
    assert doc.paragraphs[0].runs == (Run("abc"),)


def test_normalized_does_not_merge_runs_with_different_styles():
    bold = frozenset({TextStyle.BOLD})
    p = Paragraph(runs=(Run("ab", bold), Run("cd", frozenset())))
    doc = Document((p,)).normalized()
    assert doc.paragraphs[0].runs == (Run("ab", bold), Run("cd", frozenset()))


def test_normalized_does_not_merge_non_adjacent_same_style_runs():
    bold = frozenset({TextStyle.BOLD})
    p = Paragraph(runs=(Run("a", bold), Run("b", frozenset()), Run("c", bold)))
    doc = Document((p,)).normalized()
    assert doc.paragraphs[0].runs == (Run("a", bold), Run("b", frozenset()), Run("c", bold))


def test_normalized_leaves_an_already_canonical_document_unchanged():
    bold = frozenset({TextStyle.BOLD})
    doc = Document((Paragraph(runs=(Run("a", bold), Run("b", frozenset()))),))
    assert doc.normalized() == doc


def test_normalized_is_idempotent():
    bold = frozenset({TextStyle.BOLD})
    doc = Document((Paragraph(runs=(Run("ab", bold), Run("cd", bold))),))
    once = doc.normalized()
    assert once.normalized() == once


# --- Document.normalized: empty paragraphs lose their list --------------------


def test_normalized_paragraph_with_no_runs_stays_empty():
    doc = Document((Paragraph(),)).normalized()
    assert doc.paragraphs[0].runs == ()


def test_normalized_clears_list_on_a_paragraph_with_no_text():
    p = Paragraph(runs=(), list_kind=ListKind.BULLET, list_level=3)
    doc = Document((p,)).normalized()
    assert doc.paragraphs[0].list_kind is None
    assert doc.paragraphs[0].list_level == 0


def test_normalized_keeps_list_on_a_paragraph_that_has_text():
    p = Paragraph(runs=(Run("x"),), list_kind=ListKind.NUMBERED, list_level=2)
    doc = Document((p,)).normalized()
    assert doc.paragraphs[0].list_kind == ListKind.NUMBERED
    assert doc.paragraphs[0].list_level == 2


def test_normalized_preserves_style_and_alignment_of_an_empty_paragraph():
    p = Paragraph(style=ParagraphStyle.HEADING_2, alignment=Alignment.RIGHT)
    doc = Document((p,)).normalized()
    assert doc.paragraphs[0].style == ParagraphStyle.HEADING_2
    assert doc.paragraphs[0].alignment == Alignment.RIGHT


# --- Document.from_plain_text --------------------------------------------------


def test_from_plain_text_splits_on_newlines():
    doc = Document.from_plain_text("one\ntwo\nthree")
    assert [p.text for p in doc.paragraphs] == ["one", "two", "three"]


def test_from_plain_text_every_paragraph_is_normal():
    doc = Document.from_plain_text("a\nb")
    assert all(p.style == ParagraphStyle.NORMAL for p in doc.paragraphs)


def test_from_plain_text_each_line_is_a_single_unstyled_run():
    doc = Document.from_plain_text("hello")
    (run,) = doc.paragraphs[0].runs
    assert run.text == "hello"
    assert run.styles == frozenset()


def test_from_plain_text_empty_line_produces_a_paragraph_with_no_run():
    doc = Document.from_plain_text("a\n\nb")
    assert len(doc.paragraphs) == 3
    assert doc.paragraphs[1].runs == ()
    assert doc.paragraphs[1].text == ""


def test_from_plain_text_of_empty_string_is_one_empty_paragraph():
    doc = Document.from_plain_text("")
    assert len(doc.paragraphs) == 1
    assert doc.paragraphs[0].runs == ()


@pytest.mark.parametrize(
    "text",
    [
        "",
        "hello",
        "a\nb\nc",
        "\n\n\n",
        "leading and trailing\n",
        "\nleading blank",
        "one blank line\n\nthen more",
    ],
)
def test_from_plain_text_round_trips_through_plain_text(text):
    assert Document.from_plain_text(text).plain_text() == text


def test_from_plain_text_round_trips_over_random_text():
    rng = random.Random(0)
    alphabet = "abc \n\n\txyz"  # "\n" weighted in deliberately
    for _ in range(500):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        assert Document.from_plain_text(text).plain_text() == text


# --- ImportReport ---------------------------------------------------------------


def test_import_report_defaults_to_no_warnings():
    report = ImportReport(source="https://example.com/doc")
    assert report.source == "https://example.com/doc"
    assert report.warnings == ()


def test_import_report_holds_its_warnings():
    report = ImportReport(source="essay.docx", warnings=("dropped an image", "dropped a table"))
    assert report.warnings == ("dropped an image", "dropped a table")


def test_import_report_is_frozen():
    with pytest.raises(Exception):
        ImportReport(source="x").source = "y"
