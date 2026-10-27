"""Design system: tokens, a QPalette, a stylesheet, and the application font.

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

Accessibility contract
----------------------
Every foreground/background pair the stylesheet produces meets WCAG 2.1 AA:
4.5:1 for text, 3:1 for the outlines and indicators that identify a control.
``tests/gui/test_theme.py`` recomputes those ratios from the tokens in both
themes, so a future palette tweak that breaks one fails CI instead of
shipping. The brand colors (``accent``, ``success``, ``danger``,
``attention``) are *fills*; each has a readable foreground twin
(``accent_text``, ``success_text``, ...) for text and outlines drawn on the
page surfaces, because a saturated brand color rarely clears 4.5:1 as text.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from enum import Enum
from string import Template

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

from auto_essay_typer.gui import icons
from auto_essay_typer.gui.focus_ring import install_focus_visible_filter
from auto_essay_typer.platforms import OperatingSystem, current_os


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

    # -- brand / semantic fills (identical in both light and dark) --------------
    accent: str
    accent_hover: str
    accent_pressed: str
    attention: str
    success: str
    danger: str

    # -- readable foregrounds: text/outlines drawn on the page surfaces ---------
    accent_text: str  # accent used as text, a link, or a focus/hover outline
    accent_indicator: str  # progress / slider / handle fill, >= 3:1 on its track
    success_text: str
    attention_text: str
    danger_text: str
    danger_hover: str  # fill of a destructive button under the pointer (white text on it)

    # -- surfaces, per theme mode --------------------------------------------
    window: str
    surface: str
    sidebar: str
    border: str  # decorative hairlines: card edges, dividers, progress tracks
    border_strong: str  # outline of an interactive control (>= 3:1 on surface)
    text: str
    muted_text: str  # secondary text (>= 4.5:1 on window, surface and sidebar)
    disabled_text: str  # intentionally low: reads as "unavailable"
    focus_ring: str  # keyboard focus ring on filled controls (>= 3:1 on the fill)

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
    #: Total height of every button, combo box and single-line input.
    control_height: int = 32

    @property
    def on_accent(self) -> str:
        """Text/icon color for content sitting on an accent-filled surface."""
        return "#FFFFFF"

    @property
    def button_height(self) -> int:
        """Alias of :attr:`control_height` (kept for callers of the old token name)."""
        return self.control_height


_ACCENT = "#6D5AE6"
_ATTENTION = "#F5A623"
_SUCCESS = "#2EAD6B"
_DANGER = "#E5484D"

LIGHT_TOKENS = Tokens(
    accent=_ACCENT,
    accent_hover="#5F4DDB",
    accent_pressed="#4F3DC6",
    attention=_ATTENTION,
    success=_SUCCESS,
    danger=_DANGER,
    accent_text="#5B48D6",
    accent_indicator=_ACCENT,
    success_text="#17724A",
    attention_text="#8A5700",
    danger_text="#B7242A",
    danger_hover="#C42B31",
    window="#F6F6F9",
    surface="#FFFFFF",
    sidebar="#EFEFF4",
    border="#E4E4EB",
    border_strong="#8A8AA0",
    text="#1C1C24",
    muted_text="#5E5E72",
    disabled_text="#9696AA",
    focus_ring="#1C1C24",
)

DARK_TOKENS = Tokens(
    accent=_ACCENT,
    accent_hover="#6250DB",
    accent_pressed="#5440C8",
    attention=_ATTENTION,
    success=_SUCCESS,
    danger=_DANGER,
    accent_text="#A89BFF",
    accent_indicator="#8F80FF",
    success_text="#4CC38A",
    attention_text="#F5A623",
    danger_text="#FF7A80",
    danger_hover="#C42B31",
    window="#16161C",
    surface="#1F1F27",
    sidebar="#1B1B22",
    border="#2E2E38",
    border_strong="#70708A",
    text="#ECECF1",
    muted_text="#9A9AAE",
    disabled_text="#6F6F83",
    focus_ring="#ECECF1",
)


# -- color math --------------------------------------------------------------------


def _rgb(color: str) -> tuple[float, float, float]:
    value = color.lstrip("#")
    return tuple(int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def relative_luminance(color: str) -> float:
    """WCAG 2.x relative luminance of a ``#RRGGBB`` color."""

    def linear(channel: float) -> float:
        return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(c) for c in _rgb(color))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first: str, second: str) -> float:
    """WCAG 2.x contrast ratio (1.0 to 21.0) between two ``#RRGGBB`` colors."""
    lighter, darker = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def tint(color: str, over: str, alpha: float) -> str:
    """``color`` at ``alpha`` opacity composited over ``over``, as a solid ``#RRGGBB``.

    The stylesheet uses solid colors rather than ``rgba()`` so the contrast of
    text on a tinted chip or badge is a fixed, testable number.
    """
    fg, bg = _rgb(color), _rgb(over)
    mixed = (round((alpha * f + (1 - alpha) * b) * 255) for f, b in zip(fg, bg))
    return "#{:02X}{:02X}{:02X}".format(*mixed)


