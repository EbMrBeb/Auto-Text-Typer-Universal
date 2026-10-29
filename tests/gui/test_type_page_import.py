"""The Type page's Import row and document mode.

Everything here is offscreen and offline: the loader is a real ``load_document`` over a
local fixture (or a fake that raises what the real one would), Google is a fake
``fetch``, the file dialog and the failure message box are fakes, and the keyboard is a
``RecordingKeyboard``. Nothing waits with a sleep and nothing calls ``exec()``.
"""

from __future__ import annotations

import threading
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest
from PySide6.QtCore import Qt

from auto_essay_typer import targets
from auto_essay_typer.document import Document, ImportReport, Paragraph, ParagraphStyle, Run
from auto_essay_typer.gui.document_import import default_start_dir
from auto_essay_typer.importers import (
    DocumentImportError,
    FetchResult,
    load_document,
)

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "gdocs_export_sample.html"
GOOGLE_LINK = "https://docs.google.com/document/d/ABC123/edit"


def _import(qtbot, page, source: str, *, via: str = "button") -> bool:
    """Import ``source`` through the page's own controls; the page's verdict (True = a document)."""
    page.import_bar.set_source(source)
    with qtbot.waitSignal(page.importFinished, timeout=10000) as blocker:
        if via == "enter":
            qtbot.keyClick(page.import_bar.edit, Qt.Key.Key_Return)
        else:
            page.import_bar.import_button.click()
    return blocker.args[0]


def _loader_for_google(fetch):
    return lambda source: load_document(source, fetch=fetch)


# --- the page before anything is imported -------------------------------------------------------------


def test_a_new_page_is_in_plain_text_mode_with_the_document_widgets_hidden(type_page_rig):
    page = type_page_rig().page
    assert page.in_document_mode is False
    assert page.document_session() is None
    assert page.text_edit.isHidden() is False
    assert page.text_edit.isReadOnly() is False
    for hidden in (
        page.document_view,
        page.document_header,
        page.warnings_panel,
        page.import_bar.busy_widget,
        page.import_bar.message_label,
    ):
        assert hidden.isHidden() is True, hidden


def test_the_import_row_has_a_placeheld_box_and_import_and_browse_buttons(type_page_rig):
    page = type_page_rig().page
    bar = page.import_bar
    assert bar.edit.placeholderText() == "Paste a Google Docs link, or choose a file"
    assert bar.import_button.text() == "Import"
    assert bar.browse_button.text() == "Browse…"
    for control in (bar.edit, bar.import_button, bar.browse_button):
        assert control.accessibleName()
        assert control.toolTip()


def test_the_import_row_sits_above_the_text_box_in_tab_order(type_page_rig):
    page = type_page_rig().page
    chain, widget = [], page.import_bar.edit
    for _ in range(120):
        chain.append(widget)
        widget = widget.nextInFocusChain()
        if widget is page.import_bar.edit:
            break
    order = [w for w in chain if w in (page.import_bar.edit, page.import_bar.import_button, page.import_bar.browse_button, page.text_edit)]
    assert order == [page.import_bar.edit, page.import_bar.import_button, page.import_bar.browse_button, page.text_edit]


# --- a successful import enters document mode -------------------------------------------------------------


def test_importing_a_document_enters_document_mode(qtbot, type_page_rig):
    rig = type_page_rig()
    page = rig.page
    page.set_text("what was typed before")
    modes: list[bool] = []
    page.documentModeChanged.connect(modes.append)

    assert _import(qtbot, page, str(FIXTURE)) is True

    assert page.in_document_mode is True
    assert modes == [True]
    session = page.document_session()
    assert session.source == str(FIXTURE)
    # The text box gives way to the read-only document view, and the chrome for the mode appears.
    assert page.text_edit.isHidden() is True
    assert page.document_view.isHidden() is False
    assert page.document_view.isReadOnly() is True
    assert page.document_header.isHidden() is False
    assert page.warnings_panel.isHidden() is False
    assert page.back_button.isEnabled()
    assert page.document_source_label.fullText() == "Imported from gdocs_export_sample.html"
    # The imported Document is the source of truth, and the view draws its adapted form.
    assert session.document.paragraphs[0].style is ParagraphStyle.TITLE
    assert page.document_view.shown_document is session.adapted
    assert "Sample Document Title" in page.document_view.toPlainText()
    assert not rig.errors


