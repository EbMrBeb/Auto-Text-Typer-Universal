"""Tests for :class:`~auto_essay_typer.gui.settings_store.SettingsStore`.

Every test points the store at a file under ``tmp_path`` (the autouse
``_isolated_config`` fixture also redirects ``AUTO_TEXT_TYPER_SETTINGS``), so
the real settings file is never read or written.
"""

from __future__ import annotations

import json

import pytest

from auto_essay_typer import app_settings, platforms
from auto_essay_typer.app_settings import AppSettings
from auto_essay_typer.gui.settings_store import SettingsStore, validate_settings
from auto_essay_typer.platforms import OperatingSystem


@pytest.fixture
def store_path(tmp_path):
    return tmp_path / "settings.json"


def _linux(monkeypatch):
    monkeypatch.setattr(platforms, "current_os", lambda *a, **k: OperatingSystem.LINUX)


def test_a_missing_file_gives_defaults_and_no_warning(store_path):
    store = SettingsStore(store_path)
    assert store.settings == AppSettings()
    assert store.load_warning is None
    assert not store_path.exists()  # loading alone never writes


def test_update_saves_to_disk_and_announces_the_change(qtbot, store_path):
    store = SettingsStore(store_path)
    with qtbot.waitSignal(store.changed, timeout=1000) as blocker:
        store.update(countdown_seconds=9, confirm_before_typing=False)

    (announced,) = blocker.args
    assert announced.countdown_seconds == 9
    assert store.settings.confirm_before_typing is False
    saved = json.loads(store_path.read_text(encoding="utf-8"))
    assert saved["countdown_seconds"] == 9
    # A fresh store reads back exactly what was saved.
    assert SettingsStore(store_path).settings == store.settings


def test_an_update_that_changes_nothing_neither_saves_nor_announces(qtbot, store_path):
    store = SettingsStore(store_path)
    with qtbot.assertNotEmitted(store.changed):
        store.update(countdown_seconds=store.settings.countdown_seconds)
    assert not store_path.exists()


def test_invalid_values_raise_and_leave_everything_untouched(qtbot, store_path):
    store = SettingsStore(store_path)
    with qtbot.assertNotEmitted(store.changed):
        with pytest.raises(ValueError, match="countdown_seconds"):
            store.update(countdown_seconds=99)
    assert store.settings.countdown_seconds == AppSettings().countdown_seconds
    assert not store_path.exists()


def test_unknown_setting_names_are_rejected(store_path):
    store = SettingsStore(store_path)
    with pytest.raises(ValueError, match="no_such_setting"):
        store.update(no_such_setting=1)


def test_a_hotkey_that_collides_with_a_formatting_shortcut_is_refused(monkeypatch, store_path):
    _linux(monkeypatch)
    store = SettingsStore(store_path)
    # ctrl+b is Google Docs' bold on Linux, and this app presses it while typing.
    bad = {**store.settings.hotkeys, "start": "<ctrl>+b"}
    with pytest.raises(ValueError, match="collides"):
        store.update(hotkeys=bad)
    assert store.settings.hotkeys["start"] != "<ctrl>+b"
    assert not store_path.exists()


def test_a_malformed_hotkey_is_refused_with_its_reason(monkeypatch, store_path):
    _linux(monkeypatch)
    store = SettingsStore(store_path)
    with pytest.raises(ValueError, match="not a valid shortcut"):
        store.update(hotkeys={**store.settings.hotkeys, "pause": "<ctrl>+<nonsense>"})


def test_an_override_that_makes_a_saved_hotkey_collide_is_refused(monkeypatch, store_path):
    # The settings file must always load back: overriding bold to a chord the
    # hotkeys already use would make load_settings fall back to defaults.
    _linux(monkeypatch)
    store = SettingsStore(store_path)
    store.update(hotkeys={**store.settings.hotkeys, "start": "<ctrl>+<alt>+<shift>+k"})
    with pytest.raises(ValueError, match="collides"):
        store.update(keymap_overrides={"google-docs": {"toggle_bold": "ctrl+alt+shift+k"}})


def test_an_unknown_target_is_refused_as_a_value_error(store_path):
    store = SettingsStore(store_path)
    with pytest.raises(ValueError, match="unknown target"):
        store.update(target_app="no-such-editor")


def test_a_failed_save_leaves_the_in_memory_settings_alone(qtbot, monkeypatch, store_path):
    store = SettingsStore(store_path)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(app_settings, "save_settings", boom)
    with qtbot.assertNotEmitted(store.changed):
        with pytest.raises(OSError, match="disk full"):
            store.update(countdown_seconds=7)
    assert store.settings.countdown_seconds == AppSettings().countdown_seconds