# -- application font ----------------------------------------------------------------

#: Preferred UI families per OS, best first. Only families that actually exist
#: on the machine are ever requested (see :func:`resolve_ui_font_families`);
#: asking Qt for a missing family is what costs a slow alias lookup and logs
#: ``Populating font family aliases ... missing font family``.
_FONT_STACKS: dict[OperatingSystem, tuple[str, ...]] = {
    OperatingSystem.MACOS: (".AppleSystemUIFont", "SF Pro Text", "Helvetica Neue", "Helvetica", "Arial"),
    OperatingSystem.WINDOWS: ("Segoe UI", "Tahoma", "Arial"),
    OperatingSystem.LINUX: (
        "Inter",
        "Cantarell",
        "Noto Sans",
        "Ubuntu",
        "Roboto",
        "Open Sans",
        "DejaVu Sans",
        "Liberation Sans",
        "Arial",
    ),
}

#: Base UI text size. Pixel-sized (not point-sized) so 13 means 13 on every OS;
#: Qt's own default is 13pt on macOS but 9pt (12px at 96 dpi) on Windows.
BASE_FONT_PIXEL_SIZE = 13


def resolve_ui_font_families(
    os_: OperatingSystem,
    available: Iterable[str],
    platform_default: str | None = None,
) -> list[str]:
    """The families to request for the UI font on ``os_``, best first.

    ``platform_default`` (the family Qt's platform theme reports as the system
    UI font, when there is one) goes first: it is what "native" means on that
    machine. The per-OS stack follows. Only names present in ``available`` --
    compared case-insensitively -- are returned, spelled the way the font
    database spells them, so every entry is a family that really exists.
    """
    installed = {family.casefold(): family for family in available}
    wanted = ([platform_default] if platform_default else []) + list(_FONT_STACKS[os_])
    resolved: list[str] = []
    for family in wanted:
        real = installed.get(family.casefold())
        if real is not None and real not in resolved:
            resolved.append(real)
    return resolved


def build_app_font(
    os_: OperatingSystem | None = None,
    available: Collection[str] | None = None,
    platform_default: str | None = None,
) -> QFont | None:
    """The application font, or ``None`` when no known family is installed.

    ``None`` means "leave Qt's own default alone": better than naming a family
    that does not exist. All three arguments exist so tests can pin them; the
    defaults ask the running OS and Qt.
    """
    if available is None:
        available = QFontDatabase.families()
    if platform_default is None:
        # On macOS and Windows this is the system UI font. On a platform with no
        # theme (offscreen, some bare Linux sessions) it is Qt's placeholder
        # "Sans Serif", which usually is not installed -- the availability filter
        # below drops it.
        platform_default = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
    families = resolve_ui_font_families(os_ or current_os(), available, platform_default)
    if not families:
        return None
    font = QFont(families[0])
    font.setFamilies(families)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setPixelSize(BASE_FONT_PIXEL_SIZE)
    return font


