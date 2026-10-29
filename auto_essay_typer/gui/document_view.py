"""A read-only view that draws an imported document the way it will look once typed.

The Type page's document mode replaces the plain-text box with this widget. It
renders a :class:`~auto_essay_typer.document.Document` with real Qt text
formatting rather than a description of it: heading sizes, bold / italic /
underline / strikethrough, paragraph alignment, and genuine bulleted and
numbered lists whose markers and per-level indentation are drawn by the text
layout.

Two consequences of drawing lists that way are deliberate:

* the markers are *not* characters in the widget, so nothing here can leak a
  bullet or a number into the text that gets typed; and
* the widget is never the source of the text anyway. The view holds the
  :class:`Document` it was given (:attr:`DocumentView.shown_document`) and the
  page types from the document itself, never from ``toPlainText()``.

It is a ``QTextEdit`` and not a ``QPlainTextEdit`` because only the rich-text
layout can draw lists and per-fragment fonts.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import (
    QFontInfo,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
    QTextList,
    QTextListFormat,
)
from PySide6.QtWidgets import QTextEdit, QWidget

from auto_essay_typer.document import (
    Alignment,
    Document,
    ListKind,
    Paragraph,
    ParagraphStyle,
    TextStyle,
)

#: Fallback body size (in pixels) if the widget's font reports none.
_DEFAULT_PIXEL_SIZE = 13
#: Horizontal distance of one list nesting level; wide enough for a marker like "100.".
_INDENT_WIDTH = 34

#: Characters a text layout would act on rather than draw. A run never contains a
#: newline, but a stray carriage return, a Unicode line or paragraph separator would
#: split a block in two (and leave a list marker on only half of it), and U+FDD0/FDD1
#: are Qt's own placeholders for embedded objects. The view shows them as something
#: harmless; what gets typed is decided by the document, not by this drawing.
_DISPLAY_TRANSLATION = str.maketrans(
    {"\r": " ", "\u2028": " ", "\u2029": " ", "\ufdd0": "\ufffd", "\ufdd1": "\ufffd"}
)

_WEIGHT_SEMIBOLD = 600
_WEIGHT_BOLD = 700

#: Display styles from largest to smallest, as Google Docs ranks them (a subtitle sits
#: between Heading 2 and Heading 3), and each one's size as a multiple of the body size.
_SIZE_RANK: tuple[ParagraphStyle, ...] = (
    ParagraphStyle.TITLE,
    ParagraphStyle.HEADING_1,
    ParagraphStyle.HEADING_2,
    ParagraphStyle.SUBTITLE,
    ParagraphStyle.HEADING_3,
    ParagraphStyle.HEADING_4,
    ParagraphStyle.HEADING_5,
    ParagraphStyle.HEADING_6,
)
_SIZE_SCALE: dict[ParagraphStyle, float] = {
    ParagraphStyle.TITLE: 2.1,
    ParagraphStyle.HEADING_1: 1.75,
    ParagraphStyle.HEADING_2: 1.5,
    ParagraphStyle.SUBTITLE: 1.32,
    ParagraphStyle.HEADING_3: 1.25,
    ParagraphStyle.HEADING_4: 1.15,
    ParagraphStyle.HEADING_5: 1.08,
    ParagraphStyle.HEADING_6: 1.04,
}


def display_sizes(body_pixel_size: int) -> dict[ParagraphStyle, int]:
    """The pixel size of every display style for a body text of ``body_pixel_size``.

    Rounding the scales alone makes neighbouring levels collide at small body sizes
    (Heading 5 and 6 both come out at 14 pixels when the body is 13), so the sizes are
    built up from the smallest: each is its rounded scale or one pixel more than the
    next one down, whichever is larger. The result is strictly larger for every step up
    in rank, and always larger than the body.
    """
    sizes: dict[ParagraphStyle, int] = {}
    smaller = body_pixel_size
    for style in reversed(_SIZE_RANK):
        smaller = max(round(body_pixel_size * _SIZE_SCALE[style]), smaller + 1)
        sizes[style] = smaller
    return sizes


#: Heading level 1-6 for the real headings (a title or subtitle is not one).
_HEADING_LEVEL: dict[ParagraphStyle, int] = {
    ParagraphStyle.HEADING_1: 1,
    ParagraphStyle.HEADING_2: 2,
    ParagraphStyle.HEADING_3: 3,
    ParagraphStyle.HEADING_4: 4,
    ParagraphStyle.HEADING_5: 5,
    ParagraphStyle.HEADING_6: 6,
}

#: Styles drawn heavier than body text. A subtitle is the one display style that is not.
_SEMIBOLD_STYLES = frozenset(
    {ParagraphStyle.TITLE, *_HEADING_LEVEL}
)

_ALIGNMENT: dict[Alignment, Qt.AlignmentFlag] = {
    Alignment.LEFT: Qt.AlignmentFlag.AlignLeft,
    Alignment.CENTER: Qt.AlignmentFlag.AlignHCenter,
    Alignment.RIGHT: Qt.AlignmentFlag.AlignRight,
    Alignment.JUSTIFIED: Qt.AlignmentFlag.AlignJustify,
}

#: Marker styles by nesting level; deeper levels cycle through the three.
_BULLET_STYLES = (
    QTextListFormat.Style.ListDisc,
    QTextListFormat.Style.ListCircle,
    QTextListFormat.Style.ListSquare,
)
_NUMBER_STYLES = (
    QTextListFormat.Style.ListDecimal,
    QTextListFormat.Style.ListLowerAlpha,
    QTextListFormat.Style.ListLowerRoman,
)

#: Space (in pixels) left under a body paragraph, under a list item, and around a heading.
_GAP_PARAGRAPH = 6
_GAP_LIST_ITEM = 2
_GAP_HEADING_ABOVE = 12
_GAP_HEADING_BELOW = 4
#: Line spacing of body text, as a percentage of the font's natural line height.
_LINE_HEIGHT_PERCENT = 118.0


def _list_format(kind: ListKind, level: int) -> QTextListFormat:
    styles = _BULLET_STYLES if kind is ListKind.BULLET else _NUMBER_STYLES
    list_format = QTextListFormat()
    list_format.setStyle(styles[level % len(styles)])
    # Qt indents a list item by (the list's indent) x (the document's indent width).
    list_format.setIndent(level + 1)
    return list_format


class DocumentView(QTextEdit):
    """Draws a :class:`Document` read-only, with its formatting.

    Give it a document with :meth:`set_document` (``None`` clears it). The view
    can be focused and its text selected and copied, so it can be read and
    scrolled from the keyboard, but nothing typed into it changes anything.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAcceptRichText(False)
        self.setTabChangesFocus(True)
        self.setAccessibleName("Document to type")
        self.setAccessibleDescription(
            "The imported document as it will be typed, with its formatting. It can be read "
            "but not edited; use Back to plain text to change the words."
        )
        self.document().setUndoRedoEnabled(False)
        self._model: Document | None = None

    # -- the document -----------------------------------------------------------------------------------

    @property
    def shown_document(self) -> Document | None:
        """The document being drawn: the source of truth for what the view shows."""
        return self._model

    def set_document(self, document: Document | None) -> None:
        """Draw ``document`` (replacing whatever was shown); ``None`` empties the view.

        A document equal to the one already drawn is not drawn again (choosing an
        editor that needs the same adaptation, say), though the view still
        remembers the object it was given.
        """
        unchanged = document is not None and document == self._model
        self._model = document
        if not unchanged:
            self._render()

    # -- drawing ------------------------------------------------------------------------------------------

    def _base_pixel_size(self) -> int:
        size = QFontInfo(self.font()).pixelSize()
        return size if size > 0 else _DEFAULT_PIXEL_SIZE

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange and getattr(self, "_model", None) is not None:
            # The display sizes were worked out from the body size when the document was drawn.
            self._render(keep_place=True)

    def _render(self, *, keep_place: bool = False) -> None:
        place = self.verticalScrollBar().value() if keep_place else 0
        self.setUpdatesEnabled(False)
        try:
            self.clear()
            text_document = self.document()
            text_document.setUndoRedoEnabled(False)
            text_document.setIndentWidth(_INDENT_WIDTH)
            if self._model is None:
                return
            # Drawn normalized, as the rest of the project compares documents: an empty
            # list item (which no real document can have) gets no marker.
            model = self._model.normalized()
            if not model.paragraphs:
                return

            sizes = display_sizes(self._base_pixel_size())
            cursor = QTextCursor(text_document)
            cursor.beginEditBlock()
            #: The list currently open at each nesting level, and its kind.
            open_lists: dict[int, tuple[QTextList, ListKind]] = {}
            for index, paragraph in enumerate(model.paragraphs):
                block_format = self._block_format(paragraph, first=index == 0)
                block_char_format = self._char_format(paragraph.style, frozenset(), sizes)
                if index == 0:
                    cursor.setBlockFormat(block_format)
                    cursor.setBlockCharFormat(block_char_format)
                else:
                    cursor.insertBlock(block_format, block_char_format)
                for run in paragraph.runs:
                    cursor.insertText(
                        run.text.translate(_DISPLAY_TRANSLATION),
                        self._char_format(paragraph.style, run.styles, sizes),
                    )
                self._place_in_list(cursor, paragraph, open_lists)
            cursor.endEditBlock()
        finally:
            self.setUpdatesEnabled(True)
        self.moveCursor(QTextCursor.MoveOperation.Start)
        self.verticalScrollBar().setValue(place)

    @staticmethod
    def _block_format(paragraph: Paragraph, *, first: bool) -> QTextBlockFormat:
        block_format = QTextBlockFormat()
        block_format.setAlignment(_ALIGNMENT[paragraph.alignment])
        block_format.setLineHeight(
            _LINE_HEIGHT_PERCENT, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value
        )
        level = _HEADING_LEVEL.get(paragraph.style)
        if level is not None:
            block_format.setHeadingLevel(level)
        is_display = paragraph.style in _SIZE_SCALE
        if is_display:
            block_format.setTopMargin(0 if first else _GAP_HEADING_ABOVE)
            block_format.setBottomMargin(_GAP_HEADING_BELOW)
        else:
            block_format.setBottomMargin(
                _GAP_LIST_ITEM if paragraph.list_kind is not None else _GAP_PARAGRAPH
            )
        return block_format

    @staticmethod
    def _char_format(
        style: ParagraphStyle, run_styles: frozenset[TextStyle], sizes: dict[ParagraphStyle, int]
    ) -> QTextCharFormat:
        char_format = QTextCharFormat()
        size = sizes.get(style)
        if size is not None:
            char_format.setProperty(QTextFormat.Property.FontPixelSize, size)
        if TextStyle.BOLD in run_styles:
            char_format.setFontWeight(_WEIGHT_BOLD)
        elif style in _SEMIBOLD_STYLES:
            char_format.setFontWeight(_WEIGHT_SEMIBOLD)
        if TextStyle.ITALIC in run_styles:
            char_format.setFontItalic(True)
        if TextStyle.UNDERLINE in run_styles:
            char_format.setFontUnderline(True)
        if TextStyle.STRIKETHROUGH in run_styles:
            char_format.setFontStrikeOut(True)
        return char_format

    @staticmethod
    def _place_in_list(
        cursor: QTextCursor,
        paragraph: Paragraph,
        open_lists: dict[int, tuple[QTextList, ListKind]],
    ) -> None:
        """Make the cursor's block a list item when ``paragraph`` is one.

        A list keeps counting across the items between it and its next sibling
        at the same level, the way a nested numbered list does, so one
        ``QTextList`` is kept open per level. A paragraph that is not a list
        item ends every open list; a shallower item ends the deeper ones, so a
        sub-list that starts again after it starts again from one.
        """
        kind = paragraph.list_kind
        if kind is None:
            open_lists.clear()
            return
        level = paragraph.list_level
        for deeper in [open_level for open_level in open_lists if open_level > level]:
            del open_lists[deeper]
        current = open_lists.get(level)
        if current is not None and current[1] is kind:
            current[0].add(cursor.block())
        else:
            open_lists[level] = (cursor.createList(_list_format(kind, level)), kind)
