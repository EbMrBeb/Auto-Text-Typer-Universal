"""OS facts, input-injection permissions, config paths, and text normalisation.

Every other cross-platform module (``config.py``, ``targets.py``,
``app_settings.py``, ``driver.py``) builds on the primitives here rather than
inspecting ``sys.platform`` or environment variables directly, so the whole
package has exactly one place that knows what "which OS am I on" means and
how each OS answers the handful of questions typing into another app raises:
can this process actually inject keystrokes, and where does its config live.

Every function that reads OS/environment/home-directory facts accepts them as
optional parameters so tests can supply fakes instead of touching the real
OS, environment, or filesystem.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

_AX_FRAMEWORK_PATH = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"


class OperatingSystem(StrEnum):
    MACOS = "macos"
    WINDOWS = "windows"
    LINUX = "linux"


def current_os(platform: str = sys.platform) -> OperatingSystem:
    """Classify a ``sys.platform`` string into one of the three OSes this project supports.

    ``win32`` covers both 32- and 64-bit Windows (Python never renamed it),
    and ``cygwin`` is included alongside it since it is still a Windows
    keyboard underneath. Everything else -- ``linux``, the BSDs, anything
    unrecognised -- is treated as Linux, since that is where this project's
    Linux/X11/Wayland handling applies and there is nothing more specific to
    fall back to.
    """
    if platform == "darwin":
        return OperatingSystem.MACOS
    if platform in ("win32", "cygwin"):
        return OperatingSystem.WINDOWS
    return OperatingSystem.LINUX


class LinuxSession(StrEnum):
    X11 = "x11"
    WAYLAND = "wayland"
    NONE = "none"


def linux_session(env: Mapping[str, str] = os.environ) -> LinuxSession:
    """Detect whether the current Linux graphical session is X11 or Wayland.

    ``XDG_SESSION_TYPE`` is the authoritative signal when set. Wayland
    sessions run an XWayland compatibility server that still sets ``DISPLAY``,
    so Wayland-ness is checked (via ``XDG_SESSION_TYPE`` or ``WAYLAND_DISPLAY``)
    *before* falling back to ``DISPLAY`` -- otherwise a native Wayland session
    would be misdetected as X11 and this project's keystroke injection would
    silently fail to reach other windows. ``DISPLAY`` alone (no Wayland signal
    present) means a real X11 session. Neither set means no graphical session
    at all -- e.g. a bare SSH login or a headless CI box.
    """
    session_type = env.get("XDG_SESSION_TYPE", "").strip().lower()
    if session_type == "wayland" or env.get("WAYLAND_DISPLAY"):
        return LinuxSession.WAYLAND
    if session_type == "x11" or env.get("DISPLAY"):
        return LinuxSession.X11
    return LinuxSession.NONE


@dataclass(frozen=True)
class PermissionStatus:
    """Whether this process can inject keystrokes into other apps, and how to fix it if not."""

    granted: bool
    summary: str
    fix_steps: tuple[str, ...]
    can_open_settings: bool


def _ax_is_process_trusted() -> bool:
    """Real ``AXIsProcessTrusted()`` probe, loaded lazily so importing this module never
    touches ApplicationServices on a platform that doesn't have it."""
    ax = ctypes.CDLL(_AX_FRAMEWORK_PATH)
    ax.AXIsProcessTrusted.restype = ctypes.c_bool
    ax.AXIsProcessTrusted.argtypes = []
    return bool(ax.AXIsProcessTrusted())