# -- scheme resolution ---------------------------------------------------------------


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

    Covers the roles Fusion-styled widgets and the custom-painted widgets in
    ``gui/controls.py`` actually consult (including the Disabled group, so a
    disabled Type page during a run reads as disabled rather than merely
    unresponsive) -- not an exhaustive dump of every QPalette role.

    Role usage by custom painting: ``Highlight`` is the "on" fill and ``Mid`` the
    "off" track. The keyboard focus ring is *not* a palette role: ``Link`` is the
    accent-coloured link text, which is nearly the colour of the "on" track it
    surrounds. A custom-painted widget gets the ring's colour (``focus_ring``,
    the same near-black / near-white the stylesheet's rings use) from the
    stylesheet's ``qproperty-focusRingColor`` instead -- see ``ToggleSwitch``.
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
    palette.setColor(QPalette.ColorRole.BrightText, QColor(tokens.danger_text))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(tokens.accent))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(tokens.on_accent))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(tokens.muted_text))
    palette.setColor(QPalette.ColorRole.Link, QColor(tokens.accent_text))
    palette.setColor(QPalette.ColorRole.LinkVisited, QColor(tokens.accent_text))
    palette.setColor(QPalette.ColorRole.Mid, QColor(tokens.border_strong))
    palette.setColor(QPalette.ColorRole.Midlight, QColor(tokens.border))
    palette.setColor(QPalette.ColorRole.Dark, QColor(tokens.muted_text))
    palette.setColor(QPalette.ColorRole.Light, QColor(tokens.surface))
    palette.setColor(QPalette.ColorRole.Shadow, QColor(tokens.text))

    disabled = QPalette.ColorGroup.Disabled
    disabled_text = QColor(tokens.disabled_text)
    for role in (
        QPalette.ColorRole.Text,
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.PlaceholderText,
    ):
        palette.setColor(disabled, role, disabled_text)
    palette.setColor(disabled, QPalette.ColorRole.Highlight, QColor(tint(tokens.accent, tokens.surface, 0.45)))
    palette.setColor(disabled, QPalette.ColorRole.Mid, QColor(tokens.border))
    palette.setColor(disabled, QPalette.ColorRole.Link, disabled_text)
    return palette


# -- stylesheet --------------------------------------------------------------------

