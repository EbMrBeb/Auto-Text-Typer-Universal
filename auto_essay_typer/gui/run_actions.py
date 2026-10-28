"""What the tray menu and the global hotkeys do to a typing run.

Both exist for the moments the main window is out of the way, so they share this one
object, and through it the same :class:`~auto_essay_typer.gui.run_controller.RunController`
the Type page drives. A run started from the tray or a hotkey goes through the same checks
as one started from the Type page's Start button, in the same order:

1. nothing is already running;
2. there is text to type (the clipboard, for the tray; the Type page, for a hotkey), taken
   through :func:`~auto_essay_typer.platforms.normalize_input_text` like every other text
   that comes from outside the app;
3. the OS lets this process type into other apps -- if not, the same permission dialog
   the Type page opens;
4. if "Confirm before typing" is on, the user says yes to a dialog that names where the
   text came from and how much of it there is. This one matters most here: nothing about a
   background start puts the text in front of the user, and a hotkey is easy to press by
   accident. (The Type page's own Start button shows the text and counts down with a Stop
   button, so it does not ask.)

The checks that *fail* do so quietly through ``notify`` (a tray balloon and the status
bar), never a dialog the user did not ask for -- except permission, where the dialog is the
point. Everything with a side effect is injectable, so tests drive this with fakes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import StrEnum

from PySide6.QtCore import QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QMessageBox, QWidget

from auto_essay_typer import platforms
from auto_essay_typer.gui.main_window import MainWindow
from auto_essay_typer.gui.permission_dialog import PermissionDialog
from auto_essay_typer.platforms import PermissionStatus

logger = logging.getLogger(__name__)

APP_TITLE = "Auto Text Typer"


class StartOutcome(StrEnum):
    """How a request to start typing ended; what the tests (and a caller that cares) look at."""

    #: Every check passed and the run was handed to the run controller. Whether it then works
    #: (a keyboard that cannot be opened, a planning error) is reported the way it is for any run:
    #: through the controller's ``finished`` signal, which the Type page and the tray both show.
    #: It is deliberately not "is it running now": a short run can be over before this returns.
    STARTED = "started"
    BUSY = "busy"  # a run is already in flight, or a prompt for another start is still open
    EMPTY = "empty"  # nothing to type
    NO_PERMISSION = "no-permission"  # the OS would not let the app type, and still will not
    DECLINED = "declined"  # the user said no to the confirmation


class StartSource(StrEnum):
    CLIPBOARD = "clipboard"
    TYPE_PAGE = "type-page"


_SOURCE_LABEL = {
    StartSource.CLIPBOARD: "on the clipboard",
    StartSource.TYPE_PAGE: "on the Type page",
}

_EMPTY_MESSAGE = {
    StartSource.CLIPBOARD: "There is no text on the clipboard. Copy the text you want typed, then try again.",
    StartSource.TYPE_PAGE: (
        "The Type page is empty. Paste or type some text there first, or copy it and use "
        "Start typing from clipboard in the tray menu."
    ),
}


def default_clipboard_text() -> str:
    """The plain text on the system clipboard (``""`` when there is none)."""
    clipboard = QGuiApplication.clipboard()
    return clipboard.text() if clipboard is not None else ""


def default_confirm_start(parent: QWidget | None, title: str, text: str) -> bool:
    """Ask whether to go ahead, with buttons that say what they do (not Yes and No)."""
    box = QMessageBox(QMessageBox.Icon.Question, title, text, QMessageBox.StandardButton.NoButton, parent)
    start = box.addButton("Start typing", QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(start)
    box.setEscapeButton(cancel)
    box.exec()
    return box.clickedButton() is start


class RunActions(QObject):
    """Start, pause/resume and stop a run on behalf of the tray and the hotkeys.

    ``clipboard_text``, ``confirm`` (``(parent, title, text) -> bool``), ``permission_prompt``
    (``(parent, status) -> bool``: show the permission dialog, return whether permission ended
    up granted) and ``notify`` (``(title, message)``) are the injectable side effects; the
    defaults are the real clipboard, message box, permission dialog and the window's status bar.
    """

    def __init__(
        self,
        window: MainWindow,
        *,
        clipboard_text: Callable[[], str] = default_clipboard_text,
        confirm: Callable[[QWidget | None, str, str], bool] = default_confirm_start,
        permission_prompt: Callable[[QWidget | None, PermissionStatus], bool] | None = None,
        notify: Callable[[str, str], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent if parent is not None else window)
        self._window = window
        self._clipboard_text = clipboard_text
        self._confirm = confirm
        self._permission_prompt = permission_prompt if permission_prompt is not None else self._default_permission_prompt
        self._notify = notify if notify is not None else self._notify_in_window
        #: True while a permission or confirmation dialog is open, so a second request (a
        #: hotkey pressed twice) cannot stack another dialog on top of it.
        self._prompting = False

    # -- starting -----------------------------------------------------------------------

    def start_from_clipboard(self) -> StartOutcome:
        """Type what is on the clipboard (the tray's "Start typing from clipboard")."""
        text = platforms.normalize_input_text(self._clipboard_text())
        return self._start(text, StartSource.CLIPBOARD)

    def start_from_type_page(self) -> StartOutcome:
        """Type what is on the Type page (the start hotkey), exactly as its Start button would."""
        return self._start(self._window.type_page.normalized_text(), StartSource.TYPE_PAGE)

    def _start(self, text: str, source: StartSource) -> StartOutcome:
        controller = self._window.run_controller
        if controller.is_busy or self._prompting:
            self._tell("Typing is already in progress." if controller.is_busy else "Waiting for your answer first.")
            return StartOutcome.BUSY
        if not text.strip():
            self._tell(_EMPTY_MESSAGE[source])
            return StartOutcome.EMPTY

        self._prompting = True
        try:
            if not self._has_permission():
                return StartOutcome.NO_PERMISSION
            settings = self._window.settings_store.settings
            if settings.confirm_before_typing and not self._confirm(
                self._dialog_parent(), "Start typing?", self._confirmation_text(text, source, settings.countdown_seconds)
            ):
                return StartOutcome.DECLINED
        finally:
            self._prompting = False

        page = self._window.type_page
        if source is StartSource.CLIPBOARD:
            controller.start(text, page.effective_profile(), seed=None)
        else:
            # The page's own Start path: it shows the run in its panel and handles whatever it
            # holds. Its checks (text, permission) pass again here, silently.
            page.start_button.click()
        return StartOutcome.STARTED

    @staticmethod
    def _confirmation_text(text: str, source: StartSource, countdown_seconds: int) -> str:
        count = f"{len(text):,} character{'s' if len(text) != 1 else ''}"
        if countdown_seconds > 0:
            when = (
                f"Typing starts after a {countdown_seconds} second countdown, so click where the "
                "text should go."
            )
        else:
            when = "Typing starts at once, into whichever window has focus."
        return f"Type the {count} {_SOURCE_LABEL[source]}?\n\n{when}"

    # -- permission ------------------------------------------------------------------------

    def _has_permission(self) -> bool:
        status = self._window.check_permission()
        if status.granted:
            return True
        return bool(self._permission_prompt(self._dialog_parent(), status))

    def _default_permission_prompt(self, parent: QWidget | None, status: PermissionStatus) -> bool:
        dialog = PermissionDialog(
            status,
            self._window.theme_manager.tokens,
            permission_checker=self._window.check_permission,
            parent=parent,
        )
        dialog.exec()
        return dialog.granted()

    def _dialog_parent(self) -> QWidget | None:
        """The window, if it is on screen (a hidden or minimised parent would hide its dialog's home)."""
        window = self._window
        return window if window.isVisible() and not window.isMinimized() else None

    # -- pausing and stopping -------------------------------------------------------------

    def toggle_pause(self) -> None:
        """Pause a run that is typing, or resume one that is paused; anything else is ignored."""
        controller = self._window.run_controller
        if controller.state == "paused":
            controller.resume()
        elif controller.state == "typing":
            controller.pause()

    def stop(self) -> None:
        """Stop the run in flight (safe with nothing running)."""
        self._window.run_controller.stop()

    def stop_if_running(self) -> None:
        """Stop only when a run is in flight: what the stop *hotkey* does.

        The default stop hotkey is Escape, pressed for many unrelated reasons in any app, so
        it must be a no-op unless this app is actually typing.
        """
        if self._window.run_controller.is_busy:
            self._window.run_controller.stop()

    # -- telling the user ----------------------------------------------------------------------

    def _tell(self, message: str) -> None:
        self._notify(APP_TITLE, message)

    def _notify_in_window(self, _title: str, message: str) -> None:
        self._window.show_status_message(message)
