"""Helpers shared by the Advanced page and the two editors it is built from.

Kept apart from ``widgets.py`` (which belongs to the foundation layer) so the
Advanced page can carry its own small pieces without reaching into private
names there:

* :class:`ThemedMixin` -- remember the ``ThemeManager`` and rebuild a widget's
  local stylesheet whenever the theme changes.
* :class:`NoWheelSpinBox` / :class:`NoWheelDoubleSpinBox` / :class:`NoWheelComboBox`
  -- inputs that ignore the mouse wheel until they have keyboard focus, so
  scrolling a long page can never silently change a typing setting.
* :func:`spin_arrow_urls` -- tinted chevron images for styled spin boxes.
* :func:`humanize_ms` / :func:`humanize_ms_range` -- "about 20 minutes".
* :func:`default_confirm`, :func:`default_name_prompt`,
  :func:`default_open_folder` -- the real dialogs the page falls back to when
  a test does not inject a fake.
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QTransform
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QInputDialog,
    QMessageBox,
    QSpinBox,
    QWidget,
)

from auto_essay_typer.gui import icons
from auto_essay_typer.gui.theme import LIGHT_TOKENS, ThemeManager, Tokens
from auto_essay_typer.gui.widgets import rgba

# --- theming ----------------------------------------------------------------------


class ThemedMixin:
    """Mixin: remembers the theme manager and restyles when the theme changes.

    Subclasses implement :meth:`_restyle` and call :meth:`_bind_theme` once at
    the end of ``__init__`` (after every child widget the styling touches
    exists). Without a manager the light tokens are used.
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


# --- wheel-safe inputs ---------------------------------------------------------------


#: Breathing room between a spin box's left border and its text (the style
#: sheet's left padding does not reach the embedded line edit).
_SPIN_TEXT_MARGIN = 6


