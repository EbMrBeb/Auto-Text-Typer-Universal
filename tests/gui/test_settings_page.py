"""Tests for the Settings page (``gui/pages/settings_page.py``).

The page reads from and writes to a :class:`SettingsStore`, so most of what is checked here
is *persistence*: change a control, then look at the JSON file on disk and at a brand-new
store and page that read it back. The autouse ``_isolated_config`` fixture points
``AUTO_TEXT_TYPER_SETTINGS`` at ``tmp_path``, so the store's default path is the isolated
file and the real settings are never read or written. Nothing opens a real dialog (the reset
confirmation is injected) or starts a hotkey listener (the status tests use a service over a
fake backend).
"""

from __future__ import annotations

import json

import pytest
from PySide6.QtCore import Qt

from auto_essay_typer import app_settings, platforms
from auto_essay_typer.app_settings import AppSettings, default_hotkeys
from auto_essay_typer.gui.pages.settings_page import (
    HOTKEY_SYNTAX_HINT,
    SettingsPage,
    describe_hotkey_problem,
)
from auto_essay_typer.gui.settings_store import SettingsStore
from auto_essay_typer.gui.theme import ThemeManager, ThemeMode
from auto_essay_typer.platforms import OperatingSystem
from tests.gui.fakes import Recorder, failing_backend, hotkey_service

LINUX_HOTKEYS = {
    "start": "<ctrl>+<alt>+<shift>+t",
    "pause": "<ctrl>+<alt>+<shift>+p",
    "stop": "<esc>",
}


@pytest.fixture
def settings_file(tmp_path):
    return tmp_path / "settings.json"


@pytest.fixture
def linux(monkeypatch):
    """Pretend to be Linux: ctrl-based default hotkeys, and Google Docs' ctrl shortcuts."""
    monkeypatch.setattr(platforms, "current_os", lambda *a, **k: OperatingSystem.LINUX)


def _make_page(qtbot, qapp, *, store=None, confirm=None, service=None, mode=ThemeMode.LIGHT):
    theme_manager = ThemeManager(qapp, mode=mode)
    store = store if store is not None else SettingsStore()
    page = SettingsPage(
        theme_manager,
        store,
        hotkey_service=service,
        confirm=confirm if confirm is not None else Recorder(True),
    )
    qtbot.addWidget(page)
    return page, store, theme_manager


def _on_disk(settings_file) -> dict:
    return json.loads(settings_file.read_text(encoding="utf-8"))


def _commit(qtbot, page, action, text):
    """Type ``text`` into one hotkey field and press Enter, as a user would."""
    edit = page.hotkey_edits[action]
    edit.setText(text)
    qtbot.keyClick(edit, Qt.Key.Key_Return)


# --- it starts from what is saved ---------------------------------------------------------------


def test_the_store_in_these_tests_is_the_isolated_file_and_nothing_is_written_until_a_change(
    qtbot, qapp, settings_file
):
    page, store, _manager = _make_page(qtbot, qapp)
    assert store.path == settings_file  # the env override, so never the real config
    assert not settings_file.exists()  # building the page saves nothing
    assert page.countdown_spin.value() == AppSettings().countdown_seconds


def test_the_controls_start_from_the_saved_settings(qtbot, qapp, settings_file):
    store = SettingsStore()
    store.update(
        target_app="plain-text",
        countdown_seconds=9,
        confirm_before_typing=False,
        minimize_on_start=True,
        show_mini_window=False,
        global_hotkeys_enabled=False,
        theme="dark",
        hotkeys={"start": "<f9>", "pause": "<f10>", "stop": "<esc>"},
    )

    page, _store, _manager = _make_page(qtbot, qapp, store=SettingsStore())  # a fresh store reads the file

    assert page.target_combo.currentData() == "plain-text"
    assert page.countdown_spin.value() == 9
    assert page.confirm_switch.isChecked() is False
    assert page.minimize_switch.isChecked() is True
    assert page.mini_window_checkbox.isChecked() is False
    assert page.hotkeys_switch.isChecked() is False
    assert page.theme_combo.currentData() == ThemeMode.DARK
    assert {a: e.text() for a, e in page.hotkey_edits.items()} == {
        "start": "<f9>",
        "pause": "<f10>",
        "stop": "<esc>",
    }


