"""Tests for the per-editor shortcut override table (``gui/keymap_editor.py``).

Every test works against a ``SettingsStore`` backed by a file in ``tmp_path``
and pins ``platforms.current_os`` so the chords asserted on are the ones for a
known OS (macOS by default: the only OS where every target app exists).
"""

from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from auto_essay_typer import app_settings, platforms, targets
from auto_essay_typer.actions import FormatOp
from auto_essay_typer.gui.keymap_editor import (
    KeymapEditor,
    describe_chord_error,
    describe_store_error,
    normalize_chord_text,
    op_label,
)
from auto_essay_typer.gui.settings_store import SettingsStore
from auto_essay_typer.gui.theme import DARK_TOKENS, LIGHT_TOKENS, ThemeManager, ThemeMode
from auto_essay_typer.keymaps import parse_chord
from auto_essay_typer.platforms import OperatingSystem

HOTKEY_CHORD = "cmd+alt+shift+k"
HOTKEY_TEXT = "<cmd>+<alt>+<shift>+k"


@pytest.fixture(autouse=True)
def mac(monkeypatch):
    monkeypatch.setattr(platforms, "current_os", lambda *a, **k: OperatingSystem.MACOS)


@pytest.fixture(autouse=True)
def _delete_closed_widgets():
    """Run the deferred deletes of the widgets pytest-qt closed, so live widgets don't pile up
    (every ``ThemeManager`` re-polishes all of them)."""
    yield
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


class Confirm:
    """A recording stand-in for the yes/no dialog."""

    def __init__(self, answer: bool = True) -> None:
        self.answer = answer
        self.calls: list[tuple[str, str]] = []

    def __call__(self, parent, title, text) -> bool:
        self.calls.append((title, text))
        return self.answer


def _make(qtbot, qapp, tmp_path, *, store=None, confirm=None, theme_manager=None):
    store = store if store is not None else SettingsStore(tmp_path / "settings.json")
    editor = KeymapEditor(
        theme_manager if theme_manager is not None else ThemeManager(qapp),
        store,
        confirm=confirm if confirm is not None else Confirm(True),
    )
    qtbot.addWidget(editor)
    return editor, store


def _commit(row, text: str) -> None:
    """Type ``text`` into a row's box and finish editing (Enter / focus out)."""
    row.edit.setText(text)
    row.edit.editingFinished.emit()


def _file_overrides(store) -> dict:
    return json.loads(store.path.read_text(encoding="utf-8"))["keymap_overrides"]


# --- structure ---------------------------------------------------------------------------------


def test_there_is_one_row_for_every_formatting_command(qtbot, qapp, tmp_path):
    editor, _store = _make(qtbot, qapp, tmp_path)
    assert list(editor.rows) == list(FormatOp)
    assert len(editor.rows) == len(FormatOp)


def test_command_names_are_readable(qtbot, qapp, tmp_path):
    editor, _store = _make(qtbot, qapp, tmp_path)
    assert editor.rows[FormatOp.TOGGLE_BOLD].action_label.text() == "Toggle bold"
    assert editor.rows[FormatOp.SET_HEADING_1].action_label.text() == "Set heading 1"
    assert editor.rows[FormatOp.CLEAR_FORMATTING].action_label.text() == "Clear formatting"
    assert op_label(FormatOp.INDENT) == "Indent"


def test_no_user_visible_text_leaks_internal_names(qtbot, qapp, tmp_path):
    editor, _store = _make(qtbot, qapp, tmp_path)
    for row in editor.rows.values():
        assert "_" not in row.action_label.text()
        assert "FormatOp" not in row.action_label.text()


def test_the_combo_lists_the_targets_for_this_os(qtbot, qapp, tmp_path):
    editor, _store = _make(qtbot, qapp, tmp_path)
    ids = [editor.target_combo.itemData(i) for i in range(editor.target_combo.count())]
    assert ids == [t.id for t in targets.targets_for(OperatingSystem.MACOS)]
    names = [editor.target_combo.itemText(i) for i in range(editor.target_combo.count())]
    assert "Google Docs" in names