def check_input_permission(
    os_: OperatingSystem | None = None,
    env: Mapping[str, str] | None = None,
    ax_trusted_probe: Callable[[], bool] | None = None,
) -> PermissionStatus:
    """Report whether this process is currently allowed to inject keystrokes.

    macOS requires the Accessibility permission (checked via
    ``AXIsProcessTrusted``, or ``ax_trusted_probe`` when supplied -- tests
    always supply one so the real framework is never loaded off-macOS or
    on-macOS-without-permission-dialogs). Windows never needs an explicit
    grant, but typing into an elevated app requires this process to also be
    elevated, which is surfaced as a note rather than a blocker since it
    cannot be checked in advance. Linux depends entirely on the session type:
    X11 can always be injected into, Wayland blocks it outright with no
    per-app permission to grant, and no graphical session at all cannot be
    typed into either.
    """
    os_ = os_ if os_ is not None else current_os()
    env = env if env is not None else os.environ

    if os_ is OperatingSystem.MACOS:
        probe = ax_trusted_probe if ax_trusted_probe is not None else _ax_is_process_trusted
        if probe():
            return PermissionStatus(
                granted=True,
                summary="Accessibility access is granted.",
                fix_steps=(),
                can_open_settings=True,
            )
        return PermissionStatus(
            granted=False,
            summary="Auto Text Typer needs Accessibility access to type into other apps.",
            fix_steps=(
                "Open System Settings > Privacy & Security > Accessibility.",
                "Turn on Auto Text Typer. If it is already in the list but typing still does not "
                "work, turn it off and on again: macOS can keep a grant that no longer matches "
                "this copy of the app.",
                "Quit and reopen Auto Text Typer -- macOS does not grant "
                "permission to a process that is already running.",
            ),
            can_open_settings=True,
        )

    if os_ is OperatingSystem.WINDOWS:
        return PermissionStatus(
            granted=True,
            summary=(
                "No extra permission is required. Note: typing into an app "
                "that is running as administrator requires running Auto Text "
                "Typer as administrator too."
            ),
            fix_steps=(),
            can_open_settings=False,
        )

    # Linux.
    session = linux_session(env)
    if session is LinuxSession.X11:
        return PermissionStatus(
            granted=True,
            summary="Running under X11: no extra permission is required.",
            fix_steps=(),
            can_open_settings=False,
        )
    if session is LinuxSession.WAYLAND:
        return PermissionStatus(
            granted=False,
            summary=(
                "Wayland blocks apps from sending keystrokes to other windows, "
                "and there is no permission dialog that can grant an exception."
            ),
            fix_steps=(
                "Log out.",
                'At the login screen, pick an X11 / Xorg session '
                '(e.g. "Ubuntu on Xorg", "GNOME on Xorg", "Plasma (X11)").',
                "Log back in and reopen Auto Text Typer.",
            ),
            can_open_settings=False,
        )
    return PermissionStatus(
        granted=False,
        summary="No graphical session found.",
        fix_steps=("Start a graphical X11 session, then reopen Auto Text Typer.",),
        can_open_settings=False,
    )


def request_input_permission_prompt() -> bool:
    """Ask macOS to show its Accessibility permission dialog, if not already granted.

    Calls ``AXIsProcessTrustedWithOptions`` with
    ``kAXTrustedCheckOptionPrompt`` set to true, which is the only supported
    way to trigger that system dialog from outside the Accessibility settings
    pane. The real call is gated on ``current_os() == MACOS`` so that
    importing or calling this function on Windows/Linux never loads
    ApplicationServices/CoreFoundation -- there is nothing to prompt there,
    so it simply reports the current (non-macOS) permission status instead.
    """
    if current_os() != OperatingSystem.MACOS:
        return check_input_permission().granted

    import ctypes.util

    cf_path = ctypes.util.find_library("CoreFoundation")
    cf = ctypes.CDLL(cf_path) if cf_path else ctypes.CDLL(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
    )
    ax = ctypes.CDLL(_AX_FRAMEWORK_PATH)

    k_cf_string_encoding_utf8 = 0x08000100

    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFDictionaryCreate.restype = ctypes.c_void_p
    cf.CFDictionaryCreate.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_long,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    cf.CFRelease.restype = None
    cf.CFRelease.argtypes = [ctypes.c_void_p]

    ax.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
    ax.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]

    # kCFBooleanTrue is itself a pointer-sized global (a CFBooleanRef), so
    # reading it as a c_void_p gives its value directly. The two callback
    # tables, by contrast, are global *structs* rather than pointers, so what
    # CFDictionaryCreate wants is each struct's own address -- taken via
    # addressof() on an in_dll() mapping, regardless of the (irrelevant) type
    # used to perform that mapping.
    true_value = ctypes.c_void_p.in_dll(cf, "kCFBooleanTrue")
    key_callbacks = ctypes.addressof(ctypes.c_void_p.in_dll(cf, "kCFTypeDictionaryKeyCallBacks"))
    value_callbacks = ctypes.addressof(
        ctypes.c_void_p.in_dll(cf, "kCFTypeDictionaryValueCallBacks")
    )

    key = cf.CFStringCreateWithCString(None, b"AXTrustedCheckOptionPrompt", k_cf_string_encoding_utf8)
    if not key:
        raise OSError("CFStringCreateWithCString returned NULL")

    keys_array = (ctypes.c_void_p * 1)(key)
    values_array = (ctypes.c_void_p * 1)(true_value)

    options = cf.CFDictionaryCreate(
        None,
        keys_array,
        values_array,
        1,
        ctypes.c_void_p(key_callbacks),
        ctypes.c_void_p(value_callbacks),
    )
    if not options:
        cf.CFRelease(key)
        raise OSError("CFDictionaryCreate returned NULL")

    try:
        return bool(ax.AXIsProcessTrustedWithOptions(ctypes.c_void_p(options)))
    finally:
        cf.CFRelease(ctypes.c_void_p(options))
        cf.CFRelease(ctypes.c_void_p(key))


