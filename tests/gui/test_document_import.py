"""Importing a document off the GUI thread, and tidying where it comes from."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QFileDialog

from auto_essay_typer.document import Document, ImportReport, Paragraph, Run
from auto_essay_typer.gui import document_import
from auto_essay_typer.gui.document_import import (
    FILE_DIALOG_FILTER,
    SUPPORTED_SUFFIXES,
    DocumentImporter,
    ImportOutcome,
    clean_source,
    default_choose_file,
    describe_source,
)
from auto_essay_typer.importers import (
    AccessDeniedError,
    DocumentImportError,
    FetchError,
    NotFoundError,
    UnsupportedSourceError,
    load_document,
)

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "gdocs_export_sample.html"
DOCUMENT = Document((Paragraph((Run("hello"),)),))
REPORT = ImportReport(source="x", warnings=("1 image not reproduced (dropped)",))


def _import(qtbot, importer: DocumentImporter, source: str) -> ImportOutcome:
    with qtbot.waitSignal(importer.finished, timeout=10000) as blocker:
        assert importer.start(source) is True
    (outcome,) = blocker.args
    return outcome


# --- clean_source ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("  https://docs.google.com/document/d/abc/edit  ", "https://docs.google.com/document/d/abc/edit"),
        ("\n/Users/me/essay.docx\n", "/Users/me/essay.docx"),
        ('"/Users/me/My Essay.docx"', "/Users/me/My Essay.docx"),
        ("'/Users/me/My Essay.docx'", "/Users/me/My Essay.docx"),
        ("“/Users/me/My Essay.docx”", "/Users/me/My Essay.docx"),
        ("file:///Users/me/My%20Essay.docx", "/Users/me/My Essay.docx"),
        ("docs.google.com/document/d/abc/edit", "https://docs.google.com/document/d/abc/edit"),
        ("drive.google.com/file/d/abc/view", "https://drive.google.com/file/d/abc/view"),
        ("Essay.docx", "Essay.docx"),
        ("", ""),
        ("   ", ""),
        ('"', '"'),
    ],
)
def test_clean_source_tidies_what_a_person_pasted(typed, expected):
    assert clean_source(typed) == expected


def test_clean_source_expands_the_home_folder():
    assert clean_source("~/Documents/essay.docx") == str(Path.home() / "Documents" / "essay.docx")


def test_clean_source_leaves_other_links_for_the_importer_to_reject():
    assert clean_source("https://example.com/page") == "https://example.com/page"


# --- describe_source -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("/Users/me/Documents/My Essay.docx", "My Essay.docx"),
        ("essay.html", "essay.html"),
        ("https://docs.google.com/document/d/abc/edit", "a Google Docs link"),
        ("https://drive.google.com/file/d/abc/view", "a Google Drive link"),
        ("https://example.com/x", "a link"),
        ("", ""),
    ],
)
def test_describe_source_names_a_file_or_the_kind_of_link(source, expected):
    assert describe_source(source) == expected


# --- the file dialog ----------------------------------------------------------------------------------------------


def test_the_dialog_filter_lists_exactly_the_importable_file_types():
    assert SUPPORTED_SUFFIXES == (".docx", ".html", ".htm", ".zip", ".txt", ".md")
    assert FILE_DIALOG_FILTER == "Documents (*.docx *.html *.htm *.zip *.txt *.md)"


def test_every_suffix_in_the_filter_is_one_the_importer_accepts(tmp_path):
    for suffix in SUPPORTED_SUFFIXES:
        try:
            load_document(str(tmp_path / f"missing{suffix}"))
        except DocumentImportError as exc:
            assert "unsupported file type" not in str(exc), suffix


def test_choosing_a_file_returns_its_path_and_cancelling_returns_none(qtbot, monkeypatch):
    calls = []

    def fake_dialog(parent, caption, directory, name_filter):
        calls.append((directory, name_filter))
        return ("/chosen/essay.docx", name_filter)

    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(fake_dialog))
    assert default_choose_file(None, "/start") == "/chosen/essay.docx"
    assert calls == [("/start", FILE_DIALOG_FILTER)]

    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", "")))
    assert default_choose_file(None, "/start") is None


def test_the_dialog_opens_in_documents_or_failing_that_the_home_folder(monkeypatch):
    assert document_import.default_start_dir()
    monkeypatch.setattr(
        document_import.QStandardPaths, "writableLocation", staticmethod(lambda _location: "")
    )
    assert document_import.default_start_dir() == str(Path.home())


# --- the importer --------------------------------------------------------------------------------------------------


def test_a_successful_import_reports_the_document_and_its_report(qtbot):
    importer = DocumentImporter(lambda source: (DOCUMENT, REPORT))

    outcome = _import(qtbot, importer, "essay.docx")

    assert outcome.ok is True
    assert outcome.source == "essay.docx"
    assert outcome.document is DOCUMENT
    assert outcome.report is REPORT
    assert outcome.error is None


def test_the_real_loader_reads_the_sample_html_through_the_importer(qtbot):
    importer = DocumentImporter()
    outcome = _import(qtbot, importer, str(FIXTURE))
    assert outcome.ok
    assert outcome.document.paragraphs
    assert outcome.report.warnings


def test_the_loader_runs_off_the_gui_thread_so_the_window_never_freezes(qtbot):
    ran_on: list[threading.Thread] = []
    release = threading.Event()

    def loader(source):
        ran_on.append(threading.current_thread())
        release.wait(5)
        return DOCUMENT, REPORT

    importer = DocumentImporter(loader)
    busy: list[bool] = []
    importer.busyChanged.connect(busy.append)

    with qtbot.waitSignal(importer.finished, timeout=10000):
        assert importer.start("slow") is True
        # start() came back while the loader is still blocked: the GUI thread was not used.
        assert importer.is_busy is True
        assert busy == [True]
        release.set()

    assert ran_on and ran_on[0] is not threading.main_thread()
    assert importer.is_busy is False
    assert busy == [True, False]


def test_a_second_import_is_refused_while_one_is_running(qtbot):
    release = threading.Event()
    sources: list[str] = []

    def loader(source):
        sources.append(source)
        release.wait(5)
        return DOCUMENT, REPORT

    importer = DocumentImporter(loader)
    with qtbot.waitSignal(importer.finished, timeout=10000):
        assert importer.start("first") is True
        assert importer.start("second") is False
        release.set()

    assert sources == ["first"]


def test_the_importer_can_be_used_again_after_it_finishes(qtbot):
    importer = DocumentImporter(lambda source: (DOCUMENT, REPORT))
    assert _import(qtbot, importer, "one").ok
    assert _import(qtbot, importer, "two").ok


def test_busy_state_is_reported_before_the_result_so_a_page_is_never_half_updated(qtbot):
    importer = DocumentImporter(lambda source: (DOCUMENT, REPORT))
    order: list[str] = []
    importer.busyChanged.connect(lambda busy: order.append(f"busy={busy}"))
    importer.finished.connect(lambda outcome: order.append("finished"))

    _import(qtbot, importer, "x")

    assert order == ["busy=True", "busy=False", "finished"]


@pytest.mark.parametrize(
    "error",
    [
        AccessDeniedError(
            'Access denied: set sharing to "Anyone with the link can view", or download the '
            "document (File > Download > Microsoft Word (.docx)) and use that file instead."
        ),
        NotFoundError("not found - check the link (https://docs.google.com/x)"),
        UnsupportedSourceError("unsupported file type '.rtf'"),
        FetchError("could not reach Google: timed out"),
        DocumentImportError("file not found: /nope.docx"),
    ],
    ids=lambda error: type(error).__name__,
)
def test_each_kind_of_import_error_arrives_with_its_own_message_untouched(qtbot, error):
    def loader(source):
        raise error

    outcome = _import(qtbot, DocumentImporter(loader), "x")

    assert outcome.ok is False
    assert outcome.error == str(error)
    assert outcome.document is None and outcome.report is None


def test_an_unexpected_failure_is_put_in_plain_words_never_as_a_traceback(qtbot):
    def loader(source):
        raise KeyError("boom")

    outcome = _import(qtbot, DocumentImporter(loader), "x")

    assert outcome.ok is False
    assert "Something went wrong while reading that document" in outcome.error
    assert "Traceback" not in outcome.error
    assert ".docx" in outcome.error


def test_an_unexpected_failure_with_no_message_still_names_what_happened(qtbot):
    def loader(source):
        raise ValueError()

    outcome = _import(qtbot, DocumentImporter(loader), "x")
    assert "ValueError" in outcome.error


def test_the_real_importers_missing_file_message_comes_through(qtbot, tmp_path):
    outcome = _import(qtbot, DocumentImporter(), str(tmp_path / "gone.docx"))
    assert outcome.ok is False
    assert "file not found" in outcome.error


def test_a_loader_that_finishes_after_the_importer_is_gone_does_not_raise(qtbot, monkeypatch):
    """A window closed mid-import must not leave an exception behind on the worker thread."""
    import shiboken6

    release = threading.Event()
    created: list[threading.Thread] = []

    class RecordingThread(threading.Thread):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(document_import, "threading", SimpleNamespace(Thread=RecordingThread))
    importer = DocumentImporter(lambda source: (release.wait(5), (DOCUMENT, REPORT))[1])
    errors: list[BaseException] = []
    monkeypatch.setattr(threading, "excepthook", lambda args: errors.append(args.exc_value))

    importer.start("x")
    shiboken6.delete(importer)  # the page was destroyed while the loader was still running
    release.set()
    created[0].join(5)

    assert not created[0].is_alive()
    assert errors == []
