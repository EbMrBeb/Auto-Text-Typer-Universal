"""Qt objects must be destroyed on the thread that owns them, whatever the worker threads are doing.

Qt forbids destroying a widget from any thread but the GUI thread: doing so edits
``QApplication``'s list of every widget while the GUI thread may be walking it, and
the process later crashes somewhere unrelated (here: in ``QApplication.setStyle``, in
a different test). In Python the danger is easy to create: a Qt object's C++ half is
destroyed by whichever thread drops the *last reference* to its Python half, and a
worker thread whose closure or bound method mentions a widget can be that thread.

These tests pin the rule for the two kinds of worker the Type page starts. Each makes
a worker the last thing alive that could hold a reference, drops everything else, and
records the thread the Qt object is destroyed on.
"""

from __future__ import annotations

import gc
import threading
from pathlib import Path

from PySide6.QtCore import Qt

from auto_essay_typer.gui.document_import import DocumentImporter
from auto_essay_typer.gui.pages import type_page as type_page_module
from auto_essay_typer.importers import load_document
from auto_essay_typer.planner import plan as real_plan

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "gdocs_export_sample.html"


def _join_new_threads(before: set[threading.Thread]) -> None:
    """Wait for every thread started since ``before`` was taken (they are all released by now)."""
    for worker in set(threading.enumerate()) - before:
        worker.join(10)


def _watch_destruction(qobject, destroyed_on: list[threading.Thread]) -> None:
    # A direct connection runs the slot on the thread that is doing the destroying
    # (a queued one would run later on this thread and hide who did it).
    qobject.destroyed.connect(
        lambda *_: destroyed_on.append(threading.current_thread()),
        Qt.ConnectionType.DirectConnection,
    )


def _start_slow_estimate(type_page_rig, monkeypatch, destroyed_on, release) -> None:
    """Build a page, start its duration estimate (made slow), and let go of the page.

    A function, so every local reference to the page dies when it returns.
    """
    started = threading.Event()

    def slow_plan(text, profile, seed):
        started.set()
        release.wait(10)
        return real_plan(text, profile, seed)

    monkeypatch.setattr(type_page_module, "plan", slow_plan)
    page = type_page_rig().page
    _watch_destruction(page, destroyed_on)
    page.set_text("Some text whose estimate takes a while.")
    page._estimate_timer.stop()
    page._start_estimate()
    assert started.wait(10), "the estimate never started"


def test_a_slow_estimate_never_makes_its_worker_the_one_that_destroys_the_page(
    qtbot, type_page_rig, monkeypatch
):
    destroyed_on: list[threading.Thread] = []
    release = threading.Event()
    before = set(threading.enumerate())
    try:
        _start_slow_estimate(type_page_rig, monkeypatch, destroyed_on, release)
        gc.collect()
        # The page was let go of while its estimate was still running. Nothing the
        # worker holds may keep it alive, so it is destroyed right here, on this thread.
        assert destroyed_on == [threading.main_thread()]
    finally:
        release.set()
        _join_new_threads(before)


def test_a_finished_estimate_for_a_page_that_is_gone_is_dropped_quietly(qtbot, type_page_rig, monkeypatch):
    destroyed_on: list[threading.Thread] = []
    release = threading.Event()
    errors: list[BaseException] = []
    monkeypatch.setattr(threading, "excepthook", lambda args: errors.append(args.exc_value))
    before = set(threading.enumerate())

    _start_slow_estimate(type_page_rig, monkeypatch, destroyed_on, release)
    gc.collect()
    release.set()  # the estimate now finishes and tries to report to a page that no longer exists
    _join_new_threads(before)

    assert errors == []
    assert destroyed_on == [threading.main_thread()]


def _start_slow_import(destroyed_on, release, monkeypatch) -> None:
    started = threading.Event()

    def slow_loader(source):
        started.set()
        release.wait(10)
        return load_document(source)

    importer = DocumentImporter(slow_loader)
    _watch_destruction(importer, destroyed_on)
    assert importer.start(str(FIXTURE)) is True
    assert started.wait(10), "the import never started"


def test_a_slow_import_never_makes_its_worker_the_one_that_destroys_the_importer(qtbot, monkeypatch):
    destroyed_on: list[threading.Thread] = []
    release = threading.Event()
    before = set(threading.enumerate())
    try:
        _start_slow_import(destroyed_on, release, monkeypatch)
        gc.collect()
        assert destroyed_on == [threading.main_thread()]
    finally:
        release.set()
        _join_new_threads(before)