def test_the_editor_choices_are_the_ones_this_system_supports(qtbot, qapp):
    page, _store, _manager = _make_page(qtbot, qapp)
    ids = [page.target_combo.itemData(i) for i in range(page.target_combo.count())]
    from auto_essay_typer import targets

    assert ids == [t.id for t in targets.targets_for(platforms.current_os())]
    assert "google-docs" in ids and "plain-text" in ids


# --- every preference is saved the moment it changes ---------------------------------------------


def test_choosing_the_default_editor_saves_it(qtbot, qapp, settings_file):
    page, store, _manager = _make_page(qtbot, qapp)
    page.target_combo.setCurrentIndex(page.target_combo.findData("plain-text"))
    assert store.settings.target_app == "plain-text"
    assert _on_disk(settings_file)["target_app"] == "plain-text"


def test_changing_the_countdown_saves_it(qtbot, qapp, settings_file):
    page, store, _manager = _make_page(qtbot, qapp)
    page.countdown_spin.setValue(12)
    assert store.settings.countdown_seconds == 12
    assert _on_disk(settings_file)["countdown_seconds"] == 12


def test_the_countdown_can_be_zero_and_says_so(qtbot, qapp, settings_file):
    page, store, _manager = _make_page(qtbot, qapp)
    page.countdown_spin.setValue(0)
    assert _on_disk(settings_file)["countdown_seconds"] == 0
    assert page.countdown_spin.text() == "No countdown"
    assert page.countdown_spin.maximum() == 30  # the range AppSettings accepts


@pytest.mark.parametrize(
    ("switch_name", "setting"),
    [
        ("confirm_switch", "confirm_before_typing"),
        ("minimize_switch", "minimize_on_start"),
        ("mini_window_checkbox", "show_mini_window"),
        ("hotkeys_switch", "global_hotkeys_enabled"),
    ],
)
def test_each_switch_saves_both_ways(qtbot, qapp, settings_file, switch_name, setting):
    page, store, _manager = _make_page(qtbot, qapp)
    switch = getattr(page, switch_name)
    original = getattr(AppSettings(), setting)

    switch.setChecked(not original)
    assert getattr(store.settings, setting) is (not original)
    assert _on_disk(settings_file)[setting] is (not original)

    switch.setChecked(original)
    assert getattr(store.settings, setting) is original
    assert _on_disk(settings_file)[setting] is original


def test_changing_the_theme_saves_it_and_applies_it_at_once(qtbot, qapp, settings_file):
    page, store, manager = _make_page(qtbot, qapp)
    page.theme_combo.setCurrentIndex(page.theme_combo.findData(ThemeMode.DARK))
    assert manager.mode is ThemeMode.DARK  # live, no restart
    assert store.settings.theme == "dark"
    assert _on_disk(settings_file)["theme"] == "dark"

    page.theme_combo.setCurrentIndex(page.theme_combo.findData(ThemeMode.LIGHT))
    assert manager.mode is ThemeMode.LIGHT
    assert _on_disk(settings_file)["theme"] == "light"


def test_valid_hotkeys_are_saved(qtbot, qapp, settings_file, linux):
    page, store, _manager = _make_page(qtbot, qapp)
    _commit(qtbot, page, "start", "<ctrl>+<alt>+<shift>+k")
    _commit(qtbot, page, "pause", "<f10>")
    _commit(qtbot, page, "stop", "<f12>")

    assert dict(store.settings.hotkeys) == {
        "start": "<ctrl>+<alt>+<shift>+k",
        "pause": "<f10>",
        "stop": "<f12>",
    }
    assert _on_disk(settings_file)["hotkeys"] == dict(store.settings.hotkeys)
    assert page.hotkey_error_label.isHidden()