def test_the_combo_omits_editors_that_do_not_exist_on_this_os(qtbot, qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(platforms, "current_os", lambda *a, **k: OperatingSystem.LINUX)
    editor, _store = _make(qtbot, qapp, tmp_path)
    ids = [editor.target_combo.itemData(i) for i in range(editor.target_combo.count())]
    assert "microsoft-word" not in ids and "textedit" not in ids
    assert "google-docs" in ids


def test_the_combo_starts_on_the_default_editor(qtbot, qapp, tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.update(target_app="libreoffice-writer")
    editor, _store = _make(qtbot, qapp, tmp_path, store=store)
    assert editor.current_target_id() == "libreoffice-writer"


def test_the_combo_falls_back_when_the_default_editor_is_unavailable(qtbot, qapp, tmp_path, monkeypatch):
    # A saved textedit default on a non-mac OS: the first listed editor is shown instead.
    store = SettingsStore(tmp_path / "settings.json")
    store.update(target_app="textedit")
    monkeypatch.setattr(platforms, "current_os", lambda *a, **k: OperatingSystem.WINDOWS)
    editor, _store = _make(qtbot, qapp, tmp_path, store=store)
    assert editor.current_target_id() == targets.targets_for(OperatingSystem.WINDOWS)[0].id


def test_an_empty_combo_reports_a_listed_editor_not_the_word_None(qtbot, qapp, tmp_path):
    editor, _store = _make(qtbot, qapp, tmp_path)
    editor.target_combo.blockSignals(True)
    editor.target_combo.setCurrentIndex(-1)
    editor.target_combo.blockSignals(False)
    assert editor.target_combo.currentData() is None
    assert editor.current_target_id() == targets.targets_for(OperatingSystem.MACOS)[0].id


def test_set_target_rejects_an_editor_that_is_not_listed(qtbot, qapp, tmp_path):
    editor, _store = _make(qtbot, qapp, tmp_path)
    with pytest.raises(ValueError, match="not available"):
        editor.set_target("no-such-editor")


# --- defaults shown as placeholders ---------------------------------------------------------------


def test_the_default_chord_is_the_placeholder(qtbot, qapp, tmp_path):
    editor, _store = _make(qtbot, qapp, tmp_path)
    bold = editor.rows[FormatOp.TOGGLE_BOLD]
    assert bold.edit.placeholderText() == "cmd+b"
    assert bold.default_label.text() == "cmd+b"
    assert bold.edit.text() == ""  # nothing overridden


def test_a_command_with_no_default_is_marked_not_available(qtbot, qapp, tmp_path):
    editor, _store = _make(qtbot, qapp, tmp_path)
    editor.set_target("textedit")
    strike = editor.rows[FormatOp.TOGGLE_STRIKETHROUGH]
    assert strike.default_label.text() == "Not available"
    assert strike.edit.placeholderText() == "not set"


def test_a_deliberately_empty_default_is_marked_no_shortcut(qtbot, qapp, tmp_path):
    editor, _store = _make(qtbot, qapp, tmp_path)
    editor.set_target("textedit")
    clear = editor.rows[FormatOp.CLEAR_FORMATTING]
    assert clear.default_label.text() == "No shortcut"
    assert clear.edit.placeholderText() == "none"


def test_switching_editor_repopulates_the_defaults(qtbot, qapp, tmp_path):
    editor, _store = _make(qtbot, qapp, tmp_path)
    editor.set_target("google-docs")
    assert editor.rows[FormatOp.TOGGLE_STRIKETHROUGH].default_label.text() == "cmd+shift+x"
    editor.set_target("microsoft-word")
    assert editor.rows[FormatOp.TOGGLE_STRIKETHROUGH].default_label.text() == "Not available"
    assert editor.rows[FormatOp.SET_HEADING_1].default_label.text() == "cmd+alt+1"
    editor.set_target("libreoffice-writer")
    assert editor.rows[FormatOp.SET_HEADING_1].default_label.text() == "cmd+1"


def test_an_editor_that_was_not_checked_against_a_live_install_shows_a_caution(qtbot, qapp, tmp_path):
    editor, _store = _make(qtbot, qapp, tmp_path)
    editor.set_target("google-docs")
    assert editor.caution_label.isHidden()
    editor.set_target("microsoft-word")
    assert not editor.caution_label.isHidden()
    assert "Microsoft Word" in editor.caution_label.text()
    editor.set_target("plain-text")
    assert editor.caution_label.isHidden()


# --- text helpers ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("ctrl+shift+7", "ctrl+shift+7"),
        ("  Ctrl + Shift + 7 ", "ctrl+shift+7"),
        ("Command+B", "cmd+b"),
        ("OPTION + control + x", "alt+ctrl+x"),
        ("shift+Return", "shift+enter"),
        ("", ""),
        ("   ", ""),
        ("ctrl+", "ctrl+"),
        ("bogus", "bogus"),
    ],
)
def test_normalize_chord_text(typed, expected):
    assert normalize_chord_text(typed) == expected


