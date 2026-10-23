"""Preview dialog: summary cards, a verification badge, and an event timeline.

Everything shown here comes straight from ``preview.describe`` -- this
module does no planning-adjacent computation of its own, only layout.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
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


def _summary_card(title: str, value: str, tokens: Tokens) -> QWidget:
    card = QWidget()
    card.setObjectName("Card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(2)

    value_label = QLabel(value)
    value_label.setStyleSheet("font-size: 18px; font-weight: 600;")
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

    text = QLabel(f"<b>at char {event.at_char}</b> &nbsp;&middot;&nbsp; {event.detail}")
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
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(14)

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
            cards.addWidget(_summary_card(card_title, value, tokens), index // 3, index % 3)
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
                "Mismatch: the planned output does not match your text exactly -- do not use this plan"
            )
        layout.addWidget(self.verification_badge)

        timeline_title = QLabel("Timeline")
        timeline_title.setObjectName("SectionTitle")
        layout.addWidget(timeline_title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        timeline_container = QWidget()
        timeline_layout = QVBoxLayout(timeline_container)
        timeline_layout.setContentsMargins(0, 0, 0, 0)
        timeline_layout.setSpacing(2)
        if events:
            for event in events:
                timeline_layout.addWidget(_event_row(event, tokens))
        else:
            empty_label = QLabel("Nothing notable -- a clean, uninterrupted run.")
            empty_label.setObjectName("MutedLabel")
            timeline_layout.addWidget(empty_label)
        timeline_layout.addStretch(1)
        scroll.setWidget(timeline_container)
        layout.addWidget(scroll, 1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        self.close_button = QPushButton("Close")
        self.close_button.setObjectName("PrimaryButton")
        self.close_button.setDefault(True)
        self.close_button.clicked.connect(self.accept)
        close_row.addWidget(self.close_button)
        layout.addLayout(close_row)
