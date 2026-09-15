"""Tests for auto_essay_typer.importers.google: URL parsing, fetch/access
classification, and the drive-file-as-docx path. No test here ever touches
the network -- every fetch is injected.
"""

from urllib.error import URLError

import pytest

from auto_essay_typer.document import Document
from auto_essay_typer.importers.google import (
    AccessDeniedError,
    FetchError,
    FetchResult,
    GoogleRef,
    NotFoundError,
    UnsupportedSourceError,
    load_from_google,
    parse_google_url,
)

# --- parse_google_url: recognised forms --------------------------------------


def test_parse_doc_edit_url():
    ref = parse_google_url("https://docs.google.com/document/d/abc123/edit")
    assert ref == GoogleRef(kind="doc", file_id="abc123")


def test_parse_doc_edit_url_with_query_and_fragment():
    ref = parse_google_url(
        "https://docs.google.com/document/d/abc123/edit?usp=sharing#heading=h.xyz"
    )
    assert ref == GoogleRef(kind="doc", file_id="abc123")


def test_parse_doc_url_with_user_index_infix():
    ref = parse_google_url("https://docs.google.com/document/u/1/d/abc123/edit")
    assert ref == GoogleRef(kind="doc", file_id="abc123")


def test_parse_published_doc_url():
    ref = parse_google_url("https://docs.google.com/document/d/e/pub-id-999/pub")
    assert ref == GoogleRef(kind="published_doc", file_id="pub-id-999")


def test_parse_drive_file_view_url():
    ref = parse_google_url("https://drive.google.com/file/d/driveid1/view")
    assert ref == GoogleRef(kind="drive_file", file_id="driveid1")


def test_parse_drive_open_url():
    ref = parse_google_url("https://drive.google.com/open?id=driveid2")
    assert ref == GoogleRef(kind="drive_file", file_id="driveid2")


def test_parse_drive_uc_url():
    ref = parse_google_url("https://drive.google.com/uc?id=driveid3")
    assert ref == GoogleRef(kind="drive_file", file_id="driveid3")


# --- parse_google_url: unsupported forms -------------------------------------


def test_parse_spreadsheet_url_names_spreadsheet():
    with pytest.raises(UnsupportedSourceError, match="(?i)spreadsheet"):
        parse_google_url("https://docs.google.com/spreadsheets/d/abc123/edit")


def test_parse_presentation_url_names_presentation():
    with pytest.raises(UnsupportedSourceError, match="(?i)presentation"):
        parse_google_url("https://docs.google.com/presentation/d/abc123/edit")


def test_parse_forms_url_names_form():
    with pytest.raises(UnsupportedSourceError, match="(?i)form"):
        parse_google_url("https://docs.google.com/forms/d/abc123/edit")


def test_parse_unrelated_url_is_unsupported():
    with pytest.raises(UnsupportedSourceError):
        parse_google_url("https://example.com/document/d/abc123/edit")


def test_parse_unrecognised_docs_path_is_unsupported():
    with pytest.raises(UnsupportedSourceError):
        parse_google_url("https://docs.google.com/somethingelse/abc123")


def test_parse_unrecognised_drive_path_is_unsupported():
    with pytest.raises(UnsupportedSourceError):
        parse_google_url("https://drive.google.com/somethingelse/abc123")


# --- load_from_google: fetch URL construction --------------------------------

_MINIMAL_HTML = "<html><body><p>hello</p></body></html>"


def _html_fetch_recording(calls: list[str], html: str = _MINIMAL_HTML):
    def fetch(url: str) -> FetchResult:
        calls.append(url)
        return FetchResult(data=html.encode("utf-8"), content_type="text/html", final_url=url)

    return fetch


def test_doc_kind_fetches_export_html_url():
    calls: list[str] = []
    load_from_google(
        "https://docs.google.com/document/d/abc123/edit", fetch=_html_fetch_recording(calls)
    )
    assert calls == ["https://docs.google.com/document/d/abc123/export?format=html"]


def test_published_doc_kind_fetches_the_pub_url_itself():
    calls: list[str] = []
    pub_url = "https://docs.google.com/document/d/e/pub-id-999/pub"
    load_from_google(pub_url, fetch=_html_fetch_recording(calls))
    assert calls == [pub_url]


def test_drive_file_kind_fetches_uc_export_download_url():
    calls: list[str] = []

    def fetch(url: str) -> FetchResult:
        calls.append(url)
        return FetchResult(data=_docx_zip_bytes(), content_type="application/zip", final_url=url)

    load_from_google("https://drive.google.com/file/d/driveid1/view", fetch=fetch)
    assert calls == ["https://drive.google.com/uc?export=download&id=driveid1"]


