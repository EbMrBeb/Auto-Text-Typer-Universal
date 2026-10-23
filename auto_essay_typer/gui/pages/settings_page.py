"""The Settings page.

Minimal for now, by design: a theme selector and the mini-progress-window
toggle. More settings (profile management, defaults, etc.) will be added
here once the app-wide settings module (``app_settings.py``) lands from the
sibling work adding it in the main checkout -- this page's job for now is
just to host a Theme control and the one boolean the Type page's mini window
needs, not to anticipate that module's shape.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from auto_essay_typer.gui.controls import ToggleSwitch, switch_row
from auto_essay_typer.gui.theme import ThemeManager, ThemeMode

_THEME_LABELS: tuple[tuple[ThemeMode, str], ...] = (
    (ThemeMode.SYSTEM, "System"),
    (ThemeMode.LIGHT, "Light"),
    (ThemeMode.DARK, "Dark"),
)


class SettingsPage(QWidget):
    def __init__(
        self,
        theme_manager: ThemeManager,
        get_show_mini_window: Callable[[], bool],
        set_show_mini_window: Callable[[bool], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._theme_manager = theme_manager
        self._get_show_mini_window = get_show_mini_window
        self._set_show_mini_window = set_show_mini_window

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        title = QLabel("Settings")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        card = QWidget()
        card.setObjectName("Card")
        card.setMaximumWidth(560)  # a wide window shouldn't stretch two rows of controls edge to edge
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(14)

        theme_row = QHBoxLayout()
        theme_row.setSpacing(12)
        theme_label = QLabel("Theme")
        theme_label.setFixedWidth(72)  # the same label column as the Type page's rows
        theme_row.addWidget(theme_label)
        self.theme_combo = QComboBox()
        self.theme_combo.setAccessibleName("Theme")
        self.theme_combo.setToolTip("Match the system, or always use the light or dark theme.")
        theme_label.setBuddy(self.theme_combo)
        for mode, label in _THEME_LABELS:
            self.theme_combo.addItem(label, mode)
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(theme_manager.mode))
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        theme_row.addWidget(self.theme_combo, 1)
        card_layout.addLayout(theme_row)

        self.mini_window_checkbox = ToggleSwitch("Show mini progress window while typing")
        self.mini_window_checkbox.setObjectName("ToggleSwitch")
        self.mini_window_checkbox.setAccessibleName("Show mini progress window while typing")
        self.mini_window_checkbox.setToolTip(
            "Keep a small always-on-top progress window in the corner while text is typed."
        )
        self.mini_window_checkbox.setChecked(get_show_mini_window())
        self.mini_window_checkbox.toggled.connect(self._set_show_mini_window)
        card_layout.addLayout(switch_row(self.mini_window_checkbox))

        root.addWidget(card)

        more_label = QLabel("More settings are coming soon.")
        more_label.setObjectName("MutedLabel")
        root.addWidget(more_label)

        root.addStretch(1)

    def _on_theme_changed(self, _index: int) -> None:
        # ThemeMode is a str Enum; QComboBox.currentData() round-trips it
        # through Qt's QVariant, which can hand back a plain str that is ==
        # the enum member but not `is` it -- ThemeMode(...) restores the
        # real enum member rather than relying on callers to compare loosely.
        mode = ThemeMode(self.theme_combo.currentData())
        self._theme_manager.set_mode(mode)
