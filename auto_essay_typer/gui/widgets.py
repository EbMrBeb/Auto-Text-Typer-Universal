"""Small reusable widgets shared by the Type, Settings, Advanced and onboarding surfaces.

``theme.py`` owns the app-wide stylesheet and is deliberately left alone by
the feature work that added these widgets, so each widget here carries a
small *local* stylesheet built from the current :class:`~auto_essay_typer.gui.theme.Tokens`
and rebuilds it when the theme changes. Pass the app's
:class:`~auto_essay_typer.gui.theme.ThemeManager` to keep a widget in step
with a live light/dark switch; without one a widget renders with the light
tokens (handy in tests and one-off dialogs).

Everything that is not specific to one surface lives here:

* :func:`make_card` -- the bordered ``Card`` container used across pages.
* :class:`Banner` -- an inline notice (info / success / warning / danger),
  optionally dismissible.
* :class:`CollapsibleSection` -- a titled section whose body folds away.
* :class:`LinkButton` -- a button that reads as a hyperlink.
* :func:`set_tone` -- tint a label for inline validation messages.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QTransform
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from auto_essay_typer.gui import icons
from auto_essay_typer.gui.theme import LIGHT_TOKENS, ThemeManager, Tokens

#: Tones a :class:`Banner` / :func:`set_tone` understand.
TONES = ("info", "success", "warning", "danger", "muted")


def tone_color(tokens: Tokens, tone: str) -> str:
    """The ``#RRGGBB`` colour that stands for ``tone`` in ``tokens``."""
    return {
        "info": tokens.accent,
        "success": tokens.success,
        "warning": tokens.attention,
        "danger": tokens.danger,
        "muted": tokens.muted_text,
    }[tone]


def rgba(hex_color: str, alpha: float) -> str:
    """A stylesheet ``rgba(...)`` string for ``#RRGGBB`` at ``alpha`` (0..1)."""
    value = hex_color.lstrip("#")
    r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alpha:.2f})"


def make_card(object_name: str = "Card") -> QWidget:
    """A plain container styled by the app stylesheet's ``Card`` rule."""
    card = QWidget()
    card.setObjectName(object_name)
    return card


def set_tone(label: QLabel, tone: str, tokens: Tokens) -> None:
    """Colour ``label``'s text for ``tone`` (used for inline validation messages)."""
    label.setStyleSheet(f"color: {tone_color(tokens, tone)};")


class _Themed:
    """Mixin: remembers the theme manager and restyles when the theme changes.

    Subclasses implement :meth:`_restyle` and call :meth:`_bind_theme` once at
    the end of ``__init__``.
    """

    _theme_manager: ThemeManager | None = None

    def _bind_theme(self, theme_manager: ThemeManager | None) -> None:
        self._theme_manager = theme_manager
        if theme_manager is not None:
            theme_manager.themeChanged.connect(self._restyle)  # type: ignore[attr-defined]
        self._restyle()

    @property
    def tokens(self) -> Tokens:
        return self._theme_manager.tokens if self._theme_manager is not None else LIGHT_TOKENS

    def _restyle(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


# --- Banner ---------------------------------------------------------------------

_BANNER_ICONS = {
    "info": "help",
    "success": "check-circle",
    "warning": "alert-triangle",
    "danger": "alert-triangle",
    "muted": "help",
}


class Banner(QFrame, _Themed):
    """An inline notice: a tone-tinted strip with an icon, a message and an optional close button.

    ``set_message`` shows it; ``dismiss`` (or the close button) hides it and
    emits :attr:`dismissed`. Use :meth:`add_action` for a link-style action
    such as "Open Settings".
    """

    dismissed = Signal()

    def __init__(
        self,
        tone: str = "info",
        *,
        dismissible: bool = True,
        theme_manager: ThemeManager | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if tone not in TONES:
            raise ValueError(f"unknown tone {tone!r}; expected one of {TONES}")
        self.setObjectName("Banner")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._tone = tone

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 8, 10)
        layout.setSpacing(10)

        self.icon_label = QLabel()
        self.icon_label.setObjectName("BannerIcon")
        self.icon_label.setFixedSize(20, 20)
        layout.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignTop)

        self.message_label = QLabel()
        self.message_label.setObjectName("BannerText")
        self.message_label.setWordWrap(True)
        self.message_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.message_label, 1)

        self._actions_layout = QHBoxLayout()
        self._actions_layout.setSpacing(8)
        layout.addLayout(self._actions_layout)

        self.close_button = QPushButton()
        self.close_button.setObjectName("BannerClose")
        self.close_button.setFlat(True)
        self.close_button.setToolTip("Dismiss")
        self.close_button.setAccessibleName("Dismiss")
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setFixedSize(24, 24)
        self.close_button.setIconSize(QSize(14, 14))
        self.close_button.setVisible(dismissible)
        self.close_button.clicked.connect(self.dismiss)
        layout.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)

        self.setVisible(False)
        self._bind_theme(theme_manager)

    # -- content ---------------------------------------------------------------------

    def message(self) -> str:
        return self.message_label.text()

    def tone(self) -> str:
        return self._tone

    def set_message(self, text: str, tone: str | None = None) -> None:
        """Show ``text`` (an empty string hides the banner), optionally retoning it."""
        if tone is not None and tone != self._tone:
            if tone not in TONES:
                raise ValueError(f"unknown tone {tone!r}; expected one of {TONES}")
            self._tone = tone
            self._restyle()
        self.message_label.setText(text)
        self.setVisible(bool(text))

    def add_action(self, text: str, callback: Callable[[], None]) -> "LinkButton":
        button = LinkButton(text, theme_manager=self._theme_manager)
        button.clicked.connect(lambda _checked=False: callback())
        self._actions_layout.addWidget(button)
        return button

    def dismiss(self) -> None:
        self.setVisible(False)
        self.dismissed.emit()

    # -- theming ----------------------------------------------------------------------

    def _restyle(self) -> None:
        tokens = self.tokens
        color = tone_color(tokens, self._tone)
        self.setStyleSheet(
            f"""
            QFrame#Banner {{
                background: {rgba(color, 0.12)};
                border: 1px solid {rgba(color, 0.40)};
                border-radius: {tokens.radius_control}px;
            }}
            QLabel#BannerText {{ color: {tokens.text}; font-size: 13px; background: transparent; }}
            QLabel#BannerIcon {{ background: transparent; }}
            QPushButton#BannerClose {{ border: none; background: transparent; padding: 0; }}
            QPushButton#BannerClose:hover {{ background: {rgba(color, 0.18)}; border-radius: 12px; }}
            """
        )
        self.icon_label.setPixmap(icons.pixmap(_BANNER_ICONS[self._tone], color, 18))
        self.close_button.setIcon(icons.icon("close", tokens.muted_text, 14))