def test_every_preference_round_trips_through_the_file_into_a_fresh_store_and_page(
    qtbot, qapp, settings_file, linux
):
    page, _store, _manager = _make_page(qtbot, qapp)
    page.target_combo.setCurrentIndex(page.target_combo.findData("libreoffice-writer"))
    page.countdown_spin.setValue(17)
    page.confirm_switch.setChecked(False)
    page.minimize_switch.setChecked(True)
    page.mini_window_checkbox.setChecked(False)
    page.theme_combo.setCurrentIndex(page.theme_combo.findData(ThemeMode.DARK))
    page.hotkeys_switch.setChecked(False)
    _commit(qtbot, page, "start", "<ctrl>+<alt>+<shift>+j")
    _commit(qtbot, page, "pause", "<ctrl>+<alt>+<shift>+k")
    _commit(qtbot, page, "stop", "<f12>")

    saved = _on_disk(settings_file)
    assert saved["target_app"] == "libreoffice-writer"
    assert saved["countdown_seconds"] == 17
    assert saved["confirm_before_typing"] is False
    assert saved["minimize_on_start"] is True
    assert saved["show_mini_window"] is False
    assert saved["theme"] == "dark"
    assert saved["global_hotkeys_enabled"] is False
    assert saved["hotkeys"] == {
        "start": "<ctrl>+<alt>+<shift>+j",
        "pause": "<ctrl>+<alt>+<shift>+k",
        "stop": "<f12>",
    }

    reloaded_store = SettingsStore()  # what the next launch would load
    assert reloaded_store.load_warning is None
    again, _s, _m = _make_page(qtbot, qapp, store=reloaded_store)
    assert again.target_combo.currentData() == "libreoffice-writer"
    assert again.countdown_spin.value() == 17
    assert again.confirm_switch.isChecked() is False
    assert again.minimize_switch.isChecked() is True
    assert again.mini_window_checkbox.isChecked() is False
    assert again.theme_combo.currentData() == ThemeMode.DARK
    assert again.hotkeys_switch.isChecked() is False
    assert {a: e.text() for a, e in again.hotkey_edits.items()} == saved["hotkeys"]


def test_the_page_follows_a_change_made_somewhere_else(qtbot, qapp):
    page, store, _manager = _make_page(qtbot, qapp)
    store.update(countdown_seconds=3, minimize_on_start=True, confirm_before_typing=False)
    assert page.countdown_spin.value() == 3
    assert page.minimize_switch.isChecked() is True
    assert page.confirm_switch.isChecked() is False


# --- an invalid hotkey is explained and never saved -------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "text", "fragment"),
    [
        ("start", "<ctrl>+<nonsense>", "'<nonsense>' is not a key"),
        ("pause", "<ctrl>+", "is not a valid hotkey"),
        ("stop", "", "Enter a hotkey"),
        ("start", "ctrl+alt+t", "is not a key"),
    ],
)
def test_an_unparsable_hotkey_is_rejected_with_its_reason_and_not_saved(
    qtbot, qapp, settings_file, linux, action, text, fragment
):
    page, store, _manager = _make_page(qtbot, qapp)
    before = dict(store.settings.hotkeys)

    _commit(qtbot, page, action, text)

    assert page.hotkey_error_label.isHidden() is False  # the reason is on screen
    assert fragment in page.hotkey_error_label.text()
    assert page.hotkey_error_label.text().startswith(action.capitalize())  # naming the field
    assert page.hotkey_edits[action].property("invalid") is True  # and the field is marked
    assert dict(store.settings.hotkeys) == before
    assert not settings_file.exists()  # nothing was written at all


def test_two_actions_on_the_same_hotkey_are_rejected(qtbot, qapp, settings_file, linux):
    page, store, _manager = _make_page(qtbot, qapp)

    _commit(qtbot, page, "pause", "<alt>+<ctrl>+<shift>+T")  # the start hotkey, reordered and recased

    assert page.hotkey_error_label.isHidden() is False
    assert "Start and Pause use the same hotkey" in page.hotkey_error_label.text()
    assert dict(store.settings.hotkeys) == LINUX_HOTKEYS
    assert not settings_file.exists()


def test_a_hotkey_that_is_a_formatting_shortcut_is_rejected_with_a_reason(qtbot, qapp, settings_file, linux):
    page, store, _manager = _make_page(qtbot, qapp)

    # ctrl+b is Google Docs' bold on Linux, and this app presses it itself while typing.
    _commit(qtbot, page, "start", "<ctrl>+b")

    assert page.hotkey_error_label.isHidden() is False
    text = page.hotkey_error_label.text()
    assert text.startswith("Start:") and "formatting shortcut" in text
    assert page.hotkey_edits["start"].property("invalid") is True
    assert page.hotkey_edits["pause"].property("invalid") is False  # only the guilty field
    assert dict(store.settings.hotkeys) == LINUX_HOTKEYS
    assert not settings_file.exists()


