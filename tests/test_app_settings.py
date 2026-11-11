"""Tests for application preferences: defaults, hotkey validation, JSON persistence, and
the effective-keymap resolution that layers a user's overrides onto a target's built-in one.

Every filesystem-touching test uses ``tmp_path`` -- never the real config
directory -- and every OS-dependent check either parametrizes over all
three ``OperatingSystem`` members or (for ``load_settings``, which is not
OS-injectable by design -- see its docstring) uses ``platforms.primary_modifier()``
so the assertion is correct regardless of which OS actually runs the suite.
"""

from __future__ import annotations

import json

import pytest

from auto_essay_typer import app_settings, platforms, targets
from auto_essay_typer.actions import FormatOp
from auto_essay_typer.app_settings import (
    AppSettings,
    default_hotkeys,
    effective_keymap,
    load_settings,
    save_settings,
    settings_path,
    validate_hotkeys,
)
from auto_essay_typer.keymaps import NO_OP
from auto_essay_typer.platforms import OperatingSystem

# --- default_hotkeys ---------------------------------------------------------


def test_default_hotkeys_macos_uses_cmd():
    assert default_hotkeys(OperatingSystem.MACOS) == {
        "start": "<cmd>+<alt>+<shift>+t",
        "pause": "<cmd>+<alt>+<shift>+p",
        "stop": "<esc>",
    }


@pytest.mark.parametrize("os_", [OperatingSystem.WINDOWS, OperatingSystem.LINUX])
def test_default_hotkeys_non_macos_uses_ctrl(os_):
    assert default_hotkeys(os_) == {
        "start": "<ctrl>+<alt>+<shift>+t",
        "pause": "<ctrl>+<alt>+<shift>+p",
        "stop": "<esc>",
    }


# --- AppSettings: hotkeys default_factory wiring ------------------------------
#
# hotkeys defaults via field(default_factory=...) rather than a plain default
# value specifically so it is resolved fresh at construction time -- these
# pin that design choice down with tests, per the module's own docstring.


def test_hotkeys_default_reflects_current_os_at_construction_time(monkeypatch):
    monkeypatch.setattr(app_settings.platforms, "current_os", lambda: OperatingSystem.WINDOWS)
    assert AppSettings().hotkeys == default_hotkeys(OperatingSystem.WINDOWS)

    monkeypatch.setattr(app_settings.platforms, "current_os", lambda: OperatingSystem.MACOS)
    assert AppSettings().hotkeys == default_hotkeys(OperatingSystem.MACOS)


def test_default_mutable_fields_are_independent_per_instance():
    # Guards against the classic shared-mutable-default bug: each instance
    # must get its own dict, not one dict object reused (and mutated) across
    # every default-constructed AppSettings.
    a, b = AppSettings(), AppSettings()
    assert a.hotkeys == b.hotkeys
    assert a.hotkeys is not b.hotkeys
    assert a.keymap_overrides is not b.keymap_overrides


# --- validate_hotkeys ----------------------------------------------------------


@pytest.mark.parametrize("os_", [OperatingSystem.MACOS, OperatingSystem.WINDOWS, OperatingSystem.LINUX])
def test_validate_hotkeys_accepts_real_defaults_against_google_docs_keymap(os_):
    keymap = targets.keymap_for("google-docs", os_)
    validate_hotkeys(default_hotkeys(os_), keymap)  # must not raise


def test_validate_hotkeys_rejects_unparseable_text_naming_action():
    with pytest.raises(ValueError) as exc_info:
        validate_hotkeys({"start": "not a hotkey"}, {})
    message = str(exc_info.value)
    assert "start" in message
    assert "not a hotkey" in message


def test_validate_hotkeys_rejects_hotkey_colliding_with_keymap_chord():
    keymap = {FormatOp.TOGGLE_BOLD: ("cmd", "b")}
    with pytest.raises(ValueError) as exc_info:
        validate_hotkeys({"start": "<cmd>+b"}, keymap)
    assert "start" in str(exc_info.value)


