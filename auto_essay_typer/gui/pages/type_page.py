"""The Type page: paste text or import a document, tune quick settings, preview or start typing.

Layout: an import row (a link or file to import a formatted document from), a
text card (the editor + live counter/estimate line), a quick-settings card
(editor, profile, speed, mistakes, breaks), and an actions area that swaps
between the idle Preview/Start buttons and a live run panel while a run is in
flight (see :class:`RunController`).

Two modes share the page. In **plain-text mode** (the default) the text box is
an editor and what is typed is exactly what is in it. In **document mode**,
entered by importing a document, the editor is replaced by a read-only view
that draws the document's formatting, and what is typed is the document: its
words *and* its headings, bold, alignment and lists, pressed into the chosen
editor as keyboard shortcuts. The imported
:class:`~auto_essay_typer.document.Document` is the source of truth in that
mode (held in a :class:`~auto_essay_typer.gui.document_session.DocumentSession`);
the widget only displays it and nothing is ever read back out of it.

The "Typing into" picker chooses which editor's shortcuts are pressed. Its
choice is saved in the shared :class:`~auto_essay_typer.gui.settings_store.SettingsStore`,
and so are the per-editor shortcut overrides the Advanced page edits -- the page
listens to the store, so changing either re-adapts a loaded document at once.
"""

from __future__ import annotations

import dataclasses
import queue
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from auto_essay_typer import app_settings, config, platforms, targets
from auto_essay_typer.actions import FormatOp
from auto_essay_typer.gui.a11y import announce, spoken_duration
from auto_essay_typer.gui.advanced_common import NoWheelComboBox
from auto_essay_typer.gui.document_import import (
    DocumentImporter,
    DocumentLoader,
    ImportOutcome,
    default_choose_file,
    default_show_error,
    default_start_dir,
    describe_source,
)
from auto_essay_typer.gui.document_panels import ImportBar, WarningsPanel
from auto_essay_typer.gui.document_session import DocumentSession
from auto_essay_typer.gui.document_view import DocumentView
from auto_essay_typer.gui.document_warnings import editor_phrase
from auto_essay_typer.gui.labels import ElidedLabel, WrappingLabel
from auto_essay_typer.gui.permission_dialog import PermissionDialog
from auto_essay_typer.gui.preview_dialog import PreviewDialog
from auto_essay_typer.gui.quick_settings import (
    MISTAKE_STOPS,
    SPEED_MAX,
    SPEED_MIN,
    initial_target_id,
    nearest_mistake_stop_index,
    speed_label,
    target_caution,
)
from auto_essay_typer.gui.run_controller import RunController
from auto_essay_typer.gui.settings_store import SettingsStore
from auto_essay_typer.gui.theme import ThemeManager
from auto_essay_typer.gui.controls import ScrollPageLayout, ToggleSwitch, switch_row
from auto_essay_typer.importers import load_document
from auto_essay_typer.keymaps import Chord
from auto_essay_typer.planner import plan, plan_document
from auto_essay_typer.platforms import PermissionStatus
from auto_essay_typer.profiles import PRESETS, Profile
from auto_essay_typer.progress import format_duration_words, format_mmss
from auto_essay_typer.simulator import total_duration_ms
from auto_essay_typer.targets import TargetApp

_DROPPABLE_SUFFIXES = (".txt", ".md")
#: Debounce delay before a background plan() recomputes the duration estimate.
_ESTIMATE_DEBOUNCE_MS = 500
#: How often the GUI thread looks for a finished estimate while one is being computed.
_ESTIMATE_POLL_MS = 30
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


def _compute_estimate(
    make_actions: Callable[[], list], generation: int, results: "queue.SimpleQueue[tuple[int, float]]"
) -> None:
    """The estimate worker's entire body: plan, total the pauses, queue ``(generation, ms)``.

    A plain function holding no Qt object, on purpose. A worker that held the page (or
    emitted a signal on it) can outlive a closed page and be the last to let go of it,
    which destroys the page's widgets on the worker's thread. Qt forbids that, and it
    corrupts the list of all widgets so the process crashes later, somewhere unrelated.
    The page polls ``results`` from a timer on the GUI thread instead.
    """
    try:
        duration_ms = total_duration_ms(make_actions())
    except Exception:  # noqa: BLE001 - the estimate is best-effort
        duration_ms = 0.0
    results.put((generation, duration_ms))


def _card() -> QWidget:
    card = QWidget()
    card.setObjectName("Card")
    return card


#: Width of the label column in the quick-settings rows, so the combo box and
#: both sliders start at the same x.
_ROW_LABEL_WIDTH = 84
#: Width of the value readout beside a slider ("65 wpm · Natural").
_VALUE_LABEL_WIDTH = 130
#: Gap between a quick-settings row's label and its control.
_ROW_SPACING = 12
#: Shortest the document view is allowed to get: a formatted document needs more
#: room than a text box to be recognisable, and the page scrolls to make it.
_DOCUMENT_MIN_HEIGHT = 200


def _row_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setFixedWidth(_ROW_LABEL_WIDTH)
    return label