def test_importing_leaves_the_plain_text_the_user_had_untouched_behind_the_view(qtbot, type_page_rig):
    page = type_page_rig().page
    page.set_text("what was typed before")
    _import(qtbot, page, str(FIXTURE))
    assert page.text_edit.toPlainText() == "what was typed before"


def test_pressing_enter_in_the_box_imports_too(qtbot, type_page_rig):
    page = type_page_rig().page
    assert _import(qtbot, page, str(FIXTURE), via="enter") is True
    assert page.in_document_mode


def test_the_counter_and_estimate_describe_the_document_that_will_be_typed(qtbot, type_page_rig):
    page = type_page_rig().page
    _import(qtbot, page, str(FIXTURE))
    session = page.document_session()
    words = len(session.typed_text().split())
    assert page.counter_label.text().startswith(f"{len(session.typed_text()):,} characters, {words:,} words")
    qtbot.waitUntil(lambda: "est." in page.counter_label.text() and "~0 s" not in page.counter_label.text(), timeout=10000)


def test_the_view_shows_the_documents_formatting_not_just_its_words(qtbot, type_page_rig):
    page = type_page_rig().page
    _import(qtbot, page, str(FIXTURE))
    blocks = []
    block = page.document_view.document().begin()
    while block.isValid():
        blocks.append(block)
        block = block.next()
    assert any(b.textList() is not None for b in blocks), "the list items are drawn as real lists"
    assert any(b.blockFormat().headingLevel() > 0 for b in blocks), "headings are drawn as headings"
    assert any(
        fragment_format.fontWeight() >= 700
        for b in blocks
        for fragment_format in _char_formats(b)
    ), "bold text is drawn bold"


def _char_formats(block):
    iterator = block.begin()
    while not iterator.atEnd():
        yield iterator.fragment().charFormat()
        iterator += 1


# --- what will not be reproduced ---------------------------------------------------------------------------


def test_the_panel_lists_what_the_importer_could_not_read_in_plain_language(qtbot, type_page_rig):
    page = type_page_rig().page
    _import(qtbot, page, str(FIXTURE))
    lines = page.warning_lines()
    text = " ".join(lines)
    assert "1 link will be typed as plain text" in text
    assert "1 image will be left out" in text
    assert "1 table will be typed as plain paragraphs" in text
    assert "footnote or comment" in text
    assert "Custom fonts, text sizes and colours" in text
    assert not any("(dropped)" in line or "keymap" in line for line in lines)


def test_the_panel_also_lists_what_the_chosen_editor_cannot_do(qtbot, type_page_rig):
    page = type_page_rig().page
    _import(qtbot, page, str(FIXTURE))
    # The default editor is Google Docs, which has no Title or Subtitle shortcut.
    assert any("title will be typed as Heading 1, because no title shortcut is set up for Google Docs" in line for line in page.warning_lines())
    assert any("subtitle will be typed as Heading 2" in line for line in page.warning_lines())


def test_the_panel_shows_every_line_in_its_label_and_hides_the_good_news(qtbot, type_page_rig):
    page = type_page_rig().page
    _import(qtbot, page, str(FIXTURE))
    panel = page.warnings_panel
    assert panel.title_label.text() == "What will not be reproduced"
    assert panel.list_label.isHidden() is False
    assert panel.none_label.isHidden() is True
    shown = panel.list_label.text()
    for line in page.warning_lines():
        assert line in shown


