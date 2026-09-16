"""Tests for OS detection, permission checks, config paths, and text normalisation.

Every OS/env/home-directory fact this module exposes is injectable, so these
tests never depend on (or alter) the real OS, environment, or home directory
-- except the one deliberately-real smoke test noted below.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_essay_typer import platforms
from auto_essay_typer.platforms import (
    LinuxSession,
    OperatingSystem,
    PermissionStatus,
    check_input_permission,
    config_dir,
    current_os,
    legacy_config_file,
    linux_session,
    normalize_input_text,
    open_permission_settings,
    primary_modifier,
    request_input_permission_prompt,
)

# --- current_os --------------------------------------------------------------


def test_current_os_darwin_is_macos():
    assert current_os("darwin") is OperatingSystem.MACOS


@pytest.mark.parametrize("platform", ["win32", "cygwin"])
def test_current_os_windows_platforms(platform):
    assert current_os(platform) is OperatingSystem.WINDOWS


@pytest.mark.parametrize("platform", ["linux", "linux2", "freebsd13", "some-future-os"])
def test_current_os_everything_else_is_linux(platform):
    assert current_os(platform) is OperatingSystem.LINUX


# --- linux_session -------------------------------------------------------------


def test_linux_session_x11_via_session_type():
    assert linux_session({"XDG_SESSION_TYPE": "x11"}) is LinuxSession.X11


def test_linux_session_wayland_via_session_type():
    assert linux_session({"XDG_SESSION_TYPE": "wayland"}) is LinuxSession.WAYLAND


def test_linux_session_wayland_via_display_variable():
    assert linux_session({"WAYLAND_DISPLAY": "wayland-0"}) is LinuxSession.WAYLAND


def test_linux_session_x11_via_display_variable_only():
    assert linux_session({"DISPLAY": ":0"}) is LinuxSession.X11


def test_linux_session_wayland_wins_even_when_display_is_also_set():
    # XWayland sets DISPLAY too, but injection into native Wayland windows is
    # still blocked -- so this must never be misreported as X11.
    env = {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}
    assert linux_session(env) is LinuxSession.WAYLAND


def test_linux_session_wayland_display_wins_over_display_even_without_session_type():
    env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}
    assert linux_session(env) is LinuxSession.WAYLAND


def test_linux_session_none_when_nothing_set():
    assert linux_session({}) is LinuxSession.NONE


def test_linux_session_session_type_is_case_insensitive():
    assert linux_session({"XDG_SESSION_TYPE": "Wayland"}) is LinuxSession.WAYLAND
    assert linux_session({"XDG_SESSION_TYPE": "X11"}) is LinuxSession.X11


def test_linux_session_unrecognised_session_type_falls_back_to_display():
    assert linux_session({"XDG_SESSION_TYPE": "tty", "DISPLAY": ":0"}) is LinuxSession.X11
    assert linux_session({"XDG_SESSION_TYPE": "tty"}) is LinuxSession.NONE


def test_linux_session_defaults_to_real_os_environ():
    # Just needs to not raise and to return a LinuxSession member -- the
    # default parameter wiring is what's under test, not any specific value.
    assert isinstance(linux_session(), LinuxSession)


# --- check_input_permission: macOS -------------------------------------------


def test_macos_permission_granted_reports_no_fix_steps():
    status = check_input_permission(os_=OperatingSystem.MACOS, ax_trusted_probe=lambda: True)
    assert status.granted is True
    assert status.fix_steps == ()
    assert status.can_open_settings is True


def test_macos_permission_not_granted_reports_fix_steps():
    status = check_input_permission(os_=OperatingSystem.MACOS, ax_trusted_probe=lambda: False)
    assert status.granted is False
    assert len(status.fix_steps) > 0
    assert status.can_open_settings is True


def test_macos_permission_not_granted_mentions_quit_and_reopen():
    # macOS never grants permission to an already-running process -- the fix
    # steps must say so, or a user will grant it and then wonder why nothing
    # changed.
    status = check_input_permission(os_=OperatingSystem.MACOS, ax_trusted_probe=lambda: False)
    joined = " ".join(status.fix_steps).lower()
    assert "quit" in joined and "reopen" in joined


def test_macos_permission_not_granted_mentions_accessibility_settings_path():
    status = check_input_permission(os_=OperatingSystem.MACOS, ax_trusted_probe=lambda: False)
    joined = " ".join(status.fix_steps)
    assert "Accessibility" in joined


def test_macos_permission_uses_real_probe_by_default():
    # A safe, read-only smoke test of the real AXIsProcessTrusted() wiring
    # (no options/prompt, so this cannot pop a system dialog) -- everything
    # else in this file injects a fake probe instead.
    status = check_input_permission(os_=OperatingSystem.MACOS)
    assert isinstance(status, PermissionStatus)
    assert isinstance(status.granted, bool)


# --- check_input_permission: Windows -------------------------------------------


def test_windows_permission_is_always_granted():
    status = check_input_permission(os_=OperatingSystem.WINDOWS, env={})
    assert status.granted is True
    assert status.fix_steps == ()
    assert status.can_open_settings is False


def test_windows_permission_summary_mentions_administrator():
    status = check_input_permission(os_=OperatingSystem.WINDOWS, env={})
    assert "administrator" in status.summary.lower()


# --- check_input_permission: Linux ---------------------------------------------


def test_linux_x11_permission_is_granted():
    status = check_input_permission(os_=OperatingSystem.LINUX, env={"DISPLAY": ":0"})
    assert status.granted is True
    assert status.fix_steps == ()
    assert status.can_open_settings is False


def test_linux_wayland_permission_is_not_granted():
    status = check_input_permission(os_=OperatingSystem.LINUX, env={"XDG_SESSION_TYPE": "wayland"})
    assert status.granted is False
    assert status.can_open_settings is False
    assert len(status.fix_steps) > 0


def test_linux_wayland_fix_steps_mention_xorg_session_choice():
    status = check_input_permission(os_=OperatingSystem.LINUX, env={"XDG_SESSION_TYPE": "wayland"})
    joined = " ".join(status.fix_steps)
    assert "Xorg" in joined or "X11" in joined


def test_linux_no_session_permission_is_not_granted():
    status = check_input_permission(os_=OperatingSystem.LINUX, env={})
    assert status.granted is False
    assert status.can_open_settings is False
    assert "no graphical session" in status.summary.lower()


# --- request_input_permission_prompt / open_permission_settings ---------------


def test_request_input_permission_prompt_is_noop_reporting_status_off_macos(monkeypatch):
    monkeypatch.setattr(platforms, "current_os", lambda: OperatingSystem.WINDOWS)
    monkeypatch.setattr(
        platforms,
        "check_input_permission",
        lambda: PermissionStatus(True, "ok", (), False),
    )
    assert request_input_permission_prompt() is True


def test_request_input_permission_prompt_noop_reflects_ungranted_status(monkeypatch):
    monkeypatch.setattr(platforms, "current_os", lambda: OperatingSystem.LINUX)
    monkeypatch.setattr(
        platforms,
        "check_input_permission",
        lambda: PermissionStatus(False, "nope", ("do something",), False),
    )
    assert request_input_permission_prompt() is False


def test_open_permission_settings_returns_false_off_macos(monkeypatch):
    monkeypatch.setattr(platforms, "current_os", lambda: OperatingSystem.LINUX)
    assert open_permission_settings() is False


def test_open_permission_settings_runs_open_command_on_macos(monkeypatch):
    calls = []

    class _FakeResult:
        returncode = 0

    def fake_run(args, **kwargs):
        calls.append(args)
        return _FakeResult()

    monkeypatch.setattr(platforms, "current_os", lambda: OperatingSystem.MACOS)
    monkeypatch.setattr(platforms.subprocess, "run", fake_run)

    assert open_permission_settings() is True
    assert calls == [["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"]]


def test_open_permission_settings_returns_false_when_open_fails(monkeypatch):
    class _FakeResult:
        returncode = 1

    monkeypatch.setattr(platforms, "current_os", lambda: OperatingSystem.MACOS)
    monkeypatch.setattr(platforms.subprocess, "run", lambda args, **kwargs: _FakeResult())

    assert open_permission_settings() is False


# --- config_dir ----------------------------------------------------------------


def test_config_dir_macos(tmp_path):
    home = tmp_path / "home"
    result = config_dir(os_=OperatingSystem.MACOS, env={}, home=home)
    assert result == home / "Library" / "Application Support" / "Auto Text Typer"


def test_config_dir_windows_with_appdata(tmp_path):
    home = tmp_path / "home"
    appdata = tmp_path / "AppData" / "Roaming"
    result = config_dir(os_=OperatingSystem.WINDOWS, env={"APPDATA": str(appdata)}, home=home)
    assert result == appdata / "Auto Text Typer"


def test_config_dir_windows_without_appdata_falls_back_to_home(tmp_path):
    home = tmp_path / "home"
    result = config_dir(os_=OperatingSystem.WINDOWS, env={}, home=home)
    assert result == home / "AppData" / "Roaming" / "Auto Text Typer"


def test_config_dir_linux_with_xdg_config_home(tmp_path):
    home = tmp_path / "home"
    xdg = tmp_path / "xdg-config"
    result = config_dir(os_=OperatingSystem.LINUX, env={"XDG_CONFIG_HOME": str(xdg)}, home=home)
    assert result == xdg / "auto-text-typer"


def test_config_dir_linux_without_xdg_falls_back_to_dot_config(tmp_path):
    home = tmp_path / "home"
    result = config_dir(os_=OperatingSystem.LINUX, env={}, home=home)
    assert result == home / ".config" / "auto-text-typer"


def test_config_dir_uses_real_os_and_home_by_default():
    # Must not raise, and must return a Path -- the specific value depends on
    # the machine running the test.
    assert isinstance(config_dir(), Path)


# --- legacy_config_file ---------------------------------------------------------


def test_legacy_config_file_with_xdg_config_home(tmp_path):
    home = tmp_path / "home"
    xdg = tmp_path / "xdg-config"
    result = legacy_config_file(env={"XDG_CONFIG_HOME": str(xdg)}, home=home)
    assert result == xdg / "auto-essay-typer" / "profiles.toml"


def test_legacy_config_file_without_xdg_falls_back_to_dot_config(tmp_path):
    home = tmp_path / "home"
    result = legacy_config_file(env={}, home=home)
    assert result == home / ".config" / "auto-essay-typer" / "profiles.toml"


# --- primary_modifier ------------------------------------------------------------


def test_primary_modifier_macos_is_cmd():
    assert primary_modifier(OperatingSystem.MACOS) == "cmd"


@pytest.mark.parametrize("os_", [OperatingSystem.WINDOWS, OperatingSystem.LINUX])
def test_primary_modifier_non_macos_is_ctrl(os_):
    assert primary_modifier(os_) == "ctrl"


# --- normalize_input_text ---------------------------------------------------------


def test_normalize_input_text_strips_leading_bom():
    assert normalize_input_text("﻿hello") == "hello"


def test_normalize_input_text_only_strips_leading_bom_not_interior_ones():
    assert normalize_input_text("a﻿b") == "a﻿b"


def test_normalize_input_text_converts_crlf_to_lf():
    assert normalize_input_text("line1\r\nline2\r\n") == "line1\nline2\n"


def test_normalize_input_text_converts_lone_cr_to_lf():
    assert normalize_input_text("line1\rline2") == "line1\nline2"


def test_normalize_input_text_leaves_plain_lf_untouched():
    assert normalize_input_text("line1\nline2") == "line1\nline2"


def test_normalize_input_text_handles_bom_plus_crlf_together():
    assert normalize_input_text("﻿line1\r\nline2") == "line1\nline2"


def test_normalize_input_text_empty_string():
    assert normalize_input_text("") == ""


def test_normalize_input_text_does_not_double_convert_crlf_to_blank_line():
    # A naive replace("\r", "\n") *then* replace("\r\n", "\n") ordering (or
    # the reverse done carelessly) can turn "\r\n" into two newlines instead
    # of one. Guard the actual intended behaviour explicitly.
    assert normalize_input_text("a\r\nb") == "a\nb"
    assert normalize_input_text("a\r\nb") != "a\n\nb"
