"""First-run onboarding: a short three-step modal that gets a new user ready to type.

1. **Welcome** -- what the app does, in a sentence and three short points.
2. **Permission** -- asks the OS whether keystrokes may be sent to other apps,
   shows the per-OS steps to allow it, and offers *Open Settings* /
   *Check again*. It never blocks: a user who has not granted access yet can
   still finish setup (the main window's footer keeps showing the status).
3. **A real test** -- a box the user can watch the app type into, mistakes and
   all, with zero risk to their documents, followed by the plain-language
   rules for using it for real.

The dialog never talks to the keyboard of the real machine. Step 3 builds a
:class:`~auto_essay_typer.driver.Driver` around :class:`BoxKeyboard`, a sink
that turns "keystrokes" into edits of the demo box, and runs it through the
same :class:`~auto_essay_typer.gui.run_controller.RunController` the Type page
uses, so planning and typing happen off the GUI thread exactly as they do for
a real run.

Finishing (or skipping) records ``onboarding_complete`` through the shared
:class:`~auto_essay_typer.gui.settings_store.SettingsStore`; closing the
window without either leaves it unset, so setup is offered again next launch.
Everything the dialog depends on is injectable (permission probe, settings
opener, driver factory, settings store) so it can be driven in tests without
touching the OS, the real keyboard, or the real settings file.
"""

from __future__ import annotations

import html
import logging
import time
from collections.abc import Callable, Sequence

from PySide6.QtCore import QObject, QSize, Qt, Signal, Slot
from PySide6.QtGui import QKeyEvent, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from auto_essay_typer import platforms
from auto_essay_typer.driver import Driver, Keyboard
from auto_essay_typer.gui import icons
from auto_essay_typer.gui.assets import asset_path
from auto_essay_typer.gui.permission_dialog import prettify_dashes
from auto_essay_typer.gui.run_controller import RunController
from auto_essay_typer.gui.settings_store import SettingsStore
from auto_essay_typer.gui.theme import LIGHT_TOKENS, ThemeManager, Tokens
from auto_essay_typer.gui.widgets import Banner, LinkButton, make_card
from auto_essay_typer.platforms import PermissionStatus
from auto_essay_typer.profiles import PRESETS, Profile

_log = logging.getLogger(__name__)

#: What the step-3 demo types. Short enough to finish in about six seconds.
SAMPLE_TEXT = "Hello! Auto Text Typer is typing this for you, typos and all."

#: The demo's typing model: the "natural" preset, quickened and with its long
#: thinking pauses, session breaks and random revisions switched off so the
#: demo is short and predictable. The mistake rate is deliberately a little
#: high so the user sees the app slip and correct itself.
DEMO_PROFILE: Profile = PRESETS["natural"].with_overrides(
    wpm=150.0,
    session_breaks=False,
    revision_rate_per_word=0.0,
    typo_rate_per_100=2.5,
    hesitation_probability=0.0,
    sentence_pause_ms=(250.0, 450.0),
    clause_pause_ms=(60.0, 140.0),
    paragraph_pause_ms=(300.0, 600.0),
)

#: Planner seed chosen so ``SAMPLE_TEXT`` gets exactly one visible slip: the
#: "in" of "typing" typed as "ni", noticed straight away, backspaced and
#: retyped. ``tests/gui/test_onboarding.py`` re-verifies this with the real
#: planner, so a change to the planner that moves the slip is caught there.
DEMO_SEED = 17

_DIALOG_WIDTH = 560
_DIALOG_HEIGHT = 540

_INTRO_TEXT = (
    "Auto Text Typer types text into whatever app you click into, at a human pace and "
    "fixing its own typos. In editors like Google Docs it can also reproduce headings, "
    "bold, lists and more."
)

#: (icon name, lead, detail) for the welcome page's three points.
_BENEFITS: tuple[tuple[str, str, str], ...] = (
    (
        "type",
        "Works in any app",
        "Google Docs, Word, a web form, a chat box: anywhere you can click and type.",
    ),
    (
        "revision",
        "Types like a person",
        "Natural pace and pauses, with typos it notices and fixes on its own.",
    ),
    (
        "file",
        "Keeps your formatting",
        "Import a Google Doc or .docx and headings, bold, italic and lists come along.",
    ),
)

