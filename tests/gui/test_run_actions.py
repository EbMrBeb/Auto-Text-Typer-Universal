"""Tests for what the tray and the hotkeys do to a run (``gui/run_actions.py``).

The point of ``RunActions`` is that a run started from the tray or a hotkey goes through the
*same* run controller as the Type page, with the same checks: something to type, permission,
and (for these background starts) a confirmation. So most tests build a real ``MainWindow`` on
a recording keyboard and look at what ended up typed, and at which prompts were (and were
not) shown. The clipboard, the permission dialog, the confirmation and the notifications are
all injected; nothing real is typed, shown or read.

A note on waiting. ``RunController`` emits ``finished`` from its typing thread. If a test
fails *inside* a ``qtbot.waitSignal`` block, the block is abandoned while that thread is still
about to emit into it, and PySide then crashes the whole run delivering the late signal to a
freed receiver. So everything that can fail happens outside the block: ``_run`` does nothing
but start the run and wait for it, and the assertions follow.
"""

from __future__ import annotations

import threading

import pytest
import shiboken6
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QMessageBox

from auto_essay_typer.driver import Driver, RecordingKeyboard
from auto_essay_typer.gui import run_actions as run_actions_module
from auto_essay_typer.gui.main_window import MainWindow
from auto_essay_typer.gui.run_actions import (
    RunActions,
    StartOutcome,
    default_clipboard_text,
    default_confirm_start,
)
from auto_essay_typer.gui.settings_store import SettingsStore
from auto_essay_typer.gui.theme import ThemeManager
from tests.gui.fakes import Recorder, granted, not_granted

BOM = "﻿"


class Gate:
    """A ``sleep`` that parks the typing worker mid-run until the test lets it go.

    Lets a test observe (and drive) a run that is genuinely in flight -- paused, stopped, or
    refusing a second start -- without sleeping or racing a run that finishes on its own.
    """

    def __init__(self, free_calls: int = 0, *, until=None) -> None:
        """``free_calls`` waits go through at once; with ``until`` (a predicate), waits go through
        until it is true -- e.g. "three characters have been typed" -- and the next one parks."""
        self.free_calls = free_calls
        self.until = until
        self.calls = 0
        self.blocked = threading.Event()
        self.release = threading.Event()

    def sleep(self, _seconds: float) -> None:
        self.calls += 1
        if self.until is not None:
            parked = self.until()
        else:
            parked = self.calls > self.free_calls
        if parked:
            self.blocked.set()
            self.release.wait(timeout=10)


@pytest.fixture
def gate():
    gate = Gate()
    yield gate
    gate.release.set()  # never leave a worker parked, whatever the test did


def _gated_factory(gate, keyboards):
    def factory(keymap=None) -> Driver:
        keyboard = RecordingKeyboard()
        keyboards.append(keyboard)
        return Driver(keyboard=keyboard, sleep=gate.sleep, countdown_seconds=0, keymap=keymap)

    return factory


def _window(qtbot, qapp, tmp_path, driver_factory, *, permission_checker=granted, **store_settings):
    store = SettingsStore(tmp_path / "settings.json")
    if store_settings:
        store.update(**store_settings)
    window = MainWindow(
        ThemeManager(qapp),
        driver_factory=driver_factory,
        permission_checker=lambda: permission_checker(),
        settings=QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
        settings_store=store,
    )
    qtbot.addWidget(window)
    return window


def _actions(window, *, clipboard="Hello there.", confirm=True, prompt=True):
    notes = Recorder()
    clip = Recorder(clipboard)
    confirmed = Recorder(confirm)
    prompted = Recorder(prompt)
    actions = RunActions(
        window,
        clipboard_text=lambda: clip(),
        confirm=confirmed,
        permission_prompt=prompted,
        notify=notes,
    )
    return actions, notes, confirmed, prompted


def _run(qtbot, window, start):
    """Call ``start()`` (which begins a run) and wait for that run to end.

    Returns ``(what start() returned, the run's (outcome, message))``. Nothing here can fail
    while the wait is open -- see the module docstring.
    """
    with qtbot.waitSignal(window.run_controller.finished, timeout=8000) as blocker:
        result = start()
    return result, tuple(blocker.args)