# --- LinkButton -----------------------------------------------------------------


class LinkButton(QPushButton, _Themed):
    """A flat button that reads as a hyperlink (accent colour, underlined on hover)."""

    def __init__(
        self,
        text: str = "",
        *,
        theme_manager: ThemeManager | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setObjectName("LinkButton")
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._bind_theme(theme_manager)

    def _restyle(self) -> None:
        tokens = self.tokens
        self.setStyleSheet(
            f"""
            QPushButton#LinkButton {{
                color: {tokens.accent};
                background: transparent;
                border: none;
                padding: 2px 0;
                font-size: 12px;
                text-align: left;
            }}
            QPushButton#LinkButton:hover {{ color: {tokens.accent_hover}; text-decoration: underline; }}
            QPushButton#LinkButton:disabled {{ color: {tokens.muted_text}; }}
            """
        )


# --- CollapsibleSection ------------------------------------------------------------


class CollapsibleSection(QFrame, _Themed):
    """A titled card whose body can be folded away by clicking the header.

    Put content in :attr:`body_layout`. ``toggled(bool)`` fires with the new
    expanded state. Styled as a card, so it can sit directly in a page.
    """

    toggled = Signal(bool)

    def __init__(
        self,
        title: str,
        *,
        expanded: bool = True,
        subtitle: str = "",
        theme_manager: ThemeManager | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._expanded = expanded

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.header_button = QPushButton(title)
        self.header_button.setObjectName("SectionHeader")
        self.header_button.setFlat(True)
        self.header_button.setCheckable(True)
        self.header_button.setChecked(expanded)
        self.header_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header_button.setIconSize(QSize(14, 14))
        self.header_button.setAccessibleName(title)
        self.header_button.toggled.connect(self._on_header_toggled)
        outer.addWidget(self.header_button)

        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("MutedLabel")
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setContentsMargins(16, 0, 16, 6)
        self.subtitle_label.setVisible(bool(subtitle) and expanded)
        outer.addWidget(self.subtitle_label)

        self.body = QWidget()
        self.body.setObjectName("SectionBody")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(16, 4, 16, 14)
        self.body_layout.setSpacing(10)
        self.body.setVisible(expanded)
        outer.addWidget(self.body)

        self._bind_theme(theme_manager)

    def title(self) -> str:
        return self.header_button.text()

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        self.header_button.setChecked(expanded)

    def _on_header_toggled(self, checked: bool) -> None:
        self._expanded = checked
        self.body.setVisible(checked)
        self.subtitle_label.setVisible(bool(self.subtitle_label.text()) and checked)
        self._refresh_chevron()
        self.toggled.emit(checked)

    # -- theming ----------------------------------------------------------------------

    def _refresh_chevron(self) -> None:
        tokens = self.tokens
        pixmap = icons.pixmap("chevron-down", tokens.muted_text, 14)
        if not self._expanded:
            pixmap = pixmap.transformed(QTransform().rotate(-90), Qt.TransformationMode.SmoothTransformation)
        self.header_button.setIcon(QIcon(pixmap))

    def _restyle(self) -> None:
        tokens = self.tokens
        self.header_button.setStyleSheet(
            f"""
            QPushButton#SectionHeader {{
                text-align: left;
                background: transparent;
                border: none;
                padding: 12px 16px;
                font-size: 13px;
                font-weight: 600;
                color: {tokens.text};
            }}
            QPushButton#SectionHeader:hover {{ color: {tokens.accent}; }}
            """
        )
        self._refresh_chevron()
