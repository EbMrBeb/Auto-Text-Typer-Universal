"""Previewing and typing an imported document from the Type page."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut
from PySide6.QtWidgets import QLabel

from auto_essay_typer import platforms, targets
from auto_essay_typer.actions import Format, FormatOp
from auto_essay_typer.driver import Driver, RecordingKeyboard
from auto_essay_typer.gui import run_controller as run_controller_module
from auto_essay_typer.gui.pages import type_page as type_page_module
from auto_essay_typer.keymaps import NO_OP
from auto_essay_typer.planner import plan_document as real_plan_document

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "gdocs_export_sample.html"


def _import(qtbot, page, source: str = str(FIXTURE)) -> None:
    page.import_bar.set_source(source)
    with qtbot.waitSignal(page.importFinished, timeout=10000):
        page.import_bar.import_button.click()
    assert page.in_document_mode


def _real_chords(keymap) -> set:
    return {chord for chord in keymap.values() if chord != NO_OP}


def _pick(page, target_id: str) -> None:
    page.target_combo.setCurrentIndex(page.target_combo.findData(target_id))


def _clean_profile(page) -> None:
    """No typos, so a run's recorded text is simply the text (a stopped run is a clean prefix)."""
    page.mistake_slider.setValue(0)


# --- Start ------------------------------------------------------------------------------------------------------------


def test_start_in_document_mode_types_the_documents_text_and_presses_its_shortcuts(qtbot, type_page_rig):
    rig = type_page_rig()
    page = rig.page
    _import(qtbot, page)
    session = page.document_session()

    with qtbot.waitSignal(rig.controller.finished, timeout=15000) as blocker:
        page.start_button.click()

    assert blocker.args[0] == "finished"
    (keyboard,) = rig.keyboards
    assert keyboard.text == session.adapted.plain_text()
    assert keyboard.chords, "a formatted document is typed with formatting shortcuts"
    assert all(chord in _real_chords(session.keymap) for chord in keyboard.chords if chord != NO_OP)
    for op in (FormatOp.TOGGLE_BOLD, FormatOp.TOGGLE_ITALIC, FormatOp.SET_HEADING_1, FormatOp.TOGGLE_BULLET_LIST):
        assert session.keymap[op] in keyboard.chords, op
    # The driver was built for the editor that was chosen.
    assert rig.factory.keymaps == [session.keymap]
    assert rig.factory.keymaps[0] == targets.keymap_for("google-docs", platforms.current_os())


def test_the_keyboard_is_never_asked_to_type_what_is_not_in_the_document(qtbot, type_page_rig):
    rig = type_page_rig()
    page = rig.page
    _import(qtbot, page)
    page.mistake_slider.setValue(4)  # "Sloppy": lots of typos, all of them corrected again

    with qtbot.waitSignal(rig.controller.finished, timeout=15000):
        page.start_button.click()

    assert rig.keyboards[0].text == page.document_session().adapted.plain_text()


def test_what_is_typed_comes_from_the_document_never_from_a_widget(qtbot, type_page_rig, monkeypatch):
    from auto_essay_typer.gui.document_view import DocumentView

    rig = type_page_rig()
    page = rig.page
    page.set_text("words left in the plain text box")
    _import(qtbot, page)
    session = page.document_session()
    # Neither widget's text is consulted: make both lie, and the run must not notice.
    monkeypatch.setattr(DocumentView, "toPlainText", lambda self: "WIDGET TEXT THAT MUST NOT BE TYPED")
    page.text_edit.setPlainText("A DIFFERENT PLAIN TEXT BOX")

    with qtbot.waitSignal(rig.controller.finished, timeout=15000):
        page.start_button.click()

    assert rig.keyboards[0].text == session.adapted.plain_text()