def _finish(qtbot, window):
    """Wait for the run in flight (if any) to end; ``(outcome, message)``, or ``None`` if none was."""
    controller = window.run_controller
    if not controller.is_busy:
        return None
    with qtbot.waitSignal(controller.finished, timeout=8000) as blocker:
        pass
    return tuple(blocker.args)


# --- starting from the clipboard -------------------------------------------------------------------------------


def test_the_clipboard_is_typed_through_the_same_run_controller_the_type_page_uses(
    qtbot, qapp, tmp_path, recording_driver_factory
):
    factory, keyboards = recording_driver_factory
    window = _window(qtbot, qapp, tmp_path, factory, confirm_before_typing=False)
    actions, notes, _confirmed, _prompted = _actions(window, clipboard="Hello there.")

    outcome, finished = _run(qtbot, window, actions.start_from_clipboard)

    assert outcome is StartOutcome.STARTED
    assert finished == ("finished", "Done.")
    assert keyboards[0].text == "Hello there."  # the controller the Type page owns did the typing
    assert notes.calls == []  # nothing went wrong, so nothing was said


def test_a_clipboard_run_uses_the_type_pages_current_profile(
    qtbot, qapp, tmp_path, recording_driver_factory, monkeypatch
):
    factory, _keyboards = recording_driver_factory
    window = _window(qtbot, qapp, tmp_path, factory, confirm_before_typing=False)
    page = window.type_page
    page.speed_slider.setValue(95)  # a quick setting the user changed on the Type page
    started = []
    real_start = window.run_controller.start

    def spy(*args, **kwargs):
        started.append((args, kwargs))
        return real_start(*args, **kwargs)

    monkeypatch.setattr(window.run_controller, "start", spy)
    actions, *_ = _actions(window)

    _run(qtbot, window, actions.start_from_clipboard)

    ((args, kwargs),) = started
    text, profile = args
    assert text == "Hello there."
    assert profile == page.effective_profile()
    assert profile.wpm == 95.0
    assert kwargs == {"seed": None}


def test_clipboard_text_is_normalised_like_every_other_text_that_comes_from_outside(
    qtbot, qapp, tmp_path, recording_driver_factory
):
    factory, keyboards = recording_driver_factory
    window = _window(qtbot, qapp, tmp_path, factory, confirm_before_typing=False)
    actions, *_ = _actions(window, clipboard=f"{BOM}one\r\ntwo\rthree")  # a BOM, a CRLF and a lone CR

    _run(qtbot, window, actions.start_from_clipboard)

    assert keyboards[0].text == "one\ntwo\nthree"


@pytest.mark.parametrize("clipboard", ["", "   \n\t  ", BOM])
def test_nothing_to_type_says_so_and_asks_nothing(qtbot, qapp, tmp_path, recording_driver_factory, clipboard):
    factory, keyboards = recording_driver_factory
    window = _window(qtbot, qapp, tmp_path, factory, permission_checker=not_granted)
    actions, notes, confirmed, prompted = _actions(window, clipboard=clipboard)

    assert actions.start_from_clipboard() is StartOutcome.EMPTY

    assert [title for title, _message in notes.calls] == ["Auto Text Typer"]
    assert "no text on the clipboard" in notes.calls[0][1]
    assert prompted.calls == [] and confirmed.calls == []  # the cheap check comes first
    assert window.run_controller.is_busy is False
    assert keyboards == []


# --- permission ----------------------------------------------------------------------------------------------


def test_missing_permission_shows_the_permission_prompt_and_a_refusal_stops_the_start(
    qtbot, qapp, tmp_path, recording_driver_factory
):
    factory, keyboards = recording_driver_factory
    window = _window(qtbot, qapp, tmp_path, factory, permission_checker=not_granted)
    actions, _notes, confirmed, prompted = _actions(window, prompt=False)

    assert actions.start_from_clipboard() is StartOutcome.NO_PERMISSION

    assert len(prompted.calls) == 1
    _parent, status = prompted.calls[0]
    assert status.granted is False and "Accessibility" in status.summary
    assert confirmed.calls == []  # no point asking whether to type when typing cannot work
    assert window.run_controller.is_busy is False
    assert keyboards == []