def test_when_nothing_is_lost_the_panel_says_so_positively(qtbot, type_page_rig, tmp_path):
    page = type_page_rig().page
    plain_file = tmp_path / "essay.txt"
    plain_file.write_text("A plain essay.\nWith two paragraphs.", encoding="utf-8")

    assert _import(qtbot, page, str(plain_file)) is True

    assert page.warning_lines() == ()
    panel = page.warnings_panel
    assert panel.none_label.isHidden() is False
    assert panel.list_label.isHidden() is True
    assert panel.none_label.text() == (
        "No problems found. Everything the app could read from this document can be reproduced in "
        "Google Docs."
    )
    assert panel.title_label.text() == "What will not be reproduced"


# --- failures stay in plain-text mode with their own message ---------------------------------------------------


def _sign_in_page(url):
    return FetchResult(
        data=b"<html><title>Sign in - Google Accounts</title></html>",
        content_type="text/html",
        final_url="https://accounts.google.com/ServiceLogin",
    )


def _not_found(url):
    raise HTTPError(url, 404, "Not Found", hdrs=None, fp=None)


def _unreachable(url):
    raise URLError("timed out")


_FAILURES = {
    "AccessDeniedError": (GOOGLE_LINK, _loader_for_google(_sign_in_page)),
    "NotFoundError": (GOOGLE_LINK, _loader_for_google(_not_found)),
    "FetchError": (GOOGLE_LINK, _loader_for_google(_unreachable)),
    "UnsupportedSourceError": ("https://example.com/a-page", load_document),
    "DocumentImportError": ("/nowhere/missing.docx", load_document),
}


@pytest.mark.parametrize("error_class", list(_FAILURES))
def test_each_import_error_shows_its_own_message_and_stays_in_plain_text_mode(qtbot, type_page_rig, error_class):
    source, loader = _FAILURES[error_class]
    with pytest.raises(DocumentImportError) as raised:
        loader(source)
    assert type(raised.value).__name__ == error_class
    expected = str(raised.value)

    rig = type_page_rig(loader=loader)
    page = rig.page
    page.set_text("my own words")

    assert _import(qtbot, page, source) is False

    # Inline, under the import row ...
    assert page.import_bar.message_label.isHidden() is False
    assert page.import_bar.message_label.text() == expected
    # ... and in a message box, with exactly the same words.
    assert rig.errors == [("Couldn't import the document", expected)]
    # Nothing else changed.
    assert page.in_document_mode is False
    assert page.text_edit.isHidden() is False
    assert page.text_edit.toPlainText() == "my own words"
    assert page.document_view.isHidden() is True
    assert page.warnings_panel.isHidden() is True
    assert "Traceback" not in expected


def test_the_access_denied_message_keeps_its_advice_about_sharing_and_the_docx_download(qtbot, type_page_rig):
    rig = type_page_rig(loader=_loader_for_google(_sign_in_page))
    _import(qtbot, rig.page, GOOGLE_LINK)
    shown = rig.page.import_bar.message_label.text()
    assert "Anyone with the link can view" in shown
    assert ".docx" in shown
    assert rig.errors[0][1] == shown


def test_an_unexpected_failure_reaches_the_user_as_words_not_a_traceback(qtbot, type_page_rig):
    def broken_loader(source):
        raise KeyError("boom")

    rig = type_page_rig(loader=broken_loader)
    assert _import(qtbot, rig.page, "essay.docx") is False
    message = rig.page.import_bar.message_label.text()
    assert "Something went wrong while reading that document" in message
    assert "Traceback" not in message
    assert rig.errors[0][1] == message
    assert rig.page.in_document_mode is False


def test_a_document_with_no_text_is_refused_like_any_other_failure(qtbot, type_page_rig):
    blank = Document((Paragraph(), Paragraph((Run("   "),))))
    rig = type_page_rig(loader=lambda source: (blank, ImportReport(source=source)))

    assert _import(qtbot, rig.page, "empty.docx") is False

    assert rig.page.import_bar.message_label.text() == "That document has no text to type."
    assert rig.errors == [("Couldn't import the document", "That document has no text to type.")]
    assert rig.page.in_document_mode is False