def test_ctrl_return_pressed_in_the_document_view_starts_the_run(qtbot, type_page_rig):
    rig = type_page_rig()
    page = rig.page
    _import(qtbot, page)
    page.show()
    qtbot.waitExposed(page)
    qtbot.waitActive(page)
    page.document_view.setFocus()
    qtbot.waitUntil(page.document_view.hasFocus, timeout=3000)

    # A real key event, to the widget that has focus: the read-only view must not swallow it.
    with qtbot.waitSignal(rig.controller.finished, timeout=15000) as blocker:
        qtbot.keyClick(page.document_view, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)

    assert blocker.args[0] == "finished"
    assert rig.keyboards[0].text == page.document_session().adapted.plain_text()


def test_ctrl_enter_on_the_keypad_starts_a_document_run_too(qtbot, type_page_rig):
    rig = type_page_rig()
    page = rig.page
    _import(qtbot, page)
    shortcut = next(s for s in page.findChildren(QShortcut) if s.key().toString() == "Ctrl+Enter")

    with qtbot.waitSignal(rig.controller.finished, timeout=15000) as blocker:
        shortcut.activated.emit()

    assert blocker.args[0] == "finished"


def test_the_page_returns_to_idle_after_a_document_run_still_showing_the_document(qtbot, type_page_rig):
    rig = type_page_rig()
    page = rig.page
    _import(qtbot, page)
    session = page.document_session()

    with qtbot.waitSignal(rig.controller.finished, timeout=15000):
        page.start_button.click()

    assert page.in_document_mode is True
    assert page.document_session() is session
    assert page.idle_actions_widget.isHidden() is False
    assert page.run_panel_widget.isHidden() is True
    assert page.document_view.isEnabled() is True
    for control in (page.import_bar.edit, page.import_bar.import_button, page.import_bar.browse_button, page.back_button):
        assert control.isEnabled() is True


def test_start_types_for_the_editor_chosen_in_the_picker(qtbot, type_page_rig):
    rig = type_page_rig()
    page = rig.page
    _import(qtbot, page)
    _pick(page, "microsoft-word")
    session = page.document_session()
    assert session.target.id == "microsoft-word"

    with qtbot.waitSignal(rig.controller.finished, timeout=15000):
        page.start_button.click()

    (keyboard,) = rig.keyboards
    assert rig.factory.keymaps == [targets.keymap_for("microsoft-word", platforms.current_os())]
    assert keyboard.text == session.adapted.plain_text()
    # Word has no list shortcuts here, so the bullets are typed characters, not key presses.
    assert "• Top-level bullet item" in keyboard.text
    assert all(chord in _real_chords(session.keymap) for chord in keyboard.chords if chord != NO_OP)


def test_an_editor_that_cannot_format_is_typed_as_plain_text_with_no_formatting_shortcuts(qtbot, type_page_rig):
    rig = type_page_rig()
    page = rig.page
    _import(qtbot, page)
    _pick(page, "plain-text")

    with qtbot.waitSignal(rig.controller.finished, timeout=15000):
        page.start_button.click()

    assert rig.keyboards[0].text == page.document_session().adapted.plain_text()
    assert [chord for chord in rig.keyboards[0].chords if chord != NO_OP] == []


def test_a_document_run_presses_the_users_own_shortcut_where_one_was_saved(qtbot, type_page_rig):
    rig = type_page_rig()
    page = rig.page
    _import(qtbot, page)
    default_bold = targets.keymap_for("google-docs", platforms.current_os())[FormatOp.TOGGLE_BOLD]

    rig.store.update(keymap_overrides={"google-docs": {"toggle_bold": "ctrl+shift+b"}})
    with qtbot.waitSignal(rig.controller.finished, timeout=15000):
        page.start_button.click()

    assert ("ctrl", "shift", "b") in rig.keyboards[0].chords
    assert default_bold not in rig.keyboards[0].chords


def test_start_still_asks_for_permission_first(qtbot, type_page_rig, monkeypatch):
    from auto_essay_typer.platforms import PermissionStatus

    denied = PermissionStatus(granted=False, summary="no", fix_steps=("Do a thing.",), can_open_settings=True)
    rig = type_page_rig(permission_checker=lambda: denied)
    page = rig.page
    _import(qtbot, page)
    opened = []
    monkeypatch.setattr(type_page_module.PermissionDialog, "exec", lambda self: opened.append(True) or 0)
    monkeypatch.setattr(type_page_module.PermissionDialog, "granted", lambda self: False)

    page.start_button.click()

    assert opened == [True]
    assert rig.controller.is_busy is False
    assert not rig.keyboards


