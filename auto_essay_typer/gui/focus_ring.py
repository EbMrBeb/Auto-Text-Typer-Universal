"""Keyboard-only focus rings: the ``:focus-visible`` Qt does not have.

A visible focus ring is non-negotiable for keyboard users (WCAG 2.4.7), but
a ring that lights up on every mouse click looks like a bug. Qt's ``:focus``
pseudo-state cannot tell the two apart, so :class:`FocusVisibleFilter` keeps
the same rule browsers use for ``:focus-visible``: a widget shows its ring when
focus reached it from the keyboard.

"From the keyboard" is decided two ways. The focus reason says so directly
(Tab, Shift+Tab, a mnemonic shortcut). And a widget that receives focus for
some indirect reason -- a dialog opening onto its default button, a combo box
getting focus back when its popup closes -- shows its ring if the last input
the user made was a navigation key rather than a mouse press.

The ring itself is drawn by the stylesheet (``[focusRing="true"]`` rules in
``gui/theme.py``) and by custom-painted widgets (``gui/controls.py``); this
class only maintains the dynamic property.
"""

from __future__ import annotations

import weakref

import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt
from PySide6.QtWidgets import QWidget

#: The dynamic property the stylesheet and custom-painted widgets read.
FOCUS_RING_PROPERTY = "focusRing"

_RING_FOCUS_REASONS = frozenset(
    {
        Qt.FocusReason.TabFocusReason,
        Qt.FocusReason.BacktabFocusReason,
        Qt.FocusReason.ShortcutFocusReason,
    }
)

#: Keys that mean "the user is driving with the keyboard".
_NAVIGATION_KEYS = frozenset(
    {
        Qt.Key.Key_Tab,
        Qt.Key.Key_Backtab,
        Qt.Key.Key_Left,
        Qt.Key.Key_Right,
        Qt.Key.Key_Up,
        Qt.Key.Key_Down,
        Qt.Key.Key_Space,
        Qt.Key.Key_Return,
        Qt.Key.Key_Enter,
    }
)

_POINTER_EVENTS = frozenset({QEvent.Type.MouseButtonPress, QEvent.Type.TouchBegin})


def set_focus_ring(widget: QWidget, visible: bool) -> None:
    """Show or hide ``widget``'s keyboard focus ring, restyling it only on a change."""
    if bool(widget.property(FOCUS_RING_PROPERTY)) == visible:
        return
    widget.setProperty(FOCUS_RING_PROPERTY, visible)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


class FocusVisibleFilter(QObject):
    """Application-wide event filter that maintains the ``focusRing`` property.

    Install it with :func:`install_focus_visible_filter` (``ThemeManager``
    does), which keeps exactly one on the ``QApplication``. It never consumes
    an event.
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._keyboard_driven = False

    @property
    def keyboard_driven(self) -> bool:
        """Whether the user's most recent navigation input was the keyboard."""
        return self._keyboard_driven

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        kind = event.type()
        if kind == QEvent.Type.FocusIn:
            if isinstance(watched, QWidget):
                reason = event.reason()
                set_focus_ring(watched, reason in _RING_FOCUS_REASONS or self._keyboard_driven)
        elif kind == QEvent.Type.FocusOut:
            if isinstance(watched, QWidget):
                set_focus_ring(watched, False)
        elif kind == QEvent.Type.KeyPress:
            if event.key() in _NAVIGATION_KEYS:
                self._keyboard_driven = True
        elif kind in _POINTER_EVENTS:
            self._keyboard_driven = False
        return False


#: The filter :func:`install_focus_visible_filter` last put on the application.
_installed: weakref.ReferenceType[FocusVisibleFilter] | None = None


def install_focus_visible_filter(
    app: QCoreApplication, parent: QObject | None = None
) -> FocusVisibleFilter:
    """Install a new :class:`FocusVisibleFilter` on ``app``, replacing the one installed before.

    There must only ever be one. Qt hands an event to every installed filter,
    and each one *sets* the ring on a widget that gains focus, so with two the
    answer comes from whichever runs last -- the oldest -- and that one may be
    remembering a Tab key pressed long ago ("keyboard driven" until the next
    mouse press). A window shown programmatically would then open with its
    first control already ringed. Replacing the filter starts the new theme
    manager's session with no input history, which is also what a fresh
    application has.

    ``parent`` owns the new filter (``ThemeManager`` passes itself), so the
    filter goes away with it.
    """
    global _installed
    previous = _installed() if _installed is not None else None
    if previous is not None and shiboken6.isValid(previous):
        app.removeEventFilter(previous)
    focus_filter = FocusVisibleFilter(parent)
    app.installEventFilter(focus_filter)
    _installed = weakref.ref(focus_filter)
    return focus_filter
