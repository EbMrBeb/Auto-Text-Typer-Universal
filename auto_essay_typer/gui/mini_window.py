"""A small always-on-top progress window, shown while a run is in flight.

Never steals keyboard focus (``Qt.WindowDoesNotAcceptFocus`` +
``WA_ShowWithoutActivating``): the whole point of showing it is to let the
user see progress *while their real focus stays in the document being
typed into*. It reads state purely from a :class:`RunController`'s signals
and never talks to a ``Driver`` directly.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from auto_essay_typer.gui.a11y import spoken_duration
from auto_essay_typer.gui.run_controller import RunController
from auto_essay_typer.progress import format_mmss

#: States in which the mini window should be visible at all.
VISIBLE_STATES = frozenset({"countdown", "typing", "paused", "resuming"})

_STATUS_TEXT = {
    "countdown": "Starting…",
    "typing": "Typing…",
    "paused": "Paused",
    "resuming": "Resuming…",
}

#: Margin, in pixels, kept between the window and the screen's edge.
_CORNER_MARGIN = 16


class MiniProgressWindow(QWidget):
    def __init__(self, run_controller: RunController, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setObjectName("MiniWindow")
        self.setWindowTitle("Auto Text Typer")
        self.setFixedWidth(260)

        self._controller = run_controller

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        self.status_label = QLabel("Starting…")
        self.status_label.setObjectName("SectionTitle")
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setAccessibleName("Typing progress")
        self.progress_bar.setAccessibleDescription("Starting")
        layout.addWidget(self.progress_bar)

        meta_row = QHBoxLayout()
        self.percent_label = QLabel("0%")
        self.percent_label.setObjectName("MutedLabel")
        meta_row.addWidget(self.percent_label)
        meta_row.addStretch(1)
        self.eta_label = QLabel("ETA --:--")
        self.eta_label.setObjectName("MutedLabel")
        meta_row.addWidget(self.eta_label)
        layout.addLayout(meta_row)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        self.pause_button = QPushButton("Pause")
        self.pause_button.setObjectName("SecondaryButton")
        self.pause_button.setAccessibleName("Pause or resume typing")
        self.pause_button.setToolTip("Pause typing. Press again to resume after a short countdown.")
        self.pause_button.clicked.connect(self._on_pause_resume_clicked)
        button_row.addWidget(self.pause_button)

        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("DestructiveButton")
        self.stop_button.setAccessibleName("Stop typing")
        self.stop_button.setToolTip("Stop typing now")
        self.stop_button.clicked.connect(self._controller.stop)
        button_row.addWidget(self.stop_button)
        layout.addLayout(button_row)

        run_controller.stateChanged.connect(self._on_state_changed)
        run_controller.countdownTick.connect(self._on_countdown_tick)
        run_controller.progressChanged.connect(self._on_progress_changed)

    # -- signal handlers -----------------------------------------------------

    def _on_state_changed(self, state: str) -> None:
        if state in ("countdown", "resuming"):
            self.status_label.setText(_STATUS_TEXT.get(state, state))
        elif state in _STATUS_TEXT:
            self.status_label.setText(_STATUS_TEXT[state])
        self.pause_button.setText("Resume" if state == "paused" else "Pause")
        self.pause_button.setEnabled(state in ("typing", "paused"))

    def _on_countdown_tick(self, seconds_left: int) -> None:
        self.status_label.setText(f"Starting in {seconds_left}…")

    def _on_progress_changed(self, done: int, total: int, eta_seconds: float, _elapsed: float) -> None:
        percent = int((done / total) * 100) if total else 0
        self.progress_bar.setValue(percent)
        self.percent_label.setText(f"{percent}%")
        self.eta_label.setText(f"ETA {format_mmss(eta_seconds * 1000)}")
        self.progress_bar.setAccessibleDescription(
            f"{percent} percent complete, about {spoken_duration(eta_seconds)} remaining"
        )
        self.status_label.setText(_STATUS_TEXT["typing"])

    def _on_pause_resume_clicked(self) -> None:
        if self._controller.state == "paused":
            self._controller.resume()
        else:
            self._controller.pause()

    # -- positioning -----------------------------------------------------------

    def position_near_corner(self) -> None:
        """Move to the bottom-right corner of the primary screen's available area."""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        self.adjustSize()
        x = available.right() - self.width() - _CORNER_MARGIN
        y = available.bottom() - self.height() - _CORNER_MARGIN
        self.move(max(available.left(), x), max(available.top(), y))