def test_permission_granted_in_the_prompt_lets_the_start_go_on(qtbot, qapp, tmp_path, recording_driver_factory):
    factory, keyboards = recording_driver_factory
    window = _window(
        qtbot, qapp, tmp_path, factory, permission_checker=not_granted, confirm_before_typing=False
    )
    actions, _notes, _confirmed, prompted = _actions(window, prompt=True)

    outcome, _finished = _run(qtbot, window, actions.start_from_clipboard)

    assert outcome is StartOutcome.STARTED
    assert len(prompted.calls) == 1
    assert keyboards[0].text == "Hello there."


def test_granted_permission_shows_no_prompt(qtbot, qapp, tmp_path, recording_driver_factory):
    factory, _keyboards = recording_driver_factory
    window = _window(qtbot, qapp, tmp_path, factory, confirm_before_typing=False)
    actions, _notes, _confirmed, prompted = _actions(window)

    _run(qtbot, window, actions.start_from_clipboard)

    assert prompted.calls == []


# --- confirm before typing ---------------------------------------------------------------------------------------


def test_confirmation_names_the_source_the_size_and_the_countdown(qtbot, qapp, tmp_path, recording_driver_factory):
    factory, _keyboards = recording_driver_factory
    window = _window(qtbot, qapp, tmp_path, factory, countdown_seconds=7)  # confirm_before_typing is on by default
    actions, _notes, confirmed, _prompted = _actions(window, clipboard="x" * 1234, confirm=False)

    assert actions.start_from_clipboard() is StartOutcome.DECLINED

    ((_parent, title, text),) = confirmed.calls
    assert title == "Start typing?"
    assert "1,234 characters on the clipboard" in text
    assert "7 second countdown" in text


def test_a_declined_confirmation_starts_nothing(qtbot, qapp, tmp_path, recording_driver_factory):
    factory, keyboards = recording_driver_factory
    window = _window(qtbot, qapp, tmp_path, factory)
    actions, _notes, confirmed, _prompted = _actions(window, confirm=False)

    assert actions.start_from_clipboard() is StartOutcome.DECLINED

    assert len(confirmed.calls) == 1
    assert window.run_controller.is_busy is False
    assert keyboards == []  # not even a driver was built


def test_an_accepted_confirmation_starts_the_run(qtbot, qapp, tmp_path, recording_driver_factory):
    factory, keyboards = recording_driver_factory
    window = _window(qtbot, qapp, tmp_path, factory)
    actions, _notes, confirmed, _prompted = _actions(window, confirm=True)

    outcome, _finished = _run(qtbot, window, actions.start_from_clipboard)

    assert outcome is StartOutcome.STARTED
    assert len(confirmed.calls) == 1
    assert keyboards[0].text == "Hello there."


def test_with_confirmation_turned_off_nothing_is_asked(qtbot, qapp, tmp_path, recording_driver_factory):
    factory, _keyboards = recording_driver_factory
    window = _window(qtbot, qapp, tmp_path, factory, confirm_before_typing=False)
    actions, _notes, confirmed, _prompted = _actions(window)

    _run(qtbot, window, actions.start_from_clipboard)

    assert confirmed.calls == []


def test_the_confirmation_reads_the_setting_as_it_is_now_not_as_it_was_at_startup(
    qtbot, qapp, tmp_path, recording_driver_factory
):
    factory, _keyboards = recording_driver_factory
    window = _window(qtbot, qapp, tmp_path, factory)
    actions, _notes, confirmed, _prompted = _actions(window, confirm=True)
    window.settings_store.update(confirm_before_typing=False)

    _run(qtbot, window, actions.start_from_clipboard)

    assert confirmed.calls == []


def test_a_countdown_of_zero_is_described_honestly(qtbot, qapp, tmp_path, recording_driver_factory):
    factory, _keyboards = recording_driver_factory
    window = _window(qtbot, qapp, tmp_path, factory, countdown_seconds=0)
    actions, _notes, confirmed, _prompted = _actions(window, clipboard="a", confirm=False)

    actions.start_from_clipboard()

    text = confirmed.calls[0][2]
    assert "1 character on the clipboard" in text  # singular
    assert "at once" in text and "countdown" not in text


def test_the_dialogs_are_parented_to_the_window_only_while_it_is_on_screen(
    qtbot, qapp, tmp_path, recording_driver_factory
):
    factory, _keyboards = recording_driver_factory
    window = _window(qtbot, qapp, tmp_path, factory)
    actions, _notes, confirmed, _prompted = _actions(window, confirm=False)

    actions.start_from_clipboard()  # the window has never been shown
    assert confirmed.calls[-1][0] is None

    window.show()
    qtbot.waitExposed(window)
    actions.start_from_clipboard()
    assert confirmed.calls[-1][0] is window

    window.showMinimized()
    actions.start_from_clipboard()
    assert confirmed.calls[-1][0] is None


