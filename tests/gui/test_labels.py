"""Tests for :mod:`auto_essay_typer.gui.labels`: labels for a page that scrolls instead of squeezing."""

from __future__ import annotations


# --- WrappingLabel ---------------------------------------------------------------------------------------------------

_LONG_TEXT = "These words wrap onto several lines when there is little room for them. " * 6


def test_a_wrapping_label_reports_the_height_its_text_needs_at_its_current_width(qtbot):
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    from auto_essay_typer.gui.labels import WrappingLabel

    holder = QWidget()
    layout = QVBoxLayout(holder)
    label = WrappingLabel(_LONG_TEXT)
    layout.addWidget(label)
    qtbot.addWidget(holder)
    holder.resize(240, 400)
    holder.show()
    qtbot.waitExposed(holder)

    narrow = label.heightForWidth(label.width())
    qtbot.waitUntil(lambda: label.minimumSizeHint().height() == narrow, timeout=3000)
    assert narrow > 4 * label.fontMetrics().height(), "the text really does wrap"

    holder.resize(900, 400)
    qtbot.waitUntil(lambda: label.width() > 600, timeout=3000)
    # Wider means fewer lines, so the label asks for less -- not for what it needed before.
    qtbot.waitUntil(lambda: label.minimumSizeHint().height() == label.heightForWidth(label.width()), timeout=3000)
    assert label.minimumSizeHint().height() < narrow


def test_a_wrapping_label_is_not_squeezed_by_a_page_that_has_to_scroll(qtbot):
    from PySide6.QtWidgets import QPushButton, QScrollArea, QWidget

    from auto_essay_typer.gui.controls import ScrollPageLayout
    from auto_essay_typer.gui.labels import WrappingLabel

    area = QScrollArea()
    area.setWidgetResizable(True)
    content = QWidget()
    area.setWidget(content)
    layout = ScrollPageLayout(content)
    top, bottom = QPushButton("top"), QPushButton("bottom")
    label = WrappingLabel(_LONG_TEXT)
    for widget in (top, label, bottom):
        layout.addWidget(widget)
    qtbot.addWidget(area)
    area.resize(300, 140)  # far shorter than the label's text needs
    area.show()
    qtbot.waitExposed(area)

    qtbot.waitUntil(
        lambda: label.height() >= label.heightForWidth(label.width()) > 3 * label.fontMetrics().height(),
        timeout=3000,
    )
    # Nothing overlaps: the page grows past the window and scrolls instead.
    assert top.geometry().bottom() < label.geometry().top()
    assert label.geometry().bottom() < bottom.geometry().top()
    assert content.height() > area.viewport().height()


# --- ElidedLabel -------------------------------------------------------------------------------------------------------


def test_an_elided_label_keeps_the_full_text_for_the_tooltip_and_screen_readers(qtbot):
    from auto_essay_typer.gui.labels import ElidedLabel

    label = ElidedLabel("Imported from a very long file name.docx")
    qtbot.addWidget(label)
    assert label.fullText() == "Imported from a very long file name.docx"
    assert label.toolTip() == label.fullText()
    assert label.accessibleName() == label.fullText()


def test_an_elided_label_shortens_in_the_middle_to_fit_and_shows_it_all_when_there_is_room(qtbot):
    from auto_essay_typer.gui.labels import ElidedLabel

    full = "Imported from the-final-draft-of-my-long-essay-on-industrialisation (v3 edited).docx"
    label = ElidedLabel(full)
    qtbot.addWidget(label)
    label.show()
    qtbot.waitExposed(label)
    label.resize(160, 20)
    shown = label.text()
    assert shown != full and "…" in shown
    assert shown.startswith("Imported") and shown.endswith(".docx"), "both ends survive"
    assert label.fontMetrics().horizontalAdvance(shown) <= 160

    label.resize(label.fontMetrics().horizontalAdvance(full) + 20, 20)
    assert label.text() == full


def test_an_elided_label_never_widens_its_parent(qtbot):
    from PySide6.QtWidgets import QHBoxLayout, QWidget

    from auto_essay_typer.gui.labels import ElidedLabel

    holder = QWidget()
    QHBoxLayout(holder).addWidget(ElidedLabel("x" * 400))
    qtbot.addWidget(holder)
    assert holder.minimumSizeHint().width() < 100
