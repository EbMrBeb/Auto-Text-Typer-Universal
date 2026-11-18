"""The Import row and the "What will not be reproduced" card, on their own."""

from __future__ import annotations

from PySide6.QtCore import Qt

from auto_essay_typer.gui.document_panels import ImportBar, WarningsPanel


def _bar(qtbot) -> ImportBar:
    bar = ImportBar()
    qtbot.addWidget(bar)
    return bar


# --- ImportBar -----------------------------------------------------------------------------------------------------


def test_the_import_button_asks_for_the_tidied_contents_of_the_box(qtbot):
    bar = _bar(qtbot)
    bar.set_source('  "/Users/me/My Essay.docx"  ')
    with qtbot.waitSignal(bar.importRequested, timeout=1000) as blocker:
        bar.import_button.click()
    assert blocker.args == ["/Users/me/My Essay.docx"]


def test_enter_in_the_box_asks_for_an_import_too(qtbot):
    bar = _bar(qtbot)
    bar.set_source("https://docs.google.com/document/d/abc/edit")
    with qtbot.waitSignal(bar.importRequested, timeout=1000) as blocker:
        qtbot.keyClick(bar.edit, Qt.Key.Key_Return)
    assert blocker.args == ["https://docs.google.com/document/d/abc/edit"]


def test_an_empty_box_asks_for_something_instead_of_importing_nothing(qtbot):
    bar = _bar(qtbot)
    with qtbot.assertNotEmitted(bar.importRequested):
        bar.import_button.click()
    assert bar.message_label.isHidden() is False
    assert bar.message_label.text() == "Paste a Google Docs link or choose a file first."


def test_a_request_clears_an_earlier_message(qtbot):
    bar = _bar(qtbot)
    bar.show_error("something failed")
    bar.set_source("essay.docx")
    bar.import_button.click()
    assert bar.message_label.isHidden() is True


def test_browse_only_reports_the_request(qtbot):
    bar = _bar(qtbot)
    with qtbot.waitSignal(bar.browseRequested, timeout=1000):
        bar.browse_button.click()
    assert bar.edit.text() == ""


def test_the_busy_state_is_shown_and_hidden_on_request(qtbot):
    bar = _bar(qtbot)
    assert bar.busy_widget.isHidden() is True
    bar.set_busy(True)
    assert bar.busy_widget.isHidden() is False
    assert bar.progress_bar.minimum() == bar.progress_bar.maximum() == 0, "an indeterminate bar"
    bar.set_busy(False)
    assert bar.busy_widget.isHidden() is True


def test_the_box_and_both_buttons_are_turned_on_and_off_together(qtbot):
    bar = _bar(qtbot)
    bar.set_controls_enabled(False)
    assert not any(c.isEnabled() for c in (bar.edit, bar.import_button, bar.browse_button))
    bar.set_controls_enabled(True)
    assert all(c.isEnabled() for c in (bar.edit, bar.import_button, bar.browse_button))


def test_an_error_stays_until_it_is_cleared(qtbot):
    bar = _bar(qtbot)
    bar.show_error("could not reach Google")
    assert bar.message_label.text() == "could not reach Google"
    assert bar.message_label.isHidden() is False
    bar.clear_error()
    assert bar.message_label.isHidden() is True


def test_every_control_in_the_bar_has_a_name_and_a_tooltip(qtbot):
    bar = _bar(qtbot)
    for control in (bar.edit, bar.import_button, bar.browse_button):
        assert control.accessibleName()
        assert control.toolTip()
    assert bar.progress_bar.accessibleName() == "Import progress"


# --- WarningsPanel ----------------------------------------------------------------------------------------------------


def _panel(qtbot) -> WarningsPanel:
    panel = WarningsPanel()
    qtbot.addWidget(panel)
    return panel


def test_the_panel_lists_each_line_and_hides_the_good_news(qtbot):
    panel = _panel(qtbot)
    panel.show_lines(["1 image will be left out.", "2 tables will be typed as plain paragraphs."], "Google Docs")
    assert panel.lines() == ("1 image will be left out.", "2 tables will be typed as plain paragraphs.")
    assert panel.list_label.isHidden() is False
    assert panel.none_label.isHidden() is True
    text = panel.list_label.text()
    assert text.count("<li") == 2
    assert "1 image will be left out." in text


def test_with_nothing_to_list_the_panel_says_so_and_names_the_editor(qtbot):
    panel = _panel(qtbot)
    panel.show_lines([], "Microsoft Word")
    assert panel.lines() == ()
    assert panel.none_label.isHidden() is False
    assert panel.list_label.isHidden() is True
    assert panel.none_label.text() == (
        "No problems found. Everything the app could read can be reproduced in Microsoft Word."
    )


def test_a_new_list_replaces_the_old_one_and_the_good_news_comes_and_goes(qtbot):
    panel = _panel(qtbot)
    panel.show_lines(["a"], "X")
    panel.show_lines(["b", "c"], "X")
    assert panel.lines() == ("b", "c")
    assert panel.list_label.text().count("<li") == 2, "the first list is gone, not added to"
    panel.show_lines([], "X")
    assert panel.none_label.isHidden() is False
    panel.show_lines(["d"], "X")
    assert panel.none_label.isHidden() is True


def test_text_in_a_line_is_shown_as_text_never_read_as_markup(qtbot):
    panel = _panel(qtbot)
    panel.show_lines(["1 piece of <b>text</b> & more"], "X")
    shown = panel.list_label.text()
    assert "<b>text</b>" not in shown
    assert "&lt;b&gt;text&lt;/b&gt; &amp; more" in shown


def test_the_panel_has_its_title_and_is_styled_as_a_card(qtbot):
    panel = _panel(qtbot)
    assert panel.title_label.text() == "What will not be reproduced"
    assert panel.objectName() == "Card"
