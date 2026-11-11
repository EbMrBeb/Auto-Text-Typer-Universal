"""The Type page's "Typing into" editor picker."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from auto_essay_typer import platforms, targets
from auto_essay_typer.actions import FormatOp
from auto_essay_typer.gui.advanced_common import NoWheelComboBox
from auto_essay_typer.gui.quick_settings import target_caution
from auto_essay_typer.gui.settings_store import SettingsStore
from auto_essay_typer.platforms import OperatingSystem

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "gdocs_export_sample.html"


def _as(monkeypatch, os_: OperatingSystem) -> None:
    monkeypatch.setattr(platforms, "current_os", lambda *args, **kwargs: os_)


@pytest.fixture(autouse=True, params=list(OperatingSystem), ids=lambda os_: os_.value)
def os_(request, monkeypatch) -> OperatingSystem:
    """Run every test in this module as each operating system, whichever one runs the suite.

    The editors on offer differ per system (TextEdit is macOS-only, Microsoft Word has no
    Linux release), so a test that quietly assumes the list of the machine it runs on passes
    there and fails on the others. Running as all three means a Mac catches that too.
    """
    _as(monkeypatch, request.param)
    return request.param


def _offered(os_: OperatingSystem) -> list[str]:
    return [target.id for target in targets.targets_for(os_)]


def _lacking(os_: OperatingSystem) -> list[str]:
    """Editors that exist, but not on ``os_``."""
    return [target.id for target in targets.TARGET_APPS.values() if os_ not in target.platforms]


def _items(page) -> list[tuple[str, str]]:
    combo = page.target_combo
    return [(combo.itemText(i), combo.itemData(i)) for i in range(combo.count())]


def _pick(page, target_id: str) -> None:
    index = page.target_combo.findData(target_id)
    assert index >= 0, f"{target_id!r} is not offered here; the picker lists {_items(page)}"
    page.target_combo.setCurrentIndex(index)


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


@pytest.mark.parametrize("saved", sorted(targets.TARGET_APPS))
def test_the_picker_starts_on_the_saved_editor_when_this_system_has_it(type_page_rig, monkeypatch, tmp_path, os_, saved):
    # Saved where the editor exists, so the store accepts it; opened on this system.
    _as(monkeypatch, next(o for o in OperatingSystem if o in targets.get_target(saved).platforms))
    SettingsStore(tmp_path / "settings.json").update(target_app=saved)
    _as(monkeypatch, os_)

    page = type_page_rig(store=SettingsStore(tmp_path / "settings.json")).page

    expected = saved if saved in _offered(os_) else _offered(os_)[0]
    assert page.selected_target_id() == expected
    assert page.target_combo.currentText() == targets.get_target(expected).name


def test_a_saved_editor_this_system_does_not_have_falls_back_to_the_first(type_page_rig, monkeypatch, tmp_path, os_):
    # Every editor this system lacks (none on macOS, TextEdit on Windows, Word and TextEdit
    # on Linux), saved on a system that has it, then carried over to this one.
    for saved in _lacking(os_):
        _as(monkeypatch, next(o for o in OperatingSystem if o in targets.get_target(saved).platforms))
        store = SettingsStore(tmp_path / f"{saved}.json")
        store.update(target_app=saved)
        _as(monkeypatch, os_)

        page = type_page_rig(store=store).page

        assert page.selected_target_id() == _offered(os_)[0], saved
        assert page.target_combo.currentIndex() >= 0, saved
        assert page.target_notes_label.text() == targets.get_target(_offered(os_)[0]).notes, saved


# --- saving ----------------------------------------------------------------------------------------------------------------------------


def test_changing_the_editor_saves_the_choice(qtbot, type_page_rig, tmp_path):
    chosen = "libreoffice-writer"
    rig = type_page_rig()
    saved = []
    rig.store.changed.connect(saved.append)

    _pick(rig.page, chosen)

    assert rig.store.settings.target_app == chosen
    assert [settings.target_app for settings in saved] == [chosen]
    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["target_app"] == chosen
    # A page opened later (the next launch) starts on what was chosen.
    assert type_page_rig(store=SettingsStore(tmp_path / "settings.json")).page.selected_target_id() == chosen


def test_choosing_the_same_editor_again_saves_nothing(qtbot, type_page_rig, tmp_path):
    rig = type_page_rig()
    _pick(rig.page, "plain-text")
    with qtbot.assertNotEmitted(rig.store.changed):
        _pick(rig.page, "plain-text")


def test_a_choice_that_cannot_be_written_is_still_used_for_now_and_the_user_is_told(qtbot, type_page_rig, tmp_path):
    unwritable = tmp_path / "settings-is-a-folder"
    unwritable.mkdir()
    rig = type_page_rig(store=SettingsStore(unwritable))

    _pick(rig.page, "libreoffice-writer")

    assert rig.page.selected_target_id() == "libreoffice-writer"
    assert rig.page.target_notes_label.text() == targets.get_target("libreoffice-writer").notes
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


def test_the_selected_editors_notes_are_shown_under_the_picker(type_page_rig, os_):
    page = type_page_rig().page
    for target_id in _offered(os_):
        _pick(page, target_id)
        assert page.target_notes_label.text() == targets.get_target(target_id).notes, target_id
        assert page.target_notes_label.isHidden() is False, target_id


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


_UNCHECKED = {
    OperatingSystem.MACOS: {"microsoft-word", "libreoffice-writer", "textedit"},
    OperatingSystem.WINDOWS: {"microsoft-word", "libreoffice-writer"},
    OperatingSystem.LINUX: {"libreoffice-writer"},
}


def test_an_unchecked_editor_gets_a_muted_caution_pointing_to_the_advanced_page(type_page_rig, os_):
    page = type_page_rig().page
    unchecked = [t for t in targets.targets_for(os_) if not t.verified]
    assert {t.id for t in unchecked} == _UNCHECKED[os_]
    for target in unchecked:
        _pick(page, target.id)
        assert page.target_caution_label.isHidden() is False, target.id
        text = page.target_caution_label.text()
        assert text == target_caution(target)
        assert "best effort" in text and "Advanced page" in text
        assert page.target_caution_label.objectName() == "MutedLabel"


def test_the_caution_goes_away_again_when_a_checked_editor_is_chosen(type_page_rig):
    page = type_page_rig().page
    _pick(page, "libreoffice-writer")
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
    assert any("is set up for a plain-text field" in line for line in page.warning_lines())
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
    _pick(page, "libreoffice-writer")
    _pick(page, "google-docs")

    again = page.document_session()
    assert again.adapted == first.adapted
    assert page.warning_lines() == first_lines
    assert again.document is first.document


def test_the_warnings_name_the_editor_that_is_selected(qtbot, type_page_rig, os_):
    page = type_page_rig().page
    _import(qtbot, page)
    for target in [t for t in ("microsoft-word", "libreoffice-writer") if t in _offered(os_)]:
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


def test_switching_editor_in_plain_text_mode_changes_nothing_but_the_notes(qtbot, type_page_rig, os_):
    page = type_page_rig().page
    page.set_text("My plain text.")
    before = page.counter_label.text()

    _pick(page, "textedit" if os_ is OperatingSystem.MACOS else "libreoffice-writer")

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


# --- the picker cannot be changed by accident or behind the page's back ----------------------------------------------------------------


def _wheel(widget) -> "QWheelEvent":
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent

    return QWheelEvent(
        QPointF(5, 5),
        QPointF(5, 5),
        QPoint(0, 0),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def test_passing_the_mouse_wheel_over_the_picker_never_changes_the_editor(qtbot, type_page_rig):
    rig = type_page_rig()
    page = rig.page
    # The page scrolls, so the pointer crosses the picker all the time; the editor chosen here
    # is saved and decides which shortcuts are pressed into another application.
    assert isinstance(page.target_combo, NoWheelComboBox)
    assert page.target_combo.focusPolicy() == Qt.FocusPolicy.StrongFocus
    event = _wheel(page.target_combo)

    page.target_combo.wheelEvent(event)

    assert page.selected_target_id() == "google-docs"
    assert rig.store.settings.target_app == "google-docs"
    assert event.isAccepted() is False, "ignored, so the scroll area scrolls instead"


def test_settings_changed_while_a_run_types_wait_until_it_is_over(qtbot, type_page_rig):
    import time

    from auto_essay_typer.driver import Driver, RecordingKeyboard

    keyboard = RecordingKeyboard()
    rig = type_page_rig(
        driver_factory=lambda keymap=None: Driver(
            keyboard=keyboard, sleep=lambda _s: time.sleep(0.003), countdown_seconds=0, keymap=keymap
        )
    )
    page = rig.page
    _import(qtbot, page)
    page.mistake_slider.setValue(0)
    typed_for = page.document_session()
    page.start_button.click()
    qtbot.waitUntil(lambda: rig.controller.state == "typing", timeout=15000)

    rig.store.update(target_app="plain-text")  # e.g. saved from another page while this one types

    # What is on screen is still the document that is being typed, for the editor it is typed into.
    assert page.document_session() is typed_for
    assert page.selected_target_id() == "google-docs"
    assert page.document_view.shown_document is typed_for.adapted

    with qtbot.waitSignal(rig.controller.finished, timeout=15000):
        rig.controller.stop()

    assert page.selected_target_id() == "plain-text"
    assert page.document_session().target.id == "plain-text"
    assert page.target_notes_label.text() == targets.get_target("plain-text").notes


def test_a_choice_that_could_not_be_saved_survives_an_unrelated_settings_change(qtbot, type_page_rig, tmp_path):
    unwritable = tmp_path / "settings-is-a-folder"
    unwritable.mkdir()
    rig = type_page_rig(store=SettingsStore(unwritable))
    page = rig.page
    _import(qtbot, page)
    _pick(page, "libreoffice-writer")
    assert "couldn't be saved" in page.inline_message_label.text()

    unwritable.rmdir()  # the problem goes away ...
    rig.store.update(show_mini_window=False)  # ... and something else is saved

    assert page.selected_target_id() == "libreoffice-writer", "the picker must not snap back to the saved editor"
    assert page.document_session().target.id == "libreoffice-writer"
    assert rig.store.settings.target_app == "google-docs"
    assert page.inline_message_label.isHidden() is False, "it is still true: that choice was never saved"

    _pick(page, "plain-text")  # now it saves, so the old warning is out of date
    assert rig.store.settings.target_app == "plain-text"
    assert page.inline_message_label.isHidden() is True


def test_going_back_to_the_saved_editor_clears_the_warning_about_the_unsaved_one(qtbot, type_page_rig, tmp_path):
    unwritable = tmp_path / "settings-is-a-folder"
    unwritable.mkdir()
    page = type_page_rig(store=SettingsStore(unwritable)).page
    _pick(page, "libreoffice-writer")
    assert page.inline_message_label.isHidden() is False

    _pick(page, "google-docs")

    assert page.inline_message_label.isHidden() is True


def test_an_editor_another_store_saved_is_picked_up_when_the_page_is_shown(qtbot, type_page_rig, tmp_path):
    rig = type_page_rig()  # a page with a store of its own, as when it is not handed the shared one
    other = SettingsStore(tmp_path / "settings.json")
    other.update(target_app="plain-text")
    assert rig.page.selected_target_id() == "google-docs", "it cannot know yet"

    rig.page.show()
    qtbot.waitExposed(rig.page)

    assert rig.page.selected_target_id() == "plain-text"


def test_shortcuts_another_store_saved_are_used_the_moment_a_run_starts(qtbot, type_page_rig, tmp_path):
    rig = type_page_rig()
    page = rig.page
    _import(qtbot, page)
    other = SettingsStore(tmp_path / "settings.json")
    other.update(keymap_overrides={"google-docs": {"toggle_bold": "ctrl+shift+b"}})

    with qtbot.waitSignal(rig.controller.finished, timeout=15000):
        page.start_button.click()

    assert ("ctrl", "shift", "b") in rig.keyboards[0].chords


def test_choosing_an_editor_on_a_page_with_its_own_store_keeps_what_another_store_saved(qtbot, type_page_rig, tmp_path):
    rig = type_page_rig()
    other = SettingsStore(tmp_path / "settings.json")
    other.update(keymap_overrides={"google-docs": {"toggle_bold": "ctrl+shift+b"}})

    _pick(rig.page, "libreoffice-writer")

    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved["target_app"] == "libreoffice-writer"
    assert saved["keymap_overrides"] == {"google-docs": {"toggle_bold": "ctrl+shift+b"}}


def test_a_refused_choice_goes_back_to_the_editor_that_is_saved_now_not_the_one_saved_earlier(
    qtbot, type_page_rig, monkeypatch, tmp_path
):
    _as(monkeypatch, OperatingSystem.MACOS)
    path = tmp_path / "settings.json"
    setup = SettingsStore(path)
    setup.update(target_app="plain-text")
    # Cmd+Shift+X is Google Docs' strikethrough shortcut, and nobody else's here.
    setup.update(hotkeys={"start": "<cmd>+<shift>+x", "pause": "<cmd>+<alt>+<shift>+p", "stop": "<esc>"})
    rig = type_page_rig(store=SettingsStore(path))
    assert rig.page.selected_target_id() == "plain-text"
    # Meanwhile another part of the app (another store) chooses LibreOffice Writer.
    SettingsStore(path).update(target_app="libreoffice-writer")

    _pick(rig.page, "google-docs")  # refused: it would collide with the hotkey

    assert rig.page.inline_message_label.text().startswith("Couldn't switch to Google Docs")
    assert rig.store.settings.target_app == "libreoffice-writer"
    assert rig.page.selected_target_id() == "libreoffice-writer", "back to what is saved, not to what was"


# --- an empty picker is never reported as an editor -----------------------------------------------------------------------------------


def test_a_picker_with_nothing_selected_reports_an_offered_editor_not_the_word_None(type_page_rig, os_):
    rig = type_page_rig()
    page = rig.page

    page.target_combo.blockSignals(True)  # as if the handler had not run yet
    page.target_combo.setCurrentIndex(-1)  # what a lookup of an editor this system lacks does
    page.target_combo.blockSignals(False)

    assert page.target_combo.currentData() is None
    assert page.selected_target_id() in _offered(os_)
    assert page.selected_target_id() != "None"


def test_an_empty_picker_is_put_back_on_an_editor_and_nothing_is_saved(type_page_rig, os_):
    rig = type_page_rig()
    page = rig.page
    saved_before = rig.store.settings.target_app

    page.target_combo.setCurrentIndex(-1)

    assert page.target_combo.currentIndex() >= 0
    assert page.selected_target_id() == page.target_combo.currentData()
    assert rig.store.settings.target_app == saved_before


def test_selecting_an_editor_this_system_lacks_lands_on_one_it_has(type_page_rig, os_):
    page = type_page_rig().page
    for missing in _lacking(os_):
        page._select_target(missing)

        assert page.target_combo.currentIndex() >= 0, missing
        assert page.selected_target_id() in _offered(os_), missing
