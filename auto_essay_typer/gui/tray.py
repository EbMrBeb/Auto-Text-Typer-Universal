"""The system tray icon and its menu: run controls that work while the window is out of the way.

With "Minimise on start" the main window gets out of the user's way while a
run is typing into another application; the tray is how they reach Pause,
Stop and "Show window" without it. :class:`AppTray` owns a
``QSystemTrayIcon`` and a small menu::

    Show window
    Start typing from clipboard
    Pause / Resume
    Stop
    ------------------
    Quit

It does **no work of its own**: every menu entry (and a click on the icon)
just emits a Qt signal, and the main window decides what happens -- reading
the clipboard, running the preflight, pausing, quitting. The menu's enabled
states and the Pause/Resume label follow a
:class:`~auto_essay_typer.gui.run_controller.RunController`'s state.

Some platforms have no tray at all (a bare Linux session without a
notification area, the offscreen test platform). There
``QSystemTrayIcon.isSystemTrayAvailable()`` is false and :class:`AppTray`
degrades silently: nothing tray-shaped is constructed, :meth:`AppTray.show`,
:meth:`AppTray.hide` and :meth:`AppTray.notify` do nothing, and no error or
dialog appears.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from auto_essay_typer import platforms
from auto_essay_typer.gui.assets import asset_path
from auto_essay_typer.gui.run_controller import RunController
from auto_essay_typer.gui.theme import ThemeManager
from auto_essay_typer.platforms import OperatingSystem

logger = logging.getLogger(__name__)

#: Shown when hovering the icon.
TOOLTIP = "Auto Text Typer"

#: RunController states during which a run is in flight (see ``run_controller.STATES``).
BUSY_STATES = frozenset({"planning", "countdown", "typing", "paused", "resuming"})

_NOTIFY_MILLISECONDS = 5000


def load_tray_icon() -> QIcon:
    """The bundled tray icon for this platform; an empty ``QIcon`` if none can be loaded.

    macOS gets the monochrome template image, flagged as a mask so the menu
    bar tints it for light and dark bars; other platforms get the coloured
    icon. Falls back to the full-size app icon. Never raises.
    """
    names = ["tray-icon.png", "icon.png"]
    if platforms.current_os() is OperatingSystem.MACOS:
        names.insert(0, "tray-icon-template.png")
    for name in names:
        try:
            path = asset_path(name)
            if not path.exists():
                continue
            icon = QIcon(str(path))
            if icon.isNull():
                continue
            if "template" in name:
                icon.setIsMask(True)
            return icon
        except Exception:  # noqa: BLE001 - a broken asset must not break startup
            logger.debug("Could not load tray icon %r", name, exc_info=True)
    return QIcon()


class AppTray(QObject):
    """The tray icon and menu. See the module docstring for the division of labour.

    Signals
    -------
    showWindowRequested()
        "Show window" was chosen, or the icon was clicked.
    startFromClipboardRequested()
        "Start typing from clipboard" was chosen. The tray does not read the
        clipboard itself.
    pauseResumeRequested()
        The single Pause/Resume entry was chosen; the receiver pauses a
        typing run or resumes a paused one.
    stopRequested(), quitRequested()
        "Stop" / "Quit" was chosen.

    ``is_available`` and ``tray_factory`` are injectable so tests (and
    platforms without a tray) never build a real ``QSystemTrayIcon``.
    ``theme_manager``, when given, makes the tray re-apply its icon whenever
    the app theme changes, so a future light/dark icon variant needs no
    further wiring.
    """

    showWindowRequested = Signal()
    startFromClipboardRequested = Signal()
    pauseResumeRequested = Signal()
    stopRequested = Signal()
    quitRequested = Signal()

    def __init__(
        self,
        run_controller: RunController,
        *,
        is_available: Callable[[], bool] = QSystemTrayIcon.isSystemTrayAvailable,
        tray_factory: Callable[[], Any] = QSystemTrayIcon,
        theme_manager: ThemeManager | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._run_controller = run_controller
        self._theme_manager = theme_manager
        self._tray: Any = None
        self._menu: QMenu | None = None
        self._available = self._probe(is_available)

        # The actions are cheap and need no windowing system, so they always
        # exist -- the enabled/label logic is the same whether or not a tray
        # is present. Only the icon and the QMenu are skipped without one.
        self.show_action = QAction("Show window", self)
        self.clipboard_action = QAction("Start typing from clipboard", self)
        self.pause_action = QAction("Pause", self)
        self.stop_action = QAction("Stop", self)
        self.quit_action = QAction("Quit", self)
        # On macOS Qt moves an action named "Quit" into the application menu
        # when it sits in a menu bar; this one belongs in the tray menu only.
        self.quit_action.setMenuRole(QAction.MenuRole.NoRole)
        for action, signal in (
            (self.show_action, self.showWindowRequested),
            (self.clipboard_action, self.startFromClipboardRequested),
            (self.pause_action, self.pauseResumeRequested),
            (self.stop_action, self.stopRequested),
            (self.quit_action, self.quitRequested),
        ):
            action.triggered.connect(lambda _checked=False, signal=signal: signal.emit())

        if self._available:
            try:
                self._build_tray(tray_factory)
            except Exception:  # noqa: BLE001 - no tray is a fine outcome; a crash is not
                logger.exception("Could not create the system tray icon; continuing without it")
                self._tray = None
                self._menu = None
                self._available = False

        self.sync_state(run_controller.state)
        run_controller.stateChanged.connect(self.sync_state)
        if theme_manager is not None:
            theme_manager.themeChanged.connect(self._refresh_icon)

    # -- construction ------------------------------------------------------------------

    @staticmethod
    def _probe(is_available: Callable[[], bool]) -> bool:
        try:
            return bool(is_available())
        except Exception:  # noqa: BLE001
            logger.debug("Asking whether a system tray exists failed", exc_info=True)
            return False

    def _build_tray(self, tray_factory: Callable[[], Any]) -> None:
        tray = tray_factory()
        tray.setIcon(load_tray_icon())
        tray.setToolTip(TOOLTIP)

        # A parentless QMenu that this object keeps a reference to: the tray
        # icon does not take ownership of its context menu.
        menu = QMenu()
        menu.addAction(self.show_action)
        menu.addAction(self.clipboard_action)
        menu.addAction(self.pause_action)
        menu.addAction(self.stop_action)
        menu.addSeparator()
        menu.addAction(self.quit_action)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_activated)

        self._tray = tray
        self._menu = menu

    # -- public API ----------------------------------------------------------------------

    @property
    def available(self) -> bool:
        """Whether a tray icon exists. ``False`` means every method here is a no-op."""
        return self._available

    @property
    def menu(self) -> QMenu | None:
        """The tray's context menu, or ``None`` when there is no tray."""
        return self._menu

    def show(self) -> None:
        """Put the icon in the tray (no-op without a tray)."""
        if self._tray is not None:
            self._tray.show()

    def hide(self) -> None:
        """Take the icon out of the tray (no-op without a tray)."""
        if self._tray is not None:
            self._tray.hide()

    def notify(self, title: str, message: str) -> None:
        """Show a balloon/notification from the icon; silent when there is no visible tray icon."""
        tray = self._tray
        if tray is None or not tray.isVisible():
            return
        tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, _NOTIFY_MILLISECONDS)

    def sync_state(self, state: str) -> None:
        """Bring the menu's enabled states and Pause/Resume label in line with ``state``.

        ``state`` is one of ``RunController``'s state strings; an unknown one is
        treated as "not busy". Also wired to ``run_controller.stateChanged``.
        """
        busy = state in BUSY_STATES
        self.clipboard_action.setEnabled(not busy)
        self.pause_action.setText("Resume" if state == "paused" else "Pause")
        self.pause_action.setEnabled(state in ("typing", "paused"))
        self.stop_action.setEnabled(busy)
        # Show window and Quit are always available.
        self.show_action.setEnabled(True)
        self.quit_action.setEnabled(True)

    # -- internals ------------------------------------------------------------------------

    def _on_activated(self, reason: Any) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.showWindowRequested.emit()

    def _refresh_icon(self) -> None:
        if self._tray is not None:
            self._tray.setIcon(load_tray_icon())