_RULES: tuple[str, ...] = (
    "Click into your document during the countdown.",
    "Keep your hands off the mouse and keyboard while it types.",
    "Press Stop in this window or Esc to stop at any time.",
)

#: Outcome strings RunController's ``finished`` signal can carry.
_OUTCOMES = ("finished", "stopped", "failed")


def default_demo_driver(
    keyboard: Keyboard, *, sleep: Callable[[float], None] = time.sleep
) -> Driver:
    """The driver a real (not test) demo run uses: real pacing, no countdown, our sink.

    ``sleep`` exists so a test can run the genuine default path without waiting.
    """
    return Driver(keyboard=keyboard, sleep=sleep, countdown_seconds=0)


def _ordered_list_html(items: Sequence[str]) -> str:
    entries = "".join(f"<li style='margin-bottom:4px;'>{html.escape(item)}</li>" for item in items)
    return f"<ol style='margin:0; padding-left:18px;'>{entries}</ol>"


def _set_primary(button: QPushButton, primary: bool) -> None:
    """Switch ``button`` between the Primary and Secondary looks the app stylesheet defines."""
    name = "PrimaryButton" if primary else "SecondaryButton"
    if button.objectName() == name:
        return
    button.setObjectName(name)
    button.style().unpolish(button)
    button.style().polish(button)
    button.update()


class BoxKeyboard(QObject):
    """A ``driver.Keyboard`` whose keystrokes land in a :class:`QPlainTextEdit`.

    ``Driver.run`` calls :meth:`press_char` / :meth:`backspace` /
    :meth:`press_chord` from whichever thread runs it -- for the demo, the
    run controller's worker thread -- but a widget may only be edited on the
    GUI thread. So each call just emits a signal; this object lives in the GUI
    thread, which makes Qt deliver the signal there as a queued call (and, in
    order, so a typo's backspaces always land after the characters they undo).
    Called directly from the GUI thread -- as the tests do -- the signal is
    delivered immediately, so the same code path works in both situations.

    If the box has been destroyed (the dialog closed mid-run) the signal can no
    longer be emitted; that is swallowed so the worker winds down quietly
    instead of dying with a traceback.

    Shortcut presses have no meaning in a plain text box, so :meth:`press_chord`
    only records the chord in :attr:`chords`.
    """

    charTyped = Signal(str)
    backspaced = Signal(int)

    def __init__(self, edit: QPlainTextEdit) -> None:
        super().__init__(edit)
        self._edit = edit
        self.chords: list[tuple[str, ...]] = []
        self.charTyped.connect(self._insert)
        self.backspaced.connect(self._delete)

    # -- driver.Keyboard protocol (any thread) -------------------------------------

    def press_char(self, char: str) -> None:
        try:
            self.charTyped.emit(char)
        except RuntimeError:  # the box was deleted under a still-running worker
            pass

    def backspace(self, count: int) -> None:
        try:
            self.backspaced.emit(count)
        except RuntimeError:
            pass

    def press_chord(self, keys: tuple[str, ...]) -> None:
        self.chords.append(tuple(keys))

    # -- the actual edits (GUI thread) ------------------------------------------------

    @Slot(str)
    def _insert(self, char: str) -> None:
        self._edit.moveCursor(QTextCursor.MoveOperation.End)
        self._edit.insertPlainText(char)
        self._edit.ensureCursorVisible()

    @Slot(int)
    def _delete(self, count: int) -> None:
        cursor = self._edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor, count)
        cursor.removeSelectedText()
        self._edit.setTextCursor(cursor)
        self._edit.ensureCursorVisible()


