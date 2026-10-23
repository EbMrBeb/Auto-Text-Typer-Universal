"""Preview dialog: summary cards, a verification badge, and an event timeline.

Everything shown here comes straight from ``preview.describe`` -- this
module does no planning-adjacent computation of its own, only layout.
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from auto_essay_typer.actions import Action
from auto_essay_typer.gui import icons
from auto_essay_typer.gui.controls import focus_ring_bleed
from auto_essay_typer.gui.theme import Tokens
from auto_essay_typer.preview import PreviewEvent, describe
from auto_essay_typer.progress import format_duration_words

#: preview event kind -> icon name (see gui/icons.py).
_EVENT_ICON = {
    "typo": "typo",
    "revision": "revision",
    "pause": "pause-event",
    "break": "break",
}

#: Summary cards sit on a 6-column grid so a row of three and a row of two both
#: fill the full width (a 3-column grid left a hole after the fifth card).
_GRID_COLUMNS = 6


def _summary_card(title: str, value: str, tokens: Tokens) -> QWidget:
    card = QWidget()
    card.setObjectName("Card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(2)

    value_label = QLabel(value)
    value_label.setObjectName("SummaryValue")
    layout.addWidget(value_label)

    title_label = QLabel(title)
    title_label.setObjectName("MutedLabel")
    layout.addWidget(title_label)
    return card


def _event_row(event: PreviewEvent, tokens: Tokens) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(4, 4, 4, 4)
    layout.setSpacing(10)

    icon_label = QLabel()
    icon_label.setPixmap(icons.pixmap(_EVENT_ICON.get(event.kind, "pause-event"), tokens.muted_text, 16))
    icon_label.setFixedWidth(20)
    layout.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)

    # The detail quotes the user's own text ("typed '<' for ','"), so it has to be
    # escaped: left raw, a "<" or "&" in an essay would be read as markup.
    text = QLabel(f"<b>at char {event.at_char}</b> &nbsp;&middot;&nbsp; {html.escape(event.detail)}")
    text.setWordWrap(True)
    text.setTextFormat(Qt.TextFormat.RichText)
    layout.addWidget(text, 1)
    return row


class PreviewDialog(QDialog):
    """Shows what a plan will actually do, before any real key is pressed."""

    def __init__(
        self,
        actions: list[Action],
        text: str,
        tokens: Tokens,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Preview")
        self.resize(560, 640)

        events, summary = describe(actions, text)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        title = QLabel("Preview")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        cards = QGridLayout()
        cards.setSpacing(8)
        card_specs = [
            ("Duration", format_duration_words(summary.duration_ms / 1000)),
            ("Effective speed", f"{summary.effective_wpm:.0f} wpm"),
            ("Mistakes corrected", str(summary.typo_count)),
            ("Revisions", str(summary.revision_count)),
            ("Breaks", str(summary.break_count)),
        ]
        for index, (card_title, value) in enumerate(card_specs):
            row, position = divmod(index, 3) if index < 3 else (1, index - 3)
            span = _GRID_COLUMNS // (3 if row == 0 else 2)
            cards.addWidget(_summary_card(card_title, value, tokens), row, position * span, 1, span)
        for column in range(_GRID_COLUMNS):
            cards.setColumnStretch(column, 1)
        layout.addLayout(cards)

        self.verification_badge = QLabel()
        self.verification_badge.setObjectName("StatusBadge")
        self.verification_badge.setWordWrap(True)
        if summary.matches_input:
            self.verification_badge.setProperty("tone", "success")
            self.verification_badge.setText("Output verified: matches your text exactly")
        else:
            self.verification_badge.setProperty("tone", "danger")
            self.verification_badge.setText(
                "Mismatch: the planned output does not match your text exactly — do not use this plan"
            )
        layout.addWidget(self.verification_badge)

        timeline_title = QLabel("Timeline")
        timeline_title.setObjectName("SectionTitle")
        layout.addWidget(timeline_title)

        scroll = QScrollArea()
        scroll.setObjectName("Timeline")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Focusable on purpose: it is how a keyboard user scrolls the list. The
        # ring (see theme.py) is what keeps that focus visible.
        scroll.setAccessibleName("Timeline of planned mistakes, revisions and pauses")
        timeline_title.setBuddy(scroll)
        timeline_container = QWidget()
        timeline_layout = QVBoxLayout(timeline_container)
        timeline_layout.setContentsMargins(0, 0, 0, 0)
        timeline_layout.setSpacing(2)
        if events:
            for event in events:
                timeline_layout.addWidget(_event_row(event, tokens))
        else:
            empty_label = QLabel("Nothing notable — a clean, uninterrupted run.")
            empty_label.setObjectName("MutedLabel")
            timeline_layout.addWidget(empty_label)
        timeline_layout.addStretch(1)
        scroll.setWidget(timeline_container)
        # The focus ring's 2px border sits outside the dialog margin, so the rows
        # stay aligned with the cards above (same trick as the Help page).
        layout.addLayout(focus_ring_bleed(scroll), 1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        self.close_button = QPushButton("Close")
        self.close_button.setObjectName("PrimaryButton")
        self.close_button.setAccessibleName("Close preview")
        self.close_button.setDefault(True)
        self.close_button.clicked.connect(self.accept)
        close_row.addWidget(self.close_button)
        layout.addLayout(close_row)
