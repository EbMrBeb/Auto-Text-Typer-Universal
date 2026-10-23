"""The main window: sidebar navigation, the page stack, and the footer permission chip.

Navigation is a small registry (:class:`NavPage`: id, title, icon, widget
factory) built with :meth:`MainWindow.register_page`, so pages this stage
does not know about yet can be appended later without restructuring the
sidebar -- see the module spec's file-ownership split with the sibling work
landing ``targets.py``/``app_settings.py`` in the main checkout.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QSettings, QSize, Qt
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

from auto_essay_typer import __version__, platforms
from auto_essay_typer.driver import Driver
from auto_essay_typer.gui import icons
from auto_essay_typer.gui.a11y import RunAnnouncer
from auto_essay_typer.gui.assets import asset_path
from auto_essay_typer.gui.mini_window import VISIBLE_STATES, MiniProgressWindow
from auto_essay_typer.gui.pages.help_page import HelpPage
from auto_essay_typer.gui.pages.settings_page import SettingsPage
from auto_essay_typer.gui.pages.type_page import TypePage
from auto_essay_typer.gui.permission_dialog import PermissionDialog
from auto_essay_typer.gui.run_controller import RunController
from auto_essay_typer.gui.theme import ThemeManager
from auto_essay_typer.platforms import PermissionStatus

DEFAULT_WIDTH = 1000
DEFAULT_HEIGHT = 700
MIN_WIDTH = 820
MIN_HEIGHT = 600
_SIDEBAR_WIDTH = 220
_NAV_ICON_SIZE = 18
_CHIP_ICON_SIZE = 14


@dataclass(frozen=True)
class NavPage:
    """One entry in the sidebar's navigation registry."""

    page_id: str
    title: str
    icon: str
    factory: Callable[[], QWidget]


class MainWindow(QMainWindow):
    def __init__(
        self,
        theme_manager: ThemeManager,
        *,
        driver_factory: Callable[[], Driver] = Driver,
        permission_checker: Callable[[], PermissionStatus] = platforms.check_input_permission,
        version: str = __version__,
        settings: QSettings | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Auto Text Typer")
        self.resize(DEFAULT_WIDTH, DEFAULT_HEIGHT)
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)

        self._theme_manager = theme_manager
        self._permission_checker = permission_checker
        self._settings = settings if settings is not None else QSettings()
        #: Whether the mini progress window should show during a run. A
        #: settings module lands later (see module docstring) to persist
        #: this; for now it just lives here, toggled by the Settings page.
        self.show_mini_window = True

        self.run_controller = RunController(driver_factory=driver_factory)
        self.mini_window = MiniProgressWindow(self.run_controller)
        self.run_controller.stateChanged.connect(self._on_run_state_changed)
        # Speaks the run's milestones to a screen reader (nothing if none is running).
        self._announcer = RunAnnouncer(self.run_controller, self)

        self._pages: list[NavPage] = []
        self._nav_buttons: dict[str, QPushButton] = {}
        self._last_permission_status: PermissionStatus | None = None

        central = QWidget()
        central_layout = QHBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self._build_sidebar())

        self.page_stack = QStackedWidget()
        central_layout.addWidget(self.page_stack, 1)
        self.setCentralWidget(central)

        self._build_status_bar()

        self.register_page(
            NavPage(
                "type",
                "Type",
                "type",
                lambda: TypePage(
                    self.run_controller,
                    self._theme_manager,
                    permission_checker=self._permission_checker,
                ),
            )
        )
        self.register_page(
            NavPage(
                "settings",
                "Settings",
                "settings",
                lambda: SettingsPage(
                    self._theme_manager,
                    lambda: self.show_mini_window,
                    self._set_show_mini_window,
                ),
            )
        )
        self.register_page(NavPage("help", "Help", "help", lambda: HelpPage(version)))

        self._select_page("type")
        self._theme_manager.themeChanged.connect(self._refresh_nav_icons)
        self._geometry_restored = False

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
        about yet can be registered later without touching this class.
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

        self.page_stack.insertWidget(index, nav_page.factory())
        self._refresh_nav_icons()

    def _select_page(self, page_id: str) -> None:
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

    # -- footer status bar / permission chip ---------------------------------------

    def _build_status_bar(self) -> None:
        status_bar = self.statusBar()
        status_bar.setSizeGripEnabled(False)
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
        self._theme_manager.themeChanged.connect(self._refresh_chip_icon)

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
            self.permission_chip.setToolTip("Auto Text Typer can't type yet. Click to fix it.")
        # A screen reader gets the state as the control's value, not just a label.
        self.permission_chip.setAccessibleDescription(self.permission_chip.text())
        self._refresh_chip_icon()
        self.permission_chip.style().unpolish(self.permission_chip)
        self.permission_chip.style().polish(self.permission_chip)

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

    # -- mini progress window -------------------------------------------------------

    def _on_run_state_changed(self, state: str) -> None:
        if self.show_mini_window and state in VISIBLE_STATES:
            if not self.mini_window.isVisible():
                self.mini_window.position_near_corner()
                self.mini_window.show()
        else:
            self.mini_window.hide()

    def _set_show_mini_window(self, value: bool) -> None:
        self.show_mini_window = value
        if not value:
            self.mini_window.hide()
        elif self.run_controller.state in VISIBLE_STATES:
            self.mini_window.position_near_corner()
            self.mini_window.show()

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
