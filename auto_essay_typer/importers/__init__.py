"""Importers: turn an external document into this project's ``Document`` model.

A source is either a Google Docs / Drive URL, or a local file (.docx, .html,
.htm, .zip, .txt, .md). ``load_document`` is the single dispatch point for
both; see it for the full contract. Every other name here is re-exported
from the submodule that owns it so callers only need to remember one import
path -- ``auto_essay_typer.importers`` -- rather than which of google.py /
gdocs_html.py / docx.py / plain.py happens to define a given error class.
"""

from __future__ import annotations

from pathlib import Path

from auto_essay_typer.document import Document, ImportReport
from auto_essay_typer.importers.docx import parse_docx
from auto_essay_typer.importers.gdocs_html import parse_gdocs_html, parse_gdocs_html_zip
from auto_essay_typer.importers.google import (
    AccessDeniedError,
    DocumentImportError,
    FetchError,
    FetchFn,
    FetchResult,
    GoogleRef,
    NotFoundError,
    UnsupportedSourceError,
    default_fetch,
    load_from_google,
    parse_google_url,
)
from auto_essay_typer.importers.notes import NoteKind
from auto_essay_typer.importers.plain import load_plain_text

__all__ = [
    "load_document",
    "Document",
    "ImportReport",
    "NoteKind",
    "DocumentImportError",
    "AccessDeniedError",
    "NotFoundError",
    "UnsupportedSourceError",
    "FetchError",
    "GoogleRef",
    "FetchResult",
    "FetchFn",
    "default_fetch",
    "parse_google_url",
    "load_from_google",
    "parse_docx",
    "parse_gdocs_html",
    "parse_gdocs_html_zip",
    "load_plain_text",
]

# .htm/.html: a raw Docs HTML export. .zip: the Docs "Web page (.html,
# zipped)" download. .txt/.md: plain text, no formatting to recover.
_LOADERS_BY_EXTENSION = frozenset({".docx", ".html", ".htm", ".zip", ".txt", ".md"})


def _looks_like_url(source: str) -> bool:
    return source.startswith(("http://", "https://"))


def load_document(source: str, *, fetch: FetchFn | None = None) -> tuple[Document, ImportReport]:
    """Load ``source`` into a normalized ``Document`` plus an ``ImportReport``.

    ``source`` is either an ``http(s)://`` URL (a Google Docs or Drive
    link -- dispatched to ``google.load_from_google``) or a local file path,
    dispatched by extension: ``.docx``, ``.html``/``.htm``, ``.zip`` (the
    Docs "Web page, zipped" download), or ``.txt``/``.md`` (plain text).

    The returned Document is normalized but *not* yet keymap-adapted --
    ``keymaps.adapt_document`` is the caller's next step before handing it to
    ``planner.plan_document``, since a Title/Subtitle paragraph typed on a
    keymap without those shortcuts needs to be rewritten first.

    Every failure raises a ``DocumentImportError`` subclass with a message
    meant to be shown to the user as-is, never a traceback: a missing file,
    an unrecognised extension, or (via ``load_from_google``) a bad link, a
    private document, or a network failure.
    """
    if _looks_like_url(source):
        doc, warnings = load_from_google(source, fetch=fetch)
        return doc.normalized(), ImportReport(source=source, warnings=warnings)

    path = Path(source)
    suffix = path.suffix.lower()

    if suffix not in _LOADERS_BY_EXTENSION:
        supported = ", ".join(sorted(_LOADERS_BY_EXTENSION))
        raise UnsupportedSourceError(
            f"unsupported file type {suffix or '(no extension)'!r} for {source!r}; "
            f"supported types are {supported}, or a Google Docs/Drive link"
        )

    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise DocumentImportError(f"file not found: {source}") from exc
    except OSError as exc:
        raise DocumentImportError(f"could not read {source}: {exc}") from exc

    if suffix == ".docx":
        try:
            doc, warnings = parse_docx(data)
        except ValueError as exc:
            raise DocumentImportError(f"could not read {source} as a Word document: {exc}") from exc
    elif suffix in (".html", ".htm"):
        doc, warnings = parse_gdocs_html(data.decode("utf-8", errors="replace"))
    elif suffix == ".zip":
        try:
            doc, warnings = parse_gdocs_html_zip(data)
        except ValueError as exc:
            raise DocumentImportError(
                f"could not read {source} as a Google Docs web-page export: {exc}"
            ) from exc
    else:  # .txt / .md
        doc, warnings = load_plain_text(data), ()

    return doc.normalized(), ImportReport(source=source, warnings=warnings)