# --- while it types ---------------------------------------------------------------------------------------------------------


def _slow_rig(type_page_rig, **kwargs):
    keyboard = RecordingKeyboard()

    def factory(keymap=None) -> Driver:
        return Driver(keyboard=keyboard, sleep=lambda _s: time.sleep(0.003), countdown_seconds=0, keymap=keymap)

    return type_page_rig(driver_factory=factory, **kwargs), keyboard


def test_everything_that_would_change_the_document_is_off_while_it_types(qtbot, type_page_rig):
    from auto_essay_typer.importers import load_document

    loads: list[str] = []

    def counting_loader(source):
        loads.append(source)
        return load_document(source)

    rig, keyboard = _slow_rig(type_page_rig, loader=counting_loader)
    page = rig.page
    _import(qtbot, page)
    assert loads == [str(FIXTURE)]
    _clean_profile(page)

    page.start_button.click()
    qtbot.waitUntil(lambda: rig.controller.state == "typing", timeout=15000)

    assert page.document_view.isEnabled() is False
    assert page.back_button.isEnabled() is False
    for control in (page.import_bar.edit, page.import_bar.import_button, page.import_bar.browse_button, page.target_combo):
        assert control.isEnabled() is False
    # Going back, or importing again, does nothing mid-run.
    page._on_back_to_plain_clicked()
    page._on_import_requested(str(FIXTURE))
    page._on_browse_requested()
    assert page.in_document_mode is True
    assert rig.chosen == [], "Browse never opened"
    assert loads == [str(FIXTURE)], "no second import was started"
    assert page._importing is False

    with qtbot.waitSignal(rig.controller.finished, timeout=15000) as blocker:
        page.stop_button.click()

    assert blocker.args[0] == "stopped"
    assert page.back_button.isEnabled() is True
    assert page.document_view.isEnabled() is True
    assert page.target_combo.isEnabled() is True
    assert page.document_session().adapted.plain_text().startswith(keyboard.text)


# --- Preview -------------------------------------------------------------------------------------------------------------------


@pytest.fixture
def shown_dialogs(monkeypatch):
    """Collects every PreviewDialog the page would have shown, without ever running one."""
    shown = []
    monkeypatch.setattr(type_page_module.PreviewDialog, "exec", lambda self: shown.append(self) or 0)
    return shown


def _label_texts(dialog) -> list[str]:
    return [label.text() for label in dialog.findChildren(QLabel)]


def test_preview_in_document_mode_verifies_the_plan_against_the_whole_document(qtbot, type_page_rig, shown_dialogs):
    page = type_page_rig().page
    _import(qtbot, page)

    page.preview_button.click()

    (dialog,) = shown_dialogs
    assert dialog.verification_badge.property("tone") == "success"
    assert dialog.verification_badge.text() == (
        "Output verified: matches your document exactly, formatting included"
    )
    assert "Formatting steps" in _label_texts(dialog)


def test_the_preview_of_an_unchecked_editor_carries_the_best_effort_caution(qtbot, type_page_rig, shown_dialogs):
    page = type_page_rig().page
    _import(qtbot, page)

    page.preview_button.click()
    assert shown_dialogs[-1].caution_label.isHidden() is True  # Google Docs' shortcuts are checked

    _pick(page, "microsoft-word")
    page.preview_button.click()
    caution = shown_dialogs[-1].caution_label
    assert caution.isHidden() is False
    assert "Microsoft Word" in caution.text() and "best effort" in caution.text()