# --- a run already in flight, and re-entrancy ---------------------------------------------------------------------------


def test_a_start_while_typing_is_refused_not_queued(qtbot, qapp, tmp_path, gate):
    keyboards: list[RecordingKeyboard] = []
    window = _window(qtbot, qapp, tmp_path, _gated_factory(gate, keyboards), confirm_before_typing=False)
    actions, notes, _confirmed, _prompted = _actions(window)

    assert actions.start_from_clipboard() is StartOutcome.STARTED
    assert gate.blocked.wait(5)

    assert actions.start_from_clipboard() is StartOutcome.BUSY
    assert actions.start_from_type_page() is StartOutcome.BUSY
    assert "already in progress" in notes.calls[-1][1]
    assert len(keyboards) == 1  # no second driver

    gate.release.set()
    _finish(qtbot, window)


def test_a_start_requested_while_a_prompt_is_open_does_not_stack_another_prompt(
    qtbot, qapp, tmp_path, recording_driver_factory
):
    factory, _keyboards = recording_driver_factory
    window = _window(qtbot, qapp, tmp_path, factory)
    inner: list[StartOutcome] = []
    holder: dict[str, RunActions] = {}

    def confirm_that_is_pressed_again(parent, title, text):
        inner.append(holder["actions"].start_from_clipboard())  # the hotkey pressed twice
        return False

    actions = RunActions(
        window,
        clipboard_text=lambda: "Hello.",
        confirm=confirm_that_is_pressed_again,
        notify=Recorder(),
    )
    holder["actions"] = actions

    assert actions.start_from_clipboard() is StartOutcome.DECLINED
    assert inner == [StartOutcome.BUSY]


def test_a_driver_that_cannot_be_built_is_reported_through_the_run_like_any_other_failure(qtbot, qapp, tmp_path):
    def broken_factory(keymap=None):
        raise RuntimeError("no keyboard")

    window = _window(qtbot, qapp, tmp_path, broken_factory, confirm_before_typing=False)
    actions, notes, _confirmed, _prompted = _actions(window)
    finished = []
    window.run_controller.finished.connect(lambda outcome, message: finished.append((outcome, message)))

    outcome = actions.start_from_clipboard()

    # Handed over; the failure arrives the way every run's does (the Type page shows it, and the
    # shell puts it in a tray balloon). It happened on this thread, so it is already here.
    assert outcome is StartOutcome.STARTED
    assert finished == [("failed", "Keyboard setup failed: no keyboard")]
    assert window.run_controller.is_busy is False
    assert notes.calls == []


# --- starting from the Type page (the start hotkey) --------------------------------------------------------------------------


def test_the_start_hotkey_types_what_is_on_the_type_page_through_the_pages_own_start(
    qtbot, qapp, tmp_path, recording_driver_factory
):
    factory, keyboards = recording_driver_factory
    window = _window(qtbot, qapp, tmp_path, factory, confirm_before_typing=False)
    window.type_page.set_text("Typed on the page.")
    actions, notes, _confirmed, _prompted = _actions(window, clipboard="NOT THIS")

    outcome, _finished = _run(qtbot, window, actions.start_from_type_page)

    assert outcome is StartOutcome.STARTED
    assert keyboards[0].text == "Typed on the page."  # the page, not the clipboard
    assert notes.calls == []


def test_a_start_from_the_type_page_puts_the_page_into_its_run_state_as_its_own_button_would(
    qtbot, qapp, tmp_path, gate
):
    keyboards: list[RecordingKeyboard] = []
    window = _window(qtbot, qapp, tmp_path, _gated_factory(gate, keyboards), confirm_before_typing=False)
    page = window.type_page
    page.set_text("Typed on the page.")
    actions, _notes, _confirmed, _prompted = _actions(window)

    assert actions.start_from_type_page() is StartOutcome.STARTED
    assert gate.blocked.wait(5)  # the run is genuinely in flight

    assert page.idle_actions_widget.isHidden()  # the page went into its run state...
    assert page.run_panel_widget.isHidden() is False  # ...showing the run, with its Pause and Stop
    assert page.text_edit.isEnabled() is False

    actions.stop()
    gate.release.set()
    _finish(qtbot, window)
    qtbot.waitUntil(lambda: not page.idle_actions_widget.isHidden(), timeout=3000)  # and back to idle afterwards


