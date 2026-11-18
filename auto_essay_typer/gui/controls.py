"""Custom-painted widgets the stylesheet cannot express.

Only things Qt style sheets genuinely cannot do live here: the on/off switch
(a stylesheet can color a checkbox's indicator but cannot draw the sliding knob
that makes a switch read as a switch, so :class:`ToggleSwitch` paints itself),
and a couple of layout helpers for problems stylesheets cannot reach.

Colors come from the application palette (see ``build_palette`` in
``gui/theme.py``), never from literals, so a switch follows the theme --
including a live light/dark change -- with no code of its own. The
*application* palette specifically: a widget's own palette is rewritten by the
stylesheet's ``selection-background-color`` rule in every color group, which
would erase the disabled-state colors ``build_palette`` defines.

The one color that is not a palette role is the keyboard focus ring: it has to
stand clear of the accent-filled track it encircles, which no palette role does
(``Link`` is the accent). The stylesheet supplies it through the
``focusRingColor`` property (``qproperty-focusRingColor`` in ``gui/theme.py``),
the same ``focus_ring`` token every other control's ring uses.
"""

from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QEvent,
    QMargins,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
)
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPalette, QPen
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QCheckBox,
    QSizePolicy,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from auto_essay_typer.gui.focus_ring import FOCUS_RING_PROPERTY

_TRACK_WIDTH = 36
_TRACK_HEIGHT = 20
_KNOB_DIAMETER = 14
_KNOB_INSET = (_TRACK_HEIGHT - _KNOB_DIAMETER) / 2
#: Room around the track for the keyboard focus ring.
_RING_MARGIN = 3
_LABEL_GAP = 10
_ANIMATION_MS = 130


def _mix(first: QColor, second: QColor, amount: float) -> QColor:
    """``first`` blended toward ``second`` by ``amount`` (0.0 to 1.0)."""
    return QColor.fromRgbF(
        first.redF() + (second.redF() - first.redF()) * amount,
        first.greenF() + (second.greenF() - first.greenF()) * amount,
        first.blueF() + (second.blueF() - first.blueF()) * amount,
        first.alphaF() + (second.alphaF() - first.alphaF()) * amount,
    )