@pytest.mark.parametrize(
    ("typed", "fragment"),
    [
        ("ctrl+bogus+i", "'bogus' isn't a modifier key"),
        ("ctrl+shift+word", "'word' isn't a single key"),
        ("ctrl+", "A key is missing"),
        ("+b", "A key is missing"),
    ],
)
def test_describe_chord_error_is_plain_language(typed, fragment):
    with pytest.raises(ValueError) as info:
        parse_chord(typed)
    message = describe_chord_error(info.value)
    assert fragment in message
    assert "ctrl+shift+7" in message  # always shows an example
    assert "chord" not in message


def test_describe_store_error_rewords_a_hotkey_collision_and_passes_others_through():
    collision = ValueError(
        "hotkey for 'start' ('<cmd>+<alt>+<shift>+k') collides with the 'toggle_bold' formatting "
        "shortcut -- this app presses that chord itself while typing"
    )
    assert describe_store_error(collision) == "Clashes with your “start” hotkey. Pick another shortcut."
    assert describe_store_error(ValueError("something else")) == "Can't use this shortcut: something else"


def test_a_row_error_clears_its_tooltip_with_the_message(qtbot, qapp, tmp_path):
    editor, store = _make(qtbot, qapp, tmp_path)
    store.update(hotkeys={**store.settings.hotkeys, "start": HOTKEY_TEXT})
    bold = editor.rows[FormatOp.TOGGLE_BOLD]
    _commit(bold, HOTKEY_CHORD)
    assert bold.error_label.toolTip()
    bold.set_error(None)
    assert bold.error_label.toolTip() == ""


# --- saving overrides -------------------------------------------------------------------------------------


def test_a_valid_override_is_saved_normalised(qtbot, qapp, tmp_path):
    editor, store = _make(qtbot, qapp, tmp_path)
    bold = editor.rows[FormatOp.TOGGLE_BOLD]
    with qtbot.waitSignal(editor.overridesChanged, timeout=1000):
        _commit(bold, "Ctrl + Shift + B")

    assert store.settings.keymap_overrides == {"google-docs": {"toggle_bold": "ctrl+shift+b"}}
    assert _file_overrides(store) == {"google-docs": {"toggle_bold": "ctrl+shift+b"}}
    assert bold.saved == "ctrl+shift+b"
    assert bold.edit.text() == "ctrl+shift+b"  # tidied in place
    assert not bold.has_error()
    assert bold.edit.property("overridden") is True
    # And it is what the rest of the app now sees.
    effective = app_settings.effective_keymap(store.settings, OperatingSystem.MACOS)
    assert effective[FormatOp.TOGGLE_BOLD] == ("ctrl", "shift", "b")


def test_an_invalid_chord_is_rejected_with_a_message_and_not_saved(qtbot, qapp, tmp_path):
    editor, store = _make(qtbot, qapp, tmp_path)
    italic = editor.rows[FormatOp.TOGGLE_ITALIC]
    _commit(italic, "ctrl+bogus+i")

    assert italic.has_error()
    assert "bogus" in italic.error_label.text()
    assert not italic.error_label.isHidden()
    assert italic.edit.property("invalid") is True
    assert italic.saved == ""
    assert store.settings.keymap_overrides == {}
    assert not store.path.exists()