class _StepBar(QWidget):
    """"Step N of M" on the left, one small segment per step on the right."""

    def __init__(self, steps: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._steps = steps
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.label = QLabel()
        self.label.setObjectName("MutedLabel")
        layout.addWidget(self.label)
        layout.addStretch(1)

        self.segments: list[QFrame] = []
        for _ in range(steps):
            segment = QFrame()
            segment.setObjectName("StepSegment")
            segment.setFixedSize(30, 4)
            layout.addWidget(segment, 0, Qt.AlignmentFlag.AlignVCenter)
            self.segments.append(segment)
        self.set_step(0)

    def set_step(self, index: int) -> None:
        self.label.setText(f"Step {index + 1} of {self._steps}")
        for position, segment in enumerate(self.segments):
            segment.setProperty("reached", position <= index)
            segment.style().unpolish(segment)
            segment.style().polish(segment)


class OnboardingDialog(QDialog):
    """The three-step first-run dialog. See the module docstring for the flow.

    Signals
    -------
    completed()
        Emitted once, when the user presses *Finish* (after the setting is saved).
    skipped()
        Emitted instead of ``completed`` when the user presses *Skip setup*.
    demoFinished(str)
        The step-3 demo run ended: ``"finished"``, ``"stopped"`` or ``"failed"``.

    Useful attributes
    -----------------
    ``stack`` / ``step_index``
        The three pages and which one is showing (0, 1 or 2).
    ``back_button`` / ``next_button`` / ``finish_button`` / ``skip_button``
        Footer controls. ``next_button`` is hidden on the last step (where
        ``finish_button`` takes its place); ``skip_button`` is hidden there
        too. ``back_button`` is merely disabled on the first step.
    ``permission_banner`` (its text is ``permission_status_label``),
    ``permission_steps_card`` (holding ``permission_steps_label``),
    ``permission_hint_label``, ``open_settings_button``, ``check_again_button``
        The permission step. The steps card and the hint are shown only while
        permission is missing; *Open Settings* only when the OS offers a
        settings pane for it.
    ``demo_edit`` / ``try_button`` / ``stop_button`` / ``demo_status_label`` / ``demo_progress``
        The step-3 demo. ``demo_edit`` starts out holding ``SAMPLE_TEXT``, is
        cleared when the demo starts, and ends up holding it again.
    ``finished_setup``
        True once *Finish* was pressed. ``save_error`` is the reason the
        completion flag could not be saved, or ``None``.
    """

    SAMPLE_TEXT = SAMPLE_TEXT
    STEP_COUNT = 3

    completed = Signal()
    skipped = Signal()
    demoFinished = Signal(str)

    def __init__(
        self,
        theme_manager: ThemeManager | None,
        settings_store: SettingsStore,
        *,
        permission_checker: Callable[[], PermissionStatus] = platforms.check_input_permission,
        open_settings: Callable[[], bool] = platforms.open_permission_settings,
        driver_factory: Callable[[Keyboard], Driver] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("OnboardingDialog")
        self.setWindowTitle("Welcome to Auto Text Typer")
        self.setModal(True)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setSizeGripEnabled(False)

        self._theme_manager = theme_manager
        self._store = settings_store
        self._permission_checker = permission_checker
        self._open_settings = open_settings
        self._driver_factory: Callable[[Keyboard], Driver] = driver_factory or default_demo_driver

        self._permission_status: PermissionStatus | None = None
        self._demo_running = False
        self._demo_completed = False
        self._finished_setup = False
        self._status_tone: str | None = None
        self.save_error: str | None = None
        self._feature_icons: list[tuple[QLabel, str]] = []

        # Deliberately not parented to the dialog: a run that is still winding
        # down when the dialog is destroyed keeps its controller alive (the
        # worker thread references it), so its last signal never lands on a
        # deleted object.
        self._controller = RunController(driver_factory=self._build_demo_driver)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 20)
        root.setSpacing(14)

        self.step_bar = _StepBar(self.STEP_COUNT)
        root.addWidget(self.step_bar)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_welcome_page())
        self.stack.addWidget(self._build_permission_page())
        self.stack.addWidget(self._build_demo_page())
        root.addWidget(self.stack, 1)

        root.addLayout(self._build_footer())

        # The demo box's keystroke sink, and the controller's progress feed.
        self._keyboard = BoxKeyboard(self.demo_edit)
        self._controller.stateChanged.connect(self._on_run_state)
        self._controller.countdownTick.connect(self._on_countdown_tick)
        self._controller.progressChanged.connect(self._on_progress)
        self._controller.finished.connect(self._on_run_finished)

        if theme_manager is not None:
            theme_manager.themeChanged.connect(self._restyle)
        self._restyle()

        self._refresh_permission()
        self._go_to_step(0)
        self._fix_size()

    def _fix_size(self) -> None:
        """Give the dialog a comfortable size that never clips its content.

        The floor is the larger of the nominal size and what the layout needs
        once styled (a platform font or a high text-scale setting can make
        the content taller than the nominal size). Setting it explicitly, up
        front, also keeps Qt from re-asserting it on an already-created
        window later.
        """
        self.ensurePolished()
        needed = self.layout().minimumSize()
        floor = needed.expandedTo(QSize(_DIALOG_WIDTH, _DIALOG_HEIGHT))
        self.setMinimumSize(floor)
        self.resize(floor)

    # -- read-only state ----------------------------------------------------------------

    @property
    def step_index(self) -> int:
        return self.stack.currentIndex()

    @property
    def finished_setup(self) -> bool:
        return self._finished_setup

    @property
    def tokens(self) -> Tokens:
        return self._theme_manager.tokens if self._theme_manager is not None else LIGHT_TOKENS

    @property
    def demo_running(self) -> bool:
        return self._demo_running

    @property
    def demo_completed(self) -> bool:
        """True once the demo has typed through to the end at least once."""
        return self._demo_completed

    # -- page construction ------------------------------------------------------------

    def _build_welcome_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        header = QHBoxLayout()
        header.setSpacing(14)
        self.app_icon_label = QLabel()
        self.app_icon_label.setFixedSize(60, 60)
        pixmap = QPixmap(str(asset_path("icon.png")))
        if not pixmap.isNull():
            scaled = pixmap.scaled(
                120,
                120,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(2.0)  # crisp on high-DPI screens
            self.app_icon_label.setPixmap(scaled)
        header.addWidget(self.app_icon_label)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("Welcome to Auto Text Typer")
        title.setObjectName("PageTitle")
        subtitle = QLabel("Setup takes about a minute.")
        subtitle.setObjectName("MutedLabel")
        titles.addStretch(1)
        titles.addWidget(title)
        titles.addWidget(subtitle)
        titles.addStretch(1)
        header.addLayout(titles, 1)
        layout.addLayout(header)

        self.intro_label = QLabel(_INTRO_TEXT)
        self.intro_label.setObjectName("OnboardingIntro")
        self.intro_label.setWordWrap(True)
        layout.addWidget(self.intro_label)

        layout.addSpacing(6)
        for icon_name, lead, detail in _BENEFITS:
            layout.addLayout(self._feature_row(icon_name, lead, detail))

        layout.addStretch(1)
        self.next_up_label = QLabel(
            "Next, we will check that your computer lets Auto Text Typer type into other "
            "apps, then you can watch it work in a practice box."
        )
        self.next_up_label.setObjectName("MutedLabel")
        self.next_up_label.setWordWrap(True)
        layout.addWidget(self.next_up_label)
        return page

    def _feature_row(self, icon_name: str, lead: str, detail: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)

        icon_label = QLabel()
        icon_label.setFixedSize(22, 22)
        self._feature_icons.append((icon_label, icon_name))
        row.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        text.setSpacing(1)
        lead_label = QLabel(lead)
        lead_label.setObjectName("SectionTitle")
        detail_label = QLabel(detail)
        detail_label.setObjectName("MutedLabel")
        detail_label.setWordWrap(True)
        text.addWidget(lead_label)
        text.addWidget(detail_label)
        row.addLayout(text, 1)
        return row

    def _build_permission_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        title = QLabel("Allow typing into other apps")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        intro = QLabel("Auto Text Typer types by pressing keys for you, so your system has to allow that.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.permission_banner = Banner(
            "warning", dismissible=False, theme_manager=self._theme_manager
        )
        #: The status sentence (shown with a success or warning tone).
        self.permission_status_label = self.permission_banner.message_label
        layout.addWidget(self.permission_banner)

        # The per-OS fix steps sit in a card of their own, shown only while
        # permission is missing.
        self.permission_steps_card = make_card()
        steps_layout = QVBoxLayout(self.permission_steps_card)
        steps_layout.setContentsMargins(16, 12, 16, 12)
        steps_layout.setSpacing(6)
        steps_title = QLabel("How to allow it")
        steps_title.setObjectName("SectionTitle")
        steps_layout.addWidget(steps_title)
        self.permission_steps_label = QLabel()
        self.permission_steps_label.setWordWrap(True)
        self.permission_steps_label.setTextFormat(Qt.TextFormat.RichText)
        steps_layout.addWidget(self.permission_steps_label)
        layout.addWidget(self.permission_steps_card)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.open_settings_button = QPushButton("Open Settings")
        self.open_settings_button.setObjectName("SecondaryButton")
        self.open_settings_button.setAccessibleName("Open the system settings")
        self.open_settings_button.setAutoDefault(False)
        self.open_settings_button.clicked.connect(self._on_open_settings_clicked)
        buttons.addWidget(self.open_settings_button)

        self.check_again_button = QPushButton("Check again")
        self.check_again_button.setObjectName("SecondaryButton")
        self.check_again_button.setAccessibleName("Check the permission again")
        self.check_again_button.setAutoDefault(False)
        self.check_again_button.clicked.connect(self._refresh_permission)
        buttons.addWidget(self.check_again_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.open_settings_note_label = QLabel(
            "Could not open the settings automatically. Follow the steps above instead."
        )
        self.open_settings_note_label.setObjectName("MutedLabel")
        self.open_settings_note_label.setWordWrap(True)
        self.open_settings_note_label.setVisible(False)
        layout.addWidget(self.open_settings_note_label)

        self.permission_hint_label = QLabel(
            "You can finish setup without this and fix it later. Until then typing into other "
            "apps will not work; the status at the bottom of the main window shows whether "
            "you are ready."
        )
        self.permission_hint_label.setObjectName("MutedLabel")
        self.permission_hint_label.setWordWrap(True)
        layout.addWidget(self.permission_hint_label)

        layout.addStretch(1)
        return page

    def _build_demo_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        title = QLabel("Try it right here")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        intro = QLabel(
            "Press Try it here to watch it type into the box below, including a slip it "
            "catches and fixes. Nothing outside this window is touched."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.demo_edit = QPlainTextEdit()
        self.demo_edit.setObjectName("DemoEdit")
        self.demo_edit.setPlainText(self.SAMPLE_TEXT)
        self.demo_edit.setReadOnly(True)
        self.demo_edit.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.demo_edit.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.demo_edit.setCursorWidth(2)
        self.demo_edit.setFixedHeight(78)
        self.demo_edit.setAccessibleName("Practice box")
        layout.addWidget(self.demo_edit)

        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self.demo_status_label = QLabel("Ready when you are.")
        self.demo_status_label.setObjectName("MutedLabel")
        status_row.addWidget(self.demo_status_label, 1)
        self.demo_progress = QProgressBar()
        self.demo_progress.setRange(0, 100)
        self.demo_progress.setValue(0)
        self.demo_progress.setTextVisible(False)
        self.demo_progress.setFixedSize(150, 8)
        self.demo_progress.setAccessibleName("Practice typing progress")
        status_row.addWidget(self.demo_progress, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(status_row)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.try_button = QPushButton("Try it here")
        self.try_button.setObjectName("PrimaryButton")
        self.try_button.setAccessibleName("Try it here")
        self.try_button.setAutoDefault(False)
        self.try_button.clicked.connect(self._on_try_clicked)
        buttons.addWidget(self.try_button)

        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("DestructiveButton")
        self.stop_button.setAccessibleName("Stop the practice run")
        self.stop_button.setAutoDefault(False)
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._on_stop_clicked)
        buttons.addWidget(self.stop_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        rules_card = make_card()
        rules_layout = QVBoxLayout(rules_card)
        rules_layout.setContentsMargins(16, 12, 16, 12)
        rules_layout.setSpacing(6)
        rules_title = QLabel("When you use it for real")
        rules_title.setObjectName("SectionTitle")
        rules_layout.addWidget(rules_title)
        self.rules_label = QLabel(_ordered_list_html(_RULES))
        self.rules_label.setTextFormat(Qt.TextFormat.RichText)
        self.rules_label.setWordWrap(True)
        rules_layout.addWidget(self.rules_label)
        layout.addWidget(rules_card)

        layout.addStretch(1)
        return page

    def _build_footer(self) -> QHBoxLayout:
        footer = QHBoxLayout()
        footer.setSpacing(8)

        self.skip_button = LinkButton("Skip setup", theme_manager=self._theme_manager)
        self.skip_button.setAccessibleName("Skip setup")
        self.skip_button.setToolTip("Skip the rest of setup. You can run it again from Help.")
        self.skip_button.setAutoDefault(False)  # Enter always means "continue", never "skip"
        self.skip_button.clicked.connect(self._on_skip_clicked)
        footer.addWidget(self.skip_button)
        footer.addStretch(1)

        self.back_button = QPushButton("Back")
        self.back_button.setObjectName("SecondaryButton")
        self.back_button.setAccessibleName("Back to the previous step")
        self.back_button.setAutoDefault(False)
        self.back_button.clicked.connect(lambda _checked=False: self._go_to_step(self.step_index - 1))
        footer.addWidget(self.back_button)

        self.next_button = QPushButton("Next")
        self.next_button.setObjectName("PrimaryButton")
        self.next_button.setAccessibleName("Next step")
        self.next_button.clicked.connect(lambda _checked=False: self._go_to_step(self.step_index + 1))
        footer.addWidget(self.next_button)

        self.finish_button = QPushButton("Finish")
        self.finish_button.setObjectName("SecondaryButton")
        self.finish_button.setAccessibleName("Finish setup")
        self.finish_button.clicked.connect(self._on_finish_clicked)
        footer.addWidget(self.finish_button)
        return footer

    # -- theming ------------------------------------------------------------------------

    def _restyle(self) -> None:
        tokens = self.tokens
        self.setStyleSheet(
            f"""
            QFrame#StepSegment {{
                background: {tokens.border};
                border: none;
                border-radius: 2px;
            }}
            QFrame#StepSegment[reached="true"] {{
                background: {tokens.accent};
            }}
            QLabel#OnboardingIntro {{
                font-size: 14px;
            }}
            QPlainTextEdit#DemoEdit {{
                font-size: 15px;
                padding: 10px 12px;
            }}
            """
        )
        for label, icon_name in self._feature_icons:
            label.setPixmap(icons.pixmap(icon_name, tokens.accent, 22))
        self._set_status_tone(self._status_tone)  # an error colour must follow the theme too
        # Re-polish the segments: their colours come from the sheet just replaced.
        self.step_bar.set_step(self.step_index)

    # -- navigation -----------------------------------------------------------------------

    def _go_to_step(self, index: int) -> None:
        last = self.STEP_COUNT - 1
        index = max(0, min(index, last))
        self.stack.setCurrentIndex(index)
        self.step_bar.set_step(index)

        self.back_button.setEnabled(index > 0 and not self._demo_running)
        self.next_button.setVisible(index < last)
        self.finish_button.setVisible(index == last)
        self.skip_button.setVisible(index < last)

        if index == 1:
            self._refresh_permission()
        self._update_default_button()

    def _update_default_button(self) -> None:
        """Make Enter do the obvious thing on each step."""
        last = self.STEP_COUNT - 1
        for button in (self.next_button, self.finish_button, self.try_button):
            button.setDefault(False)
        if self.step_index < last:
            self.next_button.setDefault(True)
            self.next_button.setFocus()
        elif self._demo_completed:
            self.finish_button.setDefault(True)
            self.finish_button.setFocus()
        else:
            self.try_button.setDefault(True)
            self.try_button.setFocus()

    # -- step 2: permission ------------------------------------------------------------------

    def _refresh_permission(self) -> None:
        try:
            status = self._permission_checker()
        except Exception as exc:  # noqa: BLE001 - a broken probe must not break setup
            _log.exception("permission check failed")
            status = PermissionStatus(
                granted=False,
                summary=f"Could not check the permission: {exc}",
                fix_steps=(),
                can_open_settings=False,
            )
        self._permission_status = status

        self.permission_banner.set_message(
            prettify_dashes(status.summary), "success" if status.granted else "warning"
        )
        self.permission_steps_label.setText(
            _ordered_list_html([prettify_dashes(step) for step in status.fix_steps])
        )
        self.permission_steps_card.setVisible(bool(status.fix_steps) and not status.granted)
        self.open_settings_button.setVisible(status.can_open_settings)
        self.open_settings_note_label.setVisible(False)
        self.permission_hint_label.setVisible(not status.granted)

    def _on_open_settings_clicked(self) -> None:
        try:
            opened = bool(self._open_settings())
        except Exception:  # noqa: BLE001 - fall back to the written steps
            _log.exception("could not open the permission settings")
            opened = False
        self.open_settings_note_label.setVisible(not opened)

    # -- step 3: the demo ------------------------------------------------------------------------

    def _build_demo_driver(self, **_ignored: object) -> Driver:
        """RunController's zero-argument factory: wrap our sink in the (possibly injected) driver."""
        return self._driver_factory(self._keyboard)

    def _on_try_clicked(self) -> None:
        if self._controller.is_busy:
            return
        self.demo_edit.clear()
        self.demo_progress.setValue(0)
        self._set_demo_running(True)
        self._controller.start(self.SAMPLE_TEXT, DEMO_PROFILE, seed=DEMO_SEED)

    def _on_stop_clicked(self) -> None:
        if not self._controller.is_busy:
            return
        self.stop_button.setEnabled(False)
        self.demo_status_label.setText("Stopping...")
        self._controller.stop()

    def _set_demo_running(self, running: bool) -> None:
        self._demo_running = running
        self.try_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.back_button.setEnabled(not running and self.step_index > 0)
        if running:
            self.demo_status_label.setText("Getting ready...")
            self.demo_edit.setFocus()
        self._set_status_tone(None)

    def _set_status_tone(self, tone: str | None) -> None:
        """Colour the demo status line: ``None`` for the normal muted look, ``"danger"`` for errors."""
        self._status_tone = tone
        if tone == "danger":
            self.demo_status_label.setStyleSheet(f"color: {self.tokens.danger};")
        else:
            self.demo_status_label.setStyleSheet("")

    def _on_run_state(self, state: str) -> None:
        if state == "typing":
            self.demo_status_label.setText("Typing...")
        elif state == "countdown":
            self.demo_status_label.setText("Starting...")

    def _on_countdown_tick(self, seconds_left: int) -> None:
        self.demo_status_label.setText(f"Starting in {seconds_left}...")

    def _on_progress(self, done: int, total: int, _eta: float, _elapsed: float) -> None:
        self.demo_progress.setValue(int(done / total * 100) if total else 0)

    def _on_run_finished(self, outcome: str, message: str) -> None:
        self._set_demo_running(False)
        if outcome == "finished":
            self._demo_completed = True
            self.demo_progress.setValue(100)
            self.demo_status_label.setText(
                "Done. That is exactly what it will do in your documents."
            )
            self.try_button.setText("Try again")
            self.try_button.setAccessibleName("Try again")
            _set_primary(self.try_button, False)
            _set_primary(self.finish_button, True)
        elif outcome == "stopped":
            self.demo_status_label.setText("Stopped. Press Try it here to run it again.")
        else:
            self.demo_status_label.setText(f"The demo could not run: {message}")
            self._set_status_tone("danger")
        self._update_default_button()
        self.demoFinished.emit(outcome if outcome in _OUTCOMES else "failed")

    # -- finishing ------------------------------------------------------------------------------------

    def _mark_complete(self) -> None:
        try:
            self._store.update(onboarding_complete=True)
        except (ValueError, OSError) as exc:
            # Not worth blocking the user over: they will simply see setup again
            # next launch. The parent can show ``save_error`` if it wants to.
            self.save_error = str(exc)
            _log.warning("could not save onboarding_complete: %s", exc)

    def _on_finish_clicked(self) -> None:
        self._mark_complete()
        self._finished_setup = True
        self.completed.emit()
        self.accept()

    def _on_skip_clicked(self) -> None:
        self._mark_complete()
        self.skipped.emit()
        self.accept()

    # -- closing and keys ----------------------------------------------------------------------------------

    def _stop_demo_run(self) -> None:
        if self._controller.is_busy:
            self._controller.stop()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        # Esc stops a demo in progress instead of dismissing the whole dialog
        # (the rules on the last page promise exactly that).
        if event.key() == Qt.Key.Key_Escape and self._demo_running:
            self._on_stop_clicked()
            event.accept()
            return
        super().keyPressEvent(event)

    def done(self, result: int) -> None:
        self._stop_demo_run()
        super().done(result)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._stop_demo_run()
        super().closeEvent(event)
