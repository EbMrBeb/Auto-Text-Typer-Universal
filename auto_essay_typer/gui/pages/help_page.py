"""The Help page: how to use the app, importing a document, which editor it is
typing into, per-OS permission notes, and about info.

Built around :meth:`HelpPage.add_section` so new sections (a changelog, more
FAQ entries, ...) can be appended later without restructuring this page.
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from auto_essay_typer.gui.controls import focus_ring_bleed
from auto_essay_typer.gui.labels import WrappingLabel
from auto_essay_typer.platforms import OperatingSystem, current_os
from auto_essay_typer.targets import targets_for

#: Rich-text list used by the sections below. Qt's rich text has no stylesheet
#: for list items, so spacing is set inline: a little air between items and a
#: line height that reads comfortably at 13px.
_LIST_STYLE = "margin:0; padding-left:18px;"
_ITEM_STYLE = "margin-bottom:5px; line-height:140%;"


def _ordered(*items: str) -> str:
    body = "".join(f"<li style='{_ITEM_STYLE}'>{item}</li>" for item in items)
    return f"<ol style='{_LIST_STYLE}'>{body}</ol>"


def _unordered(*items: str) -> str:
    body = "".join(f"<li style='{_ITEM_STYLE}'>{item}</li>" for item in items)
    return f"<ul style='{_LIST_STYLE}'>{body}</ul>"


def _paragraph(text: str) -> str:
    return f"<p style='margin:0; line-height:140%;'>{text}</p>"


_HOW_TO_USE = _ordered(
    "Paste or type the text you want typed for you.",
    "Press <b>Start typing</b> (or Ctrl/Cmd+Return).",
    "During the countdown, click into the document or field where the text should go.",
    "Keep your hands off the mouse and keyboard while it types.",
    "Press <b>Esc</b>, or the <b>Stop</b> button, at any time to end the run early.",
)

_IMPORT = _ordered(
    "Paste the link to a Google Doc into the box at the top of the Type page, or press "
    "<b>Browse…</b> and pick a <b>.docx</b> or <b>.html</b> file on this computer (a plain "
    "<b>.txt</b> or <b>.md</b> file works too).",
    "Press <b>Import</b>. The document arrives with its real formatting — its headings, bold, "
    "italic and lists — and that is what gets typed, not just its words.",
    "<b>What will not be reproduced</b>, under the document, lists anything that will not make "
    "it across: a picture, a table, a list the editor you chose has no shortcut for. It is there "
    "to read before you start, not after.",
    "<b>Back to plain text</b> brings the text box back, with the document's words in it to edit "
    "as you like. Its formatting is dropped.",
)

_EDITOR_INTRO = (
    "<b>Typing into</b>, in <b>Quick settings</b> on the Type page, is the editor the keystrokes "
    "are going to. To put a document's formatting back the app presses that editor's own keyboard "
    "shortcuts, so choosing the wrong one presses the wrong keys and types something you did not "
    "ask for. This computer offers:"
)

_GOOGLE_DOCS = _ordered(
    "<b>Share the document before you import it.</b> It has to be shared so that "
    "<b>Anyone with the link can view</b> it, because the app opens the link the way a stranger "
    "would. If only you can open it, download it instead (<b>File &gt; Download &gt; Microsoft "
    "Word (.docx)</b>) and import that file.",
    "<b>Turn Google Docs' own corrections off before typing into it</b>, under <b>Tools &gt; "
    "Preferences</b>: smart quotes, automatic capitalisation and automatic substitution. They "
    "rewrite text after it arrives — straight quotes curl, a sentence gains a capital, two "
    "hyphens become a dash — so what ends up in the document is not quite what you gave the app.",
)

_KEYBOARD = _ordered(
    "<b>Ctrl/Cmd+Return</b> starts typing from the Type page.",
    "<b>Esc</b> stops a run in progress, and closes any dialog.",
    "<b>Ctrl/Cmd+1</b> to <b>4</b> switch between Type, Settings, Advanced and Help.",
    "<b>Tab</b> and <b>Shift+Tab</b> move between controls; <b>Space</b> presses a button "
    "or flips a switch, and the arrow keys move a slider.",
)

_ANYWHERE = _ordered(
    "The tray icon (the menu bar on macOS) has <b>Start typing from clipboard</b>, "
    "<b>Pause</b> or <b>Resume</b>, <b>Stop</b>, <b>Show window</b> and <b>Quit</b>, so a run can "
    "be controlled while this window is out of the way. Not every Linux desktop has a tray.",
    "The <b>global hotkeys</b> do the same from any app: one starts typing what is on the Type "
    "page, one pauses and resumes, and <b>Esc</b> stops a run that is typing. Turn them on or off, "
    "and change them, in Settings.",
    "<b>Settings &gt; Confirm before typing from the tray or a hotkey</b> asks first, because the "
    "text is not in front of you then.",
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


def _editors() -> str:
    """The "Typing into" section's body: the editors this computer offers, each with its own note.

    Taken from the target registry rather than written out here, so the list is
    the picker's own -- which editors a computer offers differs (Microsoft Word
    has no Linux release, TextEdit is macOS only) -- and each editor's note is
    the same sentence that is shown under the picker itself.
    """
    items = tuple(
        f"<b>{html.escape(target.name, quote=False)}</b> — {html.escape(target.notes, quote=False)}"
        for target in targets_for(current_os())
    )
    return _paragraph(_EDITOR_INTRO) + _unordered(*items)


class HelpPage(QWidget):
    """Sections are appended top-to-bottom, above a trailing stretch that keeps
    a short page from looking like it's centered oddly in a tall window.

    Signals
    -------
    showSetupRequested()
        The "Show setup again" button was pressed: the owner re-opens the first-run
        walkthrough (the page itself knows nothing about it).
    """

    showSetupRequested = Signal()

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
        self.add_section("Importing a document", _IMPORT)
        self.add_section("Typing into", _editors())
        self.add_section("Google Docs", _GOOGLE_DOCS)
        self.add_section("Keyboard", _KEYBOARD)
        self.add_section("From the tray or a hotkey", _ANYWHERE)
        os_ = current_os()
        self.add_section(f"Permission on {_OS_LABEL[os_]}", _PERMISSION_HELP[os_])

        self.show_setup_button = QPushButton("Show setup again")
        self.show_setup_button.setObjectName("SecondaryButton")
        self.show_setup_button.setAccessibleName("Show setup again")
        self.show_setup_button.setToolTip("Walk through the welcome steps again.")
        self.show_setup_button.clicked.connect(lambda _checked=False: self.showSetupRequested.emit())
        self.add_section(
            "Setup",
            _paragraph(
                "Run the welcome steps again: allowing typing into other apps, and a practice "
                "run in a box that cannot touch your documents."
            ),
            button=self.show_setup_button,
        )
        self.add_section("About", _paragraph(f"Auto Text Typer {version}<br>MIT License"))

    def add_section(self, title: str, body_html: str, *, button: QPushButton | None = None) -> None:
        """Append a new section card, above the trailing stretch. ``body_html``
        is rendered as rich text, so simple markup (``<b>``, ``<ol>``, ...) works.
        ``button``, if given, sits left-aligned under the text."""
        card = QWidget()
        card.setObjectName("Card")
        card.setMaximumWidth(680)  # keeps prose lines a comfortable reading length
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        title_label = QLabel(title)
        title_label.setObjectName("SectionTitle")
        layout.addWidget(title_label)

        # A WrappingLabel, not a plain wrapped QLabel: a wrapped label promises only
        # one line, and a long section (the editor list) is then handed a card too
        # short for it and has its last lines cut off. This one asks for the height
        # its text needs at the width it has been given.
        body_label = WrappingLabel(body_html)
        body_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(body_label)

        if button is not None:
            layout.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)

        # The layout's last item is always the trailing stretch (added once,
        # in __init__) -- insert new sections just above it.
        self._sections_layout.insertWidget(self._sections_layout.count() - 1, card)