class TypePage(QWidget):
    """The Type page. See the module docstring for the two modes it has.

    Everything with a side effect can be injected, so the page can be driven from
    tests without the network, a file dialog, a modal message box or the real
    settings file: ``document_loader`` (default
    :func:`~auto_essay_typer.importers.load_document`, called on a worker
    thread with the cleaned-up link or path), ``file_chooser`` (the Browse
    dialog), ``error_presenter`` (the message box shown when an import fails)
    and ``settings_store``.

    ``settings_store`` should be the one the rest of the app shares, so that the
    editor chosen here and the shortcut overrides saved on the Advanced page are
    the same everywhere, the moment they change. Without one the page keeps a
    private store of its own. It reads and writes the same file and takes up what
    another store saved when the page is shown and before Preview or Start (see
    ``SettingsStore.refresh``), but it cannot hear of a change the moment it is made.

    Signals
    -------
    documentModeChanged(bool)
        ``True`` on entering document mode, ``False`` on leaving it.
    importFinished(bool)
        An import ended and the page has been updated to show it: ``True`` if a
        document was imported, ``False`` if it failed (the reason is on screen).
    """

    documentModeChanged = Signal(bool)
    importFinished = Signal(bool)

    def __init__(
        self,
        run_controller: RunController,
        theme_manager: ThemeManager,
        *,
        permission_checker: Callable[[], PermissionStatus] = platforms.check_input_permission,
        profiles_provider: Callable[[], dict[str, Profile]] = config.builtin_and_user_profiles,
        user_profile_names: Callable[[], set[str]] = lambda: set(config.load_profiles()),
        settings_store: SettingsStore | None = None,
        document_loader: DocumentLoader = load_document,
        file_chooser: Callable[[QWidget | None, str], str | None] | None = None,
        error_presenter: Callable[[QWidget | None, str, str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._run_controller = run_controller
        self._theme_manager = theme_manager
        self._permission_checker = permission_checker
        self._profiles_provider = profiles_provider
        self._user_profile_names = user_profile_names
        self._settings_store = settings_store if settings_store is not None else SettingsStore(parent=self)
        self._choose_file = file_chooser if file_chooser is not None else default_choose_file
        self._present_error = error_presenter if error_presenter is not None else default_show_error

        self._profiles: dict[str, Profile] = {}
        self._base_profile = Profile()
        self._chars = 0
        self._words = 0
        #: Estimated typing time, or ``None`` while it is being worked out again (after a
        #: change of mode or editor), so the page never shows another document's estimate.
        self._duration_ms: float | None = 0.0
        self._estimate_generation = 0
        self._estimate_results: queue.SimpleQueue[tuple[int, float]] = queue.SimpleQueue()
        self._estimates_in_flight = 0
        self._estimate_poll = QTimer(self)
        self._estimate_poll.setInterval(_ESTIMATE_POLL_MS)
        self._estimate_poll.timeout.connect(self._poll_estimates)
        self._estimate_timer = QTimer(self)
        self._estimate_timer.setSingleShot(True)
        self._estimate_timer.setInterval(_ESTIMATE_DEBOUNCE_MS)
        self._estimate_timer.timeout.connect(self._start_estimate)

        #: The imported document, fitted to the selected editor; ``None`` in plain-text mode.
        self._session: DocumentSession | None = None
        self._running = False
        self._importing = False
        #: The editor the settings file named when this page last looked. The picker may be
        #: ahead of it (a choice that could not be saved), and must then stay where it is
        #: through changes to other settings.
        self._saved_target = self._settings_store.settings.target_app
        #: Settings changed while a run was typing; applied once it is over, so the page never
        #: shows an editor or document that is not the one being typed.
        self._settings_changed_during_run = False
        self._last_import_dir = ""
        self._targets: list[TargetApp] = targets.targets_for(platforms.current_os())
        self._importer = DocumentImporter(document_loader, parent=self)
        self._importer.busyChanged.connect(self._on_import_busy_changed)
        self._importer.finished.connect(self._on_import_finished)

        self._build_ui()
        self._reload_profiles()
        self._refresh_target_area()
        self._settings_store.changed.connect(self._on_settings_changed)

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
        # The page's content lives in a scroll area, so a window shorter than the
        # content (the 820x600 minimum, a large system font, a row added later)
        # scrolls instead of clipping. widgetResizable keeps the usual behavior
        # when there is room: the content stretches to fill the viewport.
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        page_layout.addWidget(self.scroll_area)
        content = QWidget()
        self.scroll_area.setWidget(content)

        root = ScrollPageLayout(content)
        root.setContentsMargins(24, 16, 24, 16)
        root.setSpacing(16)

        title = QLabel("Type")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        self.import_bar = ImportBar()
        self.import_bar.importRequested.connect(self._on_import_requested)
        self.import_bar.browseRequested.connect(self._on_browse_requested)
        root.addWidget(self.import_bar)

        # Stretch factor 1 on the text card only: it (and the QPlainTextEdit
        # inside it) is the one part of this page that should grow to fill a
        # taller window, so quick settings and the actions row always sit
        # snugly beneath it instead of floating with a gap either side.
        root.addWidget(self._build_text_card(), 1)
        # Shown only in document mode, directly above the editor picker whose
        # choice it explains.
        self.warnings_panel = WarningsPanel()
        self.warnings_panel.setVisible(False)
        root.addWidget(self.warnings_panel)
        root.addWidget(self._build_quick_settings_card())

        self.inline_message_label = WrappingLabel("")
        self.inline_message_label.setObjectName("InlineMessage")
        self.inline_message_label.setProperty("tone", "danger")
        self.inline_message_label.setVisible(False)
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
        # No card behind the editor: it is already a bordered field, and a field
        # inside a card reads as a box in a box.
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Document mode's header: where the document came from, and the way back.
        self.document_header = QWidget()
        header_layout = QHBoxLayout(self.document_header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        self.document_source_label = ElidedLabel("")
        self.document_source_label.setObjectName("MutedLabel")
        header_layout.addWidget(self.document_source_label, 1)
        self.back_button = QPushButton("Back to plain text")
        self.back_button.setObjectName("SecondaryButton")
        self.back_button.setAccessibleName("Back to plain text")
        self.back_button.setToolTip(
            "Stop using the imported document and edit its words as plain text. "
            "Its formatting is dropped."
        )
        self.back_button.clicked.connect(self._on_back_to_plain_clicked)
        header_layout.addWidget(self.back_button)
        self.document_header.setVisible(False)
        layout.addWidget(self.document_header)

        self.text_edit = _DropTextEdit()
        self.text_edit.setPlaceholderText(
            "Paste or type the text you want typed for you — an essay, an email, "
            "anything. Drop a .txt or .md file here, too. For a Word file or a "
            "Google Doc, use the box above this one."
        )
        self.text_edit.setAccessibleName("Text to type")
        self.text_edit.setAccessibleDescription(
            "Paste or type the text you want typed, or drop a .txt or .md file onto it."
        )
        self.text_edit.setMinimumHeight(120)
        self.text_edit.textChanged.connect(self._on_text_changed)
        self.text_edit.fileDropped.connect(self._on_file_dropped)
        layout.addWidget(self.text_edit)

        # Takes the text box's place in document mode (never both at once).
        self.document_view = DocumentView()
        self.document_view.setMinimumHeight(_DOCUMENT_MIN_HEIGHT)
        self.document_view.setVisible(False)
        layout.addWidget(self.document_view)

        self.counter_label = QLabel("0 characters, 0 words")
        self.counter_label.setObjectName("MutedLabel")
        self.counter_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.counter_label)
        return card

    def _build_quick_settings_card(self) -> QWidget:
        card = _card()
        self._quick_settings_card = card  # disabled as a whole during a run, labels included
        outer = QVBoxLayout(card)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(12)

        section_title = QLabel("Quick settings")
        section_title.setObjectName("SectionTitle")
        outer.addWidget(section_title)

        # -- editor row: which application's shortcuts a document's formatting is pressed into --
        editor_row = QHBoxLayout()
        editor_row.setSpacing(_ROW_SPACING)
        editor_label = _row_label("Typing into")
        editor_row.addWidget(editor_label)
        # Ignores the mouse wheel until it has focus: this page scrolls, and the editor chosen
        # here is saved and decides which shortcuts are pressed into another application, so
        # passing the pointer over it must never change it.
        self.target_combo = NoWheelComboBox()
        self.target_combo.setAccessibleName("Typing into")
        self.target_combo.setToolTip(
            "The application the text is typed into. For an imported document it decides "
            "which keyboard shortcuts are pressed to format it."
        )
        editor_label.setBuddy(self.target_combo)
        for target in self._targets:
            self.target_combo.addItem(target.name, target.id)
        self.target_combo.setCurrentIndex(
            self.target_combo.findData(
                initial_target_id(self._settings_store.settings.target_app, self._targets)
            )
        )
        self.target_combo.currentIndexChanged.connect(self._on_target_changed)
        editor_row.addWidget(self.target_combo, 1)
        editor_row.addSpacing(_VALUE_LABEL_WIDTH + editor_row.spacing())
        outer.addLayout(editor_row)

        # Under the combo, lined up with it: what to know about this editor.
        details_row = QHBoxLayout()
        details_row.setSpacing(_ROW_SPACING)
        details_row.addSpacing(_ROW_LABEL_WIDTH + _ROW_SPACING)  # lines up with the combo
        details = QVBoxLayout()
        details.setSpacing(4)
        self.target_notes_label = WrappingLabel("")
        self.target_notes_label.setObjectName("MutedLabel")
        details.addWidget(self.target_notes_label)
        self.target_caution_label = WrappingLabel("")
        self.target_caution_label.setObjectName("MutedLabel")
        details.addWidget(self.target_caution_label)
        details_row.addLayout(details, 1)
        outer.addLayout(details_row)

        # -- profile row --
        profile_row = QHBoxLayout()
        profile_row.setSpacing(12)
        profile_label = _row_label("Profile")
        profile_row.addWidget(profile_label)
        self.profile_combo = QComboBox()
        self.profile_combo.setAccessibleName("Typing profile")
        self.profile_combo.setToolTip(
            "A typing style. Choosing one sets the speed, mistakes and breaks below."
        )
        profile_label.setBuddy(self.profile_combo)
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        profile_row.addWidget(self.profile_combo, 1)
        # Same width as the value readouts below, so the combo's right edge lines
        # up with the sliders' instead of running on past them.
        profile_row.addSpacing(_VALUE_LABEL_WIDTH + profile_row.spacing())
        outer.addLayout(profile_row)

        # -- speed row --
        speed_row = QHBoxLayout()
        speed_row.setSpacing(12)
        speed_label = _row_label("Speed")
        speed_row.addWidget(speed_label)
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(SPEED_MIN, SPEED_MAX)
        self.speed_slider.setAccessibleName("Typing speed")
        self.speed_slider.setToolTip("How fast the text is typed, in words per minute.")
        speed_label.setBuddy(self.speed_slider)
        self.speed_slider.valueChanged.connect(self._on_speed_changed)
        speed_row.addWidget(self.speed_slider, 1)
        self.speed_value_label = QLabel()
        self.speed_value_label.setFixedWidth(_VALUE_LABEL_WIDTH)
        speed_row.addWidget(self.speed_value_label)
        outer.addLayout(speed_row)

        # -- mistakes row --
        mistake_row = QHBoxLayout()
        mistake_row.setSpacing(12)
        mistake_label = _row_label("Mistakes")
        mistake_row.addWidget(mistake_label)
        self.mistake_slider = QSlider(Qt.Orientation.Horizontal)
        self.mistake_slider.setRange(0, len(MISTAKE_STOPS) - 1)
        self.mistake_slider.setAccessibleName("Typing mistakes")
        self.mistake_slider.setToolTip("How often a typo is made and then corrected.")
        mistake_label.setBuddy(self.mistake_slider)
        self.mistake_slider.valueChanged.connect(self._on_mistake_changed)
        mistake_row.addWidget(self.mistake_slider, 1)
        self.mistake_value_label = QLabel()
        self.mistake_value_label.setFixedWidth(_VALUE_LABEL_WIDTH)
        mistake_row.addWidget(self.mistake_value_label)
        outer.addLayout(mistake_row)

        # -- breaks toggle --
        self.breaks_switch = ToggleSwitch("Take breaks on long documents")
        self.breaks_switch.setObjectName("ToggleSwitch")
        self.breaks_switch.setAccessibleName("Take breaks on long documents")
        self.breaks_switch.setToolTip("Pause now and then on a long document, the way a person would.")
        self.breaks_switch.toggled.connect(self._on_breaks_toggled)
        outer.addLayout(switch_row(self.breaks_switch))

        return card

    def _build_idle_actions(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addStretch(1)

        self.preview_button = QPushButton("Preview")
        self.preview_button.setObjectName("SecondaryButton")
        self.preview_button.setAccessibleName("Preview")
        self.preview_button.setToolTip(
            "See what will be typed, with its planned mistakes and pauses, without typing anything."
        )
        self.preview_button.clicked.connect(self._on_preview_clicked)
        layout.addWidget(self.preview_button)

        self.start_button = QPushButton("Start typing")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.setAccessibleName("Start typing")
        shortcut_text = QKeySequence("Ctrl+Return").toString(QKeySequence.SequenceFormat.NativeText)
        self.start_button.setToolTip(f"Start typing ({shortcut_text})")
        self.start_button.setAccessibleDescription(
            "After a short countdown, types the text into whatever you click on next."
        )
        self.start_button.clicked.connect(self._on_start_clicked)
        layout.addWidget(self.start_button)
        return widget

    def _build_run_panel(self) -> QWidget:
        panel = _card()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        # Status on the left, the two run controls on the right of the same row:
        # the whole panel stays short enough that, at the minimum window height,
        # starting a run does not push the text box off the screen.
        header_row = QHBoxLayout()
        header_row.setSpacing(8)
        self.run_message_label = QLabel("Preparing your plan…")
        self.run_message_label.setObjectName("RunMessage")
        self.run_message_label.setWordWrap(True)
        header_row.addWidget(self.run_message_label, 1)

        self.pause_resume_button = QPushButton("Pause")
        self.pause_resume_button.setObjectName("SecondaryButton")
        self.pause_resume_button.setAccessibleName("Pause or resume typing")
        self.pause_resume_button.setToolTip("Pause typing. Press again to resume after a short countdown.")
        self.pause_resume_button.clicked.connect(self._on_pause_resume_clicked)
        header_row.addWidget(self.pause_resume_button)

        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("DestructiveButton")
        self.stop_button.setAccessibleName("Stop typing")
        self.stop_button.setToolTip("Stop typing now (Esc)")
        self.stop_button.clicked.connect(self._run_controller.stop)
        header_row.addWidget(self.stop_button)
        layout.addLayout(header_row)

        self.run_progress_bar = QProgressBar()
        self.run_progress_bar.setRange(0, 0)
        self.run_progress_bar.setTextVisible(False)
        self.run_progress_bar.setAccessibleName("Typing progress")
        self.run_progress_bar.setAccessibleDescription("Preparing")
        layout.addWidget(self.run_progress_bar)

        meta_row = QHBoxLayout()
        self.run_percent_label = QLabel("0%")
        self.run_percent_label.setObjectName("MutedLabel")
        meta_row.addWidget(self.run_percent_label)
        meta_row.addStretch(1)
        self.run_eta_label = QLabel("ETA --:--")
        self.run_eta_label.setObjectName("MutedLabel")
        meta_row.addWidget(self.run_eta_label)
        meta_row.addSpacing(12)
        self.run_elapsed_label = QLabel("Elapsed 0:00")
        self.run_elapsed_label.setObjectName("MutedLabel")
        meta_row.addWidget(self.run_elapsed_label)
        layout.addLayout(meta_row)

        # What a first-time user most wants to know while the keyboard is being used for them.
        self.run_hint_label = WrappingLabel(self._run_hint_text())
        self.run_hint_label.setObjectName("MutedLabel")
        layout.addWidget(self.run_hint_label)

        return panel

    def _run_hint_text(self) -> str:
        """Reassurance shown for the whole run: where the typing goes, and how to end it."""
        settings = self._settings_store.settings
        esc = settings.global_hotkeys_enabled and settings.hotkeys.get("stop") == "<esc>"
        stop = "Stop, or press Esc," if esc else "Stop"
        return (
            "It types into whichever window has focus, so leave the keyboard and mouse alone "
            f"until it finishes. Clicking elsewhere sends the typing there. {stop} ends it at once."
        )

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
        still exists, else the one saved from the last session, else "natural",
        else whichever profile sorts first."""
        if previous in self._profiles:
            return previous
        saved = self._settings_store.settings.profile_name
        if saved in self._profiles:
            return saved
        if "natural" in self._profiles:
            return "natural"
        names = sorted(self._profiles)
        return names[0] if names else None

    def _on_profile_changed(self, _index: int) -> None:
        name = self.profile_combo.currentData()
        self._apply_profile(name)
        self._remember_profile(name)
        self._schedule_estimate()

    def _remember_profile(self, name: str | None) -> None:
        """Save the chosen profile, so the next launch starts on it.

        This page is the only one that writes ``profile_name``: its selector is
        the one a run actually uses, so visiting the Advanced page to edit some
        *other* profile must not quietly change what the next run types. That
        page only reads the field, to decide which profile to open its editor on.

        A choice that could not be saved still applies to this session, so the
        combo stays where the user put it and they are told only that it will
        not be remembered -- the same way ``_on_target_changed`` reports an
        editor choice it could not save.
        """
        if name is None or name == self._settings_store.settings.profile_name:
            return
        try:
            self._settings_store.update(profile_name=name)
        except (ValueError, OSError) as exc:
            self._show_inline_message(
                f"Now using '{name}', but that choice couldn't be saved: {exc}"
            )

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
        self.speed_value_label.setText(f"{value} wpm · {speed_label(value)}")
        # The slider alone would be read out as a bare number.
        self.speed_slider.setAccessibleDescription(f"{value} words per minute, {speed_label(value)}")

    def _on_mistake_changed(self, index: int) -> None:
        self._update_mistake_label()
        self._schedule_estimate()

    def _update_mistake_label(self) -> None:
        _rate, label = MISTAKE_STOPS[self.mistake_slider.value()]
        self.mistake_value_label.setText(label)
        self.mistake_slider.setAccessibleDescription(label)

    def _on_breaks_toggled(self, _checked: bool) -> None:
        self._schedule_estimate()

    # -- text input ---------------------------------------------------------------

    def normalized_text(self) -> str:
        """The text that Start would type right now.

        In plain-text mode that is the text box's contents. In document mode it is the
        imported document's words as they will appear on screen: the text box is not what
        gets typed then and may hold something older or nothing at all. Callers outside
        the page, which can only ask it what is about to be typed (to see that there is
        something to type, or to count it), get the right answer in either mode.
        """
        if self._session is not None:
            return platforms.normalize_input_text(self._session.typed_text())
        return platforms.normalize_input_text(self.text_edit.toPlainText())

    def _on_text_changed(self) -> None:
        self._hide_inline_message()
        self._recount()
        self._schedule_estimate()

    def _recount(self) -> None:
        """Refresh the character and word counts from whatever is going to be typed."""
        text = self._session.typed_text() if self._session is not None else self.text_edit.toPlainText()
        self._chars = len(text)
        self._words = len(text.split()) if text.strip() else 0
        self._render_counter()

    def _on_file_dropped(self, content: str) -> None:
        self.text_edit.setPlainText(platforms.normalize_input_text(content))

    def set_text(self, text: str) -> None:
        """Programmatically load plain text (used by tests and, later, importers).

        Loading text means plain-text mode: if a document was imported, it is let go.
        """
        self._leave_document_mode()
        self.text_edit.setPlainText(platforms.normalize_input_text(text))

    # -- live estimate (debounced, computed off the GUI thread) -------------------

    def _schedule_estimate(self) -> None:
        self._estimate_timer.start()

    def _start_estimate(self) -> None:
        self._estimate_generation += 1
        generation = self._estimate_generation
        session = self._session
        if session is not None:
            if not session.has_text():
                self._on_estimate_ready(generation, 0.0)
                return
            document = session.adapted
            profile = self.effective_profile()

            def make_actions():
                return plan_document(document, profile, seed=0)

        else:
            text = self.normalized_text()
            if not text.strip():
                self._on_estimate_ready(generation, 0.0)
                return
            profile = self.effective_profile()

            def make_actions():
                return plan(text, profile, seed=0)

        self._estimates_in_flight += 1
        threading.Thread(
            target=_compute_estimate, args=(make_actions, generation, self._estimate_results), daemon=True
        ).start()
        if not self._estimate_poll.isActive():
            self._estimate_poll.start()

    def _poll_estimates(self) -> None:
        """On the GUI thread: collect whatever estimates the worker threads have finished."""
        while True:
            try:
                generation, duration_ms = self._estimate_results.get_nowait()
            except queue.Empty:
                break
            self._estimates_in_flight -= 1
            self._on_estimate_ready(generation, duration_ms)
        if self._estimates_in_flight <= 0:
            self._estimate_poll.stop()

    def _on_estimate_ready(self, generation: int, duration_ms: float) -> None:
        if generation != self._estimate_generation:
            return  # superseded by a newer estimate -- discard
        self._duration_ms = duration_ms
        self._render_counter()

    def _render_counter(self) -> None:
        chars_words = f"{self._chars:,} character{'s' if self._chars != 1 else ''}, {self._words:,} word{'s' if self._words != 1 else ''}"
        if self._chars == 0 or self._duration_ms is None:
            self.counter_label.setText(chars_words)
        else:
            estimate = format_duration_words(self._duration_ms / 1000.0)
            self.counter_label.setText(f"{chars_words}   ·   est. {estimate}")

    # -- importing a document -------------------------------------------------------

    def _is_idle(self) -> bool:
        """Nothing is importing and nothing is typing: free to change what will be typed."""
        return not (self._importing or self._running or self._run_controller.is_busy)

    def _on_import_requested(self, source: str) -> None:
        if not self._is_idle():
            return
        self._importer.start(source)

    def _on_browse_requested(self) -> None:
        if not self._is_idle():
            return
        chosen = self._choose_file(self, self._last_import_dir or default_start_dir())
        if not chosen:
            return
        self._last_import_dir = str(Path(chosen).parent)
        self.import_bar.set_source(chosen)
        self.import_bar.request_import()

    def _on_import_busy_changed(self, busy: bool) -> None:
        self._importing = busy
        self.import_bar.set_busy(busy)
        self._refresh_enabled()

    def _on_import_finished(self, outcome: ImportOutcome) -> None:
        if not outcome.ok:
            self._import_failed(outcome.error or "The document could not be imported.")
            return
        document = outcome.document
        if document is None or not document.plain_text().strip():
            self._import_failed("That document has no text to type.")
            return
        self._enter_document_mode(outcome)
        self.importFinished.emit(True)

    def _import_failed(self, message: str) -> None:
        """Say why an import failed, inline and in a message box; nothing else changes.

        A document already imported stays exactly as it was, and a page in
        plain-text mode stays there.
        """
        self.import_bar.show_error(message)
        self.import_bar.edit.setFocus()
        self._present_error(self, "Couldn't import the document", message)
        self.importFinished.emit(False)

    # -- document mode ----------------------------------------------------------------

    @property
    def in_document_mode(self) -> bool:
        """Whether an imported document, not the text box, is what will be typed."""
        return self._session is not None

    def document_session(self) -> DocumentSession | None:
        """The imported document fitted to the selected editor, or ``None`` in plain-text mode."""
        return self._session

    def warning_lines(self) -> tuple[str, ...]:
        """What the "What will not be reproduced" panel lists, one entry per line."""
        return self.warnings_panel.lines()

    def _enter_document_mode(self, outcome: ImportOutcome) -> None:
        target = self._selected_target()
        keymap = self._keymap_for(target)
        report = outcome.report
        self._session = DocumentSession.create(
            outcome.document,
            outcome.source,
            report.warnings if report is not None else (),
            target,
            keymap,
        )
        self._hide_inline_message()
        self.import_bar.clear_error()
        self._show_session()
        self.document_view.setFocus()
        announce(
            self.document_view,
            f"Imported {describe_source(outcome.source)}. {self._warnings_summary(self._session)}",
            False,
        )
        self.documentModeChanged.emit(True)

    def _leave_document_mode(self, restore_text: str | None = None) -> None:
        """Return to plain-text mode (a no-op if already there).

        ``restore_text`` becomes the text box's contents, as an edit that can be
        undone, so going back never throws away what was in the box before the
        import without a way to get it back.
        """
        if self._session is None:
            return
        self._session = None
        self._duration_ms = None  # the old estimate was for the document, not for this text
        self.document_view.set_document(None)
        self._apply_mode_ui()
        if restore_text is not None:
            self._replace_plain_text(restore_text)
        self._recount()
        self._schedule_estimate()
        self.documentModeChanged.emit(False)

    def _on_back_to_plain_clicked(self) -> None:
        session = self._session
        if session is None or not self._is_idle():
            return
        self._leave_document_mode(platforms.normalize_input_text(session.document.plain_text()))
        self._hide_inline_message()
        self.text_edit.setFocus()

    def _replace_plain_text(self, text: str) -> None:
        """Make ``text`` the text box's contents as one undoable edit."""
        cursor = self.text_edit.textCursor()
        cursor.beginEditBlock()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(text)
        cursor.endEditBlock()
        self.text_edit.setTextCursor(cursor)

    def _apply_mode_ui(self) -> None:
        """Show the widgets for the current mode and hide the other mode's."""
        in_document = self._session is not None
        self.text_edit.setVisible(not in_document)
        self.document_view.setVisible(in_document)
        self.document_header.setVisible(in_document)
        self.warnings_panel.setVisible(in_document)

    def _show_session(self) -> None:
        """Draw the current session: the document, where it came from, what is lost, the counts."""
        session = self._session
        if session is None:
            return
        self._duration_ms = None  # not known again until the estimate for this document arrives
        self.document_view.set_document(session.adapted)
        self.document_source_label.setFullText(f"Imported from {describe_source(session.source)}")
        self.warnings_panel.show_lines(session.plain_warnings, editor_phrase(session.target))
        self._apply_mode_ui()
        self._recount()
        self._schedule_estimate()

    @staticmethod
    def _warnings_summary(session: DocumentSession) -> str:
        count = len(session.plain_warnings)
        editor = editor_phrase(session.target)
        if count == 0:
            return f"Everything in it can be reproduced in {editor}."
        return f"{count} thing{'s' if count != 1 else ''} will not be reproduced in {editor}."

    # -- the "Typing into" editor picker ---------------------------------------------------

    def selected_target_id(self) -> str:
        """The id of the editor chosen in the "Typing into" picker.

        The picker can momentarily have nothing selected (``currentData()`` is ``None`` when
        the index is -1). That is never reported as a target id: the answer is then the
        editor the picker would open on for the saved setting (see ``initial_target_id``),
        which is always one this system offers.
        """
        current = self.target_combo.currentData()
        if isinstance(current, str) and any(t.id == current for t in self._targets):
            return current
        return initial_target_id(self._settings_store.settings.target_app, self._targets)

    def _selected_target(self) -> TargetApp:
        return targets.get_target(self.selected_target_id())

    def _select_target(self, target_id: str) -> None:
        """Move the picker to ``target_id`` without treating it as a choice the user made.

        An editor this system does not offer (a setting carried over from another OS) moves
        the picker to the one it opens on instead, never to "nothing selected".
        """
        index = self.target_combo.findData(target_id)
        if index < 0:
            index = self.target_combo.findData(initial_target_id(target_id, self._targets))
        self.target_combo.blockSignals(True)
        self.target_combo.setCurrentIndex(index)
        self.target_combo.blockSignals(False)

    def _keymap_for(self, target: TargetApp) -> Mapping[FormatOp, Chord]:
        """The shortcuts pressed for ``target``: its built-in keymap with the user's overrides on top.

        The overrides are the ones saved from the Advanced page
        (``app_settings.effective_keymap``). The settings store refuses to save
        any that cannot be read, so this does not fail for saved settings.
        """
        settings = dataclasses.replace(self._settings_store.settings, target_app=target.id)
        return app_settings.effective_keymap(settings, platforms.current_os())

    def _on_target_changed(self, index: int) -> None:
        if index < 0:
            # Nothing is selected, which is not a choice: put the picker back on an editor.
            self._select_target(self.selected_target_id())
            self._refresh_target_area()
            return
        target = self._selected_target()
        self._hide_inline_message()  # anything it said was about the previous choice
        saved = self._settings_store.settings.target_app
        if target.id != saved:
            try:
                self._settings_store.update(target_app=target.id)
            except ValueError as exc:
                # The store refuses a choice that could not be loaded back (for example
                # an editor whose shortcuts collide with a global hotkey): go back to
                # the editor that is saved rather than use one that will not be remembered.
                # (Read again: update() first took up anything another store had saved.)
                self._select_target(
                    initial_target_id(self._settings_store.settings.target_app, self._targets)
                )
                self._refresh_target_area()
                self._show_inline_message(f"Couldn't switch to {target.name}: {exc}")
                return
            except OSError as exc:
                # The choice still applies for now (the picker stays on it; see _saved_target),
                # but the person is told it will not be there next time.
                self._show_inline_message(
                    f"Now typing into {target.name}, but that choice couldn't be saved: {exc}"
                )
            else:
                self._saved_target = target.id
        self._refresh_target_area()

    def _on_settings_changed(self, _settings: object) -> None:
        """The saved settings changed elsewhere: follow the editor and shortcut overrides."""
        if self._running:
            self._settings_changed_during_run = True
            return
        self._follow_settings()

    def _follow_settings(self) -> None:
        saved = self._settings_store.settings.target_app
        if saved != self._saved_target:
            # Somebody else chose an editor: follow them. (An unrelated settings change
            # leaves the picker alone, so a choice that could not be saved survives it.)
            self._saved_target = saved
            wanted = initial_target_id(saved, self._targets)
            if wanted != self.selected_target_id():
                self._select_target(wanted)
        self._refresh_target_area()

    def _refresh_target_area(self) -> None:
        """Show the selected editor's notes, and fit a loaded document to its shortcuts."""
        target = self._selected_target()
        self.target_notes_label.setText(target.notes)
        caution = target_caution(target)
        self.target_caution_label.setText(caution)
        self.target_caution_label.setVisible(bool(caution))
        self._reapply_session(target, self._keymap_for(target))

    def _reapply_session(self, target: TargetApp, keymap: Mapping[FormatOp, Chord]) -> None:
        """Adapt the loaded document, from its source, to ``target``'s ``keymap`` (if that changed)."""
        session = self._session
        if session is None:
            return
        if session.target.id == target.id and dict(session.keymap) == dict(keymap):
            return
        self._session = session.for_target(target, keymap)
        self._show_session()
        announce(
            self.warnings_panel,
            f"Typing into {editor_phrase(target)}. {self._warnings_summary(self._session)}",
            False,
        )

    # -- enabling and disabling ------------------------------------------------------------

    def _refresh_enabled(self) -> None:
        """Enable the import controls and the actions only when nothing else is going on.

        While a document is being read, importing again, switching mode and
        starting are all off; while a run is typing, so is everything that
        would change what it is typing.
        """
        idle = not self._running and not self._importing
        self.import_bar.set_controls_enabled(idle)
        self.back_button.setEnabled(idle)
        self.preview_button.setEnabled(not self._importing)
        self.start_button.setEnabled(not self._importing)

    # -- inline validation message --------------------------------------------------

    def _show_inline_message(self, message: str) -> None:
        self.inline_message_label.setText(message)
        self.inline_message_label.setVisible(True)
        # Focus stays on the button that was pressed, so a screen reader would
        # never reach this text on its own.
        announce(self.inline_message_label, message, True)

    def _hide_inline_message(self) -> None:
        self.inline_message_label.setVisible(False)

    # -- preview --------------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        # Coming back from another page: it may have saved shortcuts or an editor meanwhile.
        self._settings_store.refresh()

    def _on_preview_clicked(self) -> None:
        if self._importing:
            return
        self._settings_store.refresh()
        session = self._session
        if session is not None:
            self._preview_document(session)
            return
        text = self.normalized_text()
        if not text.strip():
            self._show_inline_message("Type or paste some text first.")
            return
        actions = plan(text, self.effective_profile(), seed=0)
        dialog = PreviewDialog(actions, text, self._theme_manager.tokens, parent=self)
        dialog.exec()

    def _preview_document(self, session: DocumentSession) -> None:
        """Preview the imported document: its plan, verified against the document itself."""
        if not session.has_text():
            self._show_inline_message("That document has no text to type.")
            return
        actions = plan_document(session.adapted, self.effective_profile(), seed=0)
        dialog = PreviewDialog(
            actions,
            session.typed_text(),
            self._theme_manager.tokens,
            parent=self,
            document=session.adapted,
            caution=target_caution(session.target),
        )
        dialog.exec()

    # -- start / preflight ----------------------------------------------------------

    def _on_start_clicked(self) -> None:
        if self._run_controller.is_busy or self._importing:
            return
        # What is about to be typed must use the shortcuts as saved *now*, not as this page
        # last heard of them: this may re-adapt the document, so read it afterwards.
        self._settings_store.refresh()
        session = self._session
        if session is not None:
            if not session.has_text():
                self._show_inline_message("That document has no text to type.")
                return
        else:
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
        if session is not None:
            self._run_controller.start_document(
                session.adapted, self.effective_profile(), session.keymap, seed=None
            )
        else:
            self._run_controller.start(text, self.effective_profile(), seed=None)

    # -- run panel: react to RunController signals -----------------------------------

    def _set_running_ui(self, running: bool) -> None:
        self._running = running
        # The card as a whole, so its labels dim along with the controls.
        self._quick_settings_card.setEnabled(not running)
        self.text_edit.setEnabled(not running)
        self.document_view.setEnabled(not running)
        self.profile_combo.setEnabled(not running)
        self.speed_slider.setEnabled(not running)
        self.mistake_slider.setEnabled(not running)
        self.breaks_switch.setEnabled(not running)
        self.idle_actions_widget.setVisible(not running)
        self.run_panel_widget.setVisible(running)
        self._refresh_enabled()
        if running:
            self.run_hint_label.setText(self._run_hint_text())
            self.run_progress_bar.setRange(0, 0)
            self.run_progress_bar.setValue(0)
            self.run_percent_label.setText("0%")
            self.run_eta_label.setText("ETA --:--")
            self.run_elapsed_label.setText("Elapsed 0:00")
            self.run_message_label.setText("Preparing your plan…")
            self.run_progress_bar.setAccessibleDescription("Preparing")

    def _on_state_changed(self, state: str) -> None:
        if state not in _RUNNING_STATES:
            return  # a terminal state -- handled by _on_finished instead
        if state == "planning":
            self.run_message_label.setText("Preparing your plan…")
            self.run_progress_bar.setRange(0, 0)
        elif state == "typing":
            self.run_message_label.setText("Typing…")
            self.run_progress_bar.setRange(0, 100)
        elif state == "paused":
            self.run_message_label.setText("Paused. Click Resume when you're ready.")
        self.pause_resume_button.setText("Resume" if state == "paused" else "Pause")
        self.pause_resume_button.setEnabled(state in ("typing", "paused"))
        self.stop_button.setEnabled(True)

    def _on_countdown_tick(self, seconds_left: int) -> None:
        self.run_progress_bar.setRange(0, 100)
        self.run_message_label.setText(
            f"Click where the text should go — starting in {seconds_left}"
        )

    def _on_progress_changed(self, done: int, total: int, eta_seconds: float, elapsed_seconds: float) -> None:
        percent = int((done / total) * 100) if total else 0
        self.run_progress_bar.setRange(0, 100)
        self.run_progress_bar.setValue(percent)
        self.run_percent_label.setText(f"{percent}%")
        self.run_eta_label.setText(f"ETA {format_mmss(eta_seconds * 1000)}")
        self.run_elapsed_label.setText(f"Elapsed {format_mmss(elapsed_seconds * 1000)}")
        self.run_progress_bar.setAccessibleDescription(
            f"{percent} percent complete, about {spoken_duration(eta_seconds)} remaining"
        )

    def _on_finished(self, outcome: str, message: str) -> None:
        self._set_running_ui(False)
        if self._settings_changed_during_run:
            self._settings_changed_during_run = False
            self._follow_settings()
        if outcome == "failed":
            self._show_inline_message(message)

    # -- pause / resume / stop -------------------------------------------------------

    def _on_pause_resume_clicked(self) -> None:
        if self._run_controller.state == "paused":
            self._run_controller.resume()
        else:
            self._run_controller.pause()