_STYLESHEET = Template(
    """
    QWidget {
        color: $text;
        selection-background-color: $accent;
        selection-color: $on_accent;
    }
    QWidget:disabled {
        color: $disabled_text;
    }

    QMainWindow, QDialog {
        background: $window;
    }

    QToolTip {
        background-color: $surface;
        color: $text;
        border: 1px solid $border_strong;
        padding: 4px 8px;
    }

    /* -- sidebar ----------------------------------------------------- */

    #Sidebar {
        background: $sidebar;
        border-right: 1px solid $border;
    }

    #SidebarTitle {
        font-size: 15px;
        font-weight: 600;
        color: $text;
    }

    QPushButton#NavButton {
        text-align: left;
        background: transparent;
        color: $text;
        border: 2px solid transparent;
        border-radius: ${radius_control}px;
        padding: 0 ${nav_pad}px;
        min-height: ${nav_inner}px;
        font-size: 13px;
    }
    QPushButton#NavButton:hover {
        background: $border;
    }
    QPushButton#NavButton[active="true"] {
        background: $accent;
        color: $on_accent;
        font-weight: 600;
    }
    QPushButton#NavButton[active="true"]:hover {
        background: $accent_hover;
    }
    QPushButton#NavButton[focusRing="true"] {
        border-color: $focus_ring;
    }

    /* -- generic surfaces and type ---------------------------------------- */

    #Card {
        background: $surface;
        border: 1px solid $border;
        border-radius: ${radius_card}px;
    }

    #PageTitle {
        font-size: 22px;
        font-weight: 600;
        color: $text;
    }

    #DialogTitle {
        font-size: 17px;
        font-weight: 600;
        color: $text;
    }

    #SectionTitle {
        font-size: 13px;
        font-weight: 600;
        color: $text;
    }

    #RunMessage {
        font-size: 15px;
        font-weight: 600;
        color: $text;
    }

    #SummaryValue {
        font-size: 18px;
        font-weight: 600;
        color: $text;
    }

    #MutedLabel, QLabel[muted="true"] {
        color: $muted_text;
        font-size: 12px;
    }
    #MutedLabel:disabled, QLabel[muted="true"]:disabled {
        color: $disabled_text;
    }

    QLabel#InlineMessage {
        font-size: 12px;
        font-weight: 600;
    }
    QLabel#InlineMessage[tone="danger"] {
        color: $danger_text;
    }

    /* -- buttons ---------------------------------------------------------------
       Qt's box model puts min-height on the *content* box, so every variant
       below spends the same ${control_height}px: border + min-height + border.
       Filled variants keep a constant 2px border (their own fill color) so the
       keyboard focus ring only recolors it; outlined variants trade a 1px
       border for a 2px one on focus and give the extra pixel back in padding
       and min-height, so nothing ever shifts when focus moves. */

    QPushButton {
        border-radius: ${radius_control}px;
        font-size: 13px;
    }

    QPushButton#PrimaryButton {
        background: $accent;
        color: $on_accent;
        border: 2px solid $accent;
        padding: 0 ${pad_filled}px;
        min-height: ${h_filled}px;
        font-weight: 600;
    }
    QPushButton#PrimaryButton:hover {
        background: $accent_hover;
        border-color: $accent_hover;
    }
    QPushButton#PrimaryButton:pressed {
        background: $accent_pressed;
        border-color: $accent_pressed;
    }
    QPushButton#PrimaryButton:disabled {
        background: $border;
        border-color: $border;
        color: $disabled_text;
    }
    QPushButton#PrimaryButton[focusRing="true"] {
        border-color: $focus_ring;
    }

    QPushButton#SecondaryButton, QPushButton#DestructiveButton {
        background: $surface;
        border: 1px solid $border_strong;
        padding: 0 ${pad_outlined}px;
        min-height: ${h_outlined}px;
    }
    QPushButton#SecondaryButton {
        color: $text;
    }
    QPushButton#SecondaryButton:hover {
        border-color: $accent_text;
        color: $accent_text;
    }
    QPushButton#SecondaryButton:pressed {
        background: $sidebar;
    }
    QPushButton#SecondaryButton:disabled, QPushButton#DestructiveButton:disabled {
        background: $surface;
        color: $disabled_text;
        border-color: $border;
    }
    QPushButton#SecondaryButton[focusRing="true"], QPushButton#DestructiveButton[focusRing="true"] {
        border: 2px solid $accent_text;
        padding: 0 ${pad_ring}px;
        min-height: ${h_ring}px;
    }

    QPushButton#DestructiveButton {
        color: $danger_text;
        border-color: $danger_text;
    }
    QPushButton#DestructiveButton:hover {
        background: $danger_hover;
        border-color: $danger_hover;
        color: $on_accent;
    }
    QPushButton#DestructiveButton:pressed {
        background: $danger_text;
        border-color: $danger_text;
        color: $surface;
    }

    /* -- inputs ------------------------------------------------------------ */

    QPlainTextEdit, QTextEdit {
        background: $surface;
        border: 1px solid $border_strong;
        border-radius: ${radius_control}px;
        padding: ${pad_text}px;
        selection-background-color: $accent;
        selection-color: $on_accent;
    }
    QLineEdit {
        background: $surface;
        border: 1px solid $border_strong;
        border-radius: ${radius_control}px;
        padding: 0 ${pad_text}px;
        min-height: ${h_outlined}px;
        selection-background-color: $accent;
        selection-color: $on_accent;
    }
    QPlainTextEdit:focus, QTextEdit:focus {
        border: 2px solid $accent_text;
        padding: ${pad_text_ring}px;
    }
    QLineEdit:focus {
        border: 2px solid $accent_text;
        padding: 0 ${pad_text_ring}px;
        min-height: ${h_ring}px;
    }
    QPlainTextEdit:disabled, QTextEdit:disabled, QLineEdit:disabled {
        background: $sidebar;
        border-color: $border;
    }
    /* The text box is disabled while a run types its contents; it stays readable
       (muted, not disabled-grey) so the user can follow along. */
    QPlainTextEdit:disabled, QTextEdit:disabled {
        color: $muted_text;
    }
    QLineEdit:disabled {
        color: $disabled_text;
    }

    QComboBox {
        background: $surface;
        border: 1px solid $border_strong;
        border-radius: ${radius_control}px;
        padding: 0 0 0 ${pad_text}px;
        min-height: ${h_outlined}px;
    }
    QComboBox:hover {
        border-color: $accent_text;
    }
    QComboBox[focusRing="true"], QComboBox:on {
        border: 2px solid $accent_text;
        padding: 0 0 0 ${pad_text_ring}px;
        min-height: ${h_ring}px;
    }
    QComboBox:disabled {
        background: $sidebar;
        color: $disabled_text;
        border-color: $border;
    }
    QComboBox::drop-down {
        subcontrol-origin: padding;
        subcontrol-position: center right;
        width: 30px;
        border: none;
        background: transparent;
    }
    QComboBox::down-arrow {
        image: url("$chevron");
        width: 12px;
        height: 12px;
    }
    QComboBox::down-arrow:disabled {
        image: url("$chevron_disabled");
    }
    QComboBox QAbstractItemView {
        background: $surface;
        border: 1px solid $border_strong;
        padding: 4px;
        selection-background-color: $accent;
        selection-color: $on_accent;
        outline: none;
    }
    QComboBox QAbstractItemView::item {
        min-height: 26px;
        padding: 0 8px;
    }

    /* -- slider (speed / mistakes) ------------------------------------------
       The handle's 2px surface-colored border is a gap ring around the dot; on
       keyboard focus the ring takes the focus color instead. */

    QSlider {
        min-height: 24px;
    }
    QSlider::groove:horizontal {
        height: 4px;
        background: $border;
        border-radius: 2px;
    }
    QSlider::sub-page:horizontal {
        background: $accent_indicator;
        border-radius: 2px;
    }
    QSlider::handle:horizontal {
        width: 14px;
        height: 14px;
        margin: -7px 0;
        border-radius: 9px;
        background: $accent_indicator;
        border: 2px solid $surface;
    }
    QSlider::handle:horizontal:hover {
        border-color: $accent_text;
    }
    QSlider[focusRing="true"]::handle:horizontal {
        border-color: $focus_ring;
    }
    QSlider::groove:horizontal:disabled {
        background: $border;
    }
    QSlider::sub-page:horizontal:disabled {
        background: $border_strong;
    }
    QSlider::handle:horizontal:disabled {
        background: $border_strong;
        border-color: $surface;
    }

    /* -- checkboxes ------------------------------------------------------------
       A plain QCheckBox. (The on/off switches on the Type and Settings pages are
       ToggleSwitch widgets, which paint themselves and ignore these rules.) */

    QCheckBox {
        spacing: ${space_sm}px;
        font-size: 13px;
    }
    QCheckBox::indicator {
        width: 16px;
        height: 16px;
        border: 1px solid $border_strong;
        border-radius: 4px;
        background: $surface;
    }
    QCheckBox::indicator:hover {
        border-color: $accent_text;
    }
    QCheckBox::indicator:checked {
        background: $accent;
        border-color: $accent;
        image: url("$check");
    }
    QCheckBox::indicator:disabled {
        background: $sidebar;
        border-color: $border;
    }
    QCheckBox::indicator:checked:disabled {
        background: $accent_disabled;
        border-color: $accent_disabled;
    }
    QCheckBox[focusRing="true"]::indicator {
        border: 2px solid $focus_ring;
    }
    /* The switch paints its own ring; this only hands it the colour, so the ring
       follows a light/dark change when the stylesheet is re-applied. */
    QCheckBox#ToggleSwitch {
        qproperty-focusRingColor: $focus_ring;
    }

    /* -- progress bar -------------------------------------------------------- */

    QProgressBar {
        background: $border;
        border: none;
        border-radius: 4px;
        min-height: 8px;
        max-height: 8px;
        text-align: center;
        color: transparent;
    }
    QProgressBar::chunk {
        background: $accent_indicator;
        border-radius: 4px;
    }

    /* -- permission chip (footer) -------------------------------------------- */

    /* Qt draws a rounded rect only while border-radius <= half the height, and
       falls back to square corners beyond it: 22px tall, so 11px, a true pill. */
    QPushButton#PermissionChip {
        border: 2px solid transparent;
        border-radius: 11px;
        padding: 0 ${chip_pad}px;
        min-height: 18px;
        font-size: 12px;
        font-weight: 600;
        text-align: left;
    }
    QPushButton#PermissionChip[state="ready"] {
        background: $chip_ready_bg;
        color: $success_text;
    }
    QPushButton#PermissionChip[state="warning"] {
        background: $chip_warning_bg;
        color: $attention_text;
    }
    QPushButton#PermissionChip[focusRing="true"] {
        border-color: $focus_ring;
    }

    /* -- status badges (preview dialog verification, etc.) ------------------- */

    QLabel#StatusBadge[tone="success"] {
        background: $badge_success_bg;
        color: $success_text;
        border-radius: ${radius_control}px;
        padding: ${space_sm}px ${space_md}px;
        font-weight: 600;
    }
    QLabel#StatusBadge[tone="danger"] {
        background: $badge_danger_bg;
        color: $danger_text;
        border-radius: ${radius_control}px;
        padding: ${space_sm}px ${space_md}px;
        font-weight: 600;
    }

    /* -- scroll areas and scroll bars ---------------------------------------- */

    QScrollArea {
        border: none;
        background: transparent;
    }
    QScrollArea > QWidget > QWidget {
        background: transparent;
    }
    /* A scroll area a keyboard user can focus (to scroll it with the arrow keys)
       gets a constant transparent border that the focus ring recolors. */
    QScrollArea#Timeline, QScrollArea#PageScroll {
        border: 2px solid transparent;
        border-radius: ${radius_control}px;
    }
    QScrollArea#Timeline[focusRing="true"], QScrollArea#PageScroll[focusRing="true"] {
        border-color: $accent_text;
    }

    #MiniWindow {
        background: $surface;
    }

    QScrollBar:vertical {
        background: transparent;
        width: 10px;
        margin: 0;
    }
    QScrollBar:horizontal {
        background: transparent;
        height: 10px;
        margin: 0;
    }
    QScrollBar::handle:vertical {
        background: $border_strong;
        border-radius: 4px;
        min-height: 32px;
        margin: 2px;
    }
    QScrollBar::handle:horizontal {
        background: $border_strong;
        border-radius: 4px;
        min-width: 32px;
        margin: 2px;
    }
    QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
        background: $muted_text;
    }
    QScrollBar::add-line, QScrollBar::sub-line {
        width: 0;
        height: 0;
        background: none;
        border: none;
    }
    QScrollBar::add-page, QScrollBar::sub-page {
        background: transparent;
    }

    /* -- menus (the text box's right-click menu) ---------------------------- */

    QMenu {
        background: $surface;
        border: 1px solid $border_strong;
        border-radius: ${radius_control}px;
        padding: 4px;
    }
    QMenu::item {
        padding: 6px 24px 6px 12px;
        border-radius: 6px;
    }
    QMenu::item:selected {
        background: $accent;
        color: $on_accent;
    }
    QMenu::item:disabled {
        color: $disabled_text;
        background: transparent;
    }
    QMenu::separator {
        height: 1px;
        background: $border;
        margin: 4px 8px;
    }

    QStatusBar {
        background: $surface;
        border-top: 1px solid $border;
    }
    QStatusBar::item {
        border: none;
    }
    """
)


