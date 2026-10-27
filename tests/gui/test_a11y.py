"""Accessibility: names, tooltips, keyboard operation, and screen-reader announcements."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAccessible
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QDialog,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QScrollArea,
    QSlider,
    QWidget,
)

from auto_essay_typer.gui import a11y
from auto_essay_typer.gui.a11y import RunAnnouncer, announce, spoken_duration
from auto_essay_typer.gui.main_window import MainWindow
from auto_essay_typer.gui.mini_window import MiniProgressWindow
from auto_essay_typer.gui.pages.help_page import HelpPage
from auto_essay_typer.gui.pages.settings_page import SettingsPage
from auto_essay_typer.gui.pages.type_page import TypePage
from auto_essay_typer.gui.permission_dialog import PermissionDialog
from auto_essay_typer.gui.preview_dialog import PreviewDialog
from auto_essay_typer.gui.run_controller import RunController
from auto_essay_typer.gui.theme import LIGHT_TOKENS, ThemeManager
from auto_essay_typer.planner import plan
from auto_essay_typer.platforms import OperatingSystem, PermissionStatus
from auto_essay_typer.profiles import Profile

_GRANTED = PermissionStatus(granted=True, summary="ok", fix_steps=(), can_open_settings=False)
_MAC_NOT_GRANTED = PermissionStatus(
    granted=False,
    summary="Auto Text Typer needs Accessibility access to type into other apps.",
    fix_steps=(
        "Open System Settings > Privacy & Security > Accessibility.",
        "Quit and reopen Auto Text Typer -- macOS does not grant permission to a process that is already running.",
    ),
    can_open_settings=True,
)


def _window(qtbot, qapp, tmp_path, checker=lambda: _GRANTED) -> MainWindow:
    window = MainWindow(
        ThemeManager(qapp),
        permission_checker=checker,
        settings=QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat),
    )
    qtbot.addWidget(window)
    return window


def _dialogs(qtbot, tokens=LIGHT_TOKENS):
    text = "Hello there."
    preview = PreviewDialog(plan(text, Profile(), seed=1), text, tokens)
    permission = PermissionDialog(
        _MAC_NOT_GRANTED,
        tokens,
        permission_checker=lambda: _MAC_NOT_GRANTED,
        request_prompt=lambda: True,
        open_settings=lambda: True,
        current_os=lambda: OperatingSystem.LINUX,
    )
    for dialog in (preview, permission):
        qtbot.addWidget(dialog)
    return preview, permission


# --- spoken forms ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "spoken"),
    [
        (0, "0 seconds"),
        (1, "1 second"),
        (12, "12 seconds"),
        (60, "1 minute"),
        (61, "1 minute 1 second"),
        (87, "1 minute 27 seconds"),
        (125, "2 minutes 5 seconds"),
        (-5, "0 seconds"),
    ],
)
def test_spoken_duration_reads_like_a_person_says_it(seconds, spoken):
    assert spoken_duration(seconds) == spoken


# --- announce() -----------------------------------------------------------------------------


class _FakeAccessible:
    """Stands in for QAccessible so a test can pretend a screen reader is running."""

    AnnouncementPoliteness = QAccessible.AnnouncementPoliteness

    def __init__(self, active: bool):
        self._active = active
        self.events: list = []

    def isActive(self) -> bool:  # noqa: N802 - mirrors Qt
        return self._active

    def updateAccessibility(self, event) -> None:  # noqa: N802 - mirrors Qt
        self.events.append(event)


def test_announce_does_nothing_when_no_screen_reader_is_running(qtbot, qapp, monkeypatch):
    fake = _FakeAccessible(active=False)
    monkeypatch.setattr(a11y, "QAccessible", fake)
    label = QLabel("x")
    qtbot.addWidget(label)
    assert announce(label, "hello") is False
    assert fake.events == []


def test_announce_sends_a_polite_event_when_a_screen_reader_is_running(qtbot, qapp, monkeypatch):
    fake = _FakeAccessible(active=True)
    monkeypatch.setattr(a11y, "QAccessible", fake)
    label = QLabel("x")
    qtbot.addWidget(label)
    assert announce(label, "Typing") is True
    (event,) = fake.events
    assert event.message() == "Typing"
    assert event.politeness() == QAccessible.AnnouncementPoliteness.Polite


def test_announce_can_interrupt_for_failures(qtbot, qapp, monkeypatch):
    fake = _FakeAccessible(active=True)
    monkeypatch.setattr(a11y, "QAccessible", fake)
    label = QLabel("x")
    qtbot.addWidget(label)
    announce(label, "Typing failed", True)
    assert fake.events[0].politeness() == QAccessible.AnnouncementPoliteness.Assertive


def test_announce_ignores_an_empty_message(qtbot, qapp, monkeypatch):
    fake = _FakeAccessible(active=True)
    monkeypatch.setattr(a11y, "QAccessible", fake)
    label = QLabel("x")
    qtbot.addWidget(label)
    assert announce(label, "") is False
    assert fake.events == []


# --- RunAnnouncer ---------------------------------------------------------------------------


def _announcer(qtbot):
    controller = RunController(driver_factory=lambda: None)
    anchor = QWidget()
    qtbot.addWidget(anchor)
    said: list[tuple[str, bool]] = []
    RunAnnouncer(controller, anchor, lambda _anchor, message, assertive: said.append((message, assertive)) or True)
    return controller, said


def test_the_countdown_is_announced_once_not_every_second(qtbot, qapp):
    controller, said = _announcer(qtbot)
    for seconds in (3, 2, 1):
        controller.countdownTick.emit(seconds)
    assert said == [("Click where the text should go. Typing starts in 3 seconds.", False)]


def test_a_run_is_narrated_start_to_finish(qtbot, qapp):
    controller, said = _announcer(qtbot)
    controller.stateChanged.emit("planning")
    controller.stateChanged.emit("typing")
    for done in (10, 24, 26, 49, 50, 51, 76, 100):
        controller.progressChanged.emit(done, 100, 5.0, 5.0)
    controller.stateChanged.emit("paused")
    controller.finished.emit("finished", "")
    assert [message for message, _ in said] == [
        "Typing",
        "25 percent",
        "50 percent",
        "75 percent",
        "Paused",
        "Finished typing",
    ]


def test_progress_milestones_reset_for_the_next_run(qtbot, qapp):
    controller, said = _announcer(qtbot)
    controller.stateChanged.emit("planning")
    controller.progressChanged.emit(60, 100, 1.0, 1.0)
    controller.stateChanged.emit("planning")
    controller.progressChanged.emit(60, 100, 1.0, 1.0)
    assert [m for m, _ in said].count("25 percent") == 2


def test_a_failed_run_is_announced_assertively_with_the_reason(qtbot, qapp):
    controller, said = _announcer(qtbot)
    controller.finished.emit("failed", "The keyboard is not available.")
    assert said == [("Typing failed. The keyboard is not available.", True)]


def test_a_stopped_run_is_announced(qtbot, qapp):
    controller, said = _announcer(qtbot)
    controller.finished.emit("stopped", "")
    assert said == [("Stopped", False)]


def test_a_zero_length_plan_does_not_divide_by_zero(qtbot, qapp):
    controller, said = _announcer(qtbot)
    controller.progressChanged.emit(0, 0, 0.0, 0.0)
    assert said == []


def test_the_main_window_wires_up_an_announcer(qtbot, qapp, tmp_path):
    window = _window(qtbot, qapp, tmp_path)
    assert isinstance(window._announcer, RunAnnouncer)


# --- every interactive control has a name ---------------------------------------------------

_INTERACTIVE = (QAbstractButton, QComboBox, QSlider, QPlainTextEdit, QScrollArea, QProgressBar)


def _unnamed(root: QWidget) -> list[str]:
    missing = []
    for widget in root.findChildren(QWidget):
        if not isinstance(widget, _INTERACTIVE):
            continue
        # Internals of other widgets (a combo's popup, a scroll area's viewport and bars).
        if widget.metaObject().className() in ("QComboBoxListView", "QScrollBar"):
            continue
        if isinstance(widget.parent(), QScrollArea) and not isinstance(widget, QScrollArea):
            continue
        # A scroll area nobody can focus is just a container, not a control.
        if isinstance(widget, QScrollArea) and widget.focusPolicy() == Qt.FocusPolicy.NoFocus:
            continue
        if not widget.accessibleName():
            missing.append(f"{type(widget).__name__}({getattr(widget, 'text', lambda: '')()!r})")
    return missing


def test_every_control_on_the_type_page_has_an_accessible_name(qtbot, qapp, tmp_path):
    window = _window(qtbot, qapp, tmp_path)
    page = window.page_stack.widget(0)
    assert isinstance(page, TypePage)
    assert _unnamed(page) == []


def test_every_control_on_the_settings_page_has_an_accessible_name(qtbot, qapp, tmp_path):
    page = SettingsPage(ThemeManager(qapp), lambda: True, lambda _v: None)
    qtbot.addWidget(page)
    assert _unnamed(page) == []


def test_the_help_pages_scroll_area_has_an_accessible_name(qtbot, qapp):
    page = HelpPage("1.0.0")
    qtbot.addWidget(page)
    assert _unnamed(page) == []


def test_every_control_in_the_window_chrome_has_an_accessible_name(qtbot, qapp, tmp_path):
    window = _window(qtbot, qapp, tmp_path)
    for button in window._nav_buttons.values():
        assert button.accessibleName() == button.text()
    assert window.permission_chip.accessibleName()


def test_every_control_in_the_mini_window_has_an_accessible_name(qtbot, qapp):
    ThemeManager(qapp)
    window = MiniProgressWindow(RunController(driver_factory=lambda: None))
    qtbot.addWidget(window)
    assert _unnamed(window) == []


def test_every_control_in_the_dialogs_has_an_accessible_name(qtbot, qapp):
    ThemeManager(qapp)
    preview, permission = _dialogs(qtbot)
    assert _unnamed(preview) == []
    assert _unnamed(permission) == []


def test_controls_whose_purpose_is_not_obvious_have_tooltips(qtbot, qapp, tmp_path):
    window = _window(qtbot, qapp, tmp_path)
    page = window.page_stack.widget(0)
    for control in (
        page.profile_combo,
        page.speed_slider,
        page.mistake_slider,
        page.breaks_switch,
        page.preview_button,
        page.start_button,
        page.pause_resume_button,
        page.stop_button,
        window.permission_chip,
        *window._nav_buttons.values(),
    ):
        assert control.toolTip(), control.accessibleName()
    settings = window.page_stack.widget(1)
    assert settings.theme_combo.toolTip()
    assert settings.mini_window_checkbox.toolTip()


def test_the_start_buttons_tooltip_names_the_native_shortcut(qtbot, qapp, tmp_path):
    from PySide6.QtGui import QKeySequence

    window = _window(qtbot, qapp, tmp_path)
    native = QKeySequence("Ctrl+Return").toString(QKeySequence.SequenceFormat.NativeText)
    assert native in window.page_stack.widget(0).start_button.toolTip()


# --- progress and status are exposed to screen readers --------------------------------------


def test_the_type_pages_progress_bar_describes_its_progress(qtbot, qapp, tmp_path):
    window = _window(qtbot, qapp, tmp_path)
    page = window.page_stack.widget(0)
    assert page.run_progress_bar.accessibleName() == "Typing progress"
    page._on_progress_changed(27, 100, 87.0, 19.0)
    assert page.run_progress_bar.accessibleDescription() == "27 percent complete, about 1 minute 27 seconds remaining"


def test_the_mini_windows_progress_bar_describes_its_progress(qtbot, qapp):
    ThemeManager(qapp)
    controller = RunController(driver_factory=lambda: None)
    window = MiniProgressWindow(controller)
    qtbot.addWidget(window)
    assert window.progress_bar.accessibleName() == "Typing progress"
    controller.progressChanged.emit(50, 100, 12.0, 3.0)
    assert window.progress_bar.accessibleDescription() == "50 percent complete, about 12 seconds remaining"


def test_the_permission_chip_exposes_its_state_to_screen_readers(qtbot, qapp, tmp_path):
    state = {"status": _GRANTED}
    window = _window(qtbot, qapp, tmp_path, checker=lambda: state["status"])
    chip = window.permission_chip
    assert chip.accessibleName() == "Typing permission"
    assert chip.accessibleDescription() == "Ready to type"
    state["status"] = _MAC_NOT_GRANTED
    window.refresh_permission_status()
    assert chip.accessibleDescription() == chip.text() == "Permission needed - Fix"


# --- keyboard operation ---------------------------------------------------------------------


def test_tab_order_on_the_type_page_follows_the_visual_order(qtbot, qapp, tmp_path):
    window = _window(qtbot, qapp, tmp_path)
    page = window.page_stack.widget(0)
    expected = [
        page.text_edit,
        page.profile_combo,
        page.speed_slider,
        page.mistake_slider,
        page.breaks_switch,
        page.preview_button,
        page.start_button,
    ]
    chain, widget = [], page.text_edit
    for _ in range(80):
        chain.append(widget)
        widget = widget.nextInFocusChain()
        if widget is page.text_edit:
            break
    assert [w for w in chain if w in expected] == expected


def test_every_control_can_take_keyboard_focus(qtbot, qapp, tmp_path):
    window = _window(qtbot, qapp, tmp_path)
    page = window.page_stack.widget(0)
    for control in (
        page.text_edit,
        page.profile_combo,
        page.speed_slider,
        page.mistake_slider,
        page.breaks_switch,
        page.preview_button,
        page.start_button,
        page.pause_resume_button,
        page.stop_button,
        window.permission_chip,
        *window._nav_buttons.values(),
    ):
        assert control.focusPolicy() & Qt.FocusPolicy.TabFocus, control.accessibleName()


def test_the_pages_own_scroll_area_is_not_a_tab_stop(qtbot, qapp, tmp_path):
    page = _window(qtbot, qapp, tmp_path).page_stack.widget(0)
    assert page.scroll_area.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_control_shortcuts_switch_pages(qtbot, qapp, tmp_path):
    from PySide6.QtGui import QShortcut

    window = _window(qtbot, qapp, tmp_path)
    shortcuts = {s.key().toString(): s for s in window.findChildren(QShortcut)}
    shortcuts["Ctrl+2"].activated.emit()
    assert window.page_stack.currentIndex() == 1
    shortcuts["Ctrl+3"].activated.emit()
    assert window.page_stack.currentIndex() == 2
    shortcuts["Ctrl+1"].activated.emit()
    assert window.page_stack.currentIndex() == 0
    assert "Ctrl" in window._nav_buttons["settings"].toolTip() or "⌘" in window._nav_buttons["settings"].toolTip()


def test_escape_closes_the_preview_dialog(qtbot, qapp):
    ThemeManager(qapp)
    preview, _permission = _dialogs(qtbot)
    preview.show()
    qtbot.waitExposed(preview)
    qtbot.keyClick(preview, Qt.Key.Key_Escape)
    assert preview.isVisible() is False
    assert preview.result() == QDialog.DialogCode.Rejected


def test_return_presses_the_preview_dialogs_default_button(qtbot, qapp):
    ThemeManager(qapp)
    preview, _permission = _dialogs(qtbot)
    preview.show()
    qtbot.waitExposed(preview)
    assert preview.close_button.isDefault()
    qtbot.keyClick(preview, Qt.Key.Key_Return)
    assert preview.isVisible() is False
    assert preview.result() == QDialog.DialogCode.Accepted


def test_escape_cancels_the_permission_dialog_without_granting(qtbot, qapp):
    ThemeManager(qapp)
    _preview, permission = _dialogs(qtbot)
    permission.show()
    qtbot.waitExposed(permission)
    qtbot.keyClick(permission, Qt.Key.Key_Escape)
    assert permission.isVisible() is False
    assert permission.granted() is False


def test_return_presses_check_again_in_the_permission_dialog(qtbot, qapp):
    ThemeManager(qapp)
    granted_after = {"value": False}
    status = _MAC_NOT_GRANTED
    permission = PermissionDialog(
        status,
        LIGHT_TOKENS,
        permission_checker=lambda: _GRANTED if granted_after["value"] else status,
        request_prompt=lambda: True,
        open_settings=lambda: True,
        current_os=lambda: OperatingSystem.LINUX,
    )
    qtbot.addWidget(permission)
    permission.show()
    qtbot.waitExposed(permission)
    assert permission.check_again_button.isDefault()

    qtbot.keyClick(permission, Qt.Key.Key_Return)  # still not granted: stays open, says so
    assert permission.isVisible() is True
    assert permission._retry_label.isHidden() is False

    granted_after["value"] = True
    qtbot.keyClick(permission, Qt.Key.Key_Return)
    assert permission.granted() is True
    assert permission.isVisible() is False


# --- text hygiene ---------------------------------------------------------------------------


def _visible_strings(root: QWidget) -> list[str]:
    strings = []
    for widget in [root, *root.findChildren(QWidget)]:
        for attribute in ("text", "placeholderText", "toolTip", "accessibleName", "accessibleDescription", "windowTitle"):
            value = getattr(widget, attribute, None)
            if callable(value):
                try:
                    strings.append(str(value()))
                except TypeError:
                    pass
    return strings


def test_no_visible_text_uses_an_ascii_double_dash(qtbot, qapp, tmp_path):
    window = _window(qtbot, qapp, tmp_path, checker=lambda: _MAC_NOT_GRANTED)
    preview, permission = _dialogs(qtbot)
    mismatch = PreviewDialog([], "text that was never planned", LIGHT_TOKENS)
    qtbot.addWidget(mismatch)
    for root in (window, preview, permission, mismatch, HelpPage("1.0.0")):
        for string in _visible_strings(root):
            assert " -- " not in string, string


def test_the_permission_dialog_renders_the_platform_steps_with_a_real_dash(qtbot, qapp):
    ThemeManager(qapp)
    _preview, permission = _dialogs(qtbot)
    assert " — macOS does not grant" in permission._steps_label.text()
    assert "--" not in permission._steps_label.text()