def test_an_empty_box_asks_for_a_link_or_file_and_starts_nothing(qtbot, type_page_rig):
    calls: list[str] = []
    rig = type_page_rig(loader=lambda source: calls.append(source))
    rig.page.import_bar.set_source("   ")

    rig.page.import_bar.import_button.click()

    assert rig.page.import_bar.message_label.text() == "Paste a Google Docs link or choose a file first."
    assert calls == []
    assert rig.errors == []
    assert rig.page.import_bar.busy_widget.isHidden() is True


def test_a_failed_import_does_not_disturb_a_document_already_imported(qtbot, type_page_rig):
    rig = type_page_rig()
    page = rig.page
    _import(qtbot, page, str(FIXTURE))
    first = page.document_session()

    page._importer._loader = lambda source: (_ for _ in ()).throw(DocumentImportError("file not found: x"))
    assert _import(qtbot, page, "/nowhere/x.docx") is False

    assert page.in_document_mode is True
    assert page.document_session() is first
    assert page.import_bar.message_label.text() == "file not found: x"


def test_a_later_successful_import_replaces_the_document_and_clears_the_error(qtbot, type_page_rig, tmp_path):
    rig = type_page_rig()
    page = rig.page
    assert _import(qtbot, page, "/nowhere/missing.docx") is False
    assert page.import_bar.message_label.isHidden() is False

    assert _import(qtbot, page, str(FIXTURE)) is True
    assert page.import_bar.message_label.isHidden() is True

    other = tmp_path / "other.txt"
    other.write_text("Another document entirely.", encoding="utf-8")
    assert _import(qtbot, page, str(other)) is True
    assert page.document_session().source == str(other)
    assert page.document_view.toPlainText() == "Another document entirely."


def test_what_is_in_the_box_is_tidied_before_the_loader_sees_it(qtbot, type_page_rig):
    seen: list[str] = []

    def loader(source):
        seen.append(source)
        raise DocumentImportError("stop here")

    rig = type_page_rig(loader=loader)
    _import(qtbot, rig.page, '  "/Users/me/My Essay.docx"  ')
    _import(qtbot, rig.page, "docs.google.com/document/d/abc/edit")
    assert seen == ["/Users/me/My Essay.docx", "https://docs.google.com/document/d/abc/edit"]


# --- never block the window --------------------------------------------------------------------------------------


def test_the_import_runs_off_the_gui_thread_and_shows_a_busy_state(qtbot, type_page_rig):
    ran_on: list[threading.Thread] = []
    release = threading.Event()

    def slow_loader(source):
        ran_on.append(threading.current_thread())
        release.wait(10)
        return load_document(source)

    rig = type_page_rig(loader=slow_loader)
    page = rig.page
    bar = page.import_bar
    bar.set_source(str(FIXTURE))

    with qtbot.waitSignal(page.importFinished, timeout=15000):
        bar.import_button.click()
        # click() has come back while the loader is still waiting: the GUI thread is free.
        assert bar.busy_widget.isHidden() is False
        assert bar.progress_bar.isHidden() is False
        assert "Importing" in bar.status_label.text()
        for disabled in (bar.import_button, bar.browse_button, bar.edit, page.start_button, page.preview_button):
            assert disabled.isEnabled() is False, disabled.text() if hasattr(disabled, "text") else disabled
        assert page.back_button.isEnabled() is False
        release.set()

    assert ran_on and ran_on[0] is not threading.main_thread()
    # Everything is usable again once the import is over.
    assert bar.busy_widget.isHidden() is True
    for enabled in (bar.import_button, bar.browse_button, bar.edit, page.start_button, page.preview_button, page.back_button):
        assert enabled.isEnabled() is True