def test_an_error_clears_as_soon_as_the_text_parses_again(qtbot, qapp, tmp_path):
    editor, _store = _make(qtbot, qapp, tmp_path)
    italic = editor.rows[FormatOp.TOGGLE_ITALIC]
    _commit(italic, "ctrl+bogus+i")
    assert italic.has_error()
    italic.edit.setText("ctrl+")  # still invalid: stays flagged
    assert italic.has_error()
    italic.edit.setText("ctrl+alt+i")  # parses: the flag drops before anything is saved
    assert not italic.has_error()
    assert italic.edit.property("invalid") is False


def test_an_error_clears_when_the_box_is_emptied(qtbot, qapp, tmp_path):
    editor, _store = _make(qtbot, qapp, tmp_path)
    italic = editor.rows[FormatOp.TOGGLE_ITALIC]
    _commit(italic, "nonsense+")
    assert italic.has_error()
    italic.edit.setText("")
    assert not italic.has_error()


def test_typing_a_half_finished_chord_does_not_flash_an_error(qtbot, qapp, tmp_path):
    editor, store = _make(qtbot, qapp, tmp_path)
    italic = editor.rows[FormatOp.TOGGLE_ITALIC]
    italic.edit.setText("ctrl+")  # no editingFinished yet
    assert not italic.has_error()
    assert store.settings.keymap_overrides == {}


def test_an_override_equal_to_the_default_is_dropped(qtbot, qapp, tmp_path):
    editor, store = _make(qtbot, qapp, tmp_path)
    bold = editor.rows[FormatOp.TOGGLE_BOLD]
    _commit(bold, "Command+B")  # the Google Docs default
    assert store.settings.keymap_overrides == {}
    assert bold.edit.text() == ""  # the box tidies back to showing the placeholder
    assert not store.path.exists()


def test_clearing_a_box_removes_just_that_override(qtbot, qapp, tmp_path):
    editor, store = _make(qtbot, qapp, tmp_path)
    _commit(editor.rows[FormatOp.TOGGLE_BOLD], "ctrl+shift+b")
    _commit(editor.rows[FormatOp.TOGGLE_ITALIC], "ctrl+shift+i")
    _commit(editor.rows[FormatOp.TOGGLE_BOLD], "")
    assert store.settings.keymap_overrides == {"google-docs": {"toggle_italic": "ctrl+shift+i"}}
    _commit(editor.rows[FormatOp.TOGGLE_ITALIC], "")
    assert store.settings.keymap_overrides == {}  # an empty per-editor dict is not kept


def test_overrides_for_a_command_the_editor_lacks_are_allowed(qtbot, qapp, tmp_path):
    editor, store = _make(qtbot, qapp, tmp_path)
    editor.set_target("microsoft-word")
    _commit(editor.rows[FormatOp.TOGGLE_STRIKETHROUGH], "cmd+shift+x")
    assert store.settings.keymap_overrides == {"microsoft-word": {"toggle_strikethrough": "cmd+shift+x"}}


def test_overrides_are_kept_per_editor(qtbot, qapp, tmp_path):
    editor, store = _make(qtbot, qapp, tmp_path)
    _commit(editor.rows[FormatOp.TOGGLE_BOLD], "ctrl+shift+b")
    editor.set_target("libreoffice-writer")
    assert editor.rows[FormatOp.TOGGLE_BOLD].edit.text() == ""  # a different editor
    _commit(editor.rows[FormatOp.TOGGLE_ITALIC], "ctrl+shift+i")
    assert store.settings.keymap_overrides == {
        "google-docs": {"toggle_bold": "ctrl+shift+b"},
        "libreoffice-writer": {"toggle_italic": "ctrl+shift+i"},
    }
    editor.set_target("google-docs")
    assert editor.rows[FormatOp.TOGGLE_BOLD].edit.text() == "ctrl+shift+b"


def test_one_bad_row_does_not_block_another(qtbot, qapp, tmp_path):
    editor, store = _make(qtbot, qapp, tmp_path)
    _commit(editor.rows[FormatOp.TOGGLE_ITALIC], "ctrl+bogus+i")
    _commit(editor.rows[FormatOp.TOGGLE_BOLD], "ctrl+shift+b")
    assert editor.rows[FormatOp.TOGGLE_ITALIC].has_error()
    assert store.settings.keymap_overrides == {"google-docs": {"toggle_bold": "ctrl+shift+b"}}


