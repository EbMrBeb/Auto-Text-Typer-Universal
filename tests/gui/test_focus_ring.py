"""Tests for the keyboard-only focus ring (``gui/focus_ring.py``)."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QFocusEvent, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from auto_essay_typer.gui.focus_ring import FOCUS_RING_PROPERTY, FocusVisibleFilter, set_focus_ring
from auto_essay_typer.gui.theme import ThemeManager


def _focus_in(reason):
    return QFocusEvent(QEvent.Type.FocusIn, reason)


def _key(key):
    return QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)


def _click():
    return QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(1, 1),
        QPointF(1, 1),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _ring(widget) -> bool:
    return bool(widget.property(FOCUS_RING_PROPERTY))


def test_set_focus_ring_sets_and_clears_the_property(qtbot, qapp):
    button = QPushButton("x")
    qtbot.addWidget(button)
    assert _ring(button) is False
    set_focus_ring(button, True)
    assert _ring(button) is True
    set_focus_ring(button, True)  # idempotent
    assert _ring(button) is True
    set_focus_ring(button, False)
    assert _ring(button) is False


def test_tab_focus_shows_the_ring_and_losing_focus_hides_it(qtbot, qapp):
    button = QPushButton("x")
    qtbot.addWidget(button)
    focus_filter = FocusVisibleFilter()
    focus_filter.eventFilter(button, _focus_in(Qt.FocusReason.TabFocusReason))
    assert _ring(button) is True
    focus_filter.eventFilter(button, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.TabFocusReason))
    assert _ring(button) is False


def test_backtab_and_shortcut_focus_also_show_the_ring(qtbot, qapp):
    focus_filter = FocusVisibleFilter()
    for reason in (Qt.FocusReason.BacktabFocusReason, Qt.FocusReason.ShortcutFocusReason):
        button = QPushButton("x")
        qtbot.addWidget(button)
        focus_filter.eventFilter(button, _focus_in(reason))
        assert _ring(button) is True, reason


def test_mouse_focus_does_not_show_the_ring(qtbot, qapp):
    button = QPushButton("x")
    qtbot.addWidget(button)
    focus_filter = FocusVisibleFilter()
    focus_filter.eventFilter(button, _click())
    focus_filter.eventFilter(button, _focus_in(Qt.FocusReason.MouseFocusReason))
    assert _ring(button) is False


def test_indirect_focus_after_a_navigation_key_shows_the_ring(qtbot, qapp):
    # e.g. a dialog opening onto its default button after the user pressed Return.
    button = QPushButton("x")
    qtbot.addWidget(button)
    focus_filter = FocusVisibleFilter()
    focus_filter.eventFilter(button, _key(Qt.Key.Key_Return))
    assert focus_filter.keyboard_driven is True
    focus_filter.eventFilter(button, _focus_in(Qt.FocusReason.ActiveWindowFocusReason))
    assert _ring(button) is True


def test_indirect_focus_after_a_mouse_press_does_not_show_the_ring(qtbot, qapp):
    button = QPushButton("x")
    qtbot.addWidget(button)
    focus_filter = FocusVisibleFilter()
    focus_filter.eventFilter(button, _key(Qt.Key.Key_Tab))
    focus_filter.eventFilter(button, _click())
    assert focus_filter.keyboard_driven is False
    focus_filter.eventFilter(button, _focus_in(Qt.FocusReason.PopupFocusReason))
    assert _ring(button) is False


def test_typing_ordinary_letters_does_not_count_as_keyboard_navigation(qtbot, qapp):
    focus_filter = FocusVisibleFilter()
    focus_filter.eventFilter(QWidget(), _key(Qt.Key.Key_A))
    assert focus_filter.keyboard_driven is False


def test_the_filter_never_consumes_an_event(qtbot, qapp):
    button = QPushButton("x")
    qtbot.addWidget(button)
    focus_filter = FocusVisibleFilter()
    for event in (_focus_in(Qt.FocusReason.TabFocusReason), _key(Qt.Key.Key_Tab), _click()):
        assert focus_filter.eventFilter(button, event) is False


def test_theme_manager_installs_the_filter_on_the_application(qtbot, qapp):
    manager = ThemeManager(qapp)  # the filter lives exactly as long as its manager
    assert manager is not None
    button = QPushButton("x")
    qtbot.addWidget(button)
    QApplication.sendEvent(button, _focus_in(Qt.FocusReason.TabFocusReason))
    assert _ring(button) is True
    QApplication.sendEvent(button, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.TabFocusReason))
    assert _ring(button) is False
