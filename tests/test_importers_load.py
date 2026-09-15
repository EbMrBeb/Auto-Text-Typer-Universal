"""Tests for auto_essay_typer.importers.load_document: the single dispatch
point for both Google Docs/Drive URLs and local files. Per-format parsing
correctness lives in test_importers_docx.py / test_importers_html.py /
test_importers_google.py; this file is about dispatch, error mapping, and
the normalize-before-returning contract.
"""

import zipfile
from io import BytesIO

import pytest

from auto_essay_typer.document import Document, ImportReport, Paragraph, ParagraphStyle
from auto_essay_typer.importers import (
    DocumentImportError,
    UnsupportedSourceError,
    load_document,
)
from auto_essay_typer.importers.google import FetchResult

# --- dispatch by extension: docx -------------------------------------------------

_DOCX_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _write_docx(path, body_xml: str) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
            'package/2006/content-types"></Types>',
        )
        zf.writestr(
            "word/document.xml",
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f"<w:document {_DOCX_W}><w:body>{body_xml}</w:body></w:document>",
        )


def test_dispatch_docx_by_extension(tmp_path):
    path = tmp_path / "essay.docx"
    _write_docx(path, "<w:p><w:r><w:t>hello from docx</w:t></w:r></w:p>")

    doc, report = load_document(str(path))

    assert isinstance(doc, Document)
    assert doc.plain_text() == "hello from docx"
    assert isinstance(report, ImportReport)
    assert report.source == str(path)
    assert report.warnings == ()


# --- dispatch by extension: html / htm -------------------------------------------


def test_dispatch_html_by_extension(tmp_path):
    path = tmp_path / "essay.html"
    path.write_text("<p>hello from html</p>", encoding="utf-8")

    doc, report = load_document(str(path))

    assert doc.plain_text() == "hello from html"
    assert report.warnings == ()


def test_dispatch_htm_by_extension(tmp_path):
    path = tmp_path / "essay.htm"
    path.write_text("<p>hello from htm</p>", encoding="utf-8")

    doc, _ = load_document(str(path))
    assert doc.plain_text() == "hello from htm"


def test_html_dispatch_is_case_insensitive_on_extension(tmp_path):
    path = tmp_path / "essay.HTML"
    path.write_text("<p>upper</p>", encoding="utf-8")

    doc, _ = load_document(str(path))
    assert doc.plain_text() == "upper"


# --- dispatch by extension: zip (Docs "Web page, zipped") -------------------------


def test_dispatch_zip_by_extension(tmp_path):
    path = tmp_path / "essay.zip"
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("essay.html", "<p>hello from zip</p>")
    path.write_bytes(buf.getvalue())

    doc, report = load_document(str(path))

    assert doc.plain_text() == "hello from zip"
    assert report.warnings == ()


def test_zip_that_is_not_a_valid_zip_raises_document_import_error(tmp_path):
    path = tmp_path / "essay.zip"
    path.write_bytes(b"not actually a zip")

    with pytest.raises(DocumentImportError):
        load_document(str(path))


# --- dispatch by extension: txt / md ------------------------------------------------


def test_dispatch_txt_by_extension(tmp_path):
    path = tmp_path / "essay.txt"
    path.write_text("line one\nline two", encoding="utf-8")

    doc, report = load_document(str(path))

    assert doc.plain_text() == "line one\nline two"
    assert report.warnings == ()


def test_dispatch_md_by_extension_is_typed_as_literal_plain_text(tmp_path):
    path = tmp_path / "essay.md"
    path.write_text("# Not a real heading\n**not really bold**", encoding="utf-8")

    doc, _ = load_document(str(path))

    # Markdown syntax is never interpreted -- it's typed exactly as written.
    assert doc.plain_text() == "# Not a real heading\n**not really bold**"
    assert doc.paragraphs[0].style == ParagraphStyle.NORMAL


# --- unknown extension / missing file -------------------------------------------------


