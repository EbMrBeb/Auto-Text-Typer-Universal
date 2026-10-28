"""Importing a document without freezing the window.

``importers.load_document`` may download a Google Doc (a 20-second timeout on a
bad connection) or unpack a large .docx, so it must never run on the GUI
thread. :class:`DocumentImporter` runs a *loader* -- ``load_document`` unless a
test injects another -- on a worker thread and reports back once, on the GUI
thread, with an :class:`ImportOutcome`.

The worker is a plain function that touches nothing but the loader and a
``queue.SimpleQueue``: no Qt object, no signal, no widget. The importer polls
that queue from a timer on the GUI thread. That is deliberate. A worker that held
(or emitted from) a Qt object could end up the last to let go of it, destroying it
on the wrong thread, and a worker that announced "no longer busy" and "here is the
document" as two events could leave a window between them in which a click lands on
a half-updated page. Here both happen inside one GUI-thread slot, in a fixed order.

The worker also never builds a keyboard or a ``Driver`` (building the real keyboard
backend off the main thread kills the whole process on macOS; see
``driver.preload_keyboard_backend``).

Also here, because they are about *where a document comes from*: the file
dialog's filter, tidying what a person pasted into the import box, and a short
name for a source to show beside the imported document.
"""

from __future__ import annotations

import os
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QStandardPaths, Qt, QTimer, QUrl, Signal
from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from auto_essay_typer.document import Document, ImportReport
from auto_essay_typer.importers import DocumentImportError, load_document

#: The file types ``importers.load_document`` reads, for the file dialog.
SUPPORTED_SUFFIXES = (".docx", ".html", ".htm", ".zip", ".txt", ".md")
FILE_DIALOG_FILTER = "Documents (" + " ".join(f"*{suffix}" for suffix in SUPPORTED_SUFFIXES) + ")"

DocumentLoader = Callable[[str], tuple[Document, ImportReport]]

_QUOTES = "\"'“”‘’"
_BARE_GOOGLE_HOSTS = ("docs.google.com/", "drive.google.com/")


# --- the source a person typed or chose -----------------------------------------------------------------


def clean_source(text: str) -> str:
    """What a person put in the import box, made into something ``load_document`` can read.

    Surrounding whitespace and quotes (a path copied out of a terminal, or
    dragged from a file manager that quotes it) are removed; a ``file://`` link
    becomes the path it names; a leading ``~`` means the home folder; and a
    Google link pasted without its ``https://`` (browsers often hide it) gets
    one back. Anything else is returned as typed, for the importer to judge.
    """
    source = text.strip()
    if len(source) >= 2 and source[0] in _QUOTES and source[-1] in _QUOTES:
        source = source[1:-1].strip()
    lowered = source.lower()
    if lowered.startswith("file://"):
        path = QUrl(source).toLocalFile()
        return path or source
    if lowered.startswith(_BARE_GOOGLE_HOSTS):
        return "https://" + source
    if source.startswith("~"):
        return os.path.expanduser(source)
    return source


def describe_source(source: str) -> str:
    """A short name for where a document came from: a file's name, or what kind of link."""
    if source.lower().startswith(("http://", "https://")):
        host = urlparse(source).netloc.lower()
        if host == "docs.google.com":
            return "a Google Docs link"
        if host == "drive.google.com":
            return "a Google Drive link"
        return "a link"
    return Path(source).name or source


# --- running the loader off the GUI thread -----------------------------------------------------------------


@dataclass(frozen=True)
class ImportOutcome:
    """How one import ended: a document and its report, or a message for the user."""

    source: str
    document: Document | None = None
    report: ImportReport | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _unexpected_message(error: Exception) -> str:
    detail = str(error).strip() or type(error).__name__
    return (
        f"Something went wrong while reading that document ({detail}). Try downloading it as "
        "a Word (.docx) file and importing that instead."
    )


#: How often the GUI thread looks for a finished import. Imports take seconds, and
#: this is far below what anyone can notice.
_POLL_INTERVAL_MS = 20


def _load_in_background(
    loader: DocumentLoader, source: str, results: "queue.SimpleQueue[ImportOutcome]"
) -> None:
    """The worker thread's entire body: run ``loader`` and queue how it ended.

    A plain function, not a method, so the thread cannot be holding a Qt object.
    """
    try:
        document, report = loader(source)
        outcome = ImportOutcome(source, document=document, report=report)
    except DocumentImportError as exc:
        outcome = ImportOutcome(source, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - a bug in a loader must reach the user as words
        outcome = ImportOutcome(source, error=_unexpected_message(exc))
    results.put(outcome)


class DocumentImporter(QObject):
    """Loads one document at a time on a worker thread.

    Signals
    -------
    busyChanged(bool)
        ``True`` as soon as :meth:`start` accepts a source, ``False`` when the
        import has ended.
    finished(object)
        The :class:`ImportOutcome`, on the GUI thread, after ``busyChanged(False)``.

    A :class:`~auto_essay_typer.importers.DocumentImportError` becomes the
    outcome's ``error`` unchanged -- those messages are written to be shown to
    a person as they are (the access-denied one keeps its advice about sharing
    the link and downloading a .docx). Any other exception, which would be a
    bug, is reported in plain words instead of as a traceback.
    """

    busyChanged = Signal(bool)
    finished = Signal(object)

    def __init__(self, loader: DocumentLoader = load_document, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._loader = loader
        self._busy = False
        self._results: queue.SimpleQueue[ImportOutcome] = queue.SimpleQueue()
        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)

    @property
    def is_busy(self) -> bool:
        return self._busy

    def start(self, source: str) -> bool:
        """Begin loading ``source``. Returns ``False``, doing nothing, if an import is running."""
        if self._busy:
            return False
        self._busy = True
        self.busyChanged.emit(True)
        threading.Thread(
            target=_load_in_background, args=(self._loader, source, self._results), daemon=True
        ).start()
        self._timer.start()
        return True

    def _poll(self) -> None:
        """Back on the GUI thread: if the worker has finished, report how it ended."""
        try:
            outcome = self._results.get_nowait()
        except queue.Empty:
            return
        self._timer.stop()
        self._busy = False
        self.busyChanged.emit(False)
        self.finished.emit(outcome)


# --- the real dialogs (replaced by fakes in tests) ---------------------------------------------------------


def default_start_dir() -> str:
    """Where the file dialog opens: the user's Documents folder, else their home."""
    documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    return documents or str(Path.home())


def default_choose_file(parent: QWidget | None, start_dir: str) -> str | None:
    """Ask for a document to import; ``None`` when the dialog is cancelled."""
    path, _selected_filter = QFileDialog.getOpenFileName(
        parent, "Choose a document to import", start_dir, FILE_DIALOG_FILTER
    )
    return path or None


def default_show_error(parent: QWidget | None, title: str, message: str) -> None:
    """Tell the user ``message`` in a dialog, exactly as written (never read as markup)."""
    box = QMessageBox(QMessageBox.Icon.Warning, title, message, QMessageBox.StandardButton.Ok, parent)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    box.exec()
