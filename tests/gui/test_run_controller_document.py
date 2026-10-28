"""``RunController.start_document``: typing a formatted document through an editor's keymap."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from auto_essay_typer import targets
from auto_essay_typer.actions import FormatOp
from auto_essay_typer.document import Document, Paragraph, Run, TextStyle
from auto_essay_typer.driver import Driver, RecordingKeyboard
from auto_essay_typer.gui import run_controller as run_controller_module
from auto_essay_typer.gui.document_session import DocumentSession
from auto_essay_typer.gui.run_controller import RunController
from auto_essay_typer.importers import load_document
from auto_essay_typer.keymaps import NO_OP
from auto_essay_typer.platforms import OperatingSystem
from auto_essay_typer.profiles import Profile

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "gdocs_export_sample.html"
OS = OperatingSystem.MACOS
CLEAN = Profile(typo_rate_per_100=0.0, revision_rate_per_word=0.0)


def _session(target_id: str = "google-docs") -> DocumentSession:
    document, report = load_document(str(FIXTURE))
    return DocumentSession.create(
        document,
        str(FIXTURE),
        report.warnings,
        targets.get_target(target_id),
        targets.keymap_for(target_id, OS),
    )


def _run(qtbot, controller: RunController, session: DocumentSession, profile=CLEAN, seed=1):
    with qtbot.waitSignal(controller.finished, timeout=10000) as blocker:
        controller.start_document(session.adapted, profile, session.keymap, seed=seed)
    return blocker.args


# --- a document is typed, formatting and all ------------------------------------------------------------


def test_a_document_run_types_the_documents_text_and_presses_its_formatting_shortcuts(
    qtbot, recording_driver_factory
):
    factory, keyboards = recording_driver_factory
    controller = RunController(driver_factory=factory)
    session = _session("google-docs")

    outcome, _message = _run(qtbot, controller, session)

    assert outcome == "finished"
    (keyboard,) = keyboards
    assert keyboard.text == session.adapted.plain_text()
    assert keyboard.chords, "a formatted document must press formatting shortcuts"
    reachable = {chord for chord in session.keymap.values() if chord != NO_OP}
    assert all(chord in reachable for chord in keyboard.chords if chord != NO_OP)
    # The fixture has bold, italic, underline, strikethrough, two heading levels and lists.
    for op in (
        FormatOp.TOGGLE_BOLD,
        FormatOp.TOGGLE_ITALIC,
        FormatOp.TOGGLE_UNDERLINE,
        FormatOp.TOGGLE_STRIKETHROUGH,
        FormatOp.SET_HEADING_2,
        FormatOp.TOGGLE_BULLET_LIST,
        FormatOp.ALIGN_CENTER,
    ):
        assert session.keymap[op] in keyboard.chords, op


def test_the_driver_is_built_with_the_editors_keymap(qtbot, recording_driver_factory):
    factory, _keyboards = recording_driver_factory
    controller = RunController(driver_factory=factory)
    session = _session("microsoft-word")

    _run(qtbot, controller, session)

    assert factory.keymaps == [session.keymap]


def test_a_document_typed_for_an_editor_without_formatting_presses_no_formatting_shortcuts(
    qtbot, recording_driver_factory
):
    factory, keyboards = recording_driver_factory
    controller = RunController(driver_factory=factory)
    session = _session("plain-text")

    outcome, _message = _run(qtbot, controller, session)

    assert outcome == "finished"
    assert keyboards[0].text == session.adapted.plain_text()
    # The only op this editor has is a deliberate no-op, which presses no keys.
    assert [chord for chord in keyboards[0].chords if chord != NO_OP] == []


def test_the_documents_text_comes_out_right_however_many_typos_are_made_and_fixed(
    qtbot, recording_driver_factory
):
    factory, keyboards = recording_driver_factory
    controller = RunController(driver_factory=factory)
    session = _session("google-docs")

    outcome, _message = _run(qtbot, controller, session, profile=Profile(typo_rate_per_100=6.0), seed=4)

    assert outcome == "finished"
    assert keyboards[0].text == session.adapted.plain_text()


def test_a_document_run_reports_progress_up_to_100_percent(qtbot, recording_driver_factory):
    factory, _keyboards = recording_driver_factory
    controller = RunController(driver_factory=factory)
    updates: list[tuple[int, int, float, float]] = []
    controller.progressChanged.connect(lambda *args: updates.append(args))

    _run(qtbot, controller, _session("google-docs"))

    done, total, eta, _elapsed = updates[-1]
    assert done == total > 0
    assert eta == pytest.approx(0.0, abs=1e-6)


def test_start_document_is_a_no_op_while_a_run_is_in_flight(qtbot, recording_driver_factory):
    factory, keyboards = recording_driver_factory
    controller = RunController(driver_factory=factory)
    controller._busy = True  # simulate "a run is already in flight"
    session = _session()

    controller.start_document(session.adapted, CLEAN, session.keymap, seed=1)

    assert not keyboards


def test_a_document_run_can_be_stopped(qtbot):
    keyboard = RecordingKeyboard()
    keymaps: list[object] = []

    def factory(keymap=None) -> Driver:
        keymaps.append(keymap)
        return Driver(
            keyboard=keyboard, sleep=lambda _s: time.sleep(0.003), countdown_seconds=0, keymap=keymap
        )

    controller = RunController(driver_factory=factory)
    session = _session("google-docs")

    with qtbot.waitSignal(controller.finished, timeout=10000) as blocker:
        controller.start_document(session.adapted, CLEAN, session.keymap, seed=1)
        qtbot.waitUntil(lambda: controller.state == "typing", timeout=5000)
        controller.stop()

    assert blocker.args[0] == "stopped"
    assert session.adapted.plain_text().startswith(keyboard.text)


# --- plain text is started exactly as it always was ---------------------------------------------------------


def test_a_plain_text_run_still_calls_the_driver_factory_with_no_arguments(qtbot):
    keyboard = RecordingKeyboard()

    def strict_factory() -> Driver:  # would raise TypeError if a keymap were passed
        return Driver(keyboard=keyboard, sleep=lambda _s: None, countdown_seconds=0)

    controller = RunController(driver_factory=strict_factory)

    with qtbot.waitSignal(controller.finished, timeout=5000) as blocker:
        controller.start("Plain words.", CLEAN, seed=1)

    assert blocker.args[0] == "finished"
    assert keyboard.text == "Plain words."


# --- a plan that does not reproduce the document is never typed ----------------------------------------------


def test_a_plan_that_does_not_reproduce_the_document_is_refused_before_anything_is_typed(
    qtbot, recording_driver_factory, monkeypatch
):
    factory, keyboards = recording_driver_factory
    controller = RunController(driver_factory=factory)
    session = _session("google-docs")
    other = Document((Paragraph((Run("something else entirely"),)),))
    real_plan_document = run_controller_module.plan_document
    monkeypatch.setattr(
        run_controller_module,
        "plan_document",
        lambda document, profile, seed: real_plan_document(other, profile, seed),
    )

    outcome, message = _run(qtbot, controller, session)

    assert outcome == "failed"
    assert "did not reproduce your document exactly" in message
    assert "nothing was typed" in message
    assert keyboards[0].text == ""
    assert keyboards[0].chords == []
    assert controller.is_busy is False


def test_an_editor_missing_a_shortcut_the_document_needs_is_explained_not_dumped(qtbot):
    keyboard = RecordingKeyboard()
    # An editor whose bold shortcut has been removed, and a document that is not adapted to that.
    keymap = {op: chord for op, chord in targets.keymap_for("google-docs", OS).items() if op is not FormatOp.TOGGLE_BOLD}
    document = Document((Paragraph((Run("bold words", frozenset({TextStyle.BOLD})),)),))

    def factory(keymap=None) -> Driver:
        return Driver(keyboard=keyboard, sleep=lambda _s: None, countdown_seconds=0, keymap=keymap)

    controller = RunController(driver_factory=factory)
    with qtbot.waitSignal(controller.finished, timeout=5000) as blocker:
        controller.start_document(document, CLEAN, keymap, seed=1)

    outcome, message = blocker.args
    assert outcome == "failed"
    assert "missing: toggle bold" in message
    assert "Advanced page" in message
    assert "keymap" not in message
    assert keyboard.text == ""


def test_a_keyboard_that_cannot_be_set_up_is_reported_for_a_document_too(qtbot):
    def broken_factory(keymap=None) -> Driver:
        raise RuntimeError("keyboard backend exploded")

    controller = RunController(driver_factory=broken_factory)
    seen: list[tuple[str, str]] = []
    controller.finished.connect(lambda outcome, message: seen.append((outcome, message)))
    session = _session()

    controller.start_document(session.adapted, CLEAN, session.keymap, seed=1)

    assert seen and seen[0][0] == "failed"
    assert "keyboard backend exploded" in seen[0][1]
    assert controller.state == "failed"
    assert controller.is_busy is False


# --- the macOS rule: the keyboard is built on the main thread, never on the worker -----------------------------------


def test_a_document_run_builds_its_keyboard_on_the_main_thread_and_types_on_the_worker(qtbot):
    built_on: list[threading.Thread] = []
    typed_on: list[threading.Thread] = []

    class NotingKeyboard(RecordingKeyboard):
        def press_char(self, char: str) -> None:
            typed_on.append(threading.current_thread())
            super().press_char(char)

    keyboard = NotingKeyboard()

    def factory(keymap=None) -> Driver:
        built_on.append(threading.current_thread())
        return Driver(keyboard=keyboard, sleep=lambda _s: None, countdown_seconds=0, keymap=keymap)

    controller = RunController(driver_factory=factory)
    session = _session("google-docs")

    outcome, _message = _run(qtbot, controller, session)

    assert outcome == "finished"
    assert built_on == [threading.main_thread()], "building the keyboard off the main thread kills the app on macOS"
    assert typed_on
    assert all(thread is not threading.main_thread() for thread in typed_on)