def test_an_empty_type_page_is_explained_and_nothing_starts(qtbot, qapp, tmp_path, recording_driver_factory):
    factory, keyboards = recording_driver_factory
    window = _window(qtbot, qapp, tmp_path, factory)
    actions, notes, confirmed, prompted = _actions(window)

    assert actions.start_from_type_page() is StartOutcome.EMPTY

    assert "The Type page is empty" in notes.calls[0][1]
    assert "Start typing from clipboard" in notes.calls[0][1]
    assert confirmed.calls == [] and prompted.calls == []
    assert keyboards == []


def test_the_type_page_start_is_confirmed_naming_the_page_as_the_source(qtbot, qapp, tmp_path, recording_driver_factory):
    factory, keyboards = recording_driver_factory
    window = _window(qtbot, qapp, tmp_path, factory)
    window.type_page.set_text("Some text.")
    actions, _notes, confirmed, _prompted = _actions(window, confirm=False)

    assert actions.start_from_type_page() is StartOutcome.DECLINED

    assert "10 characters on the Type page" in confirmed.calls[0][2]
    assert keyboards == []


# --- pausing and stopping -------------------------------------------------------------------------------------------------------


def test_toggle_pause_pauses_a_typing_run_and_resumes_a_paused_one(qtbot, qapp, tmp_path):
    keyboards: list[RecordingKeyboard] = []
    gate = Gate(until=lambda: bool(keyboards) and len(keyboards[-1].text) >= 3)  # park once typing has begun
    window = _window(qtbot, qapp, tmp_path, _gated_factory(gate, keyboards), confirm_before_typing=False)
    actions, _notes, _confirmed, _prompted = _actions(
        window, clipboard="A fairly long line to type, so the run is mid-flight."
    )
    controller = window.run_controller
    try:
        actions.start_from_clipboard()
        assert gate.blocked.wait(8)
        qtbot.waitUntil(lambda: controller.state == "typing", timeout=5000)

        actions.toggle_pause()
        assert controller.state == "paused"

        actions.toggle_pause()  # resume starts a short countdown before typing again
        assert controller.state == "resuming"
    finally:
        actions.stop()
        gate.release.set()
        _finish(qtbot, window)


def test_toggle_pause_is_ignored_when_nothing_is_typing(qtbot, qapp, tmp_path, recording_driver_factory):
    factory, _keyboards = recording_driver_factory
    window = _window(qtbot, qapp, tmp_path, factory)
    actions, notes, _confirmed, _prompted = _actions(window)

    actions.toggle_pause()  # idle

    assert window.run_controller.state == "idle"
    assert notes.calls == []


def test_stop_ends_the_run_in_flight_and_is_safe_when_idle(qtbot, qapp, tmp_path, gate):
    keyboards: list[RecordingKeyboard] = []
    window = _window(qtbot, qapp, tmp_path, _gated_factory(gate, keyboards), confirm_before_typing=False)
    actions, _notes, _confirmed, _prompted = _actions(window)

    actions.stop()  # nothing running: must not raise or change anything
    assert window.run_controller.state == "idle"

    actions.start_from_clipboard()
    assert gate.blocked.wait(5)
    actions.stop()
    gate.release.set()
    assert _finish(qtbot, window) == ("stopped", "Stopped.")


def test_the_stop_hotkey_does_nothing_unless_a_run_is_in_flight(qtbot, qapp, tmp_path, gate):
    # Escape is the default stop key and is pressed constantly for other reasons, in any app.
    keyboards: list[RecordingKeyboard] = []
    window = _window(qtbot, qapp, tmp_path, _gated_factory(gate, keyboards), confirm_before_typing=False)
    actions, _notes, _confirmed, _prompted = _actions(window)
    finished = []
    window.run_controller.finished.connect(lambda outcome, message: finished.append(outcome))

    actions.stop_if_running()  # idle: ignored
    qapp.processEvents()
    assert finished == []

    actions.start_from_clipboard()
    assert gate.blocked.wait(5)
    actions.stop_if_running()  # now it counts
    gate.release.set()
    assert _finish(qtbot, window) == ("stopped", "Stopped.")


