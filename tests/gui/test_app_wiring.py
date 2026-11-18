"""Tests that the parts of the window talk to each other.

* A setting changed on the Settings or Advanced page reaches the **next run**: the countdown, the
  default editor and the user's shortcut overrides are read when the run's ``Driver`` is built, so
  nothing has to be told that they changed.
* A profile saved or deleted on the Advanced page reaches the Type page and the next run.
* Settings that apply at once (theme, mini window) and the ones that apply when a run starts
  (minimise on start).
* A settings file that could not be loaded is shown, once, above the pages.

Everything is driven through the real pages where that is the point, with fakes for the keyboard
(a recording one), the name prompt and the confirmation dialogs. Runs that must be in flight use a
gate on the driver's ``sleep``; nothing real is typed.
"""

from __future__ import annotations

import json
import logging

import pytest
from PySide6.QtCore import QSettings

from auto_essay_typer import app_settings, config
from auto_essay_typer.actions import Format, FormatOp
from auto_essay_typer.driver import RecordingKeyboard, UnsupportedFormatError
from auto_essay_typer.gui.main_window import MainWindow
from auto_essay_typer.gui.pages import advanced_page as advanced_page_module
from auto_essay_typer.gui.settings_store import SettingsStore
from auto_essay_typer.gui.theme import ThemeManager, ThemeMode
from auto_essay_typer.gui.widgets import LinkButton
from auto_essay_typer.profiles import PRESETS, Profile
from tests.gui.fakes import granted
from tests.gui.test_run_actions import Gate, _gated_factory


class Keyboards:
    """A ``keyboard_factory`` that remembers each recording keyboard it built."""

    def __init__(self) -> None:
        self.built: list[RecordingKeyboard] = []

    def __call__(self) -> RecordingKeyboard:
        keyboard = RecordingKeyboard()
        self.built.append(keyboard)
        return keyboard


def _window(qtbot, qapp, tmp_path, *, store=None, keyboards=None, driver_factory=None, **store_changes) -> MainWindow:
    store = store if store is not None else SettingsStore(tmp_path / "settings.json")
    if store_changes:
        store.update(**store_changes)
    kwargs = {}
    if driver_factory is not None:
        kwargs["driver_factory"] = driver_factory
    elif keyboards is not None:
        kwargs["keyboard_factory"] = keyboards
    window = MainWindow(
        ThemeManager(qapp),
        permission_checker=granted,
        settings=QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
        settings_store=store,
        **kwargs,
    )
    qtbot.addWidget(window)
    return window


def _chord_pressed_for(driver, keyboards, op=FormatOp.TOGGLE_BOLD):
    """Run one formatting action on ``driver`` and return the chord its keyboard pressed."""
    driver.run([Format(op)])
    return keyboards.built[-1].chords[-1]


def _commit_shortcut(row, text):
    row.edit.setText(text)
    row.edit.editingFinished.emit()


# --- settings are read when the next run is built ---------------------------------------------------------------------


def test_a_run_is_built_with_the_countdown_as_saved_at_that_moment(qtbot, qapp, tmp_path):
    keyboards = Keyboards()
    window = _window(qtbot, qapp, tmp_path, keyboards=keyboards, countdown_seconds=7)
    assert window.make_driver()._countdown_seconds == 7

    window.settings_store.update(countdown_seconds=2)

    assert window.make_driver()._countdown_seconds == 2  # no restart, nothing to tell


def test_the_driver_uses_the_default_editors_keymap_with_the_users_overrides(qtbot, qapp, tmp_path):
    keyboards = Keyboards()
    window = _window(qtbot, qapp, tmp_path, keyboards=keyboards, countdown_seconds=0)
    default_bold = _chord_pressed_for(window.make_driver(), keyboards)
    assert default_bold in (("cmd", "b"), ("ctrl", "b"))  # Google Docs' own bold

    window.settings_store.update(keymap_overrides={"google-docs": {"toggle_bold": "ctrl+shift+b"}})

    assert _chord_pressed_for(window.make_driver(), keyboards) == ("ctrl", "shift", "b")


def test_an_override_for_another_editor_does_not_leak_into_the_default_one(qtbot, qapp, tmp_path):
    keyboards = Keyboards()
    window = _window(qtbot, qapp, tmp_path, keyboards=keyboards, countdown_seconds=0)
    default_bold = _chord_pressed_for(window.make_driver(), keyboards)

    window.settings_store.update(keymap_overrides={"libreoffice-writer": {"toggle_bold": "ctrl+alt+b"}})

    assert _chord_pressed_for(window.make_driver(), keyboards) == default_bold


