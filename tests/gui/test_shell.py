"""Tests for the application shell (``gui/shell.py``): the tray, the hotkeys and first-run setup.

Everything the OS would supply is a fake: the system tray (``FakeSystemTray``), the hotkey
listener (``FakeHotkeyBackend`` behind a real ``HotkeyService``), the keyboard (a recording
one), the clipboard, the permission probe and every dialog. *No real global listener is ever
installed* -- the suite-wide ``_no_real_hotkey_listener`` guard in ``tests/conftest.py`` fails
any test that tries -- and each fake records the thread it was built and called on, because the
thing this wiring must get right on macOS is *which thread builds the listener* (a stock one
started elsewhere kills the process; see ``gui/hotkeys.py``).
"""

from __future__ import annotations

import functools
import json
import threading
from collections import defaultdict

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from auto_essay_typer.driver import Driver
from auto_essay_typer.gui.main_window import MainWindow
from auto_essay_typer.gui.onboarding import OnboardingDialog
from auto_essay_typer.gui.run_actions import RunActions
from auto_essay_typer.gui.settings_store import SettingsStore
from auto_essay_typer.gui.shell import AppShell
from auto_essay_typer.gui.theme import ThemeManager
from tests.gui.fakes import (
    FakeHotkeyBackend,
    Recorder,
    build_tray,
    failing_backend,
    granted,
    hotkey_service,
    not_granted,
)
from tests.gui.test_run_actions import Gate, _gated_factory


def _instant_demo_driver(keyboard) -> Driver:
    return Driver(keyboard=keyboard, sleep=lambda _seconds: None, countdown_seconds=0)