def open_permission_settings() -> bool:
    """Open the OS settings pane for granting input-injection permission.

    macOS only: opens the Accessibility pane directly via its
    ``x-apple.systempreferences:`` URL scheme. Windows and Linux have no
    equivalent single settings pane for this (Windows needs no permission at
    all; Linux's fix is choosing a different session at the login screen, not
    a settings pane), so this always returns False there.
    """
    if current_os() != OperatingSystem.MACOS:
        return False
    result = subprocess.run(
        ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"],
        check=False,
    )
    return result.returncode == 0


def config_dir(
    os_: OperatingSystem | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """The directory Auto Text Typer's config files (profiles, settings) live in.

    Follows each OS's own convention rather than a lowest-common-denominator
    dotfile: macOS's Application Support, Windows's roaming AppData (with a
    fallback for the rare environment where ``%APPDATA%`` is unset), and
    Linux's XDG base directory spec.
    """
    os_ = os_ if os_ is not None else current_os()
    env = env if env is not None else os.environ
    home = home if home is not None else Path.home()

    if os_ is OperatingSystem.MACOS:
        return home / "Library" / "Application Support" / "Auto Text Typer"

    if os_ is OperatingSystem.WINDOWS:
        appdata = env.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
        return base / "Auto Text Typer"

    xdg_config_home = env.get("XDG_CONFIG_HOME")
    base = Path(xdg_config_home) if xdg_config_home else home / ".config"
    return base / "auto-text-typer"


def legacy_config_file(env: Mapping[str, str] | None = None, home: Path | None = None) -> Path:
    """The pre-rename config file location, from when this project was macOS-only.

    Used only by ``config.py``'s migration path -- the app itself never
    writes here again once migrated.
    """
    env = env if env is not None else os.environ
    home = home if home is not None else Path.home()
    xdg_config_home = env.get("XDG_CONFIG_HOME")
    base = Path(xdg_config_home) if xdg_config_home else home / ".config"
    return base / "auto-essay-typer" / "profiles.toml"


def primary_modifier(os_: OperatingSystem | None = None) -> str:
    """The modifier key most keyboard shortcuts build on: cmd on macOS, ctrl elsewhere."""
    os_ = os_ if os_ is not None else current_os()
    return "cmd" if os_ is OperatingSystem.MACOS else "ctrl"


def normalize_input_text(text: str) -> str:
    """Normalise text pulled from another app (clipboard, file) before it is planned.

    Drops one leading UTF-8 BOM (U+FEFF) -- Windows editors and some
    clipboard sources prepend it, and typing it as a literal keystroke would
    leave a stray invisible character at the start of the document. Converts
    ``\\r\\n`` and lone ``\\r`` line endings to ``\\n``, since the rest of the
    project's planner, simulator, and Document model all assume ``\\n`` is
    the only line separator; left untouched, a Windows-authored file would
    otherwise type as doubled line breaks. Nothing else about the text is
    changed.
    """
    if text.startswith("﻿"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")