def build_stylesheet(tokens: Tokens) -> str:
    """A Qt stylesheet layering the design system on top of Fusion.

    Object names used as stylesheet hooks by widget code (kept in one place
    here as the contract between this module and the rest of ``gui/``):

    ``Sidebar``, ``SidebarTitle``, ``NavButton`` (property ``active``),
    ``Card``, ``PageTitle``, ``DialogTitle``, ``SectionTitle``, ``RunMessage``,
    ``SummaryValue``, ``MutedLabel``, ``PrimaryButton``, ``SecondaryButton``,
    ``DestructiveButton``, ``PermissionChip`` (property ``state`` =
    ``ready``/``warning``), ``StatusBadge`` (property ``tone`` =
    ``success``/``danger``), ``InlineMessage`` (property ``tone`` = ``danger``).

    The dynamic property ``focusRing`` (set by
    :class:`~auto_essay_typer.gui.focus_ring.FocusVisibleFilter`) marks the
    widget that has *keyboard* focus, so rings appear on Tab but not on a click.
    ``ToggleSwitch`` is a custom-painted widget (``gui/controls.py``) and draws
    itself from the palette; the stylesheet only sets its label font and hands
    it the colour of its focus ring (``qproperty-focusRingColor``).
    """
    t = tokens
    h = t.control_height
    chevron = icons.themed_file("chevron-down", t.muted_text, 12, stroke_width=2.2)
    chevron_disabled = icons.themed_file("chevron-down", t.disabled_text, 12, stroke_width=2.2)
    values = {
        **{field: getattr(t, field) for field in t.__dataclass_fields__},
        "on_accent": t.on_accent,
        "control_height": h,
        # Content-box heights (Qt's min-height) for each border/ring width.
        "h_filled": h - 4,  # 2px border
        "h_outlined": h - 2,  # 1px border
        "h_ring": h - 4,  # 2px focus border
        "nav_inner": h - 4,
        "nav_pad": 10,
        "pad_filled": 14,
        "pad_outlined": 15,
        "pad_ring": 14,
        "pad_text": 11,
        "pad_text_ring": 10,
        "chip_pad": 10,
        "chevron": chevron,
        "chevron_disabled": chevron_disabled,
        "check": icons.themed_file("check", t.on_accent, 12, stroke_width=3.0),
        "accent_disabled": tint(t.accent, t.surface, 0.45),
        "chip_ready_bg": tint(t.success, t.surface, 0.14),
        "chip_warning_bg": tint(t.attention, t.surface, 0.18),
        "badge_success_bg": tint(t.success, t.window, 0.16),
        "badge_danger_bg": tint(t.danger, t.window, 0.16),
    }
    return _STYLESHEET.substitute(values)