class NoWheelSpinBox(QSpinBox):
    """A spin box that only reacts to the wheel while it has focus."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.lineEdit().setTextMargins(_SPIN_TEXT_MARGIN, 0, 0, 0)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()  # let the surrounding scroll area scroll instead


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    """The floating-point twin of :class:`NoWheelSpinBox`."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.lineEdit().setTextMargins(_SPIN_TEXT_MARGIN, 0, 0, 0)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NoWheelComboBox(QComboBox):
    """A combo box that only reacts to the wheel while it has focus."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


# --- spin box arrows -----------------------------------------------------------------

_arrow_directory: Path | None = None


def _arrows_dir() -> Path | None:
    global _arrow_directory
    if _arrow_directory is None:
        try:
            created = Path(tempfile.mkdtemp(prefix="auto_text_typer_arrows_"))
        except OSError:
            return None
        atexit.register(shutil.rmtree, created, ignore_errors=True)
        _arrow_directory = created
    return _arrow_directory


def spin_arrow_urls(color: str) -> tuple[str, str] | None:
    """Stylesheet-ready paths to ``(up, down)`` chevron images tinted ``color``.

    Qt style sheets can only take an arrow ``image:`` from a file, so the two
    small PNGs are written once per colour into a temp directory that is
    removed at exit. Returns ``None`` if they cannot be written, in which case
    the spin boxes simply keep the style's own arrows.
    """
    folder = _arrows_dir()
    if folder is None:
        return None
    key = color.lstrip("#").lower()
    down = folder / f"down-{key}.png"
    up = folder / f"up-{key}.png"
    if not (down.exists() and up.exists()):
        pixmap = icons.pixmap("chevron-down", color, 12)
        flipped = pixmap.transformed(QTransform().rotate(180), Qt.TransformationMode.SmoothTransformation)
        if not (pixmap.save(str(down)) and flipped.save(str(up))):
            return None
    return up.as_posix(), down.as_posix()


def spinbox_stylesheet(tokens: Tokens) -> str:
    """Rounded, theme-aware styling for ``QSpinBox`` / ``QDoubleSpinBox``.

    Mirrors the app stylesheet's ``QLineEdit`` look (border, radius) which
    ``theme.py`` does not cover for spin boxes. A spin box whose dynamic
    property ``invalid`` is true gets a danger-coloured border.
    """
    arrows = spin_arrow_urls(tokens.muted_text)
    arrow_rules = ""
    if arrows is not None:
        up, down = arrows
        arrow_rules = f"""
        QSpinBox::up-button, QDoubleSpinBox::up-button {{
            subcontrol-origin: border; subcontrol-position: top right;
            width: 20px; border: none; background: transparent; margin: 2px 2px 0 0;
        }}
        QSpinBox::down-button, QDoubleSpinBox::down-button {{
            subcontrol-origin: border; subcontrol-position: bottom right;
            width: 20px; border: none; background: transparent; margin: 0 2px 2px 0;
        }}
        QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url("{up}"); width: 10px; height: 10px; }}
        QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url("{down}"); width: 10px; height: 10px; }}
        """
    return f"""
        QSpinBox, QDoubleSpinBox {{
            background: {tokens.surface};
            color: {tokens.text};
            border: 1px solid {tokens.border};
            border-radius: {tokens.radius_control}px;
            padding: 3px 24px 3px 8px;
            min-height: 22px;
            selection-background-color: {tokens.accent};
            selection-color: {tokens.on_accent};
        }}
        QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {tokens.accent}; }}
        QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {tokens.accent}; }}
        QSpinBox:disabled, QDoubleSpinBox:disabled {{
            background: {tokens.sidebar}; color: {tokens.muted_text};
        }}
        QSpinBox[invalid="true"], QDoubleSpinBox[invalid="true"] {{
            border: 1px solid {tokens.danger};
        }}
        {arrow_rules}
    """


#: Width reserved for the page's vertical scroll bar (handle plus margins).
SCROLLBAR_WIDTH = 14


def scrollbar_stylesheet(tokens: Tokens) -> str:
    """A slim, theme-aware vertical scroll bar for the Advanced page's scroll area.

    The track is transparent and the handle disappears when there is nothing to
    scroll, so the always-on scroll bar (kept always-on so the column width
    never jumps) is invisible until it is needed.
    """
    handle = rgba(tokens.muted_text, 0.45)
    hover = rgba(tokens.muted_text, 0.75)
    return f"""
        QScrollArea QScrollBar:vertical {{
            background: transparent; width: {SCROLLBAR_WIDTH}px; margin: 0;
        }}
        QScrollArea QScrollBar::handle:vertical {{
            background: {handle}; border-radius: 4px; min-height: 36px; margin: 2px 3px;
        }}
        QScrollArea QScrollBar::handle:vertical:hover {{ background: {hover}; }}
        QScrollArea QScrollBar::handle:vertical:disabled {{ background: transparent; }}
        QScrollArea QScrollBar::add-line:vertical, QScrollArea QScrollBar::sub-line:vertical {{
            height: 0; width: 0; border: none; background: none;
        }}
        QScrollArea QScrollBar::add-page:vertical, QScrollArea QScrollBar::sub-page:vertical {{
            background: transparent;
        }}
    """


# --- human-readable durations --------------------------------------------------------


def _duration_unit(seconds: float) -> tuple[float, str]:
    """Pick a friendly ``(amount, unit)`` for ``seconds``.

    Under ten seconds keeps one decimal ("1.5 seconds"); then whole seconds up
    to two minutes, whole minutes up to ninety, and half-hours after that.
    """
    if seconds < 10:
        return round(seconds, 1), "second"
    if seconds < 120:
        return float(round(seconds)), "second"
    minutes = seconds / 60.0
    if minutes < 90:
        return float(round(minutes)), "minute"
    return round(minutes / 60.0 * 2) / 2, "hour"


def _plural(amount: float, unit: str) -> str:
    return f"{amount:g} {unit}{'' if amount == 1 else 's'}"


def humanize_ms(ms: float) -> str:
    """``"about 20 minutes"`` for a duration in milliseconds; ``""`` when under a second."""
    if ms < 1000:
        return ""
    amount, unit = _duration_unit(ms / 1000.0)
    return f"about {_plural(amount, unit)}"


def humanize_ms_range(low_ms: float, high_ms: float) -> str:
    """``"about 7 to 15 minutes"`` for a ``(low, high)`` pair; ``""`` when both are under a second."""
    if high_ms < 1000:
        return ""
    low_amount, low_unit = _duration_unit(low_ms / 1000.0)
    high_amount, high_unit = _duration_unit(high_ms / 1000.0)
    if low_unit == high_unit:
        return f"about {low_amount:g} to {_plural(high_amount, high_unit)}"
    return f"about {_plural(low_amount, low_unit)} to {_plural(high_amount, high_unit)}"


# --- real dialogs (replaced by fakes in tests) ---------------------------------------


def default_confirm(parent: QWidget | None, title: str, text: str) -> bool:
    """Ask a yes/no question with the standard message box; ``No`` is the default."""
    answer = QMessageBox.question(
        parent,
        title,
        text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return answer == QMessageBox.StandardButton.Yes


def default_name_prompt(parent: QWidget | None, title: str, label: str, initial: str) -> str | None:
    """Ask for a line of text; ``None`` when the user cancels."""
    text, accepted = QInputDialog.getText(parent, title, label, text=initial)
    return text if accepted else None


def default_open_folder(path: Path) -> None:
    """Reveal ``path`` in the system file manager."""
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
