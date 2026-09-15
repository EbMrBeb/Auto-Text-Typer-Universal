"""Tests for the Tkinter GUI.

Everything that needs a live Tk instance goes through the ``app`` fixture.
It is deliberately *session-scoped*: constructing (and, worse, destroying)
more than one ``tk.Tk()`` -- or even a ``Toplevel`` under one -- per process
was observed to hang this Tk/Aqua build indefinitely during development, so
one root and one TyperApp are built once and reused, with a per-test
autouse fixture resetting the bits of mutable state each test cares about.
The whole fixture chain skips outright if no window server is available, so
the suite still runs headless in CI. No test ever calls ``root.mainloop()``,
and none call ``app.start()`` with the real driver: that would inject actual
keystrokes into whatever window has focus on the machine running the tests.

``AUTO_ESSAY_TYPER_CONFIG`` is redirected to an isolated temp path before
the app is ever constructed, and re-redirected into a fresh ``tmp_path`` for
every individual test, so the real ``~/.config/auto-essay-typer`` is never
read or written.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import tkinter as tk

import pytest

from auto_essay_typer.app import (
    HOTKEYS,
    TyperApp,
    _cumulative_pause_ms_by_keypress,
    _format_mmss,
    accessibility_hint,
    profile_from_settings,
)
from auto_essay_typer.actions import KeyPress, Pause
from auto_essay_typer.profiles import PRESETS, Profile, field_names


@pytest.fixture(scope="session")
def app():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"no window server available: {exc}")
    root.withdraw()

    # A real hotkey listener installs a system-wide keyboard hook (pynput's
    # CGEventTap on macOS) -- fine for the running app, not for a test
    # process. Stub it out; the hotkey->method mapping itself is still
    # covered by test_hotkeys_map_to_real_bound_methods below.
    original_listener = TyperApp._start_hotkey_listener
    TyperApp._start_hotkey_listener = lambda self: None

    # monkeypatch is function-scoped and can't be used from a session-scoped
    # fixture, so AUTO_ESSAY_TYPER_CONFIG is saved/redirected/restored by
    # hand here, just for the one-time construction below. Every individual
    # test redirects it again itself, see ``_isolated_config``.
    original_env = os.environ.get("AUTO_ESSAY_TYPER_CONFIG")
    isolated_dir = tempfile.mkdtemp(prefix="auto_essay_typer_test_")
    os.environ["AUTO_ESSAY_TYPER_CONFIG"] = os.path.join(isolated_dir, "profiles.toml")
    try:
        instance = TyperApp(root)
    finally:
        if original_env is None:
            os.environ.pop("AUTO_ESSAY_TYPER_CONFIG", None)
        else:
            os.environ["AUTO_ESSAY_TYPER_CONFIG"] = original_env
        shutil.rmtree(isolated_dir, ignore_errors=True)

    yield instance

    TyperApp._start_hotkey_listener = original_listener
    root.destroy()


@pytest.fixture(autouse=True)
def _isolated_config(app, tmp_path, monkeypatch):
    """Give every test an empty, isolated profile store and a clean slate.

    ``_set_status`` marshals through ``root.after(0, ...)``, so a status
    update from the *previous* test can still be sitting unprocessed in
    Tk's event queue. ``update()`` must run first, to flush and discard
    that leftover, before the explicit resets below run -- otherwise a
    stale callback firing during this fixture's own ``update()`` would
    silently clobber the fresh state it just set.
    """
    monkeypatch.setenv("AUTO_ESSAY_TYPER_CONFIG", str(tmp_path / "profiles.toml"))
    app.root.update()
    app.text.delete("1.0", "end")
    app.driver = None
    app.worker = None
    app.status_var.set("")
    if app.advanced_visible:
        app._toggle_advanced()
    app._refresh_profile_list(select="natural")
    yield


# --- module-level functions kept for backward compatibility ------------------


def test_profile_from_settings_applies_preset_and_overrides():
    profile = profile_from_settings(preset_name="sloppy", wpm=95.0, typo_rate=0.5)
    assert profile.wpm == 95.0
    assert profile.typo_rate_per_100 == 0.5


def test_profile_from_settings_keeps_preset_defaults_when_not_overridden():
    profile = profile_from_settings(preset_name="clean", wpm=60.0, typo_rate=0.3)
    assert profile.revision_rate_per_word == pytest.approx(1.0 / 800.0)


def test_accessibility_hint_names_the_settings_pane():
    hint = accessibility_hint()
    assert "Accessibility" in hint
    assert "Privacy" in hint


# --- pure helpers --------------------------------------------------------------


def test_format_mmss():
    assert _format_mmss(0) == "0:00"
    assert _format_mmss(61_000) == "1:01"
    assert _format_mmss(-500) == "0:00"


def test_cumulative_pause_ms_by_keypress():
    actions = [Pause(100.0), KeyPress("a"), Pause(50.0), Pause(25.0), KeyPress("b")]
    assert _cumulative_pause_ms_by_keypress(actions) == [0.0, 100.0, 175.0]


def test_hotkeys_map_to_real_bound_methods():
    assert set(HOTKEYS) == {"<f9>", "<f10>", "<esc>"}
    for method_name in HOTKEYS.values():
        assert callable(getattr(TyperApp, method_name))


# --- construction --------------------------------------------------------------


def test_app_builds(app):
    assert app.text is not None
    assert app.preview is not None
    assert app.advanced_visible is False


def test_settings_panel_has_all_profile_fields(app):
    # Derived from field_names() rather than a hardcoded count, so adding a
    # knob to Profile never breaks this test.
    panel = app.settings_panel
    total = len(panel._scalar_vars) + len(panel._bool_vars) + len(panel._tuple_vars)
    assert total == len(field_names())


def test_settings_panel_groups_fields_into_labelled_sections():
    # Regression test: _SECTION_BOUNDARIES pairs must read as
    # (field_name, label) -- swapped order silently collapses every field
    # into a single mislabelled section (caught during development).
    from auto_essay_typer.settings_panel import _sectioned_fields

    sections = _sectioned_fields()
    assert [label for label, _ in sections] == [
        "Speed",
        "Thinking pauses",
        "Mistakes",
        "Revision",
        "Session breaks",
        "Formatting",
    ]
    assert sum(len(fields) for _, fields in sections) == len(field_names())
    # A couple of fields that must land in a specific section.
    by_label = {label: [f.name for f in fields] for label, fields in sections}
    assert "wpm" in by_label["Speed"]
    assert "session_breaks" in by_label["Session breaks"]
    assert "revision_span_words" in by_label["Revision"]
    assert "format_shortcut_ms" in by_label["Formatting"]


# --- settings panel round trip --------------------------------------------------


@pytest.mark.parametrize("name", sorted(PRESETS))
def test_settings_panel_round_trips_each_preset(app, name):
    original = PRESETS[name]
    app.settings_panel.load(original)
    restored = app.settings_panel.to_profile(Profile())
    assert restored == original


def test_settings_panel_set_value_bool_field(app):
    app.settings_panel.set_value("session_breaks", False)
    assert app.settings_panel.to_profile(Profile()).session_breaks is False
    app.settings_panel.set_value("session_breaks", True)
    assert app.settings_panel.to_profile(Profile()).session_breaks is True


def test_settings_panel_set_value_tuple_field(app):
    app.settings_panel.set_value("burst_range", (0.5, 1.9))
    assert app.settings_panel.to_profile(Profile()).burst_range == (0.5, 1.9)


def test_settings_panel_set_value_unknown_field_raises(app):
    with pytest.raises(KeyError):
        app.settings_panel.set_value("not_a_real_field", 1)


# --- advanced disclosure ---------------------------------------------------------


def test_advanced_toggle_flips_visibility(app):
    # The window is withdrawn for tests, so winfo_ismapped() is always
    # False regardless of layout; winfo_manager() reflects whether the
    # geometry manager (pack) currently governs the widget at all, which is
    # exactly what pack()/pack_forget() toggle.
    assert app.advanced_visible is False
    assert app.advanced_container.winfo_manager() == ""

    app._toggle_advanced()
    app.root.update()
    assert app.advanced_visible is True
    assert app.advanced_container.winfo_manager() == "pack"

    app._toggle_advanced()
    app.root.update()
    assert app.advanced_visible is False
    assert app.advanced_container.winfo_manager() == ""


# --- character/word counter ------------------------------------------------------


def test_counter_updates_after_inserting_text(app):
    assert app.counter_var.get() == "0 chars, 0 words"
    app.text.insert("1.0", "hello world")
    app.root.update()
    assert app.counter_var.get() == "11 chars, 2 words"


def test_counter_updates_again_after_more_text(app):
    app.text.insert("1.0", "one")
    app.root.update()
    app.text.insert("end", " two three")
    app.root.update()
    assert app.counter_var.get() == "13 chars, 3 words"


# --- dry run ---------------------------------------------------------------------


def test_dry_run_populates_preview_pane(app):
    app.text.insert("1.0", "The premise is contested. It does not follow. " * 3)
    app.dry_run()
    content = app.preview.get("1.0", "end-1c")
    assert "characters:" in content
    assert "output matches input exactly" in content or "MISMATCH" in content


def test_dry_run_with_empty_text_reports_nothing_to_type(app):
    app.dry_run()
    app.root.update()  # _set_status marshals through root.after(0, ...)
    assert app.status_var.get() == "Nothing to type."


# --- profile management -----------------------------------------------------------


def test_save_as_then_reload_persists_profile(app, monkeypatch):
    from tkinter import simpledialog

    from auto_essay_typer import config

    monkeypatch.setattr(simpledialog, "askstring", lambda *a, **k: "my-custom")
    app.wpm_var.set(111.0)
    app._on_wpm_change("111.0")
    app._save_as()

    assert "my-custom" in config.load_profiles()
    assert config.load_profiles()["my-custom"].wpm == 111.0


def test_delete_profile_refuses_builtin(app):
    assert app._selected_profile_name() == "natural"  # the default selection
    app._delete_profile()
    app.root.update()  # _set_status marshals through root.after(0, ...)
    assert app.status_var.get().endswith("cannot be deleted.")


# --- run guards (no real typing is ever triggered in tests) -----------------------


def test_start_is_idempotent_while_worker_running(app):
    class _AliveThread:
        def is_alive(self) -> bool:
            return True

    app.text.insert("1.0", "hello")
    app.worker = _AliveThread()
    app.driver = None
    app.start()
    assert app.driver is None  # start() returned early; no Driver was created


def test_abort_does_not_raise_with_no_driver(app):
    app.driver = None
    app.abort()  # must not raise


def test_toggle_pause_does_not_raise_with_no_driver(app):
    app.driver = None
    app.toggle_pause()  # must not raise
