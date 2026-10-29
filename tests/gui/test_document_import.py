"""Importing a document off the GUI thread, and tidying where it comes from."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from auto_essay_typer.document import Document, ImportReport, Paragraph, ParagraphStyle, Run, TextStyle
from auto_essay_typer.gui import document_import
from auto_essay_typer.gui.document_import import (
    FILE_DIALOG_FILTER,
    SUPPORTED_SUFFIXES,
    DocumentImporter,
    ImportOutcome,
    clean_source,
    default_choose_file,
    describe_source,
    strip_leading_bom,
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


# --- the error box ------------------------------------------------------------------------------------------------


def test_the_error_box_shows_the_message_as_written_and_is_not_left_behind(qtbot, monkeypatch):
    shown = {}

    def fake_exec(box):
        shown.update(text=box.text(), format=box.textFormat())
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    parent = QWidget()
    qtbot.addWidget(parent)
    message = "Could not read <b>that</b> link: https://example.com/?a=1&b=2"

    document_import.default_show_error(parent, "Couldn't import the document", message)

    assert shown == {"text": message, "format": Qt.TextFormat.PlainText}
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert parent.findChildren(QMessageBox) == [], "the page does not keep a hidden box for every failure"


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


def test_an_unexpected_failure_is_logged_with_its_traceback_for_whoever_has_to_fix_it(qtbot, caplog):
    def broken_loader(source):
        raise KeyError("boom")

    with caplog.at_level("ERROR", logger=document_import.__name__):
        outcome = _import(qtbot, DocumentImporter(broken_loader), "essay.docx")

    assert outcome.ok is False
    (record,) = [r for r in caplog.records if r.name == document_import.__name__]
    assert "essay.docx" in record.getMessage()
    assert record.exc_info and record.exc_info[0] is KeyError


def test_the_advice_after_an_unexpected_failure_fits_where_the_document_came_from(qtbot):
    def broken_loader(source):
        raise ValueError("bad")

    from_a_link = _import(qtbot, DocumentImporter(broken_loader), "https://docs.google.com/document/d/abc/edit")
    from_a_file = _import(qtbot, DocumentImporter(broken_loader), "/Users/me/essay.docx")

    assert "downloading it as a Word (.docx) file" in from_a_link.error
    assert "downloading" not in from_a_file.error
    assert "saving a copy as a Word (.docx) file" in from_a_file.error


def test_a_loader_that_leaves_in_an_unusual_way_still_ends_the_import():
    """The page stays busy until it hears an outcome, so there must always be one."""
    import queue

    results: queue.SimpleQueue = queue.SimpleQueue()

    def quitting_loader(source):
        raise SystemExit(3)

    with pytest.raises(SystemExit):  # not swallowed ...
        document_import._load_in_background(quitting_loader, "essay.docx", results)

    outcome = results.get_nowait()  # ... but not left unanswered either
    assert outcome.ok is False
    assert "the import was interrupted" in outcome.error


# --- the byte-order mark --------------------------------------------------------------------------------------------------


def test_a_byte_order_mark_at_the_start_of_the_text_is_removed():
    document = Document((Paragraph((Run("\ufeffFirst words"),)), Paragraph((Run("second"),))))
    cleaned = strip_leading_bom(document)
    assert cleaned.plain_text() == "First words\nsecond"
    assert cleaned.paragraphs[1] is document.paragraphs[1]


def test_a_document_with_no_byte_order_mark_comes_back_as_the_same_object():
    document = Document((Paragraph((Run("plain"),)),))
    assert strip_leading_bom(document) is document
    assert strip_leading_bom(Document(())) == Document(())
    assert strip_leading_bom(Document((Paragraph(),))) == Document((Paragraph(),))


def test_a_run_that_was_only_a_byte_order_mark_disappears_and_the_rest_keeps_its_styles():
    bold = frozenset({TextStyle.BOLD})
    document = Document(
        (Paragraph((Run("\ufeff"), Run("bold words", bold)), style=ParagraphStyle.HEADING_1),)
    )
    cleaned = strip_leading_bom(document)
    (paragraph,) = cleaned.paragraphs
    assert paragraph.runs == (Run("bold words", bold),)
    assert paragraph.style is ParagraphStyle.HEADING_1


def test_a_lone_byte_order_mark_leaves_an_empty_paragraph():
    cleaned = strip_leading_bom(Document((Paragraph((Run("\ufeff"),)), Paragraph((Run("next"),)))))
    assert cleaned.plain_text() == "\nnext"


def test_only_a_mark_at_the_very_start_is_removed():
    # Plain mode's normalize_input_text drops one leading mark and nothing else; so does this.
    document = Document((Paragraph((Run("a\ufeffb"),)), Paragraph((Run("\ufeffsecond"),))))
    assert strip_leading_bom(document) is document
    twice = Document((Paragraph((Run("\ufeff\ufeffx"),)),))
    assert strip_leading_bom(twice).plain_text() == "\ufeffx"


def test_a_text_file_saved_with_a_byte_order_mark_and_windows_line_endings_imports_clean(qtbot, tmp_path):
    from auto_essay_typer import platforms

    path = tmp_path / "notepad.txt"
    raw = "\ufeffFirst line\r\nSecond line\r\n"
    path.write_bytes(raw.encode("utf-8"))

    outcome = _import(qtbot, DocumentImporter(), str(path))

    assert outcome.ok
    # Exactly what plain mode would type for the same text pasted into the box.
    assert outcome.document.plain_text() == platforms.normalize_input_text(raw)
    assert "\ufeff" not in outcome.document.plain_text()