# --- refusals from the store ------------------------------------------------------------------------------------


def test_a_chord_that_collides_with_a_global_hotkey_is_refused_with_the_reason(qtbot, qapp, tmp_path):
    editor, store = _make(qtbot, qapp, tmp_path)
    store.update(hotkeys={**store.settings.hotkeys, "start": HOTKEY_TEXT})
    bold = editor.rows[FormatOp.TOGGLE_BOLD]
    _commit(bold, HOTKEY_CHORD)

    assert bold.has_error()
    # The row says it in plain language; the store's full reason is in the tooltip.
    assert "hotkey" in bold.error_label.text() and "start" in bold.error_label.text()
    assert "_" not in bold.error_label.text()  # no internal identifiers like toggle_bold
    assert "collides" in bold.error_label.toolTip()
    assert bold.saved == ""
    assert store.settings.keymap_overrides == {}


def test_a_collision_is_caught_for_an_editor_other_than_the_current_one(qtbot, qapp, tmp_path):
    # The store only checks the *current* target's keymap, so on its own it would
    # accept this; the editor must not, or switching to Word later would make the
    # settings file unloadable.
    editor, store = _make(qtbot, qapp, tmp_path)
    store.update(hotkeys={**store.settings.hotkeys, "start": HOTKEY_TEXT})
    assert store.settings.target_app == "google-docs"
    editor.set_target("microsoft-word")
    strike = editor.rows[FormatOp.TOGGLE_STRIKETHROUGH]
    _commit(strike, HOTKEY_CHORD)

    assert strike.has_error() and "hotkey" in strike.error_label.text()
    assert "collides" in strike.error_label.toolTip()
    assert store.settings.keymap_overrides == {}


def test_a_failed_save_is_reported_on_the_row(qtbot, qapp, tmp_path, monkeypatch):
    editor, store = _make(qtbot, qapp, tmp_path)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(app_settings, "save_settings", boom)
    bold = editor.rows[FormatOp.TOGGLE_BOLD]
    _commit(bold, "ctrl+shift+b")
    assert bold.has_error()
    assert "Could not save" in bold.error_label.text() and "disk full" in bold.error_label.text()
    assert store.settings.keymap_overrides == {}


# --- restoring defaults -------------------------------------------------------------------------------------------


def test_restore_defaults_is_disabled_until_something_is_overridden(qtbot, qapp, tmp_path):
    editor, _store = _make(qtbot, qapp, tmp_path)
    assert not editor.restore_button.isEnabled()
    _commit(editor.rows[FormatOp.TOGGLE_BOLD], "ctrl+shift+b")
    assert editor.restore_button.isEnabled()


def test_restore_defaults_asks_then_clears_only_this_editor(qtbot, qapp, tmp_path):
    confirm = Confirm(True)
    editor, store = _make(qtbot, qapp, tmp_path, confirm=confirm)
    _commit(editor.rows[FormatOp.TOGGLE_BOLD], "ctrl+shift+b")
    editor.set_target("libreoffice-writer")
    _commit(editor.rows[FormatOp.TOGGLE_ITALIC], "ctrl+shift+i")
    editor.set_target("google-docs")

    with qtbot.waitSignal(editor.overridesChanged, timeout=1000):
        editor.restore_button.click()

    assert len(confirm.calls) == 1
    assert "Google Docs" in confirm.calls[0][1]
    assert store.settings.keymap_overrides == {"libreoffice-writer": {"toggle_italic": "ctrl+shift+i"}}
    assert editor.rows[FormatOp.TOGGLE_BOLD].edit.text() == ""
    assert not editor.restore_button.isEnabled()


def test_declining_restore_defaults_changes_nothing(qtbot, qapp, tmp_path):
    confirm = Confirm(False)
    editor, store = _make(qtbot, qapp, tmp_path, confirm=confirm)
    _commit(editor.rows[FormatOp.TOGGLE_BOLD], "ctrl+shift+b")
    editor.restore_button.click()
    assert len(confirm.calls) == 1
    assert store.settings.keymap_overrides == {"google-docs": {"toggle_bold": "ctrl+shift+b"}}
    assert editor.rows[FormatOp.TOGGLE_BOLD].edit.text() == "ctrl+shift+b"


