"""The application shell: the tray, the global hotkeys and the first-run walkthrough, wired to the window.

``MainWindow`` is the UI; this is everything around it that lives while the window is out
of the way. It builds the pieces and connects them:

* the **tray icon** (:class:`~auto_essay_typer.gui.tray.AppTray`) -- its menu entries drive
  the same run controller the Type page does, through :class:`~auto_essay_typer.gui.run_actions.RunActions`;
* the **global hotkeys** (the window's :class:`~auto_essay_typer.gui.hotkeys.HotkeyService`) --
  started only when ``global_hotkeys_enabled`` is on, restarted when the setting or a hotkey
  changes, stopped when the app quits, and driving the same actions as the tray;
* the **first-run walkthrough** -- opened when ``onboarding_complete`` is false.

Nothing here is allowed to take the app down. A platform with no tray gets none, and says
nothing; hotkeys that cannot start leave a quiet note in the window's footer (and a reason
on the Settings page) and the app carries on. Hotkey presses arrive as Qt signals queued
onto the GUI thread, never as calls from the listener thread, and are connected to methods
of objects that live there.

On a first run the hotkeys start only after the walkthrough has been closed. Starting them
can make macOS ask for Input Monitoring access, and a system prompt over a welcome screen
that has not yet said what the app does is a poor first impression.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QCoreApplication, QObject, Qt

from auto_essay_typer.gui.main_window import MainWindow
from auto_essay_typer.gui.run_actions import APP_TITLE, RunActions
from auto_essay_typer.gui.tray import AppTray

logger = logging.getLogger(__name__)

_FINISH_MESSAGES = {
    "finished": "Finished typing.",
    "stopped": "Typing stopped.",
}


class AppShell(QObject):
    """See the module docstring. ``tray``, ``actions`` and ``quit_app`` are injectable for tests."""

    def __init__(
        self,
        window: MainWindow,
        *,
        tray: AppTray | None = None,
        actions: RunActions | None = None,
        quit_app: Callable[[], None] = QCoreApplication.quit,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent if parent is not None else window)
        self._window = window
        self._quit_app = quit_app
        #: Set by :meth:`start`: until then settings changes do not touch the hotkeys.
        self._started = False

        self.tray = tray if tray is not None else AppTray(
            window.run_controller, theme_manager=window.theme_manager, parent=self
        )
        self.actions = actions if actions is not None else RunActions(window, notify=self._notify, parent=self)

        # The tray: every entry is a signal, and every receiver is a method of a GUI-thread object.
        self.tray.showWindowRequested.connect(window.bring_to_front)
        self.tray.startFromClipboardRequested.connect(self.actions.start_from_clipboard)
        self.tray.pauseResumeRequested.connect(self.actions.toggle_pause)
        self.tray.stopRequested.connect(self.actions.stop)
        self.tray.quitRequested.connect(self.quit)

        # The hotkeys fire on the listener's thread and arrive here as queued signals: the connection
        # type is spelled out so that this holds even for a signal emitted on the GUI thread.
        service = window.hotkey_service
        queued = Qt.ConnectionType.QueuedConnection
        service.startRequested.connect(self.actions.start_from_type_page, queued)
        service.pauseRequested.connect(self.actions.toggle_pause, queued)
        service.stopRequested.connect(self.actions.stop_if_running, queued)

        window.settings_store.changed.connect(self._on_settings_changed)
        window.onboardingFinished.connect(self._apply_hotkeys)
        window.run_controller.finished.connect(self._on_run_finished)

    # -- life cycle -------------------------------------------------------------------------

    @property
    def started(self) -> bool:
        """Whether :meth:`start` has run."""
        return self._started

    def start(self) -> None:
        """Bring the tray up, then either run first-time setup or start the hotkeys.

        Call once, after the window is shown. Never raises: a tray that cannot be built and
        hotkeys that cannot start have already degraded in their own classes.
        """
        self._started = True
        self.tray.show()
        if not self._window.maybe_show_onboarding():
            self._apply_hotkeys()
        # else: onboardingFinished (connected above) starts them once the walkthrough closes.

    def shutdown(self) -> None:
        """Take the tray icon down and stop the hotkey listener and any run. Safe to repeat."""
        self.tray.hide()
        self._window.hotkey_service.stop()
        self._window.run_controller.stop()

    def quit(self) -> None:
        """The tray's Quit: stop typing, close the window (saving its geometry) and end the app."""
        self._window.run_controller.stop()
        self._window.close()
        self._quit_app()

    # -- hotkeys ----------------------------------------------------------------------------------

    def _apply_hotkeys(self) -> None:
        """Make the listener match the saved settings. Idempotent, and never raises."""
        if not self._started:
            return
        settings = self._window.settings_store.settings
        try:
            self._window.hotkey_service.apply(settings.global_hotkeys_enabled, settings.hotkeys)
        except Exception:  # noqa: BLE001 - the service never raises; this is belt and braces
            logger.exception("Could not apply the global hotkey settings")

    def _on_settings_changed(self, _settings: object) -> None:
        self._apply_hotkeys()

    # -- telling the user ----------------------------------------------------------------------------

    def _notify(self, title: str, message: str) -> None:
        """A start that could not go ahead: a tray balloon (if there is a tray) and the footer."""
        self.tray.notify(title, message)
        self._window.show_status_message(message)

    def _on_run_finished(self, outcome: str, message: str) -> None:
        # Only worth a balloon when the window is not where the user is looking.
        if self._window.isActiveWindow():
            return
        text = _FINISH_MESSAGES.get(outcome) or f"Typing failed. {message}".strip()
        self.tray.notify(APP_TITLE, text)
