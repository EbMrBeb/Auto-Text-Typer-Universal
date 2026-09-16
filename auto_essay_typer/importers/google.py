"""Fetch a Google Docs / Drive source and turn it into this project's
``Document`` model.

Google Docs has no public API this project is allowed to depend on (the
no-new-dependencies rule rules out the official client libraries, and OAuth
is out of scope for a tool that just wants to read a document someone
pasted a link to), so this module works entirely off the URL shapes Google
itself exposes: a document's HTML export, a published doc's public page, or
a Drive file download. All three return HTML or a downloadable file over
plain HTTP(S), fetchable with nothing but ``urllib``.

Every network call is indirected through a ``fetch`` callable so tests never
touch the network: production code gets ``default_fetch`` for free, tests
inject a fake that returns canned ``FetchResult``s or raises the ``urllib``
errors a real fetch could raise.
"""

from __future__ import annotations

import re
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from auto_essay_typer.document import Document
from auto_essay_typer.importers import gdocs_html
from auto_essay_typer.importers.docx import parse_docx
from auto_essay_typer.net import create_ssl_context

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_TIMEOUT_SECONDS = 20.0

_ACCESS_DENIED_MESSAGE = (
    'Access denied: set sharing to "Anyone with the link can view", or download the '
    "document (File > Download > Microsoft Word (.docx)) and use that file instead."
)

_CERT_VERIFICATION_MESSAGE = (
    "the secure connection to Google could not be verified; this usually means a "
    "network filter or proxy is intercepting HTTPS; try another network, or download "
    "the document as .docx and use the file instead ({reason})"
)


class DocumentImportError(Exception):
    """Base class for every user-facing document-import failure.

    Every subclass's message is written to be printed directly to a user,
    never a traceback -- importing a document a person just pasted a link to
    is exactly the kind of operation that fails for mundane, expected
    reasons (wrong sharing settings, a typo'd link, a flaky connection).
    """


class AccessDeniedError(DocumentImportError):
    """The document is not shared with "Anyone with the link can view"."""


class NotFoundError(DocumentImportError):
    """Google returned 404: the id in the link does not exist (or is wrong)."""


class UnsupportedSourceError(DocumentImportError):
    """The source is not a kind of document this tool can import."""


class FetchError(DocumentImportError):
    """A network failure (timeout, DNS, connection reset, ...) while fetching."""


@dataclass(frozen=True, slots=True)
class GoogleRef:
    """A parsed Google Docs/Drive URL: what kind of thing it points to, and its id."""

    kind: str  # "doc" | "published_doc" | "drive_file"
    file_id: str


@dataclass(frozen=True, slots=True)
class FetchResult:
    """The raw response of fetching one URL, plus what it takes to interpret it.

    ``final_url`` is the URL after following redirects -- Google redirects an
    access-denied request to its sign-in page, and that redirect target is
    often the clearest signal an importer gets that access was denied.
    """

    data: bytes
    content_type: str
    final_url: str


FetchFn = Callable[[str], FetchResult]


def default_fetch(url: str) -> FetchResult:
    """Fetch ``url`` over the network: a 20s timeout, a browser-like User-Agent,
    and a TLS certificate verified against the OS trust store (see ``net.py``).

    The User-Agent matters because Google serves a different (sign-in-only)
    response to requests that look like a bot to some of these endpoints.
    Raises the underlying ``urllib.error`` (and ``ssl``) exceptions uncaught --
    callers of this module go through ``load_from_google``, which maps them to
    the project's own error hierarchy; this function is deliberately a thin,
    literal "go fetch this URL" with no interpretation of its own.
    """
    request = Request(url, headers={"User-Agent": _USER_AGENT})
    with urlopen(
        request, timeout=_TIMEOUT_SECONDS, context=create_ssl_context()
    ) as response:  # noqa: S310 (stdlib-only by design)
        data = response.read()
        content_type = response.headers.get("Content-Type", "")
        final_url = response.geturl()
    return FetchResult(data=data, content_type=content_type, final_url=final_url)


# --- URL parsing -------------------------------------------------------------

# The published-doc form (".../document/d/e/<id>/pub") is checked before the
# general doc form below, because "e" would otherwise itself be captured as
# a (wrong) file id by the general pattern.
_PUBLISHED_DOC_RE = re.compile(r"^/document/d/e/(?P<id>[^/]+)/pub")
_DOC_RE = re.compile(r"^/document/(?:u/\d+/)?d/(?P<id>[^/]+)")
_DRIVE_FILE_RE = re.compile(r"^/file/d/(?P<id>[^/]+)")

# Other docs.google.com products this tool cannot import (no keyboard-typing
# equivalent of a spreadsheet grid, a slide canvas, or a form's field list).
_UNSUPPORTED_DOCS_PRODUCTS: tuple[tuple[str, str], ...] = (
    ("/spreadsheets/", "spreadsheet"),
    ("/presentation/", "presentation"),
    ("/forms/", "form"),
)


