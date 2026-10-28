"""Labels that behave on a page that scrolls instead of squeezing.

The Type page (like any page built on ``controls.ScrollPageLayout``) grows past
its window and scrolls rather than shrinking its contents. Two kinds of text break
that arrangement when held in a plain ``QLabel``:

* :class:`WrappingLabel` -- text that wraps. A plain wrapped label reports a
  one-line minimum height, so a page that sizes itself from minimums gives a
  paragraph of text too little room and shares the shortfall out between every
  other item on the page, which then overlap.
* :class:`ElidedLabel` -- text of unknown length (a file name, a link). A plain label
  will not shrink below the width of its text, so a long name widens the whole
  page past its window and brings up a horizontal scroll bar.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget


class WrappingLabel(QLabel):
    """A word-wrapped label whose minimum height is the height its text needs *now*.

    How tall wrapped text must be depends on how wide it is, which a layout cannot
    know until it has decided the width, so a plain wrapped ``QLabel`` can only
    promise a single line. This label reports ``heightForWidth(its current width)``
    as its minimum instead, and reports again whenever its width changes, so what
    the page allots it and what it needs stay the same.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        hint = super().minimumSizeHint()
        if self.width() > 0:  # before the first layout there is no width to wrap at
            hint.setHeight(max(hint.height(), self.heightForWidth(self.width())))
        return hint

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self.updateGeometry()


class ElidedLabel(QLabel):
    """A one-line label that never asks for more width than it is given.

    It takes whatever width its layout offers and cuts the text to fit with an
    ellipsis in the middle, where a file's name and its extension both stay
    readable. The whole text is its tooltip and its accessible name (a screen
    reader reads the name, not the shortened text on screen); read it back with
    :meth:`fullText`, since :meth:`text` is only what is showing.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._full_text = ""
        self.setFullText(text)

    def fullText(self) -> str:  # noqa: N802 - Qt-style accessor
        """The whole text, however much of it is showing."""
        return self._full_text

    def setFullText(self, text: str) -> None:  # noqa: N802 - Qt-style setter
        self._full_text = text
        self.setToolTip(text)
        self.setAccessibleName(text)
        self._elide()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        shown = self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideMiddle, max(0, self.width())
        )
        if shown != self.text():
            self.setText(shown)
