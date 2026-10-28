"""Modal shown when Start is pressed but input-injection permission is missing.

Every OS/permission call this dialog makes is injected with a real default
(``platforms.check_input_permission`` and friends) so tests can fake both
the not-granted and granted states without touching a real OS permission
store -- see ``tests/gui/test_permission_dialog.py``.
"""

from __future__ import annotations

import html
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from auto_essay_typer import platforms
from auto_essay_typer.gui.theme import Tokens
from auto_essay_typer.platforms import OperatingSystem, PermissionStatus


def prettify_dashes(text: str) -> str:
    """Typographic dashes for text written in ASCII.

    ``platforms.py`` words its permission steps for a terminal as well as for
    a window, so a spaced ``--`` stays there and becomes a real em dash on screen.
    Use this for any of that text shown in the GUI (this dialog, the onboarding step).
    """
    return text.replace(" -- ", " — ")


class PermissionDialog(QDialog):
    """Explains why typing can't start yet, and how to fix it.

    ``granted()`` is True only after "Check again" finds permission has
    actually been granted, at which point the dialog closes itself
    (``accept()``). "Cancel" (or the window's close button) rejects the
    dialog with ``granted()`` still False. Neither button starts typing --
    the caller re-checks ``granted()`` after ``exec()`` returns and decides
    whether to proceed.
    """

    def __init__(
        self,
        status: PermissionStatus,
        tokens: Tokens,
        *,
        permission_checker: Callable[[], PermissionStatus] = platforms.check_input_permission,
        request_prompt: Callable[[], bool] = platforms.request_input_permission_prompt,
        open_settings: Callable[[], bool] = platforms.open_permission_settings,
        current_os: Callable[[], OperatingSystem] = platforms.current_os,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Permission needed")
        self.setModal(True)
        self.setMinimumWidth(440)

        self._permission_checker = permission_checker
        self._open_settings = open_settings
        self._granted = False

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 24, 24, 20)

        title = QLabel("Auto Text Typer needs permission to type")
        title.setObjectName("DialogTitle")
        title.setWordWrap(True)
        layout.addWidget(title)

        self._summary_label = QLabel(prettify_dashes(status.summary))
        self._summary_label.setWordWrap(True)
        layout.addWidget(self._summary_label)

        self._steps_label = QLabel(self._format_steps(status.fix_steps))
        self._steps_label.setWordWrap(True)
        self._steps_label.setTextFormat(Qt.TextFormat.RichText)
        self._steps_label.setVisible(bool(status.fix_steps))
        layout.addWidget(self._steps_label)

        # Hidden until a "Check again" comes back still not granted: an empty,
        # always-visible label would leave a blank row of spacing in the dialog.
        self._retry_label = QLabel("")
        self._retry_label.setObjectName("InlineMessage")
        self._retry_label.setProperty("tone", "danger")
        self._retry_label.setWordWrap(True)
        self._retry_label.setVisible(False)
        layout.addWidget(self._retry_label)

        layout.addSpacing(8)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)

        self.open_settings_button = QPushButton("Open Settings")
        self.open_settings_button.setObjectName("SecondaryButton")
        self.open_settings_button.setAccessibleName("Open system settings")
        self.open_settings_button.setToolTip("Open the system settings page where this permission is granted.")
        self.open_settings_button.setVisible(status.can_open_settings)
        self.open_settings_button.clicked.connect(self._on_open_settings)
        button_row.addWidget(self.open_settings_button)
        button_row.addStretch(1)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("SecondaryButton")
        self.cancel_button.setAccessibleName("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        button_row.addWidget(self.cancel_button)

        self.check_again_button = QPushButton("Check again")
        self.check_again_button.setObjectName("PrimaryButton")
        self.check_again_button.setAccessibleName("Check again")
        self.check_again_button.setToolTip("Check whether the permission has been granted yet.")
        self.check_again_button.setDefault(True)
        self.check_again_button.clicked.connect(self._on_check_again)
        button_row.addWidget(self.check_again_button)

        layout.addLayout(button_row)

        if current_os() == OperatingSystem.MACOS:
            request_prompt()

    @staticmethod
    def _format_steps(steps: tuple[str, ...]) -> str:
        if not steps:
            return ""
        items = "".join(f"<li style='margin-bottom:4px;'>{html.escape(prettify_dashes(step))}</li>" for step in steps)
        return f"<ol style='margin:0; padding-left:18px;'>{items}</ol>"

    def granted(self) -> bool:
        return self._granted

    def _on_open_settings(self) -> None:
        self._open_settings()

    def _on_check_again(self) -> None:
        status = self._permission_checker()
        if status.granted:
            self._granted = True
            self.accept()
            return
        self._retry_label.setText("Still not granted. Complete the steps above, then try again.")
        self._retry_label.setVisible(True)