class ThemeManager(QObject):
    """Owns the active :class:`ThemeMode`, applies it, and reacts to OS theme changes.

    Construct one per application, call :meth:`apply` once at startup (the
    constructor does this for you), and read :attr:`tokens`/:attr:`is_dark`
    from it wherever a widget needs a token value outside the stylesheet
    (e.g. to recolor an SVG icon). :data:`themeChanged` fires after every
    successful :meth:`set_mode` and after every OS-driven scheme change while
    in ``SYSTEM`` mode, so widgets that cache a derived value (icon pixmaps,
    chart colors) know when to rebuild it.

    The constructor also installs the application font (a per-OS stack that only
    names families that exist, see :func:`build_app_font`) and the
    keyboard-focus tracker that drives the ``focusRing`` stylesheet property.
    """

    themeChanged = Signal()

    def __init__(
        self, app: QApplication, mode: ThemeMode = ThemeMode.SYSTEM, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._app = app
        self._mode = mode
        app.setStyle("Fusion")
        font = build_app_font()
        if font is not None:
            app.setFont(font)
        # Parented to this manager: when the manager goes away so does the
        # filter, which Qt then unregisters from the application on its own.
        # Replaces the filter an earlier manager installed rather than stacking
        # a second one beside it (see install_focus_visible_filter).
        self._focus_filter = install_focus_visible_filter(app, self)
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