class ThreadRecordingActions(RunActions):
    """The real actions, recording which thread each one ran on before doing its work."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.threads: dict[str, list[threading.Thread]] = defaultdict(list)

    def start_from_clipboard(self):
        self.threads["clipboard"].append(threading.current_thread())
        return super().start_from_clipboard()

    def start_from_type_page(self):
        self.threads["start"].append(threading.current_thread())
        return super().start_from_type_page()

    def toggle_pause(self):
        self.threads["pause"].append(threading.current_thread())
        return super().toggle_pause()

    def stop_if_running(self):
        self.threads["stop"].append(threading.current_thread())
        return super().stop_if_running()


class Rig:
    """The pieces of one test, together."""

    def __init__(self, window, shell, tray, tray_fakes, service, backends, actions, store, quit_calls, keyboards):
        self.window = window
        self.shell = shell
        self.tray = tray
        self.tray_fakes = tray_fakes
        self.service = service
        self.backends = backends
        self.actions = actions
        self.store = store
        self.quit_calls = quit_calls
        self.keyboards = keyboards

    @property
    def controller(self):
        return self.window.run_controller

    def hotkey(self, action: str) -> str:
        return self.store.settings.hotkeys[action]


@pytest.fixture
def make_rig(qtbot, qapp, tmp_path, recording_driver_factory):
    made: list[Rig] = []

    def build(
        *,
        onboarding_complete: bool = True,
        store_changes: dict | None = None,
        tray_available: bool = True,
        backends=(),
        driver_factory=None,
        keyboards=None,
        clipboard: str = "Text from the clipboard.",
        permission=granted,
        confirm=True,
        real_actions: bool = False,
    ) -> Rig:
        store = SettingsStore(tmp_path / "settings.json")
        # Confirmation off: these tests are about the wiring, and the confirmation has its own.
        changes: dict = {"confirm_before_typing": False}
        if onboarding_complete:
            changes["onboarding_complete"] = True  # otherwise it stays at its default, False
        changes.update(store_changes or {})
        store.update(**changes)

        factory, recorded = recording_driver_factory
        service, backend_factory = hotkey_service(*backends)
        window = MainWindow(
            ThemeManager(qapp),
            driver_factory=driver_factory if driver_factory is not None else factory,
            permission_checker=lambda: permission(),
            settings=QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
            settings_store=store,
            hotkey_service=service,
            onboarding_factory=functools.partial(
                OnboardingDialog, driver_factory=_instant_demo_driver, open_settings=lambda: True
            ),
        )
        qtbot.addWidget(window)
        tray, tray_fakes = build_tray(window.run_controller, available=tray_available)
        quit_calls: list[int] = []
        actions = None
        if not real_actions:
            actions = ThreadRecordingActions(
                window,
                clipboard_text=lambda: clipboard,
                confirm=Recorder(confirm),
                permission_prompt=Recorder(True),
                notify=Recorder(),
            )
        shell = AppShell(window, tray=tray, actions=actions, quit_app=lambda: quit_calls.append(1))
        rig = Rig(window, shell, tray, tray_fakes, service, backend_factory, actions, store, quit_calls,
                  keyboards if keyboards is not None else recorded)
        made.append(rig)
        return rig

    yield build
    for rig in made:
        rig.shell.shutdown()
        rig.service.stop()


def _wait_hotkeys(qtbot, rig, state="active"):
    qtbot.waitUntil(lambda: rig.service.state == state, timeout=3000)


def _typed(rig, text: str) -> bool:
    """Whether the latest recording keyboard has been typed exactly ``text``."""
    return bool(rig.keyboards) and rig.keyboards[-1].text == text


# --- the tray drives the window and the run -----------------------------------------------------------------------


def test_the_tray_menu_follows_the_windows_own_run_controller(make_rig):
    rig = make_rig()
    assert not rig.tray.pause_action.isEnabled() and rig.tray.clipboard_action.isEnabled()  # idle

    rig.controller.stateChanged.emit("typing")  # the controller the Type page uses

    assert rig.tray.pause_action.isEnabled() and rig.tray.stop_action.isEnabled()
    assert not rig.tray.clipboard_action.isEnabled()  # no second run on top of this one


def test_show_window_brings_the_window_forward_and_restores_a_minimised_one(qtbot, make_rig):
    rig = make_rig()
    rig.window.show()
    qtbot.waitExposed(rig.window)
    rig.window.showMinimized()
    assert rig.window.isMinimized()

    rig.tray.show_action.trigger()

    assert not rig.window.isMinimized()
    assert rig.window.isVisible()


def test_show_window_also_works_for_a_window_that_was_never_shown(make_rig):
    rig = make_rig()
    assert not rig.window.isVisible()
    rig.tray.show_action.trigger()
    assert rig.window.isVisible()


def test_start_from_clipboard_types_through_the_run_controller_the_type_page_uses(qtbot, make_rig):
    rig = make_rig(clipboard="Typed from the tray.")

    rig.tray.clipboard_action.trigger()
    qtbot.waitUntil(lambda: _typed(rig, "Typed from the tray."), timeout=8000)

    assert rig.actions.threads["clipboard"] == [threading.main_thread()]
    qtbot.waitUntil(lambda: not rig.controller.is_busy, timeout=8000)


def test_pause_resume_and_stop_from_the_tray_drive_the_run(qtbot, make_rig):
    keyboards = []
    gate = Gate(until=lambda: bool(keyboards) and len(keyboards[-1].text) >= 3)
    rig = make_rig(
        driver_factory=_gated_factory(gate, keyboards),
        keyboards=keyboards,
        clipboard="A long enough line that the run is mid-flight when the menu is used.",
    )
    try:
        rig.tray.clipboard_action.trigger()
        assert gate.blocked.wait(8)
        # The menu follows the run through a signal from the typing thread; a person can only
        # click Pause once it has caught up (a disabled action ignores trigger()).
        qtbot.waitUntil(lambda: rig.tray.pause_action.isEnabled(), timeout=5000)
        assert rig.controller.state == "typing"
        assert rig.tray.pause_action.text() == "Pause"

        rig.tray.pause_action.trigger()
        assert rig.controller.state == "paused"
        assert rig.tray.pause_action.text() == "Resume"  # the menu follows the run

        rig.tray.pause_action.trigger()
        assert rig.controller.state == "resuming"

        rig.tray.stop_action.trigger()
    finally:
        gate.release.set()
    qtbot.waitUntil(lambda: not rig.controller.is_busy, timeout=8000)
    assert rig.controller.state == "stopped"


def test_quit_stops_the_run_closes_the_window_and_ends_the_app(qtbot, make_rig):
    keyboards = []
    gate = Gate()
    rig = make_rig(driver_factory=_gated_factory(gate, keyboards), keyboards=keyboards)
    rig.window.show()
    qtbot.waitExposed(rig.window)
    try:
        rig.tray.clipboard_action.trigger()
        assert gate.blocked.wait(8)

        rig.tray.quit_action.trigger()

        assert rig.quit_calls == [1]  # the application was asked to quit...
        assert not rig.window.isVisible()  # ...after the window was closed (saving its geometry)
    finally:
        gate.release.set()
    qtbot.waitUntil(lambda: not rig.controller.is_busy, timeout=8000)
    assert rig.controller.state == "stopped"  # the run was stopped, not left typing into a dead app


def test_clicking_the_tray_icon_shows_the_window(make_rig):
    rig = make_rig()
    (fake,) = rig.tray_fakes
    fake.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
    assert rig.window.isVisible()


# --- no tray: silence ----------------------------------------------------------------------------------------------------------


def test_without_a_tray_the_shell_starts_quietly_and_nothing_is_built(qtbot, make_rig):
    rig = make_rig(tray_available=False, store_changes={"global_hotkeys_enabled": False})

    rig.shell.start()  # must not raise, show a dialog or log an error

    assert rig.tray.available is False
    assert rig.tray_fakes == []  # the factory was never even called
    assert QApplication.activeModalWidget() is None
    rig.shell.shutdown()  # and shutting down is just as quiet


def test_without_a_tray_a_refused_start_still_reaches_the_user_through_the_status_bar(qtbot, make_rig):
    rig = make_rig(tray_available=False, real_actions=True)
    rig.shell.actions._clipboard_text = lambda: ""  # nothing to type

    rig.tray.clipboard_action.trigger()  # the actions exist even where the menu does not

    assert "no text on the clipboard" in rig.window.statusBar().currentMessage()


def test_with_a_tray_a_refused_start_is_a_balloon_as_well(qtbot, make_rig):
    rig = make_rig(real_actions=True)
    rig.shell.actions._clipboard_text = lambda: ""
    rig.shell.start()  # the icon is shown, so a balloon can appear
    (fake,) = rig.tray_fakes

    rig.tray.clipboard_action.trigger()

    assert fake.messages and "no text on the clipboard" in fake.messages[-1][1]
    assert "no text on the clipboard" in rig.window.statusBar().currentMessage()


def test_the_real_tray_class_degrades_without_a_notification_area(qtbot, qapp, tmp_path):
    if QSystemTrayIcon.isSystemTrayAvailable():
        pytest.skip("this platform has a real tray; the test would put an icon in it")
    store = SettingsStore(tmp_path / "settings.json")
    store.update(onboarding_complete=True, global_hotkeys_enabled=False)
    window = MainWindow(
        ThemeManager(qapp),
        driver_factory=lambda keymap=None: None,
        permission_checker=granted,
        settings=QSettings(str(tmp_path / "w.ini"), QSettings.Format.IniFormat),
        settings_store=store,
    )
    qtbot.addWidget(window)

    shell = AppShell(window)  # the real AppTray over the real (absent) tray

    assert shell.tray.available is False
    shell.start()
    shell.shutdown()
    assert QApplication.activeModalWidget() is None


# --- a run that ends while the window is out of the way -------------------------------------------------------------------------


def test_a_finished_run_pops_a_balloon_when_the_window_is_not_active(qtbot, make_rig):
    rig = make_rig()
    rig.shell.start()
    (fake,) = rig.tray_fakes

    rig.controller.finished.emit("finished", "Done.")
    rig.controller.finished.emit("stopped", "Stopped.")
    rig.controller.finished.emit("failed", "The keyboard is gone.")
    qtbot.waitUntil(lambda: len(fake.messages) == 3, timeout=3000)

    assert [message for _title, message in fake.messages] == [
        "Finished typing.",
        "Typing stopped.",
        "Typing failed. The keyboard is gone.",
    ]


def test_no_balloon_when_the_window_is_the_active_window(qtbot, make_rig, monkeypatch):
    rig = make_rig()
    rig.shell.start()
    (fake,) = rig.tray_fakes
    monkeypatch.setattr(rig.window, "isActiveWindow", lambda: True)

    rig.controller.finished.emit("finished", "Done.")
    qtbot.wait(50)

    assert fake.messages == []


# --- the global hotkeys: when they start ---------------------------------------------------------------------------------------------


def test_enabled_hotkeys_start_with_the_shell_and_the_backend_is_built_on_the_main_thread(qtbot, make_rig):
    rig = make_rig()
    assert rig.backends.built == []  # nothing before start()

    rig.shell.start()
    _wait_hotkeys(qtbot, rig)

    (backend,) = rig.backends.built
    assert backend.built_on is threading.main_thread()  # the macOS rule, through the whole app wiring
    assert backend.prepare_thread is threading.main_thread()
    assert backend.start_thread is not threading.main_thread()  # only the blocking start is off-thread
    assert set(backend.bindings) == set(rig.store.settings.hotkeys.values())  # the saved hotkeys


def test_disabled_hotkeys_are_never_started(qtbot, make_rig):
    rig = make_rig(store_changes={"global_hotkeys_enabled": False})

    rig.shell.start()
    qtbot.wait(100)

    assert rig.backends.built == []
    assert rig.service.state == "off"


def test_hotkeys_follow_the_settings_after_startup(qtbot, make_rig):
    rig = make_rig(store_changes={"global_hotkeys_enabled": False})
    rig.shell.start()
    assert rig.backends.built == []

    rig.store.update(global_hotkeys_enabled=True)  # turned on in Settings
    _wait_hotkeys(qtbot, rig)
    first = rig.backends.built[0]
    assert first.started and first.built_on is threading.main_thread()

    rig.store.update(countdown_seconds=9)  # an unrelated setting changes nothing
    assert len(rig.backends.built) == 1

    new_start = "<ctrl>+<alt>+<shift>+k" if "<ctrl>" in rig.hotkey("start") else "<cmd>+<alt>+<shift>+k"
    rig.store.update(hotkeys={**dict(rig.store.settings.hotkeys), "start": new_start})  # a hotkey is edited
    qtbot.waitUntil(lambda: len(rig.backends.built) == 2 and rig.service.state == "active", timeout=3000)
    assert first.stop_calls == 1  # the old listener was stopped
    assert new_start in rig.backends.built[1].bindings
    assert rig.backends.built[1].built_on is threading.main_thread()

    rig.store.update(global_hotkeys_enabled=False)  # and off again
    assert rig.service.state == "off"
    assert rig.backends.built[1].stop_calls == 1


def test_settings_changes_before_the_shell_has_started_do_not_start_the_listener(qtbot, make_rig):
    rig = make_rig(store_changes={"global_hotkeys_enabled": False})

    rig.store.update(global_hotkeys_enabled=True)
    qtbot.wait(100)

    assert rig.backends.built == []  # start() has not been called: nothing is listening yet


# --- the global hotkeys: what they do ----------------------------------------------------------------------------------------------


def test_the_start_hotkey_types_the_type_page_and_arrives_on_the_gui_thread(qtbot, make_rig):
    rig = make_rig()
    rig.window.type_page.set_text("Typed by the start hotkey.")
    rig.shell.start()
    _wait_hotkeys(qtbot, rig)

    rig.backends.live.press(rig.hotkey("start"))  # fired from a real background thread, like pynput's
    qtbot.waitUntil(lambda: _typed(rig, "Typed by the start hotkey."), timeout=8000)

    assert rig.actions.threads["start"] == [threading.main_thread()]  # never the listener's thread
    qtbot.waitUntil(lambda: not rig.controller.is_busy, timeout=8000)


def test_the_pause_hotkey_toggles_pause_on_the_gui_thread(qtbot, make_rig):
    keyboards = []
    gate = Gate(until=lambda: bool(keyboards) and len(keyboards[-1].text) >= 3)
    rig = make_rig(
        driver_factory=_gated_factory(gate, keyboards),
        keyboards=keyboards,
        clipboard="A line that is long enough to still be typing when the hotkey is pressed.",
    )
    rig.shell.start()
    _wait_hotkeys(qtbot, rig)
    try:
        rig.tray.clipboard_action.trigger()
        assert gate.blocked.wait(8)
        qtbot.waitUntil(lambda: rig.controller.state == "typing", timeout=5000)

        rig.backends.live.press(rig.hotkey("pause"))
        qtbot.waitUntil(lambda: rig.controller.state == "paused", timeout=3000)
        assert rig.actions.threads["pause"] == [threading.main_thread()]

        rig.backends.live.press(rig.hotkey("stop"))
        qtbot.waitUntil(lambda: bool(rig.actions.threads["stop"]), timeout=3000)
        assert rig.actions.threads["stop"] == [threading.main_thread()]
    finally:
        gate.release.set()
    qtbot.waitUntil(lambda: not rig.controller.is_busy, timeout=8000)
    assert rig.controller.state == "stopped"  # Escape stopped it because a run was in flight


def test_the_stop_hotkey_is_ignored_when_nothing_is_running(qtbot, make_rig):
    rig = make_rig()
    rig.shell.start()
    _wait_hotkeys(qtbot, rig)
    finished = []
    rig.controller.finished.connect(lambda outcome, message: finished.append(outcome))

    rig.backends.live.press(rig.hotkey("stop"))  # Escape, pressed in some other app, for some other reason
    qtbot.waitUntil(lambda: bool(rig.actions.threads["stop"]), timeout=3000)

    assert finished == []
    assert rig.controller.state == "idle"


def test_the_start_hotkey_with_an_empty_type_page_says_so_instead_of_doing_nothing(qtbot, make_rig):
    rig = make_rig(real_actions=True)
    rig.shell.start()
    _wait_hotkeys(qtbot, rig)
    (fake,) = rig.tray_fakes

    rig.backends.live.press(rig.hotkey("start"))
    qtbot.waitUntil(lambda: bool(fake.messages), timeout=3000)

    assert "The Type page is empty" in fake.messages[-1][1]


# --- the global hotkeys: failing soft ----------------------------------------------------------------------------------------------------


def test_hotkeys_that_cannot_start_leave_a_quiet_note_and_the_app_carries_on(qtbot, make_rig):
    reason = "Global hotkeys need Accessibility and Input Monitoring access on macOS"
    rig = make_rig(backends=(failing_backend(reason),))
    rig.window.show()
    qtbot.waitExposed(rig.window)

    rig.shell.start()  # must not raise
    _wait_hotkeys(qtbot, rig, "unavailable")

    assert rig.service.detail == reason
    assert rig.window.hotkey_status_button.isHidden() is False  # a small note in the footer...
    assert reason in rig.window.hotkey_status_button.toolTip()  # ...with the reason under it
    assert QApplication.activeModalWidget() is None  # no dialog
    assert rig.window.isVisible()  # the window is up, and the tray too
    assert rig.tray_fakes[0].visible
    # ...and the app still does everything else: a run works.
    rig.tray.clipboard_action.trigger()
    qtbot.waitUntil(lambda: _typed(rig, "Text from the clipboard."), timeout=8000)


def test_a_listener_that_cannot_even_be_built_is_the_same_quiet_failure(qtbot, make_rig):
    rig = make_rig(backends=(FakeHotkeyBackend(prepare_error=RuntimeError("the keyboard layout lookup failed")),))

    rig.shell.start()  # the failure happens on the GUI thread, during start(): still soft

    assert rig.service.state == "unavailable"
    assert "keyboard layout lookup failed" in rig.service.detail
    assert rig.window.hotkey_status_button.isHidden() is False
    assert QApplication.activeModalWidget() is None


def test_the_footer_note_leads_to_settings_where_the_hotkeys_can_be_retried(qtbot, make_rig):
    rig = make_rig(backends=(failing_backend("This session blocks global hotkeys (Wayland)"),))
    rig.shell.start()
    _wait_hotkeys(qtbot, rig, "unavailable")

    rig.window.hotkey_status_button.click()
    settings_page = rig.window.page("settings")
    assert rig.window.page_stack.currentWidget() is settings_page
    assert "Wayland" in settings_page.hotkey_status_label.text()

    settings_page.hotkey_retry_button.click()  # the next backend (a plain one) starts
    _wait_hotkeys(qtbot, rig)
    assert rig.window.hotkey_status_button.isHidden()  # the note goes away once they work


# --- stopping cleanly ------------------------------------------------------------------------------------------------------------------


def test_shutdown_stops_the_listener_hides_the_tray_and_stops_any_run(qtbot, make_rig):
    keyboards = []
    gate = Gate()
    rig = make_rig(driver_factory=_gated_factory(gate, keyboards), keyboards=keyboards)
    rig.shell.start()
    _wait_hotkeys(qtbot, rig)
    outcomes = []
    rig.controller.finished.connect(lambda outcome, _message: outcomes.append(outcome))
    rig.tray.clipboard_action.trigger()
    assert gate.blocked.wait(8)
    (backend,) = rig.backends.built
    assert rig.tray_fakes[0].visible

    try:
        rig.shell.shutdown()
        rig.shell.shutdown()  # twice is harmless
    finally:
        gate.release.set()

    assert backend.stop_calls >= 1
    assert rig.service.state == "off"
    assert not rig.tray_fakes[0].visible
    qtbot.waitUntil(lambda: bool(outcomes), timeout=8000)
    # The run was cut short by the shutdown. Left alone it would have gone on to the end of the text
    # (the gate only holds it for a moment) and said "finished".
    assert outcomes == ["stopped"]
    assert not rig.controller.is_busy


def test_quitting_the_application_stops_the_listener(qtbot, qapp, make_rig):
    rig = make_rig()
    rig.shell.start()
    _wait_hotkeys(qtbot, rig)
    (backend,) = rig.backends.built

    qapp.aboutToQuit.emit()  # what QApplication.quit() does on its way out

    assert backend.stop_calls == 1
    assert rig.service.state == "off"


# --- first-run setup ---------------------------------------------------------------------------------------------------------------------


def _finish_walkthrough(dialog):
    dialog.next_button.click()
    dialog.next_button.click()
    dialog.finish_button.click()


def test_the_walkthrough_opens_on_a_first_run_and_the_hotkeys_wait_for_it(qtbot, make_rig):
    rig = make_rig(onboarding_complete=False)
    assert rig.window.onboarding_pending()

    rig.shell.start()

    dialog = rig.window.onboarding_dialog
    assert dialog is not None and dialog.isVisible()
    assert dialog.step_index == 0
    qtbot.wait(100)
    assert rig.backends.built == []  # no macOS permission prompt over the welcome screen

    _finish_walkthrough(dialog)

    assert rig.store.settings.onboarding_complete is True
    assert json.loads(rig.store.path.read_text(encoding="utf-8"))["onboarding_complete"] is True
    assert rig.window.onboarding_dialog is None
    _wait_hotkeys(qtbot, rig)  # and now they start
    assert rig.backends.built[0].built_on is threading.main_thread()


def test_skipping_the_walkthrough_also_completes_it(qtbot, make_rig):
    rig = make_rig(onboarding_complete=False)
    rig.shell.start()
    dialog = rig.window.onboarding_dialog

    dialog.skip_button.click()

    assert rig.store.settings.onboarding_complete is True
    assert rig.window.onboarding_dialog is None
    _wait_hotkeys(qtbot, rig)


def test_dismissing_the_walkthrough_without_finishing_leaves_it_pending_for_next_time(qtbot, make_rig):
    rig = make_rig(onboarding_complete=False)
    rig.shell.start()
    dialog = rig.window.onboarding_dialog

    dialog.reject()  # the window's close button

    assert rig.store.settings.onboarding_complete is False
    assert rig.window.onboarding_pending()
    _wait_hotkeys(qtbot, rig)  # the hotkeys still start: they are not held hostage to setup
    assert SettingsStore(rig.store.path).settings.onboarding_complete is False  # so the next launch asks again


def test_there_is_no_walkthrough_once_setup_has_been_completed(qtbot, make_rig):
    rig = make_rig(onboarding_complete=True)

    assert rig.window.maybe_show_onboarding() is False
    rig.shell.start()

    assert rig.window.onboarding_dialog is None
    _wait_hotkeys(qtbot, rig)  # straight to the hotkeys


def test_show_setup_again_on_the_help_page_reopens_the_walkthrough(qtbot, make_rig):
    rig = make_rig(onboarding_complete=True)
    rig.shell.start()
    help_page = rig.window.page("help")
    assert rig.window.onboarding_dialog is None

    help_page.show_setup_button.click()

    first = rig.window.onboarding_dialog
    assert first is not None and first.isVisible() and first.step_index == 0

    help_page.show_setup_button.click()  # pressed again while it is open: no second dialog
    assert rig.window.onboarding_dialog is first

    _finish_walkthrough(first)
    assert rig.window.onboarding_dialog is None
    assert rig.store.settings.onboarding_complete is True  # still complete

    help_page.show_setup_button.click()  # and a later request starts a fresh walkthrough from step 1
    second = rig.window.onboarding_dialog
    assert second is not None and second is not first and second.step_index == 0
    second.reject()


def test_the_permission_chip_is_refreshed_when_the_walkthrough_closes(qtbot, make_rig):
    state = {"permission": not_granted}
    rig = make_rig(onboarding_complete=False, permission=lambda: state["permission"]())
    assert rig.window.permission_chip.property("state") == "warning"
    rig.shell.start()
    dialog = rig.window.onboarding_dialog

    state["permission"] = granted  # the user granted it during step 2
    _finish_walkthrough(dialog)

    assert rig.window.permission_chip.property("state") == "ready"
    assert rig.window.permission_chip.text() == "Ready to type"


def test_the_walkthrough_is_built_with_the_windows_permission_probe_and_a_safe_demo(qtbot, make_rig):
    rig = make_rig(onboarding_complete=False, permission=not_granted)
    rig.shell.start()
    dialog = rig.window.onboarding_dialog

    dialog.next_button.click()  # step 2 reports what the window's probe says
    assert "Accessibility" in dialog.permission_status_label.text()
    dialog.next_button.click()
    dialog.try_button.click()  # the demo types into its own box, never a real keyboard
    qtbot.waitUntil(lambda: dialog.demo_completed, timeout=8000)

    assert dialog.demo_edit.toPlainText() == OnboardingDialog.SAMPLE_TEXT
    assert rig.keyboards == []  # no driver for the real keyboard was built for the demo
    dialog.reject()
