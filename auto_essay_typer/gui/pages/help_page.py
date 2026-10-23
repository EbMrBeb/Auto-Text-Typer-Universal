"""The Help page: how to use the app, per-OS permission notes, and about info.

Built around :meth:`HelpPage.add_section` so new sections (a changelog, more
FAQ entries, ...) can be appended later without restructuring this page.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from auto_essay_typer.gui.controls import focus_ring_bleed
from auto_essay_typer.platforms import OperatingSystem, current_os

#: Rich-text list used by the sections below. Qt's rich text has no stylesheet
#: for list items, so spacing is set inline: a little air between items and a
#: line height that reads comfortably at 13px.
_LIST_STYLE = "margin:0; padding-left:18px;"
_ITEM_STYLE = "margin-bottom:5px; line-height:140%;"


def _ordered(*items: str) -> str:
    body = "".join(f"<li style='{_ITEM_STYLE}'>{item}</li>" for item in items)
    return f"<ol style='{_LIST_STYLE}'>{body}</ol>"


def _paragraph(text: str) -> str:
    return f"<p style='margin:0; line-height:140%;'>{text}</p>"


_HOW_TO_USE = _ordered(
    "Paste or type the text you want typed for you.",
    "Press <b>Start typing</b> (or Ctrl/Cmd+Return).",
    "During the countdown, click into the document or field where the text should go.",
    "Keep your hands off the mouse and keyboard while it types.",
    "Press <b>Esc</b>, or the <b>Stop</b> button, at any time to end the run early.",
)

_KEYBOARD = _ordered(
    "<b>Ctrl/Cmd+Return</b> starts typing from the Type page.",
    "<b>Esc</b> stops a run in progress, and closes any dialog.",
    "<b>Ctrl/Cmd+1</b>, <b>2</b> and <b>3</b> switch between Type, Settings and Help.",
    "<b>Tab</b> and <b>Shift+Tab</b> move between controls; <b>Space</b> presses a button "
    "or flips a switch, and the arrow keys move a slider.",
)

_PERMISSION_HELP = {
    OperatingSystem.MACOS: _paragraph(
        "macOS requires Accessibility permission before Auto Text Typer can type into "
        "other apps. Grant it in <b>System Settings &gt; Privacy &amp; Security &gt; "
        "Accessibility</b>, then quit and reopen Auto Text Typer — macOS does not apply "
        "the change to an already-running app."
    ),
    OperatingSystem.WINDOWS: _paragraph(
        "Windows needs no extra permission. If the app you're typing into is running as "
        "administrator, run Auto Text Typer as administrator too, or it won't be able to "
        "reach it."
    ),
    OperatingSystem.LINUX: _paragraph(
        "On Linux, typing into other windows works under an X11 (Xorg) session. Wayland "
        "sessions block it outright, with no permission to grant — log out and choose an "
        "X11/Xorg session at the login screen if nothing gets typed."
    ),
}

_OS_LABEL = {
    OperatingSystem.MACOS: "macOS",
    OperatingSystem.WINDOWS: "Windows",
    OperatingSystem.LINUX: "Linux",
}


class HelpPage(QWidget):
    """Sections are appended top-to-bottom, above a trailing stretch that keeps
    a short page from looking like it's centered oddly in a tall window."""

    def __init__(self, version: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(16)

        title = QLabel("Help")
        title.setObjectName("PageTitle")
        outer.addWidget(title)

        scroll = QScrollArea()
        scroll.setObjectName("PageScroll")  # draws the keyboard focus ring (see theme.py)
        scroll.setAccessibleName("Help sections")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._sections_container = QWidget()
        self._sections_layout = QVBoxLayout(self._sections_container)
        self._sections_layout.setContentsMargins(0, 0, 0, 0)
        self._sections_layout.setSpacing(12)
        self._sections_layout.addStretch(1)
        scroll.setWidget(self._sections_container)
        # The scroll area draws its keyboard focus ring in a 2px border, which would
        # inset the cards inside it; let that border sit outside the page margin so
        # they still line up with the title above.
        outer.addLayout(focus_ring_bleed(scroll), 1)

        self.add_section("How to use", _HOW_TO_USE)
        self.add_section("Keyboard", _KEYBOARD)
        os_ = current_os()
        self.add_section(f"Permission on {_OS_LABEL[os_]}", _PERMISSION_HELP[os_])
        self.add_section("About", _paragraph(f"Auto Text Typer {version}<br>MIT License"))

    def add_section(self, title: str, body_html: str) -> None:
        """Append a new section card, above the trailing stretch. ``body_html``
        is rendered as rich text, so simple markup (``<b>``, ``<ol>``, ...) works."""
        card = QWidget()
        card.setObjectName("Card")
        card.setMaximumWidth(680)  # keeps prose lines a comfortable reading length
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        title_label = QLabel(title)
        title_label.setObjectName("SectionTitle")
        layout.addWidget(title_label)

        body_label = QLabel(body_html)
        body_label.setWordWrap(True)
        body_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(body_label)

        # The layout's last item is always the trailing stretch (added once,
        # in __init__) -- insert new sections just above it.
        self._sections_layout.insertWidget(self._sections_layout.count() - 1, card)
