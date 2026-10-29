"""The two self-contained panels the Type page shows around an imported document.

* :class:`ImportBar` -- the row above the text box where a link or file is
  chosen, with the busy and error states of an import.
* :class:`WarningsPanel` -- the "What will not be reproduced" card.

Both are views: they hold no import or document logic. The page decides what
an import does and what the panel lists; these only show it and report what the
user asked for.
"""

from __future__ import annotations

import html
from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from auto_essay_typer.gui.a11y import announce
from auto_essay_typer.gui.document_import import clean_source
from auto_essay_typer.gui.labels import WrappingLabel

#: Spacing for the bulleted list in the warnings panel (Qt's rich text has no
#: stylesheet for list items, so it is set inline).
_WARNING_LIST_STYLE = "margin-left:14px; margin-top:0; margin-bottom:0; -qt-list-indent:0;"
_WARNING_ITEM_STYLE = "margin-bottom:3px; line-height:128%;"


class ImportBar(QWidget):
    """A link or file to import a document from: a text box, Import and Browse…

    Signals
    -------
    importRequested(str)
        The user asked to import, and ``source`` -- tidied by
        :func:`~auto_essay_typer.gui.document_import.clean_source` -- is
        not empty. (An empty box is answered here, with a message, and nothing
        is emitted.)
    browseRequested()
        The user asked to pick a file; the page decides what to do about it.
    """

    importRequested = Signal(str)
    browseRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Paste a Google Docs link, or choose a file")
        self.edit.setAccessibleName("Document link or file")
        self.edit.setToolTip(
            "A Google Docs or Google Drive link, or the location of a .docx, .html, .zip, "
            ".txt or .md file. The document is typed with its formatting."
        )
        self.edit.returnPressed.connect(self.request_import)
        row.addWidget(self.edit, 1)

        self.import_button = QPushButton("Import")
        self.import_button.setObjectName("SecondaryButton")
        self.import_button.setAccessibleName("Import document")
        self.import_button.setToolTip(
            "Read the document and show it here with its headings, bold, alignment and lists."
        )
        self.import_button.clicked.connect(self.request_import)
        row.addWidget(self.import_button)

        self.browse_button = QPushButton("Browse…")
        self.browse_button.setObjectName("SecondaryButton")
        self.browse_button.setAccessibleName("Browse for a document file")
        self.browse_button.setToolTip("Choose a .docx, .html, .zip, .txt or .md file on this computer.")
        self.browse_button.clicked.connect(self.browseRequested)
        row.addWidget(self.browse_button)
        layout.addLayout(row)

        # Busy state: shown only while a document is being read, so an idle bar
        # gives it no room.
        self.busy_widget = QWidget()
        busy_layout = QVBoxLayout(self.busy_widget)
        busy_layout.setContentsMargins(0, 0, 0, 0)
        busy_layout.setSpacing(4)
        self.status_label = QLabel("Importing your document…")
        self.status_label.setObjectName("MutedLabel")
        busy_layout.addWidget(self.status_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setAccessibleName("Import progress")
        self.progress_bar.setAccessibleDescription("Importing your document")
        busy_layout.addWidget(self.progress_bar)
        self.busy_widget.setVisible(False)
        layout.addWidget(self.busy_widget)

        self.message_label = WrappingLabel("")
        self.message_label.setObjectName("InlineMessage")
        self.message_label.setProperty("tone", "danger")
        self.message_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.message_label.setVisible(False)
        layout.addWidget(self.message_label)

    # -- what was asked for ---------------------------------------------------------------------

    def source(self) -> str:
        """What is in the box, tidied for the importer."""
        return clean_source(self.edit.text())

    def set_source(self, text: str) -> None:
        self.edit.setText(text)

    def request_import(self) -> None:
        """Ask for the box's contents to be imported (what Import and Enter do)."""
        source = self.source()
        if not source:
            self.show_error("Paste a Google Docs link or choose a file first.")
            return
        self.clear_error()
        self.importRequested.emit(source)

    # -- states -------------------------------------------------------------------------------------

    def set_busy(self, busy: bool) -> None:
        """Show (or hide) the "importing" progress."""
        self.busy_widget.setVisible(busy)
        if busy:
            announce(self.status_label, "Importing your document", False)

    def set_controls_enabled(self, enabled: bool) -> None:
        """Turn the box and both buttons on or off together."""
        for control in (self.edit, self.import_button, self.browse_button):
            control.setEnabled(enabled)

    def show_error(self, message: str) -> None:
        self.message_label.setText(message)
        self.message_label.setVisible(True)
        # Focus stays where it was, so a screen reader would never reach this text itself.
        announce(self.message_label, message, True)

    def clear_error(self) -> None:
        self.message_label.setVisible(False)


class WarningsPanel(QWidget):
    """The "What will not be reproduced" card.

    :meth:`show_lines` fills it with one plain-language line per thing that is
    lost, or -- when nothing is -- a plain good-news sentence instead.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        self.title_label = QLabel("What will not be reproduced")
        self.title_label.setObjectName("SectionTitle")
        layout.addWidget(self.title_label)

        # The list, when there is something to say ...
        self.list_label = WrappingLabel("")
        self.list_label.setTextFormat(Qt.TextFormat.RichText)
        self.list_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.list_label)

        # ... and, when there is not, a plain good-news line.
        self.none_label = WrappingLabel("")
        self.none_label.setObjectName("StatusBadge")
        self.none_label.setProperty("tone", "success")
        layout.addWidget(self.none_label)

        self._lines: tuple[str, ...] = ()
        self.show_lines((), "the editor")

    def lines(self) -> tuple[str, ...]:
        """What is listed now, one entry per line (empty when nothing is lost)."""
        return self._lines

    def show_lines(self, lines: Sequence[str], editor: str) -> None:
        """List ``lines``; if there are none, say that everything can be reproduced in ``editor``."""
        self._lines = tuple(lines)
        if self._lines:
            items = "".join(
                f"<li style='{_WARNING_ITEM_STYLE}'>{html.escape(line, quote=False)}</li>" for line in self._lines
            )
            self.list_label.setText(f"<ul style='{_WARNING_LIST_STYLE}'>{items}</ul>")
        self.list_label.setVisible(bool(self._lines))
        # "Everything the app could read", not "everything in the document": this reports
        # what the importer noticed, and a document can hold things it cannot see.
        self.none_label.setText(
            f"No problems found. Everything the app could read from this document can be "
            f"reproduced in {editor}."
        )
        self.none_label.setVisible(not self._lines)