def test_unknown_extension_raises_unsupported_source_error_listing_supported_types(tmp_path):
    path = tmp_path / "essay.pdf"
    path.write_bytes(b"%PDF-1.4 not really")

    with pytest.raises(UnsupportedSourceError) as excinfo:
        load_document(str(path))

    message = str(excinfo.value)
    assert ".docx" in message
    assert ".txt" in message


def test_no_extension_at_all_raises_unsupported_source_error(tmp_path):
    path = tmp_path / "essay"
    path.write_text("hello", encoding="utf-8")

    with pytest.raises(UnsupportedSourceError):
        load_document(str(path))


def test_missing_file_raises_document_import_error_with_clear_message(tmp_path):
    missing = tmp_path / "does-not-exist.docx"

    with pytest.raises(DocumentImportError) as excinfo:
        load_document(str(missing))

    assert str(missing) in str(excinfo.value)


def test_missing_file_error_is_not_a_bare_unsupported_source_error(tmp_path):
    # A missing file has a perfectly good (supported) extension -- the
    # failure is that it doesn't exist, which is a different error than an
    # extension load_document doesn't know how to handle at all.
    missing = tmp_path / "does-not-exist.txt"

    with pytest.raises(DocumentImportError):
        load_document(str(missing))


def test_corrupt_docx_raises_document_import_error_not_a_bare_value_error(tmp_path):
    # parse_docx() itself raises a private ValueError subclass; load_document
    # must wrap it into the project's own error hierarchy with the file path
    # attached, never let the raw parser exception escape.
    path = tmp_path / "essay.docx"
    path.write_bytes(b"this is not a zip file at all")

    with pytest.raises(DocumentImportError) as excinfo:
        load_document(str(path))

    assert str(path) in str(excinfo.value)


# --- dispatch by URL: routes to load_from_google ------------------------------------


def test_url_source_is_routed_to_the_google_loader_not_treated_as_a_file():
    calls = []

    def fetch(url: str) -> FetchResult:
        calls.append(url)
        return FetchResult(
            data=b"<p>from google</p>", content_type="text/html", final_url=url
        )

    doc, report = load_document(
        "https://docs.google.com/document/d/abc123/edit", fetch=fetch
    )

    assert calls == ["https://docs.google.com/document/d/abc123/export?format=html"]
    assert doc.plain_text() == "from google"
    assert report.source == "https://docs.google.com/document/d/abc123/edit"


def test_http_url_is_also_routed_to_google_not_treated_as_a_file():
    def fetch(url: str) -> FetchResult:
        return FetchResult(data=b"<p>x</p>", content_type="text/html", final_url=url)

    doc, _ = load_document("http://docs.google.com/document/d/abc123/edit", fetch=fetch)
    assert doc.plain_text() == "x"


# --- normalization: load_document always returns doc.normalized() -------------------


def test_returned_document_is_normalized_empty_list_item_loses_its_list_attributes(tmp_path):
    # An empty <li> is a state a real Google Doc can never be in (Enter on an
    # empty list item ends the list) -- load_document must hand back the
    # normalized form, not the importer's raw parse.
    path = tmp_path / "essay.html"
    path.write_text('<ul class="lst-kix_a-0"><li></li></ul>', encoding="utf-8")

    doc, _ = load_document(str(path))

    assert doc.paragraphs == (Paragraph(),)
    assert doc.paragraphs[0].list_kind is None
    assert doc.paragraphs[0].list_level == 0


def test_returned_document_has_adjacent_runs_already_merged(tmp_path):
    path = tmp_path / "essay.html"
    path.write_text(
        '<style>.c1{font-weight:700}</style><p><span class="c1">ab</span><span class="c1">cd</span></p>',
        encoding="utf-8",
    )

    doc, _ = load_document(str(path))

    assert len(doc.paragraphs[0].runs) == 1
    assert doc.paragraphs[0].runs[0].text == "abcd"