# --- load_from_google: successful parse paths --------------------------------


def test_doc_html_is_parsed_into_a_document():
    doc, warnings = load_from_google(
        "https://docs.google.com/document/d/abc123/edit",
        fetch=_html_fetch_recording([]),
    )
    assert isinstance(doc, Document)
    assert doc.plain_text() == "hello"
    assert warnings == ()


def _docx_zip_bytes() -> bytes:
    """A minimal but valid .docx: just enough for docx.parse_docx to accept it."""
    import zipfile
    from io import BytesIO

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
            'package/2006/content-types"></Types>',
        )
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>hi from docx</w:t></w:r></w:p></w:body>"
            "</w:document>",
        )
    return buf.getvalue()


def test_drive_file_returning_docx_bytes_gets_parsed_as_docx():
    def fetch(url: str) -> FetchResult:
        return FetchResult(
            data=_docx_zip_bytes(), content_type="application/zip", final_url=url
        )

    doc, warnings = load_from_google(
        "https://drive.google.com/file/d/driveid1/view", fetch=fetch
    )
    assert doc.plain_text() == "hi from docx"
    assert warnings == ()


def test_drive_file_returning_unrecognised_html_is_unsupported():
    def fetch(url: str) -> FetchResult:
        return FetchResult(
            data=b"<html><body>some random Drive preview page</body></html>",
            content_type="text/html",
            final_url=url,
        )

    with pytest.raises(UnsupportedSourceError):
        load_from_google("https://drive.google.com/file/d/driveid1/view", fetch=fetch)


# --- load_from_google: access denied ------------------------------------------

_SIGNIN_HTML = """
<html><head><title>Sign in - Google Accounts</title></head>
<body><h1>You need access</h1><p>Ask for access, or switch to an account with access.</p></body>
</html>
"""


def test_access_denied_when_html_looks_like_a_signin_page():
    def fetch(url: str) -> FetchResult:
        return FetchResult(data=_SIGNIN_HTML.encode("utf-8"), content_type="text/html", final_url=url)

    with pytest.raises(AccessDeniedError) as excinfo:
        load_from_google("https://docs.google.com/document/d/abc123/edit", fetch=fetch)
    message = str(excinfo.value)
    assert "Anyone with the link can view" in message
    assert "File > Download > Microsoft Word (.docx)" in message


def test_access_denied_when_final_url_is_accounts_google_com():
    def fetch(url: str) -> FetchResult:
        return FetchResult(
            data=b"<html><body>redirected</body></html>",
            content_type="text/html",
            final_url="https://accounts.google.com/ServiceLogin?service=wise",
        )

    with pytest.raises(AccessDeniedError):
        load_from_google("https://docs.google.com/document/d/abc123/edit", fetch=fetch)


# --- load_from_google: HTTP / network error mapping ---------------------------


def test_http_404_raises_not_found_error():
    from urllib.error import HTTPError

    def fetch(url: str) -> FetchResult:
        raise HTTPError(url, 404, "Not Found", hdrs=None, fp=None)  # type: ignore[arg-type]

    with pytest.raises(NotFoundError, match="(?i)not found"):
        load_from_google("https://docs.google.com/document/d/doesnotexist/edit", fetch=fetch)


def test_url_error_raises_fetch_error():
    def fetch(url: str) -> FetchResult:
        raise URLError("nowhere to be found")

    with pytest.raises(FetchError):
        load_from_google("https://docs.google.com/document/d/abc123/edit", fetch=fetch)


def test_other_http_error_raises_fetch_error():
    from urllib.error import HTTPError

    def fetch(url: str) -> FetchResult:
        raise HTTPError(url, 500, "Server Error", hdrs=None, fp=None)  # type: ignore[arg-type]

    with pytest.raises(FetchError):
        load_from_google("https://docs.google.com/document/d/abc123/edit", fetch=fetch)


# --- GoogleRef / FetchResult are plain frozen data -----------------------------


def test_google_ref_is_frozen():
    ref = GoogleRef(kind="doc", file_id="x")
    with pytest.raises(Exception):
        ref.kind = "drive_file"


def test_fetch_result_holds_its_fields():
    result = FetchResult(data=b"abc", content_type="text/plain", final_url="https://x/y")
    assert result.data == b"abc"
    assert result.content_type == "text/plain"
    assert result.final_url == "https://x/y"