def test_the_preview_is_of_the_adapted_document_for_the_chosen_editor(qtbot, type_page_rig, shown_dialogs):
    page = type_page_rig().page
    _import(qtbot, page)
    google_steps = _formatting_steps(page, shown_dialogs)
    _pick(page, "plain-text")
    plain_steps = _formatting_steps(page, shown_dialogs)

    # Plain text presses no formatting shortcuts, so its plan has far fewer formatting steps
    # (only the deliberate clear-formatting no-op remains).
    assert google_steps > 10
    assert plain_steps == 1


def _formatting_steps(page, shown_dialogs) -> int:
    page.preview_button.click()
    dialog = shown_dialogs[-1]
    texts = _label_texts(dialog)
    return int(texts[texts.index("Formatting steps") - 1].replace(",", ""))


def test_a_preview_whose_plan_does_not_reproduce_the_document_is_a_loud_mismatch(
    qtbot, type_page_rig, shown_dialogs, monkeypatch
):
    page = type_page_rig().page
    _import(qtbot, page)

    def forgetful_plan(document, profile, seed):
        return [a for a in real_plan_document(document, profile, seed) if a != Format(FormatOp.TOGGLE_BOLD)]

    monkeypatch.setattr(type_page_module, "plan_document", forgetful_plan)

    page.preview_button.click()

    (dialog,) = shown_dialogs
    assert dialog.verification_badge.property("tone") == "danger"
    assert dialog.verification_badge.text().startswith(
        "Mismatch: the planned output does not match your document exactly"
    )
    assert "do not use this plan" in dialog.verification_badge.text()


# --- a plan that is wrong is never typed ----------------------------------------------------------------------------------------


def test_a_run_whose_plan_does_not_reproduce_the_document_types_nothing_and_says_so(
    qtbot, type_page_rig, monkeypatch
):
    rig = type_page_rig()
    page = rig.page
    _import(qtbot, page)
    monkeypatch.setattr(
        run_controller_module,
        "plan_document",
        lambda document, profile, seed: [
            a for a in real_plan_document(document, profile, seed) if a != Format(FormatOp.TOGGLE_ITALIC)
        ],
    )

    with qtbot.waitSignal(rig.controller.finished, timeout=15000) as blocker:
        page.start_button.click()

    assert blocker.args[0] == "failed"
    assert page.inline_message_label.isHidden() is False
    assert "did not reproduce your document exactly" in page.inline_message_label.text()
    assert "nothing was typed" in page.inline_message_label.text()
    assert rig.keyboards[0].text == ""
    assert rig.keyboards[0].chords == []
    assert page.idle_actions_widget.isHidden() is False  # back to idle, ready to try again


def test_a_keyboard_that_cannot_be_set_up_is_shown_to_the_user_in_document_mode(qtbot, type_page_rig):
    def broken_factory(keymap=None):
        raise RuntimeError("keyboard backend exploded")

    rig = type_page_rig(driver_factory=broken_factory)
    page = rig.page
    _import(qtbot, page)

    page.start_button.click()

    assert "keyboard backend exploded" in page.inline_message_label.text()
    assert rig.controller.is_busy is False
    assert page.start_button.isEnabled()


# --- the macOS rule ----------------------------------------------------------------------------------------------------------------


def test_starting_a_document_builds_the_keyboard_on_the_main_thread(qtbot, type_page_rig):
    built_on: list[threading.Thread] = []
    keyboard = RecordingKeyboard()

    def factory(keymap=None) -> Driver:
        built_on.append(threading.current_thread())
        return Driver(keyboard=keyboard, sleep=lambda _s: None, countdown_seconds=0, keymap=keymap)

    rig = type_page_rig(driver_factory=factory)
    page = rig.page
    _import(qtbot, page)

    with qtbot.waitSignal(rig.controller.finished, timeout=15000):
        page.start_button.click()

    assert built_on == [threading.main_thread()]


# --- text that arrives with a byte-order mark ------------------------------------------------------------------------------