class ToggleSwitch(QCheckBox):
    """A checkbox that looks and moves like an on/off switch.

    A drop-in ``QCheckBox``: same ``toggled``/``setChecked``/``isChecked`` API,
    same Space-bar and click behavior, same accessible role and name (the
    label text), so it is exactly as operable by keyboard and screen reader as
    the checkbox it replaces. The whole widget -- switch and label -- is the
    click target. The knob eases to its new position unless the platform
    disables widget animation, or the change happens while the widget is
    hidden or with signals blocked (a profile reload), in which case it simply
    snaps.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("ToggleSwitch")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._progress = 0.0
        self._animating = False
        self._focus_ring_color = QColor()  # invalid until the stylesheet supplies one
        self._animation = QPropertyAnimation(self, b"progress", self)
        self._animation.setDuration(_ANIMATION_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.finished.connect(self._on_animation_finished)
        self.toggled.connect(self._on_toggled)

    # -- focus ring colour ---------------------------------------------------------

    def _get_focus_ring_color(self) -> QColor:
        return self._focus_ring_color

    def _set_focus_ring_color(self, color: QColor) -> None:
        self._focus_ring_color = QColor(color)
        self.update()

    #: Set by the app stylesheet (``qproperty-focusRingColor``); see the module docstring.
    focusRingColor = Property(QColor, _get_focus_ring_color, _set_focus_ring_color)

    def _ring_color(self) -> QColor:
        """The colour of the keyboard focus ring.

        The stylesheet's ``focus_ring`` token. Without a stylesheet (a switch
        in a bare test window) the window-text colour, which is the same
        near-black / near-white in both themes and never close to the track.
        """
        if self._focus_ring_color.isValid():
            return self._focus_ring_color
        return QApplication.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.WindowText)

    # -- animation -----------------------------------------------------------------

    def _get_progress(self) -> float:
        return self._progress

    def _set_progress(self, value: float) -> None:
        self._progress = value
        self.update()

    progress = Property(float, _get_progress, _set_progress)

    def _target(self) -> float:
        return 1.0 if self.isChecked() else 0.0

    def _visual_progress(self) -> float:
        # Not animating: always trust the real checked state, so a change made
        # with signals blocked (which never starts an animation) still paints
        # correctly.
        return self._progress if self._animating else self._target()

    def _animations_enabled(self) -> bool:
        style = QApplication.style()
        return self.isVisible() and style.styleHint(QStyle.StyleHint.SH_Widget_Animate) != 0

    def _on_toggled(self, _checked: bool) -> None:
        if not self._animations_enabled():
            self._animation.stop()
            self._animating = False
            self.update()
            return
        self._animation.stop()
        self._animation.setStartValue(self._visual_progress_before_toggle())
        self._animation.setEndValue(self._target())
        self._animating = True
        self._animation.start()

    def _visual_progress_before_toggle(self) -> float:
        # A toggle that interrupts a running animation continues from where the
        # knob is; otherwise from the opposite end of the track.
        return self._progress if self._animating else 1.0 - self._target()

    def _on_animation_finished(self) -> None:
        self._animating = False
        self.update()

    # -- geometry ------------------------------------------------------------------

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        metrics = self.fontMetrics()
        width = 2 * _RING_MARGIN + _TRACK_WIDTH
        text = self.text()
        if text:
            width += _LABEL_GAP + metrics.horizontalAdvance(text)
        height = max(_TRACK_HEIGHT + 2 * _RING_MARGIN, metrics.height() + 4)
        return QSize(width, height)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return self.sizeHint()

    def hitButton(self, pos) -> bool:  # noqa: N802 - Qt override
        return self.rect().contains(pos)

    # -- painting ------------------------------------------------------------------

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() in (QEvent.Type.EnabledChange, QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self.updateGeometry()
            self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = QApplication.palette()
        group = QPalette.ColorGroup.Active if self.isEnabled() else QPalette.ColorGroup.Disabled
        progress = self._visual_progress()

        track = QRectF(
            _RING_MARGIN,
            (self.height() - _TRACK_HEIGHT) / 2,
            _TRACK_WIDTH,
            _TRACK_HEIGHT,
        )
        track_color = _mix(palette.color(group, QPalette.ColorRole.Mid), palette.color(group, QPalette.ColorRole.Highlight), progress)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(track, _TRACK_HEIGHT / 2, _TRACK_HEIGHT / 2)

        knob_travel = _TRACK_WIDTH - _KNOB_DIAMETER - 2 * _KNOB_INSET
        knob = QRectF(
            track.left() + _KNOB_INSET + knob_travel * progress,
            track.top() + _KNOB_INSET,
            _KNOB_DIAMETER,
            _KNOB_DIAMETER,
        )
        knob_color = palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.HighlightedText)
        if not self.isEnabled():
            # Dimmed with the track instead of vanishing into it.
            knob_color = _mix(track_color, knob_color, 0.7)
        painter.setBrush(knob_color)
        painter.drawEllipse(knob)

        if self.isEnabled() and self.property(FOCUS_RING_PROPERTY):
            ring = QPen(self._ring_color(), 2)
            painter.setPen(ring)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            # A 2px stroke centered 2px out from the track covers the margin's outer
            # 1px..3px, leaving a 1px gap between ring and track.
            outset = _RING_MARGIN - 1
            outer = track.adjusted(-outset, -outset, outset, outset)
            painter.drawRoundedRect(outer, outer.height() / 2, outer.height() / 2)

        if self.text():
            painter.setPen(palette.color(group, QPalette.ColorRole.WindowText))
            painter.setFont(self.font())
            label_left = track.right() + _RING_MARGIN + _LABEL_GAP
            painter.drawText(
                QRectF(label_left, 0, max(0.0, self.width() - label_left), self.height()),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                self.text(),
            )
        painter.end()


class OutsetLayout(QBoxLayout):
    """A box layout that lets its items spill past the cell it was given.

    Qt has no negative margins (a negative layout margin means "use the style
    default"), but a few widgets reserve transparent room around themselves for a
    focus ring, and then sit that far inside the column of labels and fields they
    belong to. Laying such a widget out in a rectangle ``outset`` larger than its
    cell cancels the reservation out, so what is *drawn* lines up. The items stay
    inside the parent widget, so nothing is clipped, and the layout's size hints
    are unchanged.
    """

    def __init__(
        self,
        direction: QBoxLayout.Direction,
        outset: QMargins,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(direction, parent)
        self._outset = outset
        self.setContentsMargins(0, 0, 0, 0)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 - Qt override
        super().setGeometry(rect.marginsAdded(self._outset))


def switch_row(switch: ToggleSwitch) -> QBoxLayout:
    """A one-widget row holding ``switch``, pulled left so its *track* lines up.

    A switch reserves a few pixels around its track for the keyboard focus ring,
    so added to a layout directly its track sits that far right of the labels and
    fields above it. This row cancels that out.
    """
    row = OutsetLayout(QBoxLayout.Direction.LeftToRight, QMargins(_RING_MARGIN, 0, 0, 0))
    row.addWidget(switch)
    row.addStretch(1)
    return row


def focus_ring_bleed(widget: QWidget, ring_width: int = 2) -> QBoxLayout:
    """A layout holding ``widget`` that lets its ``ring_width`` border sit outside the page margin.

    For a scroll area that draws its keyboard focus ring in a border (see
    ``QScrollArea#PageScroll`` in ``gui/theme.py``): the border would otherwise inset
    the content by the ring's width, pushing it off the title and cards above.
    """
    holder = OutsetLayout(QBoxLayout.Direction.TopToBottom, QMargins(ring_width, ring_width, ring_width, ring_width))
    holder.addWidget(widget)
    return holder


class ScrollPageLayout(QVBoxLayout):
    """The layout for a scroll area's content: shrink first, scroll only if it must.

    ``QScrollArea`` sizes a resizable widget from its layout's height-for-width
    whenever the layout reports having one, and any word-wrapped label gives it
    one. For a box layout that height is the *preferred* height of every item, so
    the area starts scrolling the moment preferred height outgrows the viewport
    -- even when squeezing a stretchy item (a text box) toward its minimum would
    have fit everything. Reporting no height-for-width to the scroll area makes
    it fall back to the layout's true minimum height, so the stretchy item gives
    way first. The layout still lays out its own children with height-for-width,
    so wrapped labels keep the height their text needs.
    """

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt override
        return False