def test_every_problem_is_listed_and_fixing_them_clears_the_message_and_saves(qtbot, qapp, settings_file, linux):
    page, store, _manager = _make_page(qtbot, qapp)
    page.hotkey_edits["start"].setText("<bogus>")
    page.hotkey_edits["stop"].setText("")
    qtbot.keyClick(page.hotkey_edits["stop"], Qt.Key.Key_Return)

    lines = page.hotkey_error_label.text().splitlines()
    assert len(lines) == 2 and lines[0].startswith("Start:") and lines[1].startswith("Stop:")

    page.hotkey_edits["start"].setText("<ctrl>+<alt>+<shift>+k")
    page.hotkey_edits["stop"].setText("<esc>")
    qtbot.keyClick(page.hotkey_edits["stop"], Qt.Key.Key_Return)

    assert page.hotkey_error_label.isHidden()
    assert page.hotkey_edits["start"].property("invalid") is False
    assert store.settings.hotkeys["start"] == "<ctrl>+<alt>+<shift>+k"
    assert _on_disk(settings_file)["hotkeys"]["start"] == "<ctrl>+<alt>+<shift>+k"


def test_an_invalid_edit_is_never_saved_by_an_unrelated_change_and_survives_it(qtbot, qapp, settings_file, linux):
    page, store, _manager = _make_page(qtbot, qapp)
    _commit(qtbot, page, "start", "<ctrl>+<nonsense>")
    assert page.hotkey_error_label.isHidden() is False

    page.countdown_spin.setValue(8)  # a different setting is saved...

    assert _on_disk(settings_file)["countdown_seconds"] == 8
    assert _on_disk(settings_file)["hotkeys"] == LINUX_HOTKEYS  # ...without the invalid hotkey
    assert page.hotkey_edits["start"].text() == "<ctrl>+<nonsense>"  # the half-finished edit is still there
    assert page.hotkey_error_label.isHidden() is False  # and still explained


def test_putting_a_field_back_to_what_is_saved_clears_its_complaint(qtbot, qapp, linux):
    page, store, _manager = _make_page(qtbot, qapp)
    _commit(qtbot, page, "start", "<bogus>")
    assert page.hotkey_error_label.isHidden() is False
    _commit(qtbot, page, "start", LINUX_HOTKEYS["start"])
    assert page.hotkey_error_label.isHidden()
    assert page.hotkey_edits["start"].property("invalid") is False


def test_restoring_the_default_hotkeys_repairs_a_bad_edit_and_saves_the_defaults(qtbot, qapp, settings_file, linux):
    page, store, _manager = _make_page(qtbot, qapp)
    store.update(hotkeys={"start": "<f9>", "pause": "<f10>", "stop": "<f11>"})
    _commit(qtbot, page, "pause", "<bogus>")

    page.restore_hotkeys_button.click()

    assert {a: e.text() for a, e in page.hotkey_edits.items()} == LINUX_HOTKEYS
    assert dict(store.settings.hotkeys) == LINUX_HOTKEYS
    assert _on_disk(settings_file)["hotkeys"] == LINUX_HOTKEYS
    assert page.hotkey_error_label.isHidden()
    assert LINUX_HOTKEYS == default_hotkeys(OperatingSystem.LINUX)


def test_switching_the_default_editor_to_one_that_collides_with_a_hotkey_is_refused(
    qtbot, qapp, settings_file, linux
):
    page, store, _manager = _make_page(qtbot, qapp)
    # ctrl+l is nothing in Google Docs (left-align is ctrl+shift+l) but is left-align in LibreOffice.
    _commit(qtbot, page, "start", "<ctrl>+l")
    assert store.settings.hotkeys["start"] == "<ctrl>+l"

    page.target_combo.setCurrentIndex(page.target_combo.findData("libreoffice-writer"))

    assert page.target_error_label.isHidden() is False
    assert "formatting shortcut" in page.target_error_label.text()
    assert store.settings.target_app == "google-docs"  # unchanged...
    assert page.target_combo.currentData() == "google-docs"  # ...and the control says so
    assert _on_disk(settings_file)["target_app"] == "google-docs"