def test_nothing_can_start_while_a_document_is_being_imported(qtbot, type_page_rig):
    release = threading.Event()
    loads: list[str] = []

    def slow_loader(source):
        loads.append(source)
        release.wait(10)
        return load_document(source)

    rig = type_page_rig(loader=slow_loader)
    page = rig.page
    page.set_text("some plain text that could be typed")
    page.import_bar.set_source(str(FIXTURE))

    with qtbot.waitSignal(page.importFinished, timeout=15000):
        page.import_bar.import_button.click()
        # Start (button and Ctrl+Return alike), a second import and Browse all do nothing.
        page._on_start_clicked()
        page.import_bar.request_import()
        page._on_browse_requested()
        assert rig.controller.is_busy is False
        assert not rig.keyboards
        assert rig.chosen == []
        release.set()

    assert loads == [str(FIXTURE)]


# --- Browse ---------------------------------------------------------------------------------------------------------------


def test_browse_fills_the_box_with_the_chosen_file_and_imports_it(qtbot, type_page_rig):
    rig = type_page_rig(file_to_choose=str(FIXTURE))
    page = rig.page

    with qtbot.waitSignal(page.importFinished, timeout=10000) as blocker:
        page.import_bar.browse_button.click()

    assert blocker.args == [True]
    assert page.import_bar.edit.text() == str(FIXTURE)
    assert page.in_document_mode
    assert rig.chosen == [default_start_dir()]


def test_browse_reopens_in_the_folder_of_the_last_file_chosen(qtbot, type_page_rig):
    rig = type_page_rig(file_to_choose=str(FIXTURE))
    for _ in range(2):
        with qtbot.waitSignal(rig.page.importFinished, timeout=10000):
            rig.page.import_bar.browse_button.click()
    assert rig.chosen == [default_start_dir(), str(FIXTURE.parent)]


def test_cancelling_browse_does_nothing(qtbot, type_page_rig):
    calls: list[str] = []
    rig = type_page_rig(loader=lambda source: calls.append(source), file_to_choose=None)
    rig.page.import_bar.browse_button.click()
    assert calls == []
    assert rig.page.import_bar.edit.text() == ""
    assert rig.page.in_document_mode is False


# --- Back to plain text ---------------------------------------------------------------------------------------------------


def test_back_to_plain_text_leaves_the_documents_words_in_an_editable_box(qtbot, type_page_rig):
    rig = type_page_rig()
    page = rig.page
    _import(qtbot, page, str(FIXTURE))
    document = page.document_session().document
    modes: list[bool] = []
    page.documentModeChanged.connect(modes.append)

    page.back_button.click()

    assert page.in_document_mode is False
    assert modes == [False]
    assert page.document_session() is None
    assert page.text_edit.isHidden() is False
    assert page.text_edit.isReadOnly() is False
    assert page.text_edit.isEnabled() is True
    # The words are the *imported* document's, with no formatting and no typed markers.
    assert page.text_edit.toPlainText() == document.plain_text()
    assert "Sample Document Title" in page.text_edit.toPlainText()
    for gone in (page.document_view, page.document_header, page.warnings_panel):
        assert gone.isHidden() is True
    assert page.document_view.toPlainText() == ""
    assert page.document_view.shown_document is None


def test_the_restored_text_really_is_editable_and_is_what_gets_typed(qtbot, type_page_rig):
    rig = type_page_rig()
    page = rig.page
    _import(qtbot, page, str(FIXTURE))
    page.back_button.click()

    page.text_edit.setPlainText("Edited after going back.")
    with qtbot.waitSignal(rig.controller.finished, timeout=10000):
        page._on_start_clicked()

    assert rig.keyboards[0].text == "Edited after going back."
    assert rig.factory.keymaps == [None], "plain-text runs are started exactly as before"
    assert rig.keyboards[0].chords == []


