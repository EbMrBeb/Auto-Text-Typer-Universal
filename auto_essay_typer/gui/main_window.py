"""The main window: sidebar navigation, the page stack, and the footer permission chip.

Navigation is a small registry (:class:`NavPage`: id, title, icon, widget
factory) built with :meth:`MainWindow.register_page`, so a page can be appended
later without restructuring the sidebar. A page registered ``lazy`` is built the
first time it is opened (the Advanced page builds a great many widgets, and most
sessions never visit it); until then the stack holds a cheap placeholder.

The window also owns the app's shared services, which is what makes the pieces
talk to each other:

* :attr:`settings_store` -- the one live copy of the persisted preferences. The
  Settings, Advanced and onboarding pages write to it; this window reacts to
  changes that apply at once (the theme, the mini window) and reads the rest when
  they matter (the countdown and the editor's shortcuts when a run is built, the
  minimise-on-start preference when it starts).
* :attr:`run_controller` -- the single run lifecycle the Type page, the mini
  window, the tray and the global hotkeys all drive.
* :attr:`hotkey_service` -- built here, started by the application shell.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QSettings, QSize, Qt, Signal
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from auto_essay_typer import __version__, app_settings, platforms
from auto_essay_typer.actions import FormatOp
from auto_essay_typer.app_settings import AppSettings
from auto_essay_typer.driver import Driver, Keyboard
from auto_essay_typer.gui import icons
from auto_essay_typer.gui.a11y import RunAnnouncer
from auto_essay_typer.gui.assets import asset_path
from auto_essay_typer.gui.hotkeys import STATE_UNAVAILABLE, HotkeyService
from auto_essay_typer.gui.mini_window import VISIBLE_STATES, MiniProgressWindow
from auto_essay_typer.gui.onboarding import OnboardingDialog
from auto_essay_typer.gui.pages.advanced_page import AdvancedPage
from auto_essay_typer.gui.pages.help_page import HelpPage
from auto_essay_typer.gui.pages.settings_page import SettingsPage
from auto_essay_typer.gui.pages.type_page import TypePage
from auto_essay_typer.gui.permission_dialog import PermissionDialog
from auto_essay_typer.gui.run_controller import RunController
from auto_essay_typer.gui.settings_store import SettingsStore
from auto_essay_typer.gui.theme import ThemeManager, ThemeMode
from auto_essay_typer.gui.widgets import Banner
from auto_essay_typer.keymaps import Chord
from auto_essay_typer.platforms import PermissionStatus

logger = logging.getLogger(__name__)

DEFAULT_WIDTH = 1000
DEFAULT_HEIGHT = 700
MIN_WIDTH = 820
MIN_HEIGHT = 600
_SIDEBAR_WIDTH = 220
_NAV_ICON_SIZE = 18
_CHIP_ICON_SIZE = 14
#: How long a transient message stays in the status bar.
_STATUS_MESSAGE_MS = 8000

#: Run states in which a run is over (see ``run_controller.STATES``).
_TERMINAL_STATES = frozenset({"finished", "stopped", "failed"})


@dataclass(frozen=True)
class NavPage:
    """One entry in the sidebar's navigation registry.

    ``lazy`` pages are built by ``factory`` the first time they are opened rather
    than when they are registered.
    """

    page_id: str
    title: str
    icon: str
    factory: Callable[[], QWidget]
    lazy: bool = False


class MainWindow(QMainWindow):
    """The application window. See the module docstring for what it owns.

    Signals
    -------
    onboardingFinished()
        The first-run walkthrough was closed (finished, skipped or dismissed).
    """

    onboardingFinished = Signal()

    def __init__(
        self,
        theme_manager: ThemeManager,
        *,
        driver_factory: Callable[..., Driver] | None = None,
        keyboard_factory: Callable[[], Keyboard] | None = None,
        permission_checker: Callable[[], PermissionStatus] = platforms.check_input_permission,
        version: str = __version__,
        settings: QSettings | None = None,
        settings_store: SettingsStore | None = None,
        hotkey_service: HotkeyService | None = None,
        onboarding_factory: Callable[..., OnboardingDialog] = OnboardingDialog,
        parent: QWidget | None = None,
    ) -> None:
        """``driver_factory`` replaces how a run's :class:`Driver` is built (tests, ``--self-test``);
        left out, the driver is built from the saved settings (``keyboard_factory`` supplies its
        keyboard, the real one by default). ``settings`` is the ``QSettings`` that remembers the
        window's geometry; ``settings_store`` holds the app preferences."""
        super().__init__(parent)
        self.setWindowTitle("Auto Text Typer")
        self.resize(DEFAULT_WIDTH, DEFAULT_HEIGHT)
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)

        self._theme_manager = theme_manager
        self._permission_checker = permission_checker
        self._version = version
        self._keyboard_factory = keyboard_factory
        self._onboarding_factory = onboarding_factory
        self._onboarding: OnboardingDialog | None = None
        self._settings = settings if settings is not None else QSettings()
        self.settings_store = settings_store if settings_store is not None else SettingsStore(parent=self)
        self.hotkey_service = hotkey_service if hotkey_service is not None else HotkeyService(parent=self)
        #: Whether this window minimised itself for the run in flight (once per run).
        self._minimised_for_run = False

        self.run_controller = RunController(
            driver_factory=driver_factory if driver_factory is not None else self.make_driver
        )
        self.mini_window = MiniProgressWindow(self.run_controller)
        self.run_controller.stateChanged.connect(self._on_run_state_changed)
        # Speaks the run's milestones to a screen reader (nothing if none is running).
        self._announcer = RunAnnouncer(self.run_controller, self)

        self._pages: list[NavPage] = []
        self._nav_buttons: dict[str, QPushButton] = {}
        #: Placeholders standing in the stack for lazy pages not built yet.
        self._placeholders: dict[str, QWidget] = {}
        self._last_permission_status: PermissionStatus | None = None

        central = QWidget()
        central_layout = QHBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self._build_sidebar())

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self._build_notice_banner())
        self.page_stack = QStackedWidget()
        content_layout.addWidget(self.page_stack, 1)
        central_layout.addWidget(content, 1)
        self.setCentralWidget(central)

        self._build_status_bar()

        self.register_page(NavPage("type", "Type", "type", self._build_type_page))
        self.register_page(NavPage("settings", "Settings", "settings", self._build_settings_page))
        self.register_page(NavPage("advanced", "Advanced", "code", self._build_advanced_page, lazy=True))
        self.register_page(NavPage("help", "Help", "help", self._build_help_page))

        self._select_page("type")
        self._theme_manager.themeChanged.connect(self._refresh_nav_icons)
        #: The theme setting the theme manager was last told about, so an unrelated change (the
        #: countdown, onboarding being finished) never re-applies a theme the user did not touch.
        self._applied_theme = self.settings_store.settings.theme
        self.settings_store.changed.connect(self._on_settings_changed)
        self.hotkey_service.statusChanged.connect(self._on_hotkey_status)
        self._geometry_restored = False

        warning = self.settings_store.take_load_warning()
        if warning:
            self.show_notice(
                "Your saved settings could not be read, so the defaults are in use. "
                f"Details: {warning}. The file is replaced the next time you change a setting.",
                action=("Open Settings", lambda: self.show_page("settings")),
            )

    # -- the pages ----------------------------------------------------------------------

    def _build_type_page(self) -> QWidget:
        return TypePage(
            self.run_controller,
            self._theme_manager,
            permission_checker=self._permission_checker,
            settings_store=self.settings_store,
        )

    def _build_settings_page(self) -> QWidget:
        return SettingsPage(
            self._theme_manager,
            self.settings_store,
            hotkey_service=self.hotkey_service,
        )

    def _build_advanced_page(self) -> QWidget:
        page = AdvancedPage(self._theme_manager, self.settings_store)
        # A saved or deleted profile reaches the Type page's profile list, so the next run uses it.
        page.profilesChanged.connect(self._reload_type_page_profiles)
        return page

    def _build_help_page(self) -> QWidget:
        page = HelpPage(self._version)
        page.showSetupRequested.connect(self.show_onboarding)
        return page

    def _reload_type_page_profiles(self) -> None:
        """Make the Type page re-read the profile file after the Advanced page changed it."""
        type_page = self.page("type")
        reload = getattr(type_page, "reload_profiles", None) or getattr(type_page, "_reload_profiles", None)
        if callable(reload):
            reload()

    @property
    def theme_manager(self) -> ThemeManager:
        return self._theme_manager

    @property
    def type_page(self) -> TypePage:
        return self.page("type")  # type: ignore[return-value]

    # -- sidebar / navigation registry --------------------------------------------

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(_SIDEBAR_WIDTH)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 20, 16, 16)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(8)
        icon_label = QLabel()
        app_icon = QPixmap(str(asset_path("icon.png")))
        if not app_icon.isNull():
            # Scaled to the screen's pixel ratio, not to 28 physical pixels, which
            # would be blurry on a Retina/HiDPI display.
            ratio = max(1, math.ceil(self.devicePixelRatioF()))
            logo = app_icon.scaled(
                28 * ratio,
                28 * ratio,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            logo.setDevicePixelRatio(ratio)
            icon_label.setPixmap(logo)
        header.addWidget(icon_label)
        name_label = QLabel("Auto Text Typer")
        name_label.setObjectName("SidebarTitle")
        name_label.setWordWrap(True)
        header.addWidget(name_label, 1)
        layout.addLayout(header)
        layout.addSpacing(16)

        # register_page() inserts nav buttons just above this trailing stretch.
        layout.addStretch(1)
        self._nav_layout = layout
        return sidebar

    def register_page(self, nav_page: NavPage, index: int | None = None) -> None:
        """Add a page to the sidebar and the page stack.

        Safe to call after construction, so a page this stage doesn't know
        about yet can be registered later without touching this class. A ``lazy``
        page is only built when it is first opened.
        """
        if index is None:
            index = len(self._pages)
        self._pages.insert(index, nav_page)

        button = QPushButton(nav_page.title)
        button.setObjectName("NavButton")
        button.setCheckable(True)
        button.setAutoExclusive(True)
        button.setIconSize(QSize(_NAV_ICON_SIZE, _NAV_ICON_SIZE))
        button.setAccessibleName(nav_page.title)
        button.clicked.connect(lambda _checked=False, pid=nav_page.page_id: self._select_page(pid))
        # Ctrl+1, Ctrl+2, ... (Cmd on macOS) jump straight to a page, in sidebar order.
        position = len(self._nav_buttons) + 1
        if position <= 9:
            sequence = QKeySequence(f"Ctrl+{position}")
            native = sequence.toString(QKeySequence.SequenceFormat.NativeText)
            button.setToolTip(f"{nav_page.title} ({native})")
            shortcut = QShortcut(sequence, self)
            shortcut.activated.connect(lambda pid=nav_page.page_id: self._select_page(pid))
        else:
            button.setToolTip(nav_page.title)
        self._nav_layout.insertWidget(self._nav_layout.count() - 1, button)
        self._nav_buttons[nav_page.page_id] = button

        if nav_page.lazy:
            placeholder = QWidget()
            placeholder.setObjectName("LazyPagePlaceholder")
            self._placeholders[nav_page.page_id] = placeholder
            self.page_stack.insertWidget(index, placeholder)
        else:
            self.page_stack.insertWidget(index, nav_page.factory())
        self._refresh_nav_icons()

    def page(self, page_id: str) -> QWidget:
        """The widget for ``page_id``, built first if it is a lazy page nobody has opened."""
        index = next(i for i, p in enumerate(self._pages) if p.page_id == page_id)
        placeholder = self._placeholders.pop(page_id, None)
        if placeholder is not None:
            real = self._pages[index].factory()
            self.page_stack.removeWidget(placeholder)
            self.page_stack.insertWidget(index, real)
            placeholder.deleteLater()
        return self.page_stack.widget(index)

    def is_page_built(self, page_id: str) -> bool:
        """False only for a lazy page that has not been opened yet."""
        return page_id not in self._placeholders

    def show_page(self, page_id: str) -> None:
        """Switch to ``page_id`` (building it first if it is lazy)."""
        self._select_page(page_id)

    def _select_page(self, page_id: str) -> None:
        self.page(page_id)  # builds a lazy page before it is shown
        index = next(i for i, p in enumerate(self._pages) if p.page_id == page_id)
        self.page_stack.setCurrentIndex(index)
        for pid, button in self._nav_buttons.items():
            active = pid == page_id
            button.setChecked(active)
            button.setProperty("active", active)
            button.style().unpolish(button)
            button.style().polish(button)
        self._refresh_nav_icons()

    def _refresh_nav_icons(self) -> None:
        tokens = self._theme_manager.tokens
        for nav_page in self._pages:
            button = self._nav_buttons[nav_page.page_id]
            active = button.property("active") is True
            color = tokens.on_accent if active else tokens.text
            button.setIcon(icons.icon(nav_page.icon, color, _NAV_ICON_SIZE))

    # -- a notice above the pages ---------------------------------------------------------

    def _build_notice_banner(self) -> QWidget:
        """The strip above the page stack: empty, and taking no room, until a notice is shown."""
        self._notice_holder = QWidget()
        self._notice_layout = QVBoxLayout(self._notice_holder)
        self._notice_layout.setContentsMargins(24, 16, 24, 0)
        #: The banner showing the current notice (``None`` before the first one).
        self.notice_banner: Banner | None = None
        self._notice_holder.setVisible(False)
        return self._notice_holder

    def show_notice(
        self,
        text: str,
        tone: str = "warning",
        *,
        action: tuple[str, Callable[[], None]] | None = None,
    ) -> None:
        """Show ``text`` in a dismissible banner above the pages, replacing any earlier notice.

        ``action`` is an optional ``(label, callback)`` link inside the banner.
        """
        previous = self.notice_banner
        if previous is not None:
            self._notice_layout.removeWidget(previous)
            previous.setParent(None)
            previous.deleteLater()
        banner = Banner(tone, theme_manager=self._theme_manager)
        banner.dismissed.connect(lambda: self._notice_holder.setVisible(False))
        if action is not None:
            label, callback = action
            banner.add_action(label, callback).setAccessibleName(label)
        banner.set_message(text, tone)
        self._notice_layout.addWidget(banner)
        self.notice_banner = banner
        self._notice_holder.setVisible(True)

    # -- footer status bar / permission chip ---------------------------------------

    def _build_status_bar(self) -> None:
        status_bar = self.statusBar()
        status_bar.setSizeGripEnabled(False)

        # Quiet by default: shown only when the global hotkeys cannot work, as a small link
        # to the Settings page, which says why and offers a retry.
        self.hotkey_status_button = QPushButton("Hotkeys unavailable")
        self.hotkey_status_button.setObjectName("HotkeyStatusButton")
        self.hotkey_status_button.setFlat(True)
        self.hotkey_status_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hotkey_status_button.setAccessibleName("Global hotkeys unavailable")
        self.hotkey_status_button.setToolTip("The global hotkeys could not start. Click for details.")
        self.hotkey_status_button.clicked.connect(lambda _checked=False: self.show_page("settings"))
        self.hotkey_status_button.setVisible(False)
        status_bar.addPermanentWidget(self.hotkey_status_button)

        self.permission_chip = QPushButton()
        self.permission_chip.setObjectName("PermissionChip")
        self.permission_chip.setFlat(True)
        self.permission_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.permission_chip.setIconSize(QSize(_CHIP_ICON_SIZE, _CHIP_ICON_SIZE))
        self.permission_chip.setAccessibleName("Typing permission")
        self.permission_chip.clicked.connect(self._on_permission_chip_clicked)
        # A QStatusBar sets permanent widgets flush against its edges, so the chip
        # lives in a small padded holder to get the margin it needs.
        holder = QWidget()
        holder_layout = QHBoxLayout(holder)
        holder_layout.setContentsMargins(0, 4, 16, 4)
        holder_layout.addWidget(self.permission_chip)
        status_bar.addPermanentWidget(holder)
        self.refresh_permission_status()
        self._restyle_status_bar()
        self._theme_manager.themeChanged.connect(self._refresh_chip_icon)
        self._theme_manager.themeChanged.connect(self._restyle_status_bar)

    def _restyle_status_bar(self) -> None:
        tokens = self._theme_manager.tokens
        self.hotkey_status_button.setStyleSheet(
            f"""
            QPushButton#HotkeyStatusButton {{
                border: none; background: transparent; padding: 0 12px;
                color: {tokens.muted_text}; font-size: 12px;
            }}
            QPushButton#HotkeyStatusButton:hover {{ color: {tokens.accent_text}; text-decoration: underline; }}
            """
        )

    def _on_hotkey_status(self, state: str, detail: str) -> None:
        unavailable = state == STATE_UNAVAILABLE
        self.hotkey_status_button.setVisible(unavailable)
        if unavailable:
            reason = f" {detail}." if detail else ""
            self.hotkey_status_button.setToolTip(f"The global hotkeys could not start.{reason} Click for details.")
            self.hotkey_status_button.setAccessibleDescription(detail)

    def refresh_permission_status(self) -> None:
        """Re-check and re-render the footer permission chip."""
        status = self._permission_checker()
        self._last_permission_status = status
        if status.granted:
            self.permission_chip.setText("Ready to type")
            self.permission_chip.setProperty("state", "ready")
            self.permission_chip.setToolTip("Typing permission is granted. Click to review.")
        else:
            self.permission_chip.setText("Permission needed - Fix")
            self.permission_chip.setProperty("state", "warning")
            self.permission_chip.setToolTip("Your computer is not letting Auto Text Typer type yet. Click to see how to allow it.")
        # A screen reader gets the state as the control's value, not just a label.
        self.permission_chip.setAccessibleDescription(self.permission_chip.text())
        self._refresh_chip_icon()
        self.permission_chip.style().unpolish(self.permission_chip)
        self.permission_chip.style().polish(self.permission_chip)

    def check_permission(self) -> PermissionStatus:
        """The current typing permission (what the footer chip reflects), freshly checked."""
        return self._permission_checker()

    def _refresh_chip_icon(self) -> None:
        """Tint the chip's check/alert icon to match its text in the current theme."""
        tokens = self._theme_manager.tokens
        if self.permission_chip.property("state") == "warning":
            self.permission_chip.setIcon(icons.icon("alert-triangle", tokens.attention_text, _CHIP_ICON_SIZE))
        else:
            self.permission_chip.setIcon(icons.icon("check-circle", tokens.success_text, _CHIP_ICON_SIZE))

    def _on_permission_chip_clicked(self) -> None:
        status = self._last_permission_status or self._permission_checker()
        dialog = PermissionDialog(
            status,
            self._theme_manager.tokens,
            permission_checker=self._permission_checker,
            parent=self,
        )
        dialog.exec()
        self.refresh_permission_status()

    def show_status_message(self, text: str) -> None:
        """A transient line in the footer (it fades after a few seconds)."""
        self.statusBar().showMessage(text, _STATUS_MESSAGE_MS)

    # -- bringing the window forward --------------------------------------------------------

    def bring_to_front(self) -> None:
        """Show, un-minimise and raise the window, e.g. for the tray's "Show window"."""
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    # -- settings: what changes at once, and what a run is built from ------------------------

    @property
    def show_mini_window(self) -> bool:
        """Whether the mini progress window is wanted (the saved ``show_mini_window`` setting)."""
        return self.settings_store.settings.show_mini_window

    def _on_settings_changed(self, settings: object) -> None:
        if not isinstance(settings, AppSettings):
            return
        # The theme and the mini window take effect the moment they are changed...
        if settings.theme != self._applied_theme:
            self._applied_theme = settings.theme
            self._theme_manager.set_mode(ThemeMode(settings.theme))
        if not settings.show_mini_window:
            self.mini_window.hide()
        elif self.run_controller.state in VISIBLE_STATES and not self.mini_window.isVisible():
            self.mini_window.position_near_corner()
            self.mini_window.show()
        # ...everything else (countdown, shortcuts, confirmation) is read when it is needed.

    def make_driver(self, keymap: Mapping[FormatOp, Chord] | None = None) -> Driver:
        """A :class:`Driver` for the next run, built from the settings as they are *now*.

        The countdown is the saved ``countdown_seconds`` and the formatting shortcuts are the
        default editor's, with the user's overrides from the Advanced page applied -- so a
        change on either page affects the next run without anything having to be told. A
        ``keymap`` passed in (a caller that has already worked out which editor a document is
        for) wins. Called on the GUI thread, which the real keyboard backend requires (see
        ``driver.preload_keyboard_backend``).
        """
        settings = self.settings_store.settings
        if keymap is None:
            try:
                keymap = app_settings.effective_keymap(settings, platforms.current_os())
            except (ValueError, KeyError) as exc:
                logger.warning("Using the default shortcuts: %s", exc)
        keyboard = self._keyboard_factory() if self._keyboard_factory is not None else None
        return Driver(keyboard=keyboard, countdown_seconds=settings.countdown_seconds, keymap=keymap)

    # -- mini progress window / minimise on start ----------------------------------------------

    def _on_run_state_changed(self, state: str) -> None:
        if self.show_mini_window and state in VISIBLE_STATES:
            if not self.mini_window.isVisible():
                self.mini_window.position_near_corner()
                self.mini_window.show()
        else:
            self.mini_window.hide()

        if state == "planning" or state in _TERMINAL_STATES:
            self._minimised_for_run = False
        elif (
            state in ("countdown", "typing")
            and not self._minimised_for_run
            and self.settings_store.settings.minimize_on_start
        ):
            self._minimised_for_run = True
            # Only a window that is up gets minimised: one the user already hid (to the tray)
            # must not be brought back onto the screen just to be put in the Dock.
            if self.isVisible() and not self.isMinimized():
                self.showMinimized()

    # -- first-run onboarding ------------------------------------------------------------------

    @property
    def onboarding_dialog(self) -> OnboardingDialog | None:
        """The walkthrough while it is open, otherwise ``None``."""
        return self._onboarding

    def onboarding_pending(self) -> bool:
        """True until the walkthrough has been finished or skipped once."""
        return not self.settings_store.settings.onboarding_complete

    def maybe_show_onboarding(self) -> bool:
        """Open the walkthrough if this is the first run. Returns whether it was opened."""
        if not self.onboarding_pending():
            return False
        self.show_onboarding()
        return True

    def show_onboarding(self) -> OnboardingDialog:
        """Open the walkthrough (the Help page's "Show setup again" does this too).

        It is window-modal but does not block: the call returns at once and the walkthrough
        records ``onboarding_complete`` itself when it is finished or skipped. If it is already
        open it is raised instead of opening a second one.
        """
        if self._onboarding is not None:
            self._onboarding.raise_()
            self._onboarding.activateWindow()
            return self._onboarding
        dialog = self._onboarding_factory(
            self._theme_manager,
            self.settings_store,
            permission_checker=self._permission_checker,
            parent=self,
        )
        dialog.finished.connect(lambda _result, d=dialog: self._on_onboarding_closed(d))
        self._onboarding = dialog
        dialog.open()
        return dialog

    def _on_onboarding_closed(self, dialog: OnboardingDialog) -> None:
        if self._onboarding is dialog:
            self._onboarding = None
        dialog.deleteLater()
        # The walkthrough's permission step may have changed the answer the footer shows.
        self.refresh_permission_status()
        self.onboardingFinished.emit()

    # -- window geometry (QSettings) -------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        # Restoring geometry here, on the first real show, rather than in
        # __init__: restoreGeometry() on a window that has never been shown
        # (still unlaid-out) is unreliable -- QMainWindow's own layout can
        # re-assert a minimum-size-driven width once it activates, fighting
        # the just-restored geometry. By the time showEvent fires the layout
        # is already active, so restoring here sticks.
        super().showEvent(event)
        if not self._geometry_restored:
            self._geometry_restored = True
            geometry = self._settings.value("mainWindow/geometry")
            if geometry is not None:
                self.restoreGeometry(geometry)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._settings.setValue("mainWindow/geometry", self.saveGeometry())
        self.mini_window.close()
        super().closeEvent(event)
