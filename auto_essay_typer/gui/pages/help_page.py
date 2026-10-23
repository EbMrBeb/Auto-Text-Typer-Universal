"""The Help page: how to use the app, per-OS permission notes, and about info.

Built around :meth:`HelpPage.add_section` so new sections (a changelog, more
FAQ entries, ...) can be appended later without restructuring this page.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from auto_essay_typer.platforms import OperatingSystem, current_os

_HOW_TO_USE = (
    "<ol style='margin:0; padding-left:18px;'>"
    "<li>Paste or type the text you want typed for you.</li>"
    "<li>Press <b>Start typing</b> (or Ctrl/Cmd+Return).</li>"
    "<li>During the countdown, click into the document or field where the "
    "text should go.</li>"
    "<li>Keep your hands off the mouse and keyboard while it types.</li>"
    "<li>Press <b>Esc</b>, or the <b>Stop</b> button, at any time to end the run early.</li>"
    "</ol>"
)

_PERMISSION_HELP = {
    OperatingSystem.MACOS: (
        "macOS requires Accessibility permission before Auto Text Typer can type into "
        "other apps. Grant it in <b>System Settings &gt; Privacy &amp; Security &gt; "
        "Accessibility</b>, then quit and reopen Auto Text Typer -- macOS does not apply "
        "the change to an already-running app."
    ),
    OperatingSystem.WINDOWS: (
        "Windows needs no extra permission. If the app you're typing into is running as "
        "administrator, run Auto Text Typer as administrator too, or it won't be able to "
        "reach it."
    ),
    OperatingSystem.LINUX: (
        "On Linux, typing into other windows works under an X11 (Xorg) session. Wayland "
        "sessions block it outright, with no permission to grant -- log out and choose an "
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
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(16)

        title = QLabel("Help")
        title.setObjectName("PageTitle")
        outer.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._sections_container = QWidget()
        self._sections_layout = QVBoxLayout(self._sections_container)
        self._sections_layout.setContentsMargins(0, 0, 0, 0)
        self._sections_layout.setSpacing(12)
        self._sections_layout.addStretch(1)
        scroll.setWidget(self._sections_container)
        outer.addWidget(scroll, 1)

        self.add_section("How to use", _HOW_TO_USE)
        os_ = current_os()
        self.add_section(f"Permission on {_OS_LABEL[os_]}", _PERMISSION_HELP[os_])
        self.add_section("About", f"Auto Text Typer {version}<br>MIT License")

    def add_section(self, title: str, body_html: str) -> None:
        """Append a new section card, above the trailing stretch. ``body_html``
        is rendered as rich text, so simple markup (``<b>``, ``<ol>``, ...) works."""
        card = QWidget()
        card.setObjectName("Card")
        card.setMaximumWidth(680)  # keeps prose lines a comfortable reading length
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

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