def parse_google_url(url: str) -> GoogleRef:
    """Classify a Google Docs/Drive URL into a :class:`GoogleRef`.

    Raises :class:`UnsupportedSourceError` (naming what the link actually
    points to, when known) for anything this tool cannot import: Sheets,
    Slides, Forms, an unrecognised path shape, or a non-Google host.
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path

    if host == "docs.google.com":
        m = _PUBLISHED_DOC_RE.match(path)
        if m:
            return GoogleRef(kind="published_doc", file_id=m.group("id"))
        m = _DOC_RE.match(path)
        if m:
            return GoogleRef(kind="doc", file_id=m.group("id"))
        for prefix, label in _UNSUPPORTED_DOCS_PRODUCTS:
            if path.startswith(prefix):
                raise UnsupportedSourceError(
                    f"this link points to a Google {label}, which this tool cannot import "
                    "(only Google Docs documents are supported)"
                )
        raise UnsupportedSourceError(f"unrecognised docs.google.com link: {url}")

    if host == "drive.google.com":
        m = _DRIVE_FILE_RE.match(path)
        if m:
            return GoogleRef(kind="drive_file", file_id=m.group("id"))
        if path in ("/open", "/uc"):
            file_ids = parse_qs(parsed.query).get("id")
            if file_ids:
                return GoogleRef(kind="drive_file", file_id=file_ids[0])
        raise UnsupportedSourceError(f"unrecognised drive.google.com link: {url}")

    raise UnsupportedSourceError(f"not a Google Docs or Drive link: {url}")


def _fetch_url_for(ref: GoogleRef, original_url: str) -> str:
    if ref.kind == "doc":
        return f"https://docs.google.com/document/d/{ref.file_id}/export?format=html"
    if ref.kind == "published_doc":
        return original_url
    if ref.kind == "drive_file":
        return f"https://drive.google.com/uc?export=download&id={ref.file_id}"
    raise AssertionError(f"unhandled GoogleRef kind {ref.kind!r}")  # pragma: no cover


def _fetch_or_raise(fetch: FetchFn, url: str) -> FetchResult:
    """Call ``fetch(url)``, translating ``urllib``/``ssl`` failures to this module's errors.

    A certificate-verification failure is deliberately distinguished from
    every other connection problem: it is not a "Google is unreachable" or
    "flaky network" situation a retry would fix, but almost always a
    network filter or proxy intercepting HTTPS, so it gets its own
    message pointing at the actual cause and a way to route around it.
    ``fetch`` can surface this either as a bare ``ssl.SSLCertVerificationError``
    or (the shape ``urlopen`` itself produces) a ``URLError`` whose ``reason``
    is one.
    """
    try:
        return fetch(url)
    except HTTPError as exc:
        if exc.code == 404:
            raise NotFoundError(f"not found - check the link ({url})") from exc
        raise FetchError(f"Google returned an error fetching the document: HTTP {exc.code} {exc.reason}") from exc
    except ssl.SSLCertVerificationError as exc:
        raise FetchError(_CERT_VERIFICATION_MESSAGE.format(reason=exc)) from exc
    except URLError as exc:
        if isinstance(exc.reason, ssl.SSLCertVerificationError):
            raise FetchError(_CERT_VERIFICATION_MESSAGE.format(reason=exc.reason)) from exc
        raise FetchError(f"could not reach Google: {exc.reason}") from exc


_ACCESS_DENIED_MARKERS = (
    "you need access",
    "request access",
    "sign in - google accounts",
    "the document owner has not granted",
)


def _looks_like_signin_or_request_access_page(html_text: str) -> bool:
    lowered = html_text.lower()
    return any(marker in lowered for marker in _ACCESS_DENIED_MARKERS)


def _raise_if_access_denied(html_text: str, final_url: str) -> None:
    if urlparse(final_url).netloc.lower() == "accounts.google.com":
        raise AccessDeniedError(_ACCESS_DENIED_MESSAGE)
    if _looks_like_signin_or_request_access_page(html_text):
        raise AccessDeniedError(_ACCESS_DENIED_MESSAGE)


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


def _looks_like_zip(data: bytes) -> bool:
    return data[:4] in _ZIP_MAGIC


def _is_docx_zip(data: bytes) -> bool:
    import zipfile
    from io import BytesIO

    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            return "word/document.xml" in zf.namelist()
    except zipfile.BadZipFile:
        return False


def _load_drive_file(result: FetchResult) -> tuple[Document, tuple[str, ...]]:
    """A Drive "uploaded file" is sniffed by its bytes, not its declared type.

    Drive's download endpoint reports a generic content type for many file
    kinds, so the only reliable signal is the bytes themselves: a zip
    containing ``word/document.xml`` is a .docx; anything else that isn't a
    zip is assumed to be an HTML page (a sign-in page, or a Drive preview
    page for a file type this tool cannot type out).
    """
    if _looks_like_zip(result.data):
        if _is_docx_zip(result.data):
            return parse_docx(result.data)
        raise UnsupportedSourceError(
            "this Google Drive file is a zip archive but not a Word (.docx) document"
        )

    html_text = _decode(result.data)
    _raise_if_access_denied(html_text, result.final_url)
    raise UnsupportedSourceError(
        "this Google Drive file is not a Word (.docx) document this tool can import "
        "(download it as .docx, or share a Google Doc link instead)"
    )


def load_from_google(url: str, *, fetch: FetchFn | None = None) -> tuple[Document, tuple[str, ...]]:
    """Fetch and parse a Google Docs/Drive URL into a ``Document`` plus warnings.

    The returned Document is not yet normalized or keymap-adapted -- see
    ``importers.load_document``, the public entry point that does both after
    calling this for a Google source.
    """
    if fetch is None:
        fetch = default_fetch

    ref = parse_google_url(url)
    fetch_url = _fetch_url_for(ref, url)
    result = _fetch_or_raise(fetch, fetch_url)

    if ref.kind == "drive_file":
        return _load_drive_file(result)

    html_text = _decode(result.data)
    _raise_if_access_denied(html_text, result.final_url)
    return gdocs_html.parse_gdocs_html(html_text)