def test_choosing_another_default_editor_changes_which_shortcuts_the_next_run_can_press(qtbot, qapp, tmp_path):
    keyboards = Keyboards()
    window = _window(qtbot, qapp, tmp_path, keyboards=keyboards, countdown_seconds=0)
    window.settings_store.update(target_app="plain-text")  # an editor with no formatting shortcuts at all

    with pytest.raises(UnsupportedFormatError, match="toggle_bold"):
        window.make_driver().run([Format(FormatOp.TOGGLE_BOLD)])


def test_a_keymap_chosen_by_the_caller_wins_over_the_settings(qtbot, qapp, tmp_path):
    keyboards = Keyboards()
    window = _window(qtbot, qapp, tmp_path, keyboards=keyboards, countdown_seconds=0)
    window.settings_store.update(keymap_overrides={"google-docs": {"toggle_bold": "ctrl+shift+b"}})

    driver = window.make_driver(keymap={FormatOp.TOGGLE_BOLD: ("ctrl", "alt", "x")})

    assert _chord_pressed_for(driver, keyboards) == ("ctrl", "alt", "x")


def test_a_keymap_that_cannot_be_resolved_falls_back_to_the_defaults_and_says_so(
    qtbot, qapp, tmp_path, monkeypatch, caplog
):
    keyboards = Keyboards()
    window = _window(qtbot, qapp, tmp_path, keyboards=keyboards, countdown_seconds=0)

    def broken(settings, os_):
        raise ValueError("a hand-edited override no longer parses")

    monkeypatch.setattr(app_settings, "effective_keymap", broken)
    with caplog.at_level(logging.WARNING, logger="auto_essay_typer.gui.main_window"):
        driver = window.make_driver()  # must not raise: a run should still be possible

    assert _chord_pressed_for(driver, keyboards) in (("cmd", "b"), ("ctrl", "b"))
    assert any("hand-edited override" in record.getMessage() for record in caplog.records)


# --- ...and the pages are where they change ----------------------------------------------------------------------------


def test_the_countdown_set_on_the_settings_page_is_the_next_runs_countdown(qtbot, qapp, tmp_path):
    window = _window(qtbot, qapp, tmp_path, keyboards=Keyboards())

    window.page("settings").countdown_spin.setValue(11)

    assert window.make_driver()._countdown_seconds == 11
    assert json.loads(window.settings_store.path.read_text(encoding="utf-8"))["countdown_seconds"] == 11


def test_the_default_editor_set_on_the_settings_page_is_the_next_runs_editor(qtbot, qapp, tmp_path):
    window = _window(qtbot, qapp, tmp_path, keyboards=Keyboards(), countdown_seconds=0)
    settings_page = window.page("settings")

    settings_page.target_combo.setCurrentIndex(settings_page.target_combo.findData("plain-text"))

    with pytest.raises(UnsupportedFormatError):
        window.make_driver().run([Format(FormatOp.TOGGLE_BOLD)])


def test_a_shortcut_override_made_on_the_advanced_page_affects_the_next_run(qtbot, qapp, tmp_path):
    keyboards = Keyboards()
    window = _window(qtbot, qapp, tmp_path, keyboards=keyboards, countdown_seconds=0)
    window._nav_buttons["advanced"].click()
    advanced = window.page_stack.currentWidget()
    default_bold = _chord_pressed_for(window.make_driver(), keyboards)

    _commit_shortcut(advanced.shortcut_rows[FormatOp.TOGGLE_BOLD], "ctrl+shift+b")

    assert window.settings_store.settings.keymap_overrides == {"google-docs": {"toggle_bold": "ctrl+shift+b"}}
    assert _chord_pressed_for(window.make_driver(), keyboards) == ("ctrl", "shift", "b")
    assert default_bold != ("ctrl", "shift", "b")


