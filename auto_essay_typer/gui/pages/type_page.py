"""The Type page: paste text, tune quick settings, preview or start typing.

Layout: a text card (the editor + live counter/estimate line), a
quick-settings card (profile, speed, mistakes, breaks), and an actions area
that swaps between the idle Preview/Start buttons and a live run panel while
a run is in flight (see :class:`RunController`).
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from auto_essay_typer import config, platforms
from auto_essay_typer.gui.permission_dialog import PermissionDialog
from auto_essay_typer.gui.preview_dialog import PreviewDialog
from auto_essay_typer.gui.quick_settings import (
    MISTAKE_STOPS,
    SPEED_MAX,
    SPEED_MIN,
    nearest_mistake_stop_index,
    speed_label,
)
from auto_essay_typer.gui.run_controller import RunController
from auto_essay_typer.gui.theme import ThemeManager
from auto_essay_typer.planner import plan
from auto_essay_typer.platforms import PermissionStatus
from auto_essay_typer.profiles import PRESETS, Profile
from auto_essay_typer.progress import format_duration_words, format_mmss
from auto_essay_typer.simulator import total_duration_ms

_DROPPABLE_SUFFIXES = (".txt", ".md")
#: Debounce delay before a background plan() recomputes the duration estimate.
_ESTIMATE_DEBOUNCE_MS = 500
#: States in which the run panel (rather than idle Preview/Start) is shown.
_RUNNING_STATES = frozenset({"planning", "countdown", "typing", "paused", "resuming"})


def _has_droppable_file(event) -> bool:
    mime = event.mimeData()
    if not mime.hasUrls():
        return False
    return any(
        url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in _DROPPABLE_SUFFIXES
        for url in mime.urls()
    )


class _DropTextEdit(QPlainTextEdit):
    """QPlainTextEdit that accepts .txt/.md drag-and-drop and normalizes pasted text.

    Anything that carries URLs never reaches the document. Qt reports a URL
    list as *text* (``QMimeData.hasText()`` is true for it, and ``text()`` is
    the ``file:///...`` line), so QPlainTextEdit's own drop and paste handling
    would insert that line -- never what the user meant. A dropped .txt/.md
    file has its contents loaded through ``fileDropped``; every other dropped
    file or link is refused, with the drag cursor saying so. Plain text, dragged
    or pasted, still goes in (normalized), and so does a pasted web link that
    comes with its own text (browsers put one on the clipboard).
    """

    fileDropped = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt override
        if _has_droppable_file(event):
            event.acceptProposedAction()
        elif event.mimeData().hasUrls():
            event.ignore()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        if _has_droppable_file(event):
            event.acceptProposedAction()
        elif event.mimeData().hasUrls():
            event.ignore()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override
        mime = event.mimeData()
        if mime.hasUrls():
            for url in mime.urls():
                if not url.isLocalFile():
                    continue
                path = Path(url.toLocalFile())
                if path.suffix.lower() not in _DROPPABLE_SUFFIXES:
                    continue
                try:
                    content = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                self.fileDropped.emit(content)
                event.acceptProposedAction()
                return
            # URLs, but nothing loadable: refuse the drop outright. Falling
            # through to QPlainTextEdit.dropEvent would insert the URL text.
            event.ignore()
            return
        super().dropEvent(event)

    @staticmethod
    def _is_insertable(source) -> bool:
        """Whether ``source`` holds text worth inserting, as opposed to files or links.

        A URL list with no text of its own is just a reference to something,
        and local files are never pasted as their paths; a web link that
        arrives with real ``text/plain`` alongside it (what a browser puts on
        the clipboard) is text the user meant to paste.
        """
        if source.hasUrls():
            if any(url.isLocalFile() for url in source.urls()):
                return False
            return source.hasFormat("text/plain")
        return source.hasText()

    def canInsertFromMimeData(self, source) -> bool:  # noqa: N802 - Qt override
        if source.hasUrls():
            return self._is_insertable(source)
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source) -> None:  # noqa: N802 - Qt override
        if source.hasUrls() and not self._is_insertable(source):
            return
        if source.hasText():
            self.insertPlainText(platforms.normalize_input_text(source.text()))
        else:
            super().insertFromMimeData(source)


class _EstimateSignals(QObject):
    """Delivers a background duration estimate back to the GUI thread."""

    ready = Signal(int, float)  # generation, duration_ms


def _card() -> QWidget:
    card = QWidget()
    card.setObjectName("Card")
    return card


class TypePage(QWidget):
    def __init__(
        self,
        run_controller: RunController,
        theme_manager: ThemeManager,
        *,
        permission_checker: Callable[[], PermissionStatus] = platforms.check_input_permission,
        profiles_provider: Callable[[], dict[str, Profile]] = config.builtin_and_user_profiles,
        user_profile_names: Callable[[], set[str]] = lambda: set(config.load_profiles()),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._run_controller = run_controller
        self._theme_manager = theme_manager
        self._permission_checker = permission_checker
        self._profiles_provider = profiles_provider
        self._user_profile_names = user_profile_names

        self._profiles: dict[str, Profile] = {}
        self._base_profile = Profile()
        self._chars = 0
        self._words = 0
        self._duration_ms = 0.0
        self._estimate_generation = 0
        self._estimate_signals = _EstimateSignals()
        self._estimate_signals.ready.connect(self._on_estimate_ready)
        self._estimate_timer = QTimer(self)
        self._estimate_timer.setSingleShot(True)
        self._estimate_timer.setInterval(_ESTIMATE_DEBOUNCE_MS)
        self._estimate_timer.timeout.connect(self._start_estimate)

        self._build_ui()
        self._reload_profiles()

        run_controller.stateChanged.connect(self._on_state_changed)
        run_controller.countdownTick.connect(self._on_countdown_tick)
        run_controller.progressChanged.connect(self._on_progress_changed)
        run_controller.finished.connect(self._on_finished)

        for sequence in ("Ctrl+Return", "Ctrl+Enter"):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(self._on_start_clicked)

        # Esc stops an in-flight run (see the Help page); harmless -- and a
        # documented no-op -- when nothing is running.
        escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        escape_shortcut.activated.connect(self._run_controller.stop)

    # -- construction -----------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        title = QLabel("Type")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        # Stretch factor 1 on the text card only: it (and the QPlainTextEdit
        # inside it) is the one part of this page that should grow to fill a
        # taller window, so quick settings and the actions row always sit
        # snugly beneath it instead of floating with a gap either side.
        root.addWidget(self._build_text_card(), 1)
        root.addWidget(self._build_quick_settings_card())

        self.inline_message_label = QLabel("")
        self.inline_message_label.setObjectName("InlineMessage")
        self.inline_message_label.setProperty("tone", "danger")
        self.inline_message_label.setVisible(False)
        self.inline_message_label.setWordWrap(True)
        root.addWidget(self.inline_message_label)

        # Two sibling widgets with one hidden at a time, rather than a
        # QStackedWidget: a stacked widget's sizeHint is the *largest* of all
        # its pages (so switching pages never resizes the window), which here
        # meant the compact idle-actions row always sat inside a slot sized
        # for the taller run panel, leaving a visible gap beneath it. A
        # hidden widget contributes no space to its layout, so toggling
        # setVisible() instead sizes this area to whichever page is showing.
        self.idle_actions_widget = self._build_idle_actions()
        self.run_panel_widget = self._build_run_panel()
        self.run_panel_widget.setVisible(False)
        root.addWidget(self.idle_actions_widget)
        root.addWidget(self.run_panel_widget)

    def _build_text_card(self) -> QWidget:
        card = _card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(6)

        self.text_edit = _DropTextEdit()
        self.text_edit.setPlaceholderText(
            "Paste or type the text you want typed for you -- an essay, an email, "
            "anything. Drop a .txt or .md file here, too."
        )
        self.text_edit.setMinimumHeight(220)
        self.text_edit.textChanged.connect(self._on_text_changed)
        self.text_edit.fileDropped.connect(self._on_file_dropped)
        layout.addWidget(self.text_edit)

        self.counter_label = QLabel("0 characters, 0 words")
        self.counter_label.setObjectName("MutedLabel")
        self.counter_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.counter_label)
        return card

    def _build_quick_settings_card(self) -> QWidget:
        card = _card()
        outer = QVBoxLayout(card)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(12)

        section_title = QLabel("Quick settings")
        section_title.setObjectName("SectionTitle")
        outer.addWidget(section_title)

        # -- profile row --
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("Profile"))
        self.profile_combo = QComboBox()
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        profile_row.addWidget(self.profile_combo, 1)
        outer.addLayout(profile_row)

        # -- speed row --
        speed_row = QHBoxLayout()
        speed_row.addWidget(QLabel("Speed"))
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(SPEED_MIN, SPEED_MAX)
        self.speed_slider.valueChanged.connect(self._on_speed_changed)
        speed_row.addWidget(self.speed_slider, 1)
        self.speed_value_label = QLabel()
        self.speed_value_label.setFixedWidth(130)
        speed_row.addWidget(self.speed_value_label)
        outer.addLayout(speed_row)

        # -- mistakes row --
        mistake_row = QHBoxLayout()
        mistake_row.addWidget(QLabel("Mistakes"))
        self.mistake_slider = QSlider(Qt.Orientation.Horizontal)
        self.mistake_slider.setRange(0, len(MISTAKE_STOPS) - 1)
        self.mistake_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.mistake_slider.valueChanged.connect(self._on_mistake_changed)
        mistake_row.addWidget(self.mistake_slider, 1)
        self.mistake_value_label = QLabel()
        self.mistake_value_label.setFixedWidth(130)
        mistake_row.addWidget(self.mistake_value_label)
        outer.addLayout(mistake_row)

        # -- breaks toggle --
        self.breaks_switch = QCheckBox("Take breaks on long documents")
        self.breaks_switch.setObjectName("ToggleSwitch")
        self.breaks_switch.toggled.connect(self._on_breaks_toggled)
        outer.addWidget(self.breaks_switch)

        return card

    def _build_idle_actions(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch(1)

        self.preview_button = QPushButton("Preview")
        self.preview_button.setObjectName("SecondaryButton")
        self.preview_button.clicked.connect(self._on_preview_clicked)
        layout.addWidget(self.preview_button)

        self.start_button = QPushButton("Start typing")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.setToolTip("Ctrl+Return")
        self.start_button.clicked.connect(self._on_start_clicked)
        layout.addWidget(self.start_button)
        return widget

    def _build_run_panel(self) -> QWidget:
        panel = _card()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        self.run_message_label = QLabel("Preparing your plan...")
        self.run_message_label.setStyleSheet("font-size: 15px; font-weight: 600;")
        self.run_message_label.setWordWrap(True)
        layout.addWidget(self.run_message_label)

        self.run_progress_bar = QProgressBar()
        self.run_progress_bar.setRange(0, 0)
        self.run_progress_bar.setTextVisible(False)
        layout.addWidget(self.run_progress_bar)

        meta_row = QHBoxLayout()
        self.run_percent_label = QLabel("0%")
        self.run_percent_label.setObjectName("MutedLabel")
        meta_row.addWidget(self.run_percent_label)
        meta_row.addStretch(1)
        self.run_eta_label = QLabel("ETA --:--")
        self.run_eta_label.setObjectName("MutedLabel")
        meta_row.addWidget(self.run_eta_label)
        self.run_elapsed_label = QLabel("Elapsed 0:00")
        self.run_elapsed_label.setObjectName("MutedLabel")
        meta_row.addWidget(self.run_elapsed_label)
        layout.addLayout(meta_row)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.pause_resume_button = QPushButton("Pause")
        self.pause_resume_button.setObjectName("SecondaryButton")
        self.pause_resume_button.clicked.connect(self._on_pause_resume_clicked)
        button_row.addWidget(self.pause_resume_button)

        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("DestructiveButton")
        self.stop_button.clicked.connect(self._run_controller.stop)
        button_row.addWidget(self.stop_button)
        layout.addLayout(button_row)

        return panel

    # -- profiles -----------------------------------------------------------------

    def _reload_profiles(self) -> None:
        self._profiles = dict(self._profiles_provider())
        user_names = self._user_profile_names()

        previous = self.profile_combo.currentData()
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for name in sorted(self._profiles):
            is_pure_builtin = name in PRESETS and name not in user_names
            label = f"{name}  (Built-in)" if is_pure_builtin else name
            self.profile_combo.addItem(label, name)
        target = self._preferred_profile_name(previous)
        if target is not None:
            self.profile_combo.setCurrentIndex(self.profile_combo.findData(target))
        self.profile_combo.blockSignals(False)
        self._apply_profile(self.profile_combo.currentData())

    def _preferred_profile_name(self, previous: str | None) -> str | None:
        """The profile to select after a reload: whatever was selected before if it
        still exists, else "natural", else whichever profile sorts first."""
        if previous in self._profiles:
            return previous
        if "natural" in self._profiles:
            return "natural"
        names = sorted(self._profiles)
        return names[0] if names else None

    def _on_profile_changed(self, _index: int) -> None:
        self._apply_profile(self.profile_combo.currentData())
        self._schedule_estimate()

    def _apply_profile(self, name: str | None) -> None:
        if name is None or name not in self._profiles:
            return
        profile = self._profiles[name]
        self._base_profile = profile

        self.speed_slider.blockSignals(True)
        self.speed_slider.setValue(max(SPEED_MIN, min(SPEED_MAX, round(profile.wpm))))
        self.speed_slider.blockSignals(False)
        self._update_speed_label()

        self.mistake_slider.blockSignals(True)
        self.mistake_slider.setValue(nearest_mistake_stop_index(profile.typo_rate_per_100))
        self.mistake_slider.blockSignals(False)
        self._update_mistake_label()

        self.breaks_switch.blockSignals(True)
        self.breaks_switch.setChecked(profile.session_breaks)
        self.breaks_switch.blockSignals(False)

    def effective_profile(self) -> Profile:
        """The profile this run would use: the selected profile with the quick
        controls (speed/mistakes/breaks) layered on top as overrides."""
        typo_rate, _label = MISTAKE_STOPS[self.mistake_slider.value()]
        return self._base_profile.with_overrides(
            wpm=float(self.speed_slider.value()),
            typo_rate_per_100=typo_rate,
            session_breaks=self.breaks_switch.isChecked(),
        )

    # -- quick-setting change handlers -------------------------------------------

    def _on_speed_changed(self, _value: int) -> None:
        self._update_speed_label()
        self._schedule_estimate()

    def _update_speed_label(self) -> None:
        value = self.speed_slider.value()
        self.speed_value_label.setText(f"{value} wpm - {speed_label(value)}")

    def _on_mistake_changed(self, index: int) -> None:
        self._update_mistake_label()
        self._schedule_estimate()

    def _update_mistake_label(self) -> None:
        _rate, label = MISTAKE_STOPS[self.mistake_slider.value()]
        self.mistake_value_label.setText(label)

    def _on_breaks_toggled(self, _checked: bool) -> None:
        self._schedule_estimate()

    # -- text input ---------------------------------------------------------------

    def normalized_text(self) -> str:
        return platforms.normalize_input_text(self.text_edit.toPlainText())

    def _on_text_changed(self) -> None:
        self._hide_inline_message()
        text = self.text_edit.toPlainText()
        self._chars = len(text)
        self._words = len(text.split()) if text.strip() else 0
        self._render_counter()
        self._schedule_estimate()

    def _on_file_dropped(self, content: str) -> None:
        self.text_edit.setPlainText(platforms.normalize_input_text(content))

    def set_text(self, text: str) -> None:
        """Programmatically load text (used by tests and, later, importers)."""
        self.text_edit.setPlainText(platforms.normalize_input_text(text))

    # -- live estimate (debounced, computed off the GUI thread) -------------------

    def _schedule_estimate(self) -> None:
        self._estimate_timer.start()

    def _start_estimate(self) -> None:
        self._estimate_generation += 1
        generation = self._estimate_generation
        text = self.normalized_text()
        if not text.strip():
            self._on_estimate_ready(generation, 0.0)
            return
        profile = self.effective_profile()

        def work() -> None:
            try:
                duration_ms = total_duration_ms(plan(text, profile, seed=0))
            except Exception:  # noqa: BLE001 - the estimate is best-effort
                duration_ms = 0.0
            self._estimate_signals.ready.emit(generation, duration_ms)

        threading.Thread(target=work, daemon=True).start()

    def _on_estimate_ready(self, generation: int, duration_ms: float) -> None:
        if generation != self._estimate_generation:
            return  # superseded by a newer estimate -- discard
        self._duration_ms = duration_ms
        self._render_counter()

    def _render_counter(self) -> None:
        chars_words = f"{self._chars:,} character{'s' if self._chars != 1 else ''}, {self._words:,} word{'s' if self._words != 1 else ''}"
        if self._chars == 0:
            self.counter_label.setText(chars_words)
        else:
            estimate = format_duration_words(self._duration_ms / 1000.0)
            self.counter_label.setText(f"{chars_words}   ·   est. {estimate}")

    # -- inline validation message --------------------------------------------------

    def _show_inline_message(self, message: str) -> None:
        self.inline_message_label.setText(message)
        self.inline_message_label.setVisible(True)

    def _hide_inline_message(self) -> None:
        self.inline_message_label.setVisible(False)

    # -- preview --------------------------------------------------------------------

    def _on_preview_clicked(self) -> None:
        text = self.normalized_text()
        if not text.strip():
            self._show_inline_message("Type or paste some text first.")
            return
        actions = plan(text, self.effective_profile(), seed=0)
        dialog = PreviewDialog(actions, text, self._theme_manager.tokens, parent=self)
        dialog.exec()

    # -- start / preflight ----------------------------------------------------------

    def _on_start_clicked(self) -> None:
        if self._run_controller.is_busy:
            return
        text = self.normalized_text()
        if not text.strip():
            self._show_inline_message("Type or paste some text first.")
            return

        status = self._permission_checker()
        if not status.granted:
            dialog = PermissionDialog(
                status,
                self._theme_manager.tokens,
                permission_checker=self._permission_checker,
                parent=self,
            )
            dialog.exec()
            if not dialog.granted():
                return

        self._hide_inline_message()
        self._set_running_ui(True)
        self._run_controller.start(text, self.effective_profile(), seed=None)

    # -- run panel: react to RunController signals -----------------------------------

    def _set_running_ui(self, running: bool) -> None:
        self.text_edit.setEnabled(not running)
        self.profile_combo.setEnabled(not running)
        self.speed_slider.setEnabled(not running)
        self.mistake_slider.setEnabled(not running)
        self.breaks_switch.setEnabled(not running)
        self.idle_actions_widget.setVisible(not running)
        self.run_panel_widget.setVisible(running)
        if running:
            self.run_progress_bar.setRange(0, 0)
            self.run_progress_bar.setValue(0)
            self.run_percent_label.setText("0%")
            self.run_eta_label.setText("ETA --:--")
            self.run_elapsed_label.setText("Elapsed 0:00")
            self.run_message_label.setText("Preparing your plan...")

    def _on_state_changed(self, state: str) -> None:
        if state not in _RUNNING_STATES:
            return  # a terminal state -- handled by _on_finished instead
        if state == "planning":
            self.run_message_label.setText("Preparing your plan...")
            self.run_progress_bar.setRange(0, 0)
        elif state == "typing":
            self.run_message_label.setText("Typing...")
            self.run_progress_bar.setRange(0, 100)
        elif state == "paused":
            self.run_message_label.setText("Paused. Click Resume when you're ready.")
        self.pause_resume_button.setText("Resume" if state == "paused" else "Pause")
        self.pause_resume_button.setEnabled(state in ("typing", "paused"))
        self.stop_button.setEnabled(True)

    def _on_countdown_tick(self, seconds_left: int) -> None:
        self.run_progress_bar.setRange(0, 100)
        self.run_message_label.setText(
            f"Click where the text should go -- starting in {seconds_left}"
        )

    def _on_progress_changed(self, done: int, total: int, eta_seconds: float, elapsed_seconds: float) -> None:
        percent = int((done / total) * 100) if total else 0
        self.run_progress_bar.setRange(0, 100)
        self.run_progress_bar.setValue(percent)
        self.run_percent_label.setText(f"{percent}%")
        self.run_eta_label.setText(f"ETA {format_mmss(eta_seconds * 1000)}")
        self.run_elapsed_label.setText(f"Elapsed {format_mmss(elapsed_seconds * 1000)}")

    def _on_finished(self, outcome: str, message: str) -> None:
        self._set_running_ui(False)
        if outcome == "failed":
            self._show_inline_message(message)

    # -- pause / resume / stop -------------------------------------------------------

    def _on_pause_resume_clicked(self) -> None:
        if self._run_controller.state == "paused":
            self._run_controller.resume()
        else:
            self._run_controller.pause()
