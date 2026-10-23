"""Design system: tokens, a QPalette, and a stylesheet generated from them.

Built on the Fusion style (the one Qt style that looks and behaves the same
on macOS, Windows, and Linux) plus a custom :class:`~PySide6.QtGui.QPalette`
and a stylesheet assembled from the token values below, so the app looks
consistent -- and consistently *itself*, not like a native-but-dated control
panel -- on all three platforms.

Three theme modes: :attr:`ThemeMode.SYSTEM` (the default) follows
``QGuiApplication.styleHints().colorScheme()`` and updates live when the OS
theme changes (``colorSchemeChanged``); :attr:`ThemeMode.LIGHT` and
:attr:`ThemeMode.DARK` pin one palette regardless of the OS setting.

Every color a widget needs should come from :class:`Tokens` (via
:attr:`ThemeManager.tokens`) or the generated stylesheet -- never a literal
hex string sprinkled in page/dialog code -- so retuning the palette here is
the only place that needs to change.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


class ThemeMode(str, Enum):
    """A user-selectable theme preference. See ``gui/pages/settings_page.py``."""

    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


@dataclass(frozen=True)
class Tokens:
    """One resolved palette's worth of design tokens.

    Colors are ``#RRGGBB`` strings (straight into ``QColor``/stylesheets).
    Sizes are in (unscaled) pixels -- Qt handles HiDPI scaling itself.
    """

    # -- brand / semantic colors, identical in both light and dark ---------
    accent: str
    accent_hover: str
    accent_pressed: str
    attention: str
    success: str
    danger: str

    # -- surfaces, per theme mode --------------------------------------------
    window: str
    surface: str
    sidebar: str
    border: str
    text: str
    muted_text: str

    # -- shape ---------------------------------------------------------------
    radius_card: int = 10
    radius_control: int = 8

    # -- spacing scale ---------------------------------------------------------
    space_xs: int = 4
    space_sm: int = 8
    space_md: int = 12
    space_lg: int = 16
    space_xl: int = 24

    # -- fixed control sizing, shared by both palettes -----------------------
    button_height: int = 36

    @property
    def on_accent(self) -> str:
        """Text/icon color for content sitting on an accent-filled surface."""
        return "#FFFFFF"


_ACCENT = "#6D5AE6"
_ACCENT_HOVER = "#7C6CF0"
_ACCENT_PRESSED = "#5B48D6"
_ATTENTION = "#F5A623"
_SUCCESS = "#2EAD6B"
_DANGER = "#E5484D"

LIGHT_TOKENS = Tokens(
    accent=_ACCENT,
    accent_hover=_ACCENT_HOVER,
    accent_pressed=_ACCENT_PRESSED,
    attention=_ATTENTION,
    success=_SUCCESS,
    danger=_DANGER,
    window="#F6F6F9",
    surface="#FFFFFF",
    sidebar="#EFEFF4",
    border="#E4E4EB",
    text="#1C1C24",
    muted_text="#6B6B7B",
)

DARK_TOKENS = Tokens(
    accent=_ACCENT,
    accent_hover=_ACCENT_HOVER,
    accent_pressed=_ACCENT_PRESSED,
    attention=_ATTENTION,
    success=_SUCCESS,
    danger=_DANGER,
    window="#16161C",
    surface="#1F1F27",
    sidebar="#1B1B22",
    border="#2E2E38",
    text="#ECECF1",
    muted_text="#9A9AAE",
)


def resolve_scheme(mode: ThemeMode) -> Qt.ColorScheme:
    """The effective light/dark scheme for ``mode``.

    ``SYSTEM`` asks Qt for the OS's current preference. Offscreen/headless
    platforms (and some Linux desktops with no such signal) report
    ``Unknown``, which falls back to Light rather than leaving callers to
    handle a third scheme value everywhere.
    """
    # Equality, not identity: ThemeMode is a str Enum, and a value round-tripped
    # through Qt's QVariant (e.g. QComboBox.currentData()) can come back as a
    # plain str that is == the enum member but not `is` it.
    if mode == ThemeMode.LIGHT:
        return Qt.ColorScheme.Light
    if mode == ThemeMode.DARK:
        return Qt.ColorScheme.Dark
    scheme = QApplication.styleHints().colorScheme()
    if scheme == Qt.ColorScheme.Unknown:
        return Qt.ColorScheme.Light
    return scheme


def tokens_for(scheme: Qt.ColorScheme) -> Tokens:
    return DARK_TOKENS if scheme == Qt.ColorScheme.Dark else LIGHT_TOKENS


def build_palette(tokens: Tokens) -> QPalette:
    """A QPalette matching ``tokens``, for ``QApplication.setPalette``.

    Covers the roles Fusion-styled widgets actually consult (including the
    Disabled group, so a disabled Type page during a run reads as disabled
    rather than merely unresponsive) -- not an exhaustive dump of every
    QPalette role.
    """
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(tokens.window))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(tokens.text))
    palette.setColor(QPalette.ColorRole.Base, QColor(tokens.surface))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(tokens.sidebar))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(tokens.surface))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(tokens.text))
    palette.setColor(QPalette.ColorRole.Text, QColor(tokens.text))
    palette.setColor(QPalette.ColorRole.Button, QColor(tokens.surface))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(tokens.text))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(tokens.danger))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(tokens.accent))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(tokens.on_accent))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(tokens.muted_text))
    palette.setColor(QPalette.ColorRole.Link, QColor(tokens.accent))

    disabled_text = QColor(tokens.muted_text)
    for role in (
        QPalette.ColorRole.Text,
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, disabled_text)
    return palette


def build_stylesheet(tokens: Tokens) -> str:
    """A Qt stylesheet layering the design system on top of Fusion.

    Object names used as stylesheet hooks by widget code (kept in one place
    here as the contract between this module and the rest of ``gui/``):

    ``Sidebar``, ``SidebarTitle``, ``NavButton`` (property ``active``),
    ``Card``, ``PageTitle``, ``SectionTitle``, ``MutedLabel``,
    ``PrimaryButton``, ``SecondaryButton``, ``DestructiveButton``,
    ``PermissionChip`` (property ``state`` = ``ready``/``warning``),
    ``StatusBadge`` (property ``tone`` = ``success``/``danger``),
    ``InlineMessage`` (property ``tone`` = ``danger``), ``ToggleSwitch``.
    """
    t = tokens
    return f"""
    QWidget {{
        color: {t.text};
        selection-background-color: {t.accent};
        selection-color: {t.on_accent};
    }}

    QMainWindow, QDialog {{
        background: {t.window};
    }}

    /* -- sidebar ----------------------------------------------------- */

    #Sidebar {{
        background: {t.sidebar};
        border-right: 1px solid {t.border};
    }}

    #SidebarTitle {{
        font-size: 15px;
        font-weight: 600;
        color: {t.text};
    }}

    QPushButton#NavButton {{
        text-align: left;
        border: none;
        border-radius: {t.radius_control}px;
        padding: {t.space_sm}px {t.space_md}px;
        background: transparent;
        color: {t.text};
        font-size: 13px;
    }}
    QPushButton#NavButton:hover {{
        background: {t.border};
    }}
    QPushButton#NavButton[active="true"] {{
        background: {t.accent};
        color: {t.on_accent};
        font-weight: 600;
    }}

    /* -- generic surfaces ---------------------------------------------- */

    #Card {{
        background: {t.surface};
        border: 1px solid {t.border};
        border-radius: {t.radius_card}px;
    }}

    #PageTitle {{
        font-size: 22px;
        font-weight: 600;
        color: {t.text};
    }}

    #SectionTitle {{
        font-size: 13px;
        font-weight: 600;
        color: {t.text};
    }}

    #MutedLabel, QLabel[muted="true"] {{
        color: {t.muted_text};
        font-size: 12px;
    }}

    QLabel#InlineMessage {{
        font-size: 12px;
        font-weight: 600;
    }}
    QLabel#InlineMessage[tone="danger"] {{
        color: {t.danger};
    }}

    /* -- buttons ---------------------------------------------------------- */

    QPushButton {{
        border-radius: {t.radius_control}px;
        padding: {t.space_sm}px {t.space_lg}px;
        font-size: 13px;
    }}

    QPushButton#PrimaryButton {{
        background: {t.accent};
        color: {t.on_accent};
        border: none;
        min-height: {t.button_height}px;
        font-weight: 600;
    }}
    QPushButton#PrimaryButton:hover {{
        background: {t.accent_hover};
    }}
    QPushButton#PrimaryButton:pressed {{
        background: {t.accent_pressed};
    }}
    QPushButton#PrimaryButton:disabled {{
        background: {t.border};
        color: {t.muted_text};
    }}

    QPushButton#SecondaryButton {{
        background: {t.surface};
        color: {t.text};
        border: 1px solid {t.border};
        min-height: {t.button_height}px;
    }}
    QPushButton#SecondaryButton:hover {{
        border-color: {t.accent};
        color: {t.accent};
    }}
    QPushButton#SecondaryButton:pressed {{
        background: {t.sidebar};
    }}
    QPushButton#SecondaryButton:disabled {{
        color: {t.muted_text};
        border-color: {t.border};
    }}

    QPushButton#DestructiveButton {{
        background: {t.surface};
        color: {t.danger};
        border: 1px solid {t.danger};
        min-height: {t.button_height}px;
    }}
    QPushButton#DestructiveButton:hover {{
        background: {t.danger};
        color: {t.on_accent};
    }}
    QPushButton#DestructiveButton:disabled {{
        color: {t.muted_text};
        border-color: {t.border};
    }}

    /* -- inputs ------------------------------------------------------------ */

    QPlainTextEdit, QTextEdit, QLineEdit {{
        background: {t.surface};
        border: 1px solid {t.border};
        border-radius: {t.radius_control}px;
        padding: {t.space_sm}px;
        selection-background-color: {t.accent};
        selection-color: {t.on_accent};
    }}
    QPlainTextEdit:focus, QTextEdit:focus, QLineEdit:focus {{
        border: 1px solid {t.accent};
    }}
    QPlainTextEdit:disabled, QTextEdit:disabled, QLineEdit:disabled {{
        background: {t.sidebar};
        color: {t.muted_text};
        border-color: {t.border};
    }}

    QComboBox {{
        background: {t.surface};
        border: 1px solid {t.border};
        border-radius: {t.radius_control}px;
        padding: {t.space_xs}px {t.space_sm}px;
        min-height: {t.button_height - 8}px;
    }}
    QComboBox:hover {{
        border-color: {t.accent};
    }}
    QComboBox:disabled {{
        background: {t.sidebar};
        color: {t.muted_text};
        border-color: {t.border};
    }}
    QComboBox QAbstractItemView {{
        background: {t.surface};
        border: 1px solid {t.border};
        selection-background-color: {t.accent};
        selection-color: {t.on_accent};
        outline: none;
    }}

    /* -- slider (speed / mistakes) ------------------------------------------ */

    QSlider::groove:horizontal {{
        height: 4px;
        background: {t.border};
        border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{
        height: 4px;
        background: {t.accent};
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        width: 16px;
        height: 16px;
        margin: -6px 0;
        border-radius: 8px;
        background: {t.accent};
        border: 2px solid {t.surface};
    }}
    QSlider::handle:horizontal:hover {{
        background: {t.accent_hover};
    }}
    QSlider::groove:horizontal:disabled {{
        background: {t.sidebar};
    }}
    QSlider::sub-page:horizontal:disabled {{
        background: {t.muted_text};
    }}
    QSlider::handle:horizontal:disabled {{
        background: {t.muted_text};
        border-color: {t.window};
    }}

    /* -- toggle switch (Settings-page checkboxes styled as switches) ------ */

    QCheckBox#ToggleSwitch {{
        spacing: {t.space_sm}px;
        font-size: 13px;
    }}
    QCheckBox#ToggleSwitch:disabled {{
        color: {t.muted_text};
    }}
    QCheckBox#ToggleSwitch::indicator {{
        width: 34px;
        height: 20px;
        border-radius: 10px;
        background: {t.border};
    }}
    QCheckBox#ToggleSwitch::indicator:disabled {{
        background: {t.sidebar};
    }}
    QCheckBox#ToggleSwitch::indicator:checked:disabled {{
        background: {t.muted_text};
    }}
    QCheckBox#ToggleSwitch::indicator:checked {{
        background: {t.accent};
    }}

    /* -- progress bar -------------------------------------------------------- */

    QProgressBar {{
        background: {t.border};
        border: none;
        border-radius: 4px;
        height: 8px;
        text-align: center;
        color: transparent;
    }}
    QProgressBar::chunk {{
        background: {t.accent};
        border-radius: 4px;
    }}

    /* -- permission chip (footer) -------------------------------------------- */

    QPushButton#PermissionChip {{
        border-radius: 12px;
        padding: {t.space_xs}px {t.space_md}px;
        font-size: 12px;
        font-weight: 600;
        border: none;
        text-align: left;
    }}
    QPushButton#PermissionChip[state="ready"] {{
        background: rgba(46, 173, 107, 0.16);
        color: {t.success};
    }}
    QPushButton#PermissionChip[state="warning"] {{
        background: rgba(245, 166, 35, 0.18);
        color: {t.attention};
    }}

    /* -- status badges (preview dialog verification, etc.) ------------------- */

    QLabel#StatusBadge[tone="success"] {{
        background: rgba(46, 173, 107, 0.16);
        color: {t.success};
        border-radius: {t.radius_control}px;
        padding: {t.space_sm}px {t.space_md}px;
        font-weight: 600;
    }}
    QLabel#StatusBadge[tone="danger"] {{
        background: rgba(229, 72, 77, 0.16);
        color: {t.danger};
        border-radius: {t.radius_control}px;
        padding: {t.space_sm}px {t.space_md}px;
        font-weight: 600;
    }}

    /* -- scroll areas: no visible frame, they're used purely for overflow -- */

    QScrollArea {{
        border: none;
        background: transparent;
    }}
    QScrollArea > QWidget > QWidget {{
        background: transparent;
    }}

    QStatusBar {{
        background: {t.surface};
        border-top: 1px solid {t.border};
    }}
    """


class ThemeManager(QObject):
    """Owns the active :class:`ThemeMode`, applies it, and reacts to OS theme changes.

    Construct one per application, call :meth:`apply` once at startup (the
    constructor does this for you), and read :attr:`tokens`/:attr:`is_dark`
    from it wherever a widget needs a token value outside the stylesheet
    (e.g. to recolor an SVG icon). :data:`themeChanged` fires after every
    successful :meth:`set_mode` and after every OS-driven scheme change while
    in ``SYSTEM`` mode, so widgets that cache a derived value (icon pixmaps,
    chart colors) know when to rebuild it.
    """

    themeChanged = Signal()

    def __init__(
        self, app: QApplication, mode: ThemeMode = ThemeMode.SYSTEM, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._app = app
        self._mode = mode
        app.setStyle("Fusion")
        QApplication.styleHints().colorSchemeChanged.connect(self._on_system_scheme_changed)
        self.apply()

    @property
    def mode(self) -> ThemeMode:
        return self._mode

    def set_mode(self, mode: ThemeMode) -> None:
        if mode == self._mode:
            return
        self._mode = mode
        self.apply()

    @property
    def scheme(self) -> Qt.ColorScheme:
        return resolve_scheme(self._mode)

    @property
    def tokens(self) -> Tokens:
        return tokens_for(self.scheme)

    @property
    def is_dark(self) -> bool:
        return self.scheme == Qt.ColorScheme.Dark

    def apply(self) -> None:
        """(Re)build the palette and stylesheet for the current mode and install them."""
        tokens = self.tokens
        self._app.setPalette(build_palette(tokens))
        self._app.setStyleSheet(build_stylesheet(tokens))
        self.themeChanged.emit()

    def _on_system_scheme_changed(self, _scheme: Qt.ColorScheme) -> None:
        if self._mode == ThemeMode.SYSTEM:
            self.apply()