def test_restoring_the_default_shortcuts_on_the_advanced_page_restores_the_next_runs_chords(
    qtbot, qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(advanced_page_module, "default_confirm", lambda parent, title, text: True)
    keyboards = Keyboards()
    window = _window(qtbot, qapp, tmp_path, keyboards=keyboards, countdown_seconds=0)
    default_bold = _chord_pressed_for(window.make_driver(), keyboards)
    advanced = window.page("advanced")
    _commit_shortcut(advanced.shortcut_rows[FormatOp.TOGGLE_BOLD], "ctrl+shift+b")
    assert _chord_pressed_for(window.make_driver(), keyboards) == ("ctrl", "shift", "b")

    advanced.restore_defaults_button.click()

    assert _chord_pressed_for(window.make_driver(), keyboards) == default_bold


# --- a profile saved on the Advanced page is the Type page's, and the next run's ---------------------------------------------


@pytest.fixture
def named(monkeypatch):
    """Make the Advanced page's "Save as..." name prompt answer with a fixed name, and every confirmation yes."""
    answer = {"name": "wired speed"}
    monkeypatch.setattr(advanced_page_module, "default_name_prompt", lambda parent, title, label, initial: answer["name"])
    monkeypatch.setattr(advanced_page_module, "default_confirm", lambda parent, title, text: True)
    return answer


def _spy_on_starts(window, monkeypatch):
    """Record ``run_controller.start`` calls instead of running anything."""
    calls: list[tuple] = []
    monkeypatch.setattr(window.run_controller, "start", lambda text, profile, seed=None: calls.append((text, profile, seed)))
    return calls


def test_a_profile_saved_on_the_advanced_page_appears_on_the_type_page_and_is_what_the_next_run_uses(
    qtbot, qapp, tmp_path, named, monkeypatch
):
    window = _window(qtbot, qapp, tmp_path, keyboards=Keyboards())
    type_page = window.type_page
    assert type_page.profile_combo.findData("wired speed") == -1

    advanced = window.page("advanced")
    advanced.editor.rows["wpm"].spin_boxes()[0].setValue(90)
    advanced.save_as_button.click()

    assert "wired speed" in config.load_profiles()  # it is on disk (the isolated config file)...
    index = type_page.profile_combo.findData("wired speed")
    assert index >= 0  # ...and the Type page already lists it
    type_page.profile_combo.setCurrentIndex(index)
    assert type_page.effective_profile().wpm == 90.0

    starts = _spy_on_starts(window, monkeypatch)
    type_page.set_text("Hello there.")
    type_page.start_button.click()
    ((text, profile, _seed),) = starts
    assert text == "Hello there."
    assert profile.wpm == 90.0  # the edited speed, not the built-in's
    assert PRESETS["natural"].wpm != 90.0


def test_editing_the_profile_the_type_page_is_using_changes_the_next_run(qtbot, qapp, tmp_path, named, monkeypatch):
    window = _window(qtbot, qapp, tmp_path, keyboards=Keyboards())
    type_page = window.type_page
    advanced = window.page("advanced")
    advanced.editor.rows["wpm"].spin_boxes()[0].setValue(90)
    advanced.save_as_button.click()
    type_page.profile_combo.setCurrentIndex(type_page.profile_combo.findData("wired speed"))
    assert type_page.effective_profile().wpm == 90.0

    advanced.editor.rows["wpm"].spin_boxes()[0].setValue(120)
    advanced.save_button.click()  # Save (not Save as): the same profile, edited

    assert type_page.profile_combo.currentData() == "wired speed"  # still the one selected
    assert type_page.effective_profile().wpm == 120.0
    starts = _spy_on_starts(window, monkeypatch)
    type_page.set_text("Again.")
    type_page.start_button.click()
    assert starts[0][1].wpm == 120.0


def test_deleting_a_profile_on_the_advanced_page_removes_it_from_the_type_page(qtbot, qapp, tmp_path, named):
    window = _window(qtbot, qapp, tmp_path, keyboards=Keyboards())
    advanced = window.page("advanced")
    advanced.editor.rows["wpm"].spin_boxes()[0].setValue(90)
    advanced.save_as_button.click()
    assert window.type_page.profile_combo.findData("wired speed") >= 0

    advanced.delete_button.click()

    assert window.type_page.profile_combo.findData("wired speed") == -1
    assert "wired speed" not in config.load_profiles()


def test_a_profile_reload_leaves_the_pages_selection_alone_when_it_still_exists(qtbot, qapp, tmp_path, named):
    window = _window(qtbot, qapp, tmp_path, keyboards=Keyboards())
    type_page = window.type_page
    type_page.profile_combo.setCurrentIndex(type_page.profile_combo.findData("sloppy"))
    advanced = window.page("advanced")
    advanced.editor.rows["wpm"].spin_boxes()[0].setValue(90)
    advanced.save_as_button.click()  # saves "wired speed" -- the Type page keeps "sloppy" selected

    assert type_page.profile_combo.currentData() == "sloppy"


# --- the window follows the settings ----------------------------------------------------------------------------------------


def test_the_theme_changes_the_moment_it_is_saved_and_an_unrelated_change_does_not_touch_it(qtbot, qapp, tmp_path):
    window = _window(qtbot, qapp, tmp_path, keyboards=Keyboards())
    window.page("settings").theme_combo.setCurrentIndex(window.page("settings").theme_combo.findData(ThemeMode.DARK))
    assert window.theme_manager.mode is ThemeMode.DARK

    window.theme_manager.set_mode(ThemeMode.LIGHT)  # something else (a test, a future control) changed it
    window.settings_store.update(countdown_seconds=3)  # an unrelated setting is saved

    assert window.theme_manager.mode is ThemeMode.LIGHT  # not reset to what the file says


def test_a_theme_saved_by_anything_reaches_the_theme_manager(qtbot, qapp, tmp_path):
    window = _window(qtbot, qapp, tmp_path, keyboards=Keyboards())
    window.settings_store.update(theme="dark")
    assert window.theme_manager.mode is ThemeMode.DARK
    window.settings_store.update(theme="light")
    assert window.theme_manager.mode is ThemeMode.LIGHT


def test_the_mini_window_follows_its_setting_while_a_run_is_in_flight(qtbot, qapp, tmp_path):
    keyboards: list[RecordingKeyboard] = []
    gate = Gate(until=lambda: bool(keyboards) and len(keyboards[-1].text) >= 3)
    window = _window(qtbot, qapp, tmp_path, driver_factory=_gated_factory(gate, keyboards))
    assert window.show_mini_window is True
    try:
        window.run_controller.start("A line long enough to be typing for a while.", Profile(), seed=1)
        assert gate.blocked.wait(8)
        qtbot.waitUntil(lambda: window.mini_window.isVisible(), timeout=3000)

        window.settings_store.update(show_mini_window=False)  # turned off mid-run: gone at once
        assert window.show_mini_window is False
        assert not window.mini_window.isVisible()

        window.settings_store.update(show_mini_window=True)  # and back on: it returns
        assert window.mini_window.isVisible()
    finally:
        window.run_controller.stop()
        gate.release.set()
        qtbot.waitUntil(lambda: not window.run_controller.is_busy, timeout=8000)


def test_the_mini_window_never_appears_when_it_is_turned_off(qtbot, qapp, tmp_path):
    keyboards: list[RecordingKeyboard] = []
    gate = Gate(until=lambda: bool(keyboards) and len(keyboards[-1].text) >= 3)
    window = _window(
        qtbot, qapp, tmp_path, driver_factory=_gated_factory(gate, keyboards), show_mini_window=False
    )
    try:
        window.run_controller.start("A line long enough to be typing for a while.", Profile(), seed=1)
        assert gate.blocked.wait(8)
        qtbot.waitUntil(lambda: window.run_controller.state == "typing", timeout=3000)
        qapp.processEvents()
        assert not window.mini_window.isVisible()
    finally:
        window.run_controller.stop()
        gate.release.set()
        qtbot.waitUntil(lambda: not window.run_controller.is_busy, timeout=8000)


def _start_gated_run(window, keyboards, gate):
    window.run_controller.start("A line long enough to be typing for a while.", Profile(), seed=1)
    assert gate.blocked.wait(8)


def test_minimise_on_start_minimises_the_window_when_a_run_begins(qtbot, qapp, tmp_path):
    keyboards: list[RecordingKeyboard] = []
    gate = Gate(until=lambda: bool(keyboards) and len(keyboards[-1].text) >= 3)
    window = _window(
        qtbot, qapp, tmp_path, driver_factory=_gated_factory(gate, keyboards), minimize_on_start=True
    )
    window.show()
    qtbot.waitExposed(window)
    assert not window.isMinimized()
    try:
        _start_gated_run(window, keyboards, gate)
        qtbot.waitUntil(window.isMinimized, timeout=3000)
    finally:
        window.run_controller.stop()
        gate.release.set()
        qtbot.waitUntil(lambda: not window.run_controller.is_busy, timeout=8000)


def test_without_the_setting_the_window_stays_where_it_is(qtbot, qapp, tmp_path):
    keyboards: list[RecordingKeyboard] = []
    gate = Gate(until=lambda: bool(keyboards) and len(keyboards[-1].text) >= 3)
    window = _window(qtbot, qapp, tmp_path, driver_factory=_gated_factory(gate, keyboards))
    window.show()
    qtbot.waitExposed(window)
    try:
        _start_gated_run(window, keyboards, gate)
        qtbot.waitUntil(lambda: window.run_controller.state == "typing", timeout=3000)
        qapp.processEvents()
        assert not window.isMinimized()
    finally:
        window.run_controller.stop()
        gate.release.set()
        qtbot.waitUntil(lambda: not window.run_controller.is_busy, timeout=8000)


def test_a_window_the_user_hid_is_not_brought_back_just_to_be_minimised(qtbot, qapp, tmp_path):
    keyboards: list[RecordingKeyboard] = []
    gate = Gate(until=lambda: bool(keyboards) and len(keyboards[-1].text) >= 3)
    window = _window(
        qtbot, qapp, tmp_path, driver_factory=_gated_factory(gate, keyboards), minimize_on_start=True
    )
    assert not window.isVisible()  # e.g. started from the tray with the window put away
    try:
        _start_gated_run(window, keyboards, gate)
        qtbot.waitUntil(lambda: window.run_controller.state == "typing", timeout=3000)
        qapp.processEvents()
        assert not window.isVisible()
    finally:
        window.run_controller.stop()
        gate.release.set()
        qtbot.waitUntil(lambda: not window.run_controller.is_busy, timeout=8000)


def test_minimising_happens_once_per_run_and_not_again_after_the_user_restores_the_window(qtbot, qapp, tmp_path):
    keyboards: list[RecordingKeyboard] = []
    gate = Gate(until=lambda: bool(keyboards) and len(keyboards[-1].text) >= 3)
    window = _window(
        qtbot, qapp, tmp_path, driver_factory=_gated_factory(gate, keyboards), minimize_on_start=True
    )
    window.show()
    qtbot.waitExposed(window)
    try:
        _start_gated_run(window, keyboards, gate)
        qtbot.waitUntil(window.isMinimized, timeout=3000)

        window.bring_to_front()  # the user brings it back mid-run (tray: Show window)
        window.run_controller.pause()  # state changes during the same run must not minimise it again
        qapp.processEvents()
        assert not window.isMinimized()
    finally:
        window.run_controller.stop()
        gate.release.set()
        qtbot.waitUntil(lambda: not window.run_controller.is_busy, timeout=8000)


# --- a settings file that could not be loaded -----------------------------------------------------------------------------------


def test_a_settings_file_that_could_not_be_read_is_reported_once_above_the_pages(qtbot, qapp, tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ this is not json", encoding="utf-8")
    store = SettingsStore(path)
    assert store.load_warning is not None

    window = _window(qtbot, qapp, tmp_path, store=store)

    banner = window.notice_banner
    assert banner is not None and window._notice_holder.isHidden() is False
    assert "could not be read" in banner.message()
    assert str(path) in banner.message()  # which file, so it can be found and fixed
    assert "defaults are in use" in banner.message()
    assert path.read_text(encoding="utf-8") == "{ this is not json"  # loading never rewrites the file
    assert store.load_warning is None  # taken: shown once

    # A second window on the same store (a second launch in one process) does not repeat it.
    again = _window(qtbot, qapp, tmp_path, store=store)
    assert again.notice_banner is None and again._notice_holder.isHidden()


def test_the_notice_can_be_dismissed_and_leads_to_settings(qtbot, qapp, tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")  # valid JSON, wrong shape
    window = _window(qtbot, qapp, tmp_path, store=SettingsStore(path))
    banner = window.notice_banner
    assert banner is not None
    (link,) = banner.findChildren(LinkButton)
    assert link.text() == "Open Settings" and link.accessibleName() == "Open Settings"

    link.click()
    assert window.page_stack.currentWidget() is window.page("settings")

    banner.dismiss()
    assert window._notice_holder.isHidden()


def test_the_notice_says_the_file_is_replaced_by_the_next_change_and_it_is(qtbot, qapp, tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("not json at all", encoding="utf-8")
    window = _window(qtbot, qapp, tmp_path, store=SettingsStore(path))
    assert "replaced the next time you change a setting" in window.notice_banner.message()

    window.page("settings").countdown_spin.setValue(4)

    assert json.loads(path.read_text(encoding="utf-8"))["countdown_seconds"] == 4  # valid again


def test_a_new_notice_replaces_the_old_one(qtbot, qapp, tmp_path):
    window = _window(qtbot, qapp, tmp_path)
    assert window.notice_banner is None and window._notice_holder.isHidden()

    window.show_notice("First.", "warning", action=("Do it", lambda: None))
    first = window.notice_banner
    window.show_notice("Second.", "danger")

    assert window.notice_banner is not first
    assert window.notice_banner.message() == "Second."
    assert window.notice_banner.findChildren(LinkButton) == []  # the old link went with the old banner
    assert window._notice_holder.isHidden() is False


def test_the_notice_holder_takes_no_room_until_there_is_a_notice(qtbot, qapp, tmp_path):
    window = _window(qtbot, qapp, tmp_path)
    window.show()
    qtbot.waitExposed(window)
    assert window._notice_holder.isHidden()
    assert window._notice_holder.height() <= 1 or not window._notice_holder.isVisible()