def test_validate_hotkeys_collision_is_modifier_order_independent():
    keymap = {FormatOp.SET_HEADING_1: ("cmd", "alt", "shift", "t")}
    with pytest.raises(ValueError):
        validate_hotkeys({"start": "<shift>+<alt>+<cmd>+t"}, keymap)


def test_validate_hotkeys_ignores_no_op_chords():
    # A NO_OP entry has no keys at all, so it must never be reported as
    # colliding with anything, no matter what the hotkey text is.
    keymap = {FormatOp.CLEAR_FORMATTING: NO_OP}
    validate_hotkeys({"start": "<cmd>+<alt>+<shift>+t"}, keymap)  # must not raise


def test_validate_hotkeys_checks_parse_validity_even_without_collision():
    # (a) is independent of (b): an unparseable hotkey must be rejected even
    # when the keymap it's checked against is empty (so collision could
    # never have been the reason).
    with pytest.raises(ValueError):
        validate_hotkeys({"start": "not a hotkey"}, {})


# --- settings_path -------------------------------------------------------------


def test_settings_path_honors_env_override(tmp_path, monkeypatch):
    override = tmp_path / "custom-settings.json"
    monkeypatch.setenv("AUTO_TEXT_TYPER_SETTINGS", str(override))
    assert settings_path() == override


def test_settings_path_defers_to_config_dir_when_env_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTO_TEXT_TYPER_SETTINGS", raising=False)
    monkeypatch.setattr(app_settings.platforms, "config_dir", lambda: tmp_path / "cfgdir")
    assert settings_path() == tmp_path / "cfgdir" / "settings.json"


# --- load_settings ---------------------------------------------------------------


def test_load_settings_missing_file_returns_defaults(tmp_path):
    settings, warning = load_settings(tmp_path / "does-not-exist.json")
    assert settings == AppSettings()
    assert warning is None


def test_save_and_load_round_trips_all_fields(tmp_path):
    path = tmp_path / "settings.json"
    original = AppSettings(
        target_app="plain-text",
        profile_name="sloppy",
        countdown_seconds=3,
        theme="dark",
        show_mini_window=False,
        minimize_on_start=True,
        global_hotkeys_enabled=False,
        hotkeys={
            "start": "<ctrl>+<alt>+<shift>+t",
            "pause": "<ctrl>+<alt>+<shift>+p",
            "stop": "<esc>",
        },
        keymap_overrides={"plain-text": {"toggle_bold": "alt+shift+b"}},
        onboarding_complete=True,
        confirm_before_typing=False,
    )

    save_settings(original, path)
    loaded, warning = load_settings(path)

    assert warning is None
    assert loaded == original


def test_load_settings_malformed_json_returns_defaults_and_warning(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not valid json", encoding="utf-8")

    settings, warning = load_settings(path)

    assert settings == AppSettings()
    assert warning is not None


@pytest.mark.parametrize("bad_field", [{"countdown_seconds": 999}, {"theme": "neon"}])
def test_load_settings_out_of_range_value_returns_defaults_and_warning(tmp_path, bad_field):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(bad_field), encoding="utf-8")

    settings, warning = load_settings(path)

    assert settings == AppSettings()
    assert warning is not None


def test_load_settings_ignores_unknown_top_level_key(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"countdown_seconds": 7, "this_key_does_not_exist": 123}),
        encoding="utf-8",
    )

    settings, warning = load_settings(path)

    assert warning is None
    assert settings.countdown_seconds == 7


def test_load_settings_falls_back_when_saved_hotkey_collides_with_keymap(tmp_path):
    # google-docs binds TOGGLE_BOLD to cmd+b on macOS and ctrl+b on
    # Windows/Linux -- primary_modifier() (no OS argument) picks whichever
    # one matches the real OS running this test, since load_settings itself
    # always validates against platforms.current_os(), not an injected one.
    path = tmp_path / "settings.json"
    mod = platforms.primary_modifier()
    data = {
        "target_app": "google-docs",
        "hotkeys": {
            "start": f"<{mod}>+b",
            "pause": "<ctrl>+<alt>+<shift>+p",
            "stop": "<esc>",
        },
    }
    path.write_text(json.dumps(data), encoding="utf-8")

    settings, warning = load_settings(path)

    assert settings == AppSettings()
    assert warning is not None