def test_a_text_file_saved_with_a_byte_order_mark_is_typed_exactly_as_plain_mode_would_type_it(
    qtbot, type_page_rig, tmp_path
):
    from auto_essay_typer import platforms

    raw = "\ufeffDear reader,\r\n\r\nThis is the letter.\r\n"
    path = tmp_path / "letter.txt"
    path.write_bytes(raw.encode("utf-8"))

    document_rig = type_page_rig()
    _import(qtbot, document_rig.page, str(path))
    with qtbot.waitSignal(document_rig.controller.finished, timeout=15000):
        document_rig.page.start_button.click()

    plain_rig = type_page_rig()
    plain_rig.page.set_text(raw)  # the same bytes pasted into the box
    with qtbot.waitSignal(plain_rig.controller.finished, timeout=15000):
        plain_rig.page.start_button.click()

    assert "\ufeff" not in document_rig.keyboards[0].text
    assert document_rig.keyboards[0].text == plain_rig.keyboards[0].text == platforms.normalize_input_text(raw)


# --- the estimate shown is always for what is on screen -----------------------------------------------------------------------------


def test_an_estimate_for_one_mode_is_never_shown_beside_the_other_modes_text(qtbot, type_page_rig):
    rig = type_page_rig()
    page = rig.page
    page.set_text("Some plain words that will be typed in the plain mode of this page.")
    qtbot.waitUntil(lambda: "est." in page.counter_label.text() and "~0 s" not in page.counter_label.text(), timeout=10000)
    plain_estimate = page.counter_label.text()

    _import(qtbot, page)
    assert "est." not in page.counter_label.text(), "the plain text's estimate must not sit beside the document's counts"
    qtbot.waitUntil(lambda: "est." in page.counter_label.text(), timeout=10000)
    document_estimate = page.counter_label.text()
    assert document_estimate != plain_estimate

    page.back_button.click()
    assert "est." not in page.counter_label.text(), "nor the document's beside the restored text"
    qtbot.waitUntil(lambda: "est." in page.counter_label.text(), timeout=10000)


def test_changing_the_editor_does_not_leave_the_old_documents_estimate_on_screen(qtbot, type_page_rig):
    page = type_page_rig().page
    _import(qtbot, page)
    qtbot.waitUntil(lambda: "est." in page.counter_label.text(), timeout=10000)

    _pick(page, "plain-text")

    assert "est." not in page.counter_label.text()
    qtbot.waitUntil(lambda: "est." in page.counter_label.text(), timeout=10000)


def test_the_estimate_poll_stops_when_nothing_is_being_computed(qtbot, type_page_rig):
    page = type_page_rig().page
    page.set_text("Words to estimate.")
    qtbot.waitUntil(lambda: page._estimate_poll.isActive(), timeout=10000)
    qtbot.waitUntil(lambda: "est." in page.counter_label.text(), timeout=10000)
    qtbot.waitUntil(lambda: not page._estimate_poll.isActive(), timeout=3000)
    assert page._estimates_in_flight == 0


# --- what the rest of the app is told is about to be typed -----------------------------------------------------------


def test_asked_what_will_be_typed_the_page_answers_for_the_mode_it_is_in(qtbot, type_page_rig):
    """Code outside the page asks it for its text, to see there is something to type and to count it.

    In document mode the text box is not what gets typed, and may hold something older or
    nothing at all, so the answer has to be the document's words.
    """
    page = type_page_rig().page
    page.set_text("words left in the plain text box")
    assert page.normalized_text() == "words left in the plain text box"

    _import(qtbot, page)
    assert page.normalized_text() == page.document_session().adapted.plain_text()
    assert "plain text box" not in page.normalized_text()

    page.back_button.click()
    assert page.normalized_text() == page.text_edit.toPlainText()
    assert page.normalized_text().strip(), "going back leaves the document's words in the box"


def test_a_loaded_document_is_not_an_empty_page_when_the_text_box_is_empty(qtbot, type_page_rig):
    page = type_page_rig().page
    assert page.text_edit.toPlainText() == ""
    assert not page.normalized_text().strip()

    _import(qtbot, page)

    assert page.text_edit.toPlainText() == ""
    assert page.normalized_text().strip(), "the document is what would be typed, whatever the box holds"