def test_restore_defaults_also_clears_half_typed_rows(qtbot, qapp, tmp_path):
    editor, _store = _make(qtbot, qapp, tmp_path)
    _commit(editor.rows[FormatOp.TOGGLE_BOLD], "ctrl+shift+b")
    _commit(editor.rows[FormatOp.TOGGLE_ITALIC], "ctrl+bogus+i")
    editor.restore_button.click()
    italic = editor.rows[FormatOp.TOGGLE_ITALIC]
    assert italic.edit.text() == ""
    assert not italic.has_error()


# --- staying in step with the store -----------------------------------------------------------------------------------------


def test_a_change_made_elsewhere_shows_up_in_the_rows(qtbot, qapp, tmp_path):
    editor, store = _make(qtbot, qapp, tmp_path)
    store.update(keymap_overrides={"google-docs": {"toggle_italic": "cmd+alt+i"}})
    italic = editor.rows[FormatOp.TOGGLE_ITALIC]
    assert italic.edit.text() == "cmd+alt+i"
    assert italic.saved == "cmd+alt+i"
    assert editor.restore_button.isEnabled()


def test_an_outside_change_does_not_wipe_a_row_that_is_showing_an_error(qtbot, qapp, tmp_path):
    editor, store = _make(qtbot, qapp, tmp_path)
    bold = editor.rows[FormatOp.TOGGLE_BOLD]
    _commit(bold, "ctrl+bogus+b")
    store.update(keymap_overrides={"google-docs": {"toggle_italic": "cmd+alt+i"}})
    assert bold.has_error()
    assert bold.edit.text() == "ctrl+bogus+b"


def test_overrides_already_in_the_settings_are_shown_at_start(qtbot, qapp, tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.update(keymap_overrides={"google-docs": {"toggle_bold": "ctrl+shift+b"}})
    editor, _store = _make(qtbot, qapp, tmp_path, store=store)
    assert editor.rows[FormatOp.TOGGLE_BOLD].edit.text() == "ctrl+shift+b"
    assert editor.restore_button.isEnabled()


# --- duplicate shortcuts ---------------------------------------------------------------------------------------------------------


def test_two_commands_sharing_a_chord_get_a_gentle_warning(qtbot, qapp, tmp_path):
    editor, _store = _make(qtbot, qapp, tmp_path)
    assert editor.duplicates_label.isHidden()  # the shipped defaults never clash
    _commit(editor.rows[FormatOp.TOGGLE_ITALIC], "cmd+b")  # the same as bold's default
    assert not editor.duplicates_label.isHidden()
    text = editor.duplicates_label.text()
    assert "cmd+b" in text and "Toggle bold" in text and "Toggle italic" in text
    _commit(editor.rows[FormatOp.TOGGLE_ITALIC], "")
    assert editor.duplicates_label.isHidden()


def test_the_warning_does_not_block_saving(qtbot, qapp, tmp_path):
    editor, store = _make(qtbot, qapp, tmp_path)
    _commit(editor.rows[FormatOp.TOGGLE_ITALIC], "cmd+b")
    assert store.settings.keymap_overrides == {"google-docs": {"toggle_italic": "cmd+b"}}


# --- theming ---------------------------------------------------------------------------------------------------------------------------


def test_the_editor_restyles_when_the_theme_changes(qtbot, qapp, tmp_path):
    theme_manager = ThemeManager(qapp, mode=ThemeMode.LIGHT)
    editor, _store = _make(qtbot, qapp, tmp_path, theme_manager=theme_manager)
    assert LIGHT_TOKENS.muted_text in editor.styleSheet()
    theme_manager.set_mode(ThemeMode.DARK)
    assert DARK_TOKENS.muted_text in editor.styleSheet()
    assert LIGHT_TOKENS.muted_text not in editor.styleSheet()
    theme_manager.set_mode(ThemeMode.SYSTEM)