def test_reset_keeps_onboarding_progress_and_keymap_overrides(store_path):
    store = SettingsStore(store_path)
    store.update(
        onboarding_complete=True,
        countdown_seconds=12,
        minimize_on_start=True,
        keymap_overrides={"google-docs": {"toggle_bold": "ctrl+shift+b"}},
    )
    store.reset_to_defaults()

    assert store.settings.countdown_seconds == AppSettings().countdown_seconds
    assert store.settings.minimize_on_start is False
    assert store.settings.onboarding_complete is True
    assert store.settings.keymap_overrides == {"google-docs": {"toggle_bold": "ctrl+shift+b"}}


def test_the_load_warning_is_available_once(store_path):
    store_path.write_text("{ this is not json", encoding="utf-8")
    store = SettingsStore(store_path)

    assert store.settings == AppSettings()
    assert store.load_warning is not None
    first = store.take_load_warning()
    assert first is not None and "settings" in first
    assert store.take_load_warning() is None
    assert store.load_warning is None


def test_callers_cannot_alias_the_stored_mappings(store_path):
    store = SettingsStore(store_path)
    hotkeys = dict(store.settings.hotkeys)
    store.update(hotkeys=hotkeys)
    hotkeys["start"] = "tampered"
    assert store.settings.hotkeys["start"] != "tampered"


def test_validate_settings_accepts_the_defaults():
    validate_settings(AppSettings())


# --- more than one store on the same file -------------------------------------------------------------------------------------
#
# Every page is meant to share one store, but a page that is built without one keeps a store of its own, and
# the file can also be edited by hand or by another copy of the app. Two stores must never silently overwrite
# each other's changes.


def test_refresh_adopts_what_another_store_saved_and_announces_it(qtbot, store_path):
    mine = SettingsStore(store_path)
    other = SettingsStore(store_path)
    other.update(countdown_seconds=9)

    with qtbot.waitSignal(mine.changed, timeout=1000) as blocker:
        assert mine.refresh() is True

    assert mine.settings.countdown_seconds == 9
    assert blocker.args[0].countdown_seconds == 9


def test_refresh_with_nothing_new_changes_nothing_and_says_nothing(qtbot, store_path):
    store = SettingsStore(store_path)
    store.update(countdown_seconds=7)
    with qtbot.assertNotEmitted(store.changed):
        assert store.refresh() is False
        assert store.refresh() is False


def test_a_stores_own_save_is_never_mistaken_for_someone_elses(qtbot, store_path):
    store = SettingsStore(store_path)
    store.update(countdown_seconds=7)
    with qtbot.assertNotEmitted(store.changed):
        store.refresh()


def test_an_update_goes_on_top_of_what_another_store_saved_instead_of_over_it(store_path):
    page_store = SettingsStore(store_path)  # a page built without the shared store
    shared = SettingsStore(store_path)
    shared.update(keymap_overrides={"google-docs": {"toggle_bold": "ctrl+shift+b"}})

    page_store.update(target_app="plain-text")  # the page's own, unrelated change

    saved = json.loads(store_path.read_text(encoding="utf-8"))
    assert saved["target_app"] == "plain-text"
    assert saved["keymap_overrides"] == {"google-docs": {"toggle_bold": "ctrl+shift+b"}}, "the other store's change survived"
    assert page_store.settings.keymap_overrides == shared.settings.keymap_overrides


def test_resetting_keeps_the_overrides_another_store_saved(store_path):
    mine = SettingsStore(store_path)
    other = SettingsStore(store_path)
    other.update(keymap_overrides={"google-docs": {"toggle_bold": "ctrl+shift+b"}})

    mine.reset_to_defaults()

    assert json.loads(store_path.read_text(encoding="utf-8"))["keymap_overrides"] == {
        "google-docs": {"toggle_bold": "ctrl+shift+b"}
    }


def test_a_file_that_cannot_be_read_is_ignored_by_refresh_and_replaced_by_the_next_save(qtbot, store_path):
    store = SettingsStore(store_path)
    store.update(countdown_seconds=8)
    store_path.write_text("{ this is not json", encoding="utf-8")

    with qtbot.assertNotEmitted(store.changed):
        assert store.refresh() is False
    assert store.settings.countdown_seconds == 8

    store.update(confirm_before_typing=False)
    saved = json.loads(store_path.read_text(encoding="utf-8"))
    assert saved["countdown_seconds"] == 8 and saved["confirm_before_typing"] is False


def test_refresh_follows_a_settings_file_that_was_deleted_back_to_the_defaults(qtbot, store_path):
    store = SettingsStore(store_path)
    store.update(countdown_seconds=8)
    store_path.unlink()

    with qtbot.waitSignal(store.changed, timeout=1000):
        assert store.refresh() is True

    assert store.settings == AppSettings()


def test_refresh_on_a_store_that_has_never_had_a_file_does_nothing(qtbot, store_path):
    store = SettingsStore(store_path)
    with qtbot.assertNotEmitted(store.changed):
        assert store.refresh() is False
    assert not store_path.exists()