def test_describe_hotkey_problem_names_what_is_wrong_in_plain_words():
    assert describe_hotkey_problem("<ctrl>+<alt>+t") is None
    assert describe_hotkey_problem("<esc>") is None
    assert describe_hotkey_problem("  <f9>  ") is None  # surrounding space is not a problem
    assert "Enter a hotkey" in describe_hotkey_problem("")
    assert "'<bogus>'" in describe_hotkey_problem("<ctrl>+<bogus>")
    assert "valid hotkey" in describe_hotkey_problem("<ctrl>+<ctrl>")  # a key twice
    assert "valid hotkey" in describe_hotkey_problem("<ctrl>+")
    assert "<ctrl>" in HOTKEY_SYNTAX_HINT and "+" in HOTKEY_SYNTAX_HINT


# --- reset to defaults ----------------------------------------------------------------------------------------------


def _dirty(store):
    store.update(
        target_app="plain-text",
        countdown_seconds=14,
        confirm_before_typing=False,
        minimize_on_start=True,
        show_mini_window=False,
        global_hotkeys_enabled=False,
        theme="dark",
        hotkeys={"start": "<f9>", "pause": "<f10>", "stop": "<f11>"},
        onboarding_complete=True,
        keymap_overrides={"google-docs": {"toggle_bold": "ctrl+shift+b"}},
    )


def test_reset_asks_first_and_does_nothing_if_declined(qtbot, qapp, settings_file, linux):
    store = SettingsStore()
    _dirty(store)
    confirm = Recorder(False)
    page, _store, manager = _make_page(qtbot, qapp, store=store, confirm=confirm, mode=ThemeMode.DARK)

    page.reset_button.click()

    assert len(confirm.calls) == 1
    title, question = confirm.calls[0][1], confirm.calls[0][2]
    assert "Reset" in title and "profiles" in question  # says what is kept
    assert store.settings.countdown_seconds == 14
    assert _on_disk(settings_file)["countdown_seconds"] == 14
    assert page.countdown_spin.value() == 14


def test_reset_restores_every_preference_after_confirmation_and_keeps_setup_progress(
    qtbot, qapp, settings_file, linux
):
    store = SettingsStore()
    _dirty(store)
    page, _store, manager = _make_page(qtbot, qapp, store=store, confirm=Recorder(True), mode=ThemeMode.DARK)

    page.reset_button.click()

    defaults = AppSettings()
    saved = _on_disk(settings_file)
    assert saved["target_app"] == defaults.target_app
    assert saved["countdown_seconds"] == defaults.countdown_seconds
    assert saved["confirm_before_typing"] is defaults.confirm_before_typing
    assert saved["minimize_on_start"] is defaults.minimize_on_start
    assert saved["show_mini_window"] is defaults.show_mini_window
    assert saved["global_hotkeys_enabled"] is defaults.global_hotkeys_enabled
    assert saved["theme"] == defaults.theme
    assert saved["hotkeys"] == LINUX_HOTKEYS
    # Not preferences: kept.
    assert saved["onboarding_complete"] is True
    assert saved["keymap_overrides"] == {"google-docs": {"toggle_bold": "ctrl+shift+b"}}

    # The controls, and the live theme, follow.
    assert page.target_combo.currentData() == defaults.target_app
    assert page.countdown_spin.value() == defaults.countdown_seconds
    assert page.confirm_switch.isChecked() is True
    assert page.minimize_switch.isChecked() is False
    assert page.mini_window_checkbox.isChecked() is True
    assert page.hotkeys_switch.isChecked() is True
    assert page.theme_combo.currentData() == ThemeMode.SYSTEM
    assert manager.mode is ThemeMode.SYSTEM
    assert {a: e.text() for a, e in page.hotkey_edits.items()} == LINUX_HOTKEYS


def test_reset_also_clears_a_half_typed_invalid_hotkey(qtbot, qapp, linux):
    page, store, _manager = _make_page(qtbot, qapp, confirm=Recorder(True))
    _commit(qtbot, page, "start", "<bogus>")  # an invalid edit is pending; the stored settings are already the defaults

    page.reset_button.click()

    assert page.hotkey_edits["start"].text() == LINUX_HOTKEYS["start"]
    assert page.hotkey_error_label.isHidden()


# --- failing to save ----------------------------------------------------------------------------------------------------


