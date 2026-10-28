"""The Type page's "Typing into" editor picker."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from auto_essay_typer import platforms, targets
from auto_essay_typer.actions import FormatOp
from auto_essay_typer.gui.quick_settings import target_caution
from auto_essay_typer.gui.settings_store import SettingsStore
from auto_essay_typer.platforms import OperatingSystem

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "gdocs_export_sample.html"


def _as(monkeypatch, os_: OperatingSystem) -> None:
    monkeypatch.setattr(platforms, "current_os", lambda *args, **kwargs: os_)


def _items(page) -> list[tuple[str, str]]:
    combo = page.target_combo
    return [(combo.itemText(i), combo.itemData(i)) for i in range(combo.count())]


def _pick(page, target_id: str) -> None:
    page.target_combo.setCurrentIndex(page.target_combo.findData(target_id))


def _import(qtbot, page) -> None:
    page.import_bar.set_source(str(FIXTURE))
    with qtbot.waitSignal(page.importFinished, timeout=10000):
        page.import_bar.import_button.click()


# --- what is offered --------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("os_", list(OperatingSystem))
def test_the_picker_lists_exactly_the_editors_this_system_supports_by_display_name(type_page_rig, monkeypatch, os_):
    _as(monkeypatch, os_)
    page = type_page_rig().page
    expected = [(target.name, target.id) for target in targets.targets_for(os_)]
    assert _items(page) == expected


def test_each_system_is_offered_its_own_editors(type_page_rig, monkeypatch):
    names = {}
    for os_ in OperatingSystem:
        _as(monkeypatch, os_)
        names[os_] = [name for name, _id in _items(type_page_rig().page)]
    assert "TextEdit" in names[OperatingSystem.MACOS]
    assert "TextEdit" not in names[OperatingSystem.WINDOWS] + names[OperatingSystem.LINUX]
    assert "Microsoft Word" in names[OperatingSystem.WINDOWS]
    assert "Microsoft Word" not in names[OperatingSystem.LINUX]
    assert all("Google Docs" in listed for listed in names.values())


def test_the_picker_has_a_label_a_name_and_a_tooltip(type_page_rig):
    page = type_page_rig().page
    assert page.target_combo.accessibleName() == "Typing into"
    assert page.target_combo.toolTip()


# --- where it starts -----------------------------------------------------------------------------------------------------------------


def test_the_picker_defaults_to_google_docs_with_no_saved_choice(type_page_rig):
    assert type_page_rig().page.selected_target_id() == "google-docs"


@pytest.mark.parametrize("saved", ["microsoft-word", "libreoffice-writer", "plain-text"])
def test_the_picker_starts_on_the_saved_editor(type_page_rig, monkeypatch, tmp_path, saved):
    _as(monkeypatch, OperatingSystem.MACOS)
    store = SettingsStore(tmp_path / "settings.json")
    store.update(target_app=saved)

    page = type_page_rig(store=SettingsStore(tmp_path / "settings.json")).page

    assert page.selected_target_id() == saved
    assert page.target_combo.currentText() == targets.get_target(saved).name


def test_a_saved_editor_this_system_does_not_have_falls_back_to_the_first(type_page_rig, monkeypatch, tmp_path):
    _as(monkeypatch, OperatingSystem.MACOS)
    store = SettingsStore(tmp_path / "settings.json")
    store.update(target_app="textedit")
    # Carried over to a Linux box, where TextEdit does not exist.
    _as(monkeypatch, OperatingSystem.LINUX)

    page = type_page_rig(store=store).page

    assert page.selected_target_id() == "google-docs"


# --- saving ----------------------------------------------------------------------------------------------------------------------------


def test_changing_the_editor_saves_the_choice(qtbot, type_page_rig, tmp_path):
    rig = type_page_rig()
    saved = []
    rig.store.changed.connect(saved.append)

    _pick(rig.page, "microsoft-word")

    assert rig.store.settings.target_app == "microsoft-word"
    assert [settings.target_app for settings in saved] == ["microsoft-word"]
    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["target_app"] == "microsoft-word"
    # A page opened later (the next launch) starts on what was chosen.
    assert type_page_rig(store=SettingsStore(tmp_path / "settings.json")).page.selected_target_id() == "microsoft-word"


def test_choosing_the_same_editor_again_saves_nothing(qtbot, type_page_rig, tmp_path):
    rig = type_page_rig()
    _pick(rig.page, "plain-text")
    with qtbot.assertNotEmitted(rig.store.changed):
        _pick(rig.page, "plain-text")


def test_a_choice_that_cannot_be_written_is_still_used_for_now_and_the_user_is_told(qtbot, type_page_rig, tmp_path):
    unwritable = tmp_path / "settings-is-a-folder"
    unwritable.mkdir()
    rig = type_page_rig(store=SettingsStore(unwritable))

    _pick(rig.page, "microsoft-word")

    assert rig.page.selected_target_id() == "microsoft-word"
    assert rig.page.target_notes_label.text() == targets.get_target("microsoft-word").notes
    assert rig.page.inline_message_label.isHidden() is False
    assert "couldn't be saved" in rig.page.inline_message_label.text()


def test_a_choice_the_store_refuses_puts_the_picker_back_and_says_why(qtbot, type_page_rig, monkeypatch, tmp_path):
    _as(monkeypatch, OperatingSystem.MACOS)
    store = SettingsStore(tmp_path / "settings.json")
    store.update(target_app="plain-text")
    # Cmd+B is a fine global hotkey while typing into a plain-text field ...
    store.update(hotkeys={"start": "<cmd>+b", "pause": "<cmd>+<alt>+<shift>+p", "stop": "<esc>"})
    page = type_page_rig(store=store).page
    assert page.selected_target_id() == "plain-text"

    # ... but it is Google Docs' bold shortcut, which this app would press while typing.
    _pick(page, "google-docs")

    assert page.selected_target_id() == "plain-text"
    assert store.settings.target_app == "plain-text"
    assert page.target_notes_label.text() == targets.get_target("plain-text").notes
    assert page.inline_message_label.isHidden() is False
    assert page.inline_message_label.text().startswith("Couldn't switch to Google Docs")


# --- what the page says about the editor ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("target_id", ["google-docs", "microsoft-word", "libreoffice-writer", "plain-text"])
def test_the_selected_editors_notes_are_shown_under_the_picker(type_page_rig, target_id):
    page = type_page_rig().page
    _pick(page, target_id)
    assert page.target_notes_label.text() == targets.get_target(target_id).notes
    assert page.target_notes_label.isHidden() is False


def test_the_notes_follow_the_selection(type_page_rig):
    page = type_page_rig().page
    notes = []
    for target in targets.targets_for(platforms.current_os()):
        _pick(page, target.id)
        notes.append(page.target_notes_label.text())
    assert notes == [target.notes for target in targets.targets_for(platforms.current_os())]


def test_a_checked_editor_gets_no_caution(type_page_rig):
    page = type_page_rig().page
    for target in targets.targets_for(platforms.current_os()):
        if target.verified:
            _pick(page, target.id)
            assert page.target_caution_label.isHidden() is True, target.id


def test_an_unchecked_editor_gets_a_muted_caution_pointing_to_the_advanced_page(type_page_rig, monkeypatch):
    _as(monkeypatch, OperatingSystem.MACOS)
    page = type_page_rig().page
    unchecked = [t for t in targets.targets_for(OperatingSystem.MACOS) if not t.verified]
    assert {t.id for t in unchecked} == {"microsoft-word", "libreoffice-writer", "textedit"}
    for target in unchecked:
        _pick(page, target.id)
        assert page.target_caution_label.isHidden() is False, target.id
        text = page.target_caution_label.text()
        assert text == target_caution(target)
        assert "best effort" in text and "Advanced page" in text
        assert page.target_caution_label.objectName() == "MutedLabel"


def test_the_caution_goes_away_again_when_a_checked_editor_is_chosen(type_page_rig):
    page = type_page_rig().page
    _pick(page, "microsoft-word")
    assert page.target_caution_label.isHidden() is False
    _pick(page, "google-docs")
    assert page.target_caution_label.isHidden() is True


# --- changing the editor re-adapts a loaded document ---------------------------------------------------------------------------------


def test_switching_editor_re_adapts_the_document_and_refreshes_the_warnings(qtbot, type_page_rig):
    page = type_page_rig().page
    _import(qtbot, page)
    google = page.document_session()
    google_lines = page.warning_lines()
    assert google.target.id == "google-docs"

    _pick(page, "plain-text")

    plain = page.document_session()
    assert plain.target.id == "plain-text"
    assert plain.document is google.document, "adapted again from the imported document"
    assert plain.adapted != google.adapted
    assert len(page.warning_lines()) > len(google_lines)
    assert any("a plain-text field has no" in line for line in page.warning_lines())
    # The panel itself, the view and the counter all moved with it.
    assert "plain-text field" in page.warnings_panel.list_label.text()
    assert page.document_view.shown_document is plain.adapted
    assert "• Top-level bullet item" in page.document_view.toPlainText()
    assert page.counter_label.text().startswith(f"{len(plain.typed_text()):,} characters")


def test_switching_back_gives_back_exactly_what_the_first_editor_had(qtbot, type_page_rig):
    page = type_page_rig().page
    _import(qtbot, page)
    first = page.document_session()
    first_lines = page.warning_lines()

    _pick(page, "plain-text")
    _pick(page, "microsoft-word")
    _pick(page, "google-docs")

    again = page.document_session()
    assert again.adapted == first.adapted
    assert page.warning_lines() == first_lines
    assert again.document is first.document


def test_the_warnings_name_the_editor_that_is_selected(qtbot, type_page_rig):
    page = type_page_rig().page
    _import(qtbot, page)
    for target in ("microsoft-word", "libreoffice-writer"):
        _pick(page, target)
        name = targets.get_target(target).name
        editor_lines = [line for line in page.warning_lines() if "shortcut" in line]
        assert editor_lines and all(name in line for line in editor_lines), target


def test_an_editor_that_loses_nothing_turns_the_panel_to_good_news_and_back(qtbot, type_page_rig, tmp_path):
    page = type_page_rig().page
    plain_file = tmp_path / "essay.txt"
    plain_file.write_text("Just words.\nMore words.", encoding="utf-8")
    page.import_bar.set_source(str(plain_file))
    with qtbot.waitSignal(page.importFinished, timeout=10000):
        page.import_bar.import_button.click()
    assert page.warning_lines() == ()

    # A document with no formatting reproduces perfectly in every editor.
    for target in targets.targets_for(platforms.current_os()):
        _pick(page, target.id)
        assert page.warning_lines() == (), target.id
        assert page.warnings_panel.none_label.isHidden() is False


def test_switching_editor_in_plain_text_mode_changes_nothing_but_the_notes(qtbot, type_page_rig):
    page = type_page_rig().page
    page.set_text("My plain text.")
    before = page.counter_label.text()

    _pick(page, "textedit" if platforms.current_os() is OperatingSystem.MACOS else "libreoffice-writer")

    assert page.in_document_mode is False
    assert page.text_edit.toPlainText() == "My plain text."
    assert page.counter_label.text() == before
    assert page.warnings_panel.isHidden() is True


def test_the_editor_cannot_be_changed_while_a_run_is_typing(qtbot, type_page_rig):
    import time

    from auto_essay_typer.driver import Driver, RecordingKeyboard

    keyboard = RecordingKeyboard()
    rig = type_page_rig(
        driver_factory=lambda keymap=None: Driver(
            keyboard=keyboard, sleep=lambda _s: time.sleep(0.003), countdown_seconds=0, keymap=keymap
        )
    )
    page = rig.page
    page.set_text("a run that takes a little while to finish typing out")
    page.start_button.click()
    qtbot.waitUntil(lambda: rig.controller.state == "typing", timeout=15000)

    assert page.target_combo.isEnabled() is False

    with qtbot.waitSignal(rig.controller.finished, timeout=15000):
        rig.controller.stop()
    assert page.target_combo.isEnabled() is True


# --- the shortcut overrides saved on the Advanced page ---------------------------------------------------------------------------------


def test_shortcuts_saved_for_the_editor_apply_to_a_loaded_document_at_once(qtbot, type_page_rig):
    rig = type_page_rig()
    page = rig.page
    _import(qtbot, page)
    default = page.document_session().keymap[FormatOp.TOGGLE_BOLD]

    rig.store.update(keymap_overrides={"google-docs": {"toggle_bold": "ctrl+shift+b"}})

    session = page.document_session()
    assert session.keymap[FormatOp.TOGGLE_BOLD] == ("ctrl", "shift", "b") != default
    # Restoring the defaults is picked up as well.
    rig.store.update(keymap_overrides={})
    assert page.document_session().keymap[FormatOp.TOGGLE_BOLD] == default


def test_shortcuts_saved_for_another_editor_do_not_leak_into_this_one(qtbot, type_page_rig):
    rig = type_page_rig()
    page = rig.page
    _import(qtbot, page)
    before = page.document_session()

    rig.store.update(keymap_overrides={"microsoft-word": {"toggle_bold": "ctrl+shift+b"}})

    assert page.document_session() is before  # nothing about Google Docs changed, so nothing was redone


def test_the_picker_follows_an_editor_change_made_somewhere_else(qtbot, type_page_rig):
    rig = type_page_rig()
    page = rig.page
    _import(qtbot, page)

    rig.store.update(target_app="plain-text")

    assert page.selected_target_id() == "plain-text"
    assert page.document_session().target.id == "plain-text"
    assert page.target_notes_label.text() == targets.get_target("plain-text").notes


def test_resetting_the_settings_returns_the_picker_and_document_to_the_default_editor(qtbot, type_page_rig):
    rig = type_page_rig()
    page = rig.page
    rig.store.update(target_app="plain-text")
    _import(qtbot, page)
    assert page.document_session().target.id == "plain-text"

    rig.store.reset_to_defaults()

    assert page.selected_target_id() == "google-docs"
    assert page.document_session().target.id == "google-docs"