# --- the default side effects ------------------------------------------------------------------------------------------------------


def test_the_default_notification_goes_to_the_windows_status_bar(qtbot, qapp, tmp_path, recording_driver_factory):
    factory, _keyboards = recording_driver_factory
    window = _window(qtbot, qapp, tmp_path, factory)
    actions = RunActions(window, clipboard_text=lambda: "")

    actions.start_from_clipboard()

    assert "no text on the clipboard" in window.statusBar().currentMessage()


def test_the_default_confirmation_has_buttons_that_say_what_they_do(qtbot, qapp, monkeypatch):
    seen = {}

    def fake_exec(self):
        seen["buttons"] = [button.text() for button in self.buttons()]
        seen["default"] = self.defaultButton().text()
        seen["escape"] = self.escapeButton().text()
        self.buttons()[0].click()  # the user presses "Start typing"
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)

    assert default_confirm_start(None, "Start typing?", "Type 5 characters?") is True
    assert seen == {"buttons": ["Start typing", "Cancel"], "default": "Start typing", "escape": "Cancel"}


def test_cancelling_the_default_confirmation_says_no(qtbot, qapp, monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", lambda self: (self.buttons()[1].click(), 0)[1])
    assert default_confirm_start(None, "Start typing?", "Type 5 characters?") is False


def test_a_confirmation_box_is_deleted_the_moment_it_has_been_answered(qtbot, qapp, monkeypatch):
    # Made without a parent, it is Python's to free -- and if it were left to the cycle collector
    # that could happen on the typing thread, which crashes the process (see _dispose).
    boxes = []

    def fake_exec(self):
        boxes.append(self)
        self.buttons()[0].click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)

    default_confirm_start(None, "Start typing?", "Type 5 characters?")

    assert shiboken6.isValid(boxes[0]) is False


def test_the_confirmation_box_opens_above_other_apps(qtbot, qapp, monkeypatch):
    # It answers a hotkey or a tray click, usually while another app has focus. A window of an
    # inactive app can open behind that app's, and a prompt nobody can see leaves every later
    # hotkey press waiting on it.
    flags = []

    def fake_exec(self):
        flags.append(self.windowFlags())
        self.buttons()[0].click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)

    default_confirm_start(None, "Start typing?", "Type 5 characters?")

    assert flags[0] & Qt.WindowType.WindowStaysOnTopHint


def test_the_permission_dialog_is_deleted_the_moment_it_has_been_dealt_with(
    qtbot, qapp, tmp_path, recording_driver_factory, monkeypatch
):
    factory, _keyboards = recording_driver_factory
    window = _window(qtbot, qapp, tmp_path, factory, permission_checker=not_granted)
    dialogs = []
    flags = []

    def fake_exec(self):
        dialogs.append(self)
        flags.append(self.windowFlags())
        return 0

    monkeypatch.setattr(run_actions_module.PermissionDialog, "exec", fake_exec)
    actions = RunActions(window, clipboard_text=lambda: "Hello.", notify=Recorder())

    actions.start_from_clipboard()

    assert shiboken6.isValid(dialogs[0]) is False
    assert flags[0] & Qt.WindowType.WindowStaysOnTopHint  # and it was opened above other apps' windows


def test_the_default_permission_prompt_is_the_permission_dialog(
    qtbot, qapp, tmp_path, recording_driver_factory, monkeypatch
):
    factory, _keyboards = recording_driver_factory
    window = _window(qtbot, qapp, tmp_path, factory, permission_checker=not_granted)
    shown = []

    def fake_exec(self):
        shown.append(self.windowTitle())
        return 0

    monkeypatch.setattr(run_actions_module.PermissionDialog, "exec", fake_exec)
    actions = RunActions(window, clipboard_text=lambda: "Hello.", notify=Recorder())

    assert actions.start_from_clipboard() is StartOutcome.NO_PERMISSION  # the dialog was dismissed

    assert shown == ["Permission needed"]


def test_the_default_clipboard_reads_the_system_clipboard_text(qtbot, qapp):
    from PySide6.QtGui import QGuiApplication

    if QGuiApplication.platformName() != "offscreen":
        pytest.skip("only the offscreen platform has a clipboard that is safe to overwrite")
    QGuiApplication.clipboard().setText("from the clipboard")
    assert default_clipboard_text() == "from the clipboard"