def test_going_back_can_be_undone_to_get_the_earlier_text_back(qtbot, type_page_rig):
    page = type_page_rig().page
    page.set_text("my earlier text")
    _import(qtbot, page, str(FIXTURE))
    page.back_button.click()
    assert page.text_edit.toPlainText() != "my earlier text"

    page.text_edit.undo()

    assert page.text_edit.toPlainText() == "my earlier text"


def test_the_counter_returns_to_describing_the_text_box(qtbot, type_page_rig):
    page = type_page_rig().page
    _import(qtbot, page, str(FIXTURE))
    page.back_button.click()
    text = page.text_edit.toPlainText()
    assert page.counter_label.text().startswith(f"{len(text):,} characters, {len(text.split()):,} words")


def test_back_to_plain_text_does_nothing_in_plain_text_mode(qtbot, type_page_rig):
    page = type_page_rig().page
    page.set_text("plain")
    page._on_back_to_plain_clicked()
    assert page.text_edit.toPlainText() == "plain"
    assert page.in_document_mode is False


def test_loading_plain_text_programmatically_leaves_document_mode(qtbot, type_page_rig):
    page = type_page_rig().page
    _import(qtbot, page, str(FIXTURE))
    page.set_text("fresh text")
    assert page.in_document_mode is False
    assert page.text_edit.isHidden() is False
    assert page.text_edit.toPlainText() == "fresh text"


# --- plain-text mode is exactly what it was --------------------------------------------------------------------------------


def test_a_plain_text_run_still_types_exactly_the_box_and_asks_for_no_keymap(qtbot, type_page_rig):
    rig = type_page_rig()
    page = rig.page
    page.set_text("Exactly this, as before.")

    with qtbot.waitSignal(rig.controller.finished, timeout=10000) as blocker:
        page.start_button.click()

    assert blocker.args[0] == "finished"
    assert rig.keyboards[0].text == "Exactly this, as before."
    assert rig.keyboards[0].chords == []
    assert rig.factory.keymaps == [None]


def test_the_plain_text_preview_is_the_same_dialog_as_before(qtbot, type_page_rig, monkeypatch):
    from auto_essay_typer.gui.pages import type_page as type_page_module

    shown = []
    monkeypatch.setattr(type_page_module.PreviewDialog, "exec", lambda self: shown.append(self) or 0)
    page = type_page_rig().page
    page.set_text("Plain words for the preview.")

    page.preview_button.click()

    (dialog,) = shown
    assert dialog.verification_badge.text() == "Output verified: matches your text exactly"
    from PySide6.QtWidgets import QLabel

    assert "Formatting steps" not in [label.text() for label in dialog.findChildren(QLabel)]


def test_a_plain_text_box_with_nothing_in_it_still_blocks_start_and_preview(qtbot, type_page_rig):
    rig = type_page_rig()
    page = rig.page
    page.start_button.click()
    assert page.inline_message_label.text() == "Type or paste some text first."
    page.preview_button.click()
    assert rig.controller.is_busy is False
    assert not rig.keyboards


# --- what a person typed is echoed back as text, never as markup ------------------------------------------------------------------


def test_an_error_that_echoes_a_path_with_angle_brackets_shows_it_literally(qtbot, type_page_rig):
    from PySide6.QtCore import Qt

    message = "file not found: /Users/me/<b>essay</b>.docx"

    def loader(source):
        raise DocumentImportError(message)

    rig = type_page_rig(loader=loader)
    _import(qtbot, rig.page, "/Users/me/<b>essay</b>.docx")

    label = rig.page.import_bar.message_label
    assert label.textFormat() == Qt.TextFormat.PlainText
    assert label.text() == message
    assert rig.errors == [("Couldn't import the document", message)]


def test_an_inline_message_on_the_page_is_plain_text_too(qtbot, type_page_rig):
    from PySide6.QtCore import Qt

    page = type_page_rig().page
    page._show_inline_message("Couldn't switch to <i>Word</i>")
    assert page.inline_message_label.textFormat() == Qt.TextFormat.PlainText
    assert page.inline_message_label.text() == "Couldn't switch to <i>Word</i>"