def test_a_save_that_fails_says_so_and_puts_the_control_back(qtbot, qapp, monkeypatch):
    page, store, _manager = _make_page(qtbot, qapp)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(app_settings, "save_settings", boom)
    page.countdown_spin.setValue(20)

    assert page.save_error_label.isHidden() is False
    assert "disk full" in page.save_error_label.text()
    assert store.settings.countdown_seconds == AppSettings().countdown_seconds  # nothing changed
    assert page.countdown_spin.value() == AppSettings().countdown_seconds  # the control does not lie


def test_a_later_successful_save_clears_the_failure_message(qtbot, qapp, monkeypatch):
    page, store, _manager = _make_page(qtbot, qapp)
    real_save = app_settings.save_settings
    monkeypatch.setattr(app_settings, "save_settings", lambda *a, **k: (_ for _ in ()).throw(OSError("no")))
    page.confirm_switch.setChecked(False)
    assert page.save_error_label.isHidden() is False

    monkeypatch.setattr(app_settings, "save_settings", real_save)
    page.confirm_switch.setChecked(False)
    assert page.save_error_label.isHidden()
    assert store.settings.confirm_before_typing is False


# --- the hotkey status line -------------------------------------------------------------------------------------------------


def test_the_hotkey_status_shows_each_state(qtbot, qapp):
    service, backends = hotkey_service()
    page, store, _manager = _make_page(qtbot, qapp, service=service)

    assert "off" in page.hotkey_status_label.text()
    assert page.hotkey_retry_button.isHidden()

    service.apply(True, store.settings.hotkeys)
    qtbot.waitUntil(lambda: service.state == "active", timeout=3000)
    assert "on" in page.hotkey_status_label.text()
    assert page.hotkey_retry_button.isHidden()
    service.stop()


def test_an_unavailable_hotkey_service_gives_its_reason_and_a_retry(qtbot, qapp):
    service, backends = hotkey_service(failing_backend("This session blocks global hotkeys (Wayland)"))
    page, store, _manager = _make_page(qtbot, qapp, service=service)

    service.apply(True, store.settings.hotkeys)
    qtbot.waitUntil(lambda: service.state == "unavailable", timeout=3000)

    assert "Wayland" in page.hotkey_status_label.text()
    assert page.hotkey_status_label.property("tone") == "danger"
    assert page.hotkey_retry_button.isHidden() is False

    page.hotkey_retry_button.click()  # the second backend (a plain one) starts
    qtbot.waitUntil(lambda: service.state == "active", timeout=3000)
    assert len(backends.built) == 2
    assert page.hotkey_retry_button.isHidden()
    service.stop()


def test_without_a_hotkey_service_there_is_no_status_line(qtbot, qapp):
    page, _store, _manager = _make_page(qtbot, qapp)
    assert page.hotkey_status_label.isHidden()
    assert page.hotkey_retry_button.isHidden()


# --- accessibility -------------------------------------------------------------------------------------------------------------


def test_every_control_has_an_accessible_name_and_the_hotkey_fields_say_how_to_write_one(qtbot, qapp):
    from PySide6.QtWidgets import QAbstractButton, QComboBox, QLineEdit, QSpinBox

    page, _store, _manager = _make_page(qtbot, qapp)
    for widget in page.findChildren(QAbstractButton) + page.findChildren(QComboBox):
        if widget.metaObject().className() == "QComboBoxListView":
            continue
        assert widget.accessibleName(), widget
    assert page.countdown_spin.accessibleName()
    assert [e.accessibleName() for e in page.hotkey_edits.values()] == [
        "Start hotkey",
        "Pause hotkey",
        "Stop hotkey",
    ]
    for edit in page.hotkey_edits.values():
        assert "<ctrl>" in edit.toolTip()
    for control in (page.target_combo, page.countdown_spin, page.confirm_switch, page.minimize_switch, page.hotkeys_switch):
        assert control.toolTip()
    assert QSpinBox and QLineEdit  # imported for the isinstance-free checks above


def test_the_page_scrolls_so_nothing_is_clipped_in_a_short_window(qtbot, qapp):
    from PySide6.QtWidgets import QScrollArea

    page, _store, _manager = _make_page(qtbot, qapp)
    scroll = page.findChild(QScrollArea)
    assert scroll is not None and scroll.accessibleName() == "Settings sections"
    assert scroll.widgetResizable()