# --- save_settings ---------------------------------------------------------------


def test_save_settings_creates_missing_parent_directories(tmp_path):
    path = tmp_path / "a" / "b" / "settings.json"

    save_settings(AppSettings(), path)

    assert path.exists()
    loaded, warning = load_settings(path)
    assert warning is None
    assert loaded == AppSettings()


def test_save_settings_leaves_no_stray_temp_file(tmp_path):
    path = tmp_path / "settings.json"

    save_settings(AppSettings(), path)

    assert list(path.parent.iterdir()) == [path]


# --- effective_keymap ---------------------------------------------------------------


@pytest.mark.parametrize("os_", [OperatingSystem.MACOS, OperatingSystem.WINDOWS, OperatingSystem.LINUX])
def test_effective_keymap_with_no_overrides_matches_base_keymap(os_):
    settings = AppSettings(target_app="google-docs")
    assert effective_keymap(settings, os_) == dict(targets.keymap_for("google-docs", os_))


def test_effective_keymap_override_replaces_existing_chord():
    settings = AppSettings(
        target_app="google-docs",
        keymap_overrides={"google-docs": {"toggle_bold": "alt+shift+b"}},
    )

    result = effective_keymap(settings, OperatingSystem.MACOS)

    assert result[FormatOp.TOGGLE_BOLD] == ("alt", "shift", "b")


def test_effective_keymap_override_adds_op_missing_from_base_keymap():
    base = targets.keymap_for("microsoft-word", OperatingSystem.WINDOWS)
    assert FormatOp.TOGGLE_STRIKETHROUGH not in base

    settings = AppSettings(
        target_app="microsoft-word",
        keymap_overrides={"microsoft-word": {"toggle_strikethrough": "ctrl+alt+5"}},
    )
    result = effective_keymap(settings, OperatingSystem.WINDOWS)

    assert result[FormatOp.TOGGLE_STRIKETHROUGH] == ("ctrl", "alt", "5")


def test_effective_keymap_invalid_op_text_raises_naming_it():
    settings = AppSettings(
        target_app="google-docs",
        keymap_overrides={"google-docs": {"not_a_real_op": "cmd+b"}},
    )

    with pytest.raises(ValueError) as exc_info:
        effective_keymap(settings, OperatingSystem.MACOS)
    assert "not_a_real_op" in str(exc_info.value)


def test_effective_keymap_invalid_chord_text_raises_naming_it():
    settings = AppSettings(
        target_app="google-docs",
        keymap_overrides={"google-docs": {"toggle_bold": "cmd+nonsense"}},
    )

    with pytest.raises(ValueError) as exc_info:
        effective_keymap(settings, OperatingSystem.MACOS)
    assert "nonsense" in str(exc_info.value)


def test_effective_keymap_empty_chord_text_becomes_no_op():
    settings = AppSettings(
        target_app="microsoft-word",
        keymap_overrides={"microsoft-word": {"toggle_bold": ""}},
    )

    result = effective_keymap(settings, OperatingSystem.MACOS)

    assert result[FormatOp.TOGGLE_BOLD] == NO_OP


# --- AppSettings validation ---------------------------------------------------------


def test_countdown_seconds_negative_raises():
    with pytest.raises(ValueError):
        AppSettings(countdown_seconds=-1)


def test_countdown_seconds_above_thirty_raises():
    with pytest.raises(ValueError):
        AppSettings(countdown_seconds=31)


def test_countdown_seconds_boundaries_are_accepted():
    AppSettings(countdown_seconds=0)
    AppSettings(countdown_seconds=30)


def test_invalid_theme_raises():
    with pytest.raises(ValueError):
        AppSettings(theme="neon")
