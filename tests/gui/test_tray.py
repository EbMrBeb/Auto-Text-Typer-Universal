"""Tests for :mod:`auto_essay_typer.gui.tray`.

The tray is built against a fake ``QSystemTrayIcon`` (injected through
``tray_factory``), so no real tray icon is ever created or shown. The menu is
a real ``QMenu``; the run state comes from a real
:class:`~auto_essay_typer.gui.run_controller.RunController`.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QSystemTrayIcon

from auto_essay_typer import platforms
from auto_essay_typer.gui import tray as tray_module
from auto_essay_typer.gui.assets import asset_path
from auto_essay_typer.gui.run_controller import STATES, RunController
from auto_essay_typer.gui.theme import ThemeManager, ThemeMode
from auto_essay_typer.gui.tray import BUSY_STATES, AppTray, load_tray_icon
from auto_essay_typer.platforms import OperatingSystem

REASON = QSystemTrayIcon.ActivationReason


class FakeTray(QObject):
    """Stands in for QSystemTrayIcon: records what the AppTray does to it."""

    activated = Signal(object)

    def __init__(self):
        super().__init__()
        self.icons: list[QIcon] = []
        self.tooltip: str | None = None
        self.menu = None
        self.visible = False
        self.messages: list[tuple] = []

    def setIcon(self, icon):  # noqa: N802 - mirrors Qt's API
        self.icons.append(icon)

    def setToolTip(self, text):  # noqa: N802
        self.tooltip = text

    def setContextMenu(self, menu):  # noqa: N802
        self.menu = menu

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False

    def isVisible(self):  # noqa: N802
        return self.visible

    def showMessage(self, title, message, icon=None, msecs=10000):  # noqa: N802
        self.messages.append((title, message, icon, msecs))


def make_tray(*, available=True, controller=None, **kwargs):
    """``(tray, controller, built_fakes)``; ``built_fakes`` lists every FakeTray the factory made."""
    built: list[FakeTray] = []

    def factory():
        fake = FakeTray()
        built.append(fake)
        return fake

    controller = controller if controller is not None else RunController()
    tray = AppTray(controller, is_available=lambda: available, tray_factory=factory, **kwargs)
    return tray, controller, built


# --- no tray available: degrade silently -------------------------------------------------


def test_without_a_tray_nothing_is_built_and_every_method_is_a_safe_no_op(qapp):
    tray, controller, built = make_tray(available=False)

    assert tray.available is False
    assert built == []  # the factory was never even called
    assert tray.menu is None

    tray.show()
    tray.hide()
    tray.notify("Auto Text Typer", "Done")
    tray.sync_state("typing")
    controller.stateChanged.emit("paused")  # still wired up, still harmless


def test_a_failing_availability_probe_counts_as_no_tray(qapp):
    def explode():
        raise RuntimeError("no platform plugin")

    built = []
    tray = AppTray(
        RunController(),
        is_available=explode,
        tray_factory=lambda: built.append(1),
    )
    assert tray.available is False
    assert built == []


def test_a_tray_that_cannot_be_built_degrades_instead_of_crashing(qapp):
    def factory():
        raise RuntimeError("the notification area vanished")

    tray = AppTray(RunController(), is_available=lambda: True, tray_factory=factory)

    assert tray.available is False
    assert tray.menu is None
    tray.show()  # still a no-op
    tray.notify("x", "y")


def test_the_default_construction_follows_the_platform(qapp):
    # Under the offscreen test platform there is no system tray, so the defaults
    # (the real QSystemTrayIcon probe and factory) degrade rather than build one.
    tray = AppTray(RunController())
    assert tray.available == QSystemTrayIcon.isSystemTrayAvailable()
    if not tray.available:
        tray.show()
        tray.hide()
        tray.notify("x", "y")
        assert tray.menu is None


# --- the tray and its menu -----------------------------------------------------------------


def test_the_tray_is_built_with_an_icon_a_tooltip_and_a_menu(qapp):
    tray, _controller, built = make_tray()

    assert tray.available is True
    (fake,) = built
    assert len(fake.icons) == 1
    assert fake.tooltip == "Auto Text Typer"
    assert fake.menu is tray.menu  # the tray's menu is the one the AppTray keeps alive
    assert fake.visible is False  # building does not show it


def test_show_and_hide_drive_the_tray_icon(qapp):
    tray, _controller, (fake,) = make_tray()
    tray.show()
    assert fake.visible is True
    tray.hide()
    assert fake.visible is False


def test_the_menu_has_the_five_actions_and_a_separator_before_quit(qapp):
    tray, _controller, _built = make_tray()

    actions = tray.menu.actions()
    assert [a.text() for a in actions] == [
        "Show window",
        "Start typing from clipboard",
        "Pause",
        "Stop",
        "",
        "Quit",
    ]
    assert [a.isSeparator() for a in actions] == [False, False, False, False, True, False]
    assert actions[0] is tray.show_action
    assert actions[1] is tray.clipboard_action
    assert actions[2] is tray.pause_action
    assert actions[3] is tray.stop_action
    assert actions[5] is tray.quit_action


# --- the menu follows the run state ------------------------------------------------------------


@pytest.mark.parametrize("state", STATES)
def test_menu_enabled_states_follow_every_run_state(qapp, state):
    tray, controller, _built = make_tray()

    controller.stateChanged.emit(state)

    busy = state in BUSY_STATES
    assert tray.clipboard_action.isEnabled() is (not busy)
    assert tray.stop_action.isEnabled() is busy
    assert tray.pause_action.isEnabled() is (state in ("typing", "paused"))
    assert tray.pause_action.text() == ("Resume" if state == "paused" else "Pause")
    assert tray.show_action.isEnabled() is True
    assert tray.quit_action.isEnabled() is True


def test_quit_stays_in_the_tray_menu_on_macos(qapp):
    tray, _controller, _built = make_tray()
    assert tray.quit_action.menuRole() == QAction.MenuRole.NoRole


def test_the_busy_states_are_exactly_the_runs_in_flight():
    assert BUSY_STATES == {"planning", "countdown", "typing", "paused", "resuming"}
    assert BUSY_STATES <= set(STATES)


def test_the_menu_starts_in_the_controllers_current_state(qapp):
    controller = RunController()
    controller._state = "paused"  # a run already in flight when the tray is built

    tray, _controller, _built = make_tray(controller=controller)

    assert tray.pause_action.text() == "Resume"
    assert tray.pause_action.isEnabled() is True
    assert tray.stop_action.isEnabled() is True
    assert tray.clipboard_action.isEnabled() is False


def test_an_idle_tray_offers_the_clipboard_and_nothing_to_stop(qapp):
    tray, _controller, _built = make_tray()
    assert tray.clipboard_action.isEnabled() is True
    assert tray.pause_action.isEnabled() is False
    assert tray.stop_action.isEnabled() is False


def test_an_unknown_state_is_treated_as_not_busy(qapp):
    tray, _controller, _built = make_tray()
    tray.sync_state("something-new")
    assert tray.clipboard_action.isEnabled() is True
    assert tray.stop_action.isEnabled() is False


def test_the_pause_label_flips_while_paused_and_back_again(qapp):
    tray, controller, _built = make_tray()
    controller.stateChanged.emit("typing")
    assert tray.pause_action.text() == "Pause"
    controller.stateChanged.emit("paused")
    assert tray.pause_action.text() == "Resume"
    controller.stateChanged.emit("resuming")  # the resume countdown
    assert tray.pause_action.text() == "Pause"
    assert tray.pause_action.isEnabled() is False
    controller.stateChanged.emit("typing")
    assert tray.pause_action.text() == "Pause"
    assert tray.pause_action.isEnabled() is True


# --- the menu emits signals -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action_name", "signal_name"),
    [
        ("show_action", "showWindowRequested"),
        ("clipboard_action", "startFromClipboardRequested"),
        ("pause_action", "pauseResumeRequested"),
        ("stop_action", "stopRequested"),
        ("quit_action", "quitRequested"),
    ],
)
def test_each_menu_action_emits_its_signal(qtbot, action_name, signal_name):
    tray, _controller, _built = make_tray()
    tray.sync_state("typing")  # every action is enabled while typing, except the clipboard one:
    tray.clipboard_action.setEnabled(True)

    with qtbot.waitSignal(getattr(tray, signal_name), timeout=1000):
        getattr(tray, action_name).trigger()


def test_triggering_one_action_emits_only_its_own_signal(qtbot):
    tray, _controller, _built = make_tray()
    emitted = []
    for name in (
        "showWindowRequested",
        "startFromClipboardRequested",
        "pauseResumeRequested",
        "stopRequested",
        "quitRequested",
    ):
        getattr(tray, name).connect(lambda name=name: emitted.append(name))

    tray.show_action.trigger()

    assert emitted == ["showWindowRequested"]


# --- clicking the icon ------------------------------------------------------------------------------


@pytest.mark.parametrize("reason", [REASON.Trigger, REASON.DoubleClick])
def test_clicking_the_icon_asks_to_show_the_window(qtbot, reason):
    tray, _controller, (fake,) = make_tray()
    with qtbot.waitSignal(tray.showWindowRequested, timeout=1000):
        fake.activated.emit(reason)


@pytest.mark.parametrize("reason", [REASON.Context, REASON.MiddleClick])
def test_a_right_or_middle_click_does_not_show_the_window(qtbot, reason):
    tray, _controller, (fake,) = make_tray()
    with qtbot.assertNotEmitted(tray.showWindowRequested):
        fake.activated.emit(reason)


# --- notifications -------------------------------------------------------------------------------------


def test_notify_shows_a_message_when_the_icon_is_visible(qapp):
    tray, _controller, (fake,) = make_tray()
    tray.show()

    tray.notify("Typing finished", "All 240 words were typed.")

    ((title, message, icon, msecs),) = fake.messages
    assert (title, message) == ("Typing finished", "All 240 words were typed.")
    assert icon == QSystemTrayIcon.MessageIcon.Information
    assert msecs > 0


def test_notify_is_silent_while_the_icon_is_hidden(qapp):
    tray, _controller, (fake,) = make_tray()
    tray.notify("Typing finished", "All 240 words were typed.")
    assert fake.messages == []

    tray.show()
    tray.hide()
    tray.notify("Typing finished", "All 240 words were typed.")
    assert fake.messages == []


# --- theming ------------------------------------------------------------------------------------------------


def test_the_icon_is_reapplied_when_the_theme_changes(qapp):
    theme_manager = ThemeManager(qapp, mode=ThemeMode.LIGHT)
    tray, _controller, (fake,) = make_tray(theme_manager=theme_manager)
    assert len(fake.icons) == 1

    theme_manager.set_mode(ThemeMode.DARK)

    assert len(fake.icons) == 2
    theme_manager.set_mode(ThemeMode.SYSTEM)


def test_a_theme_change_without_a_tray_is_harmless(qapp):
    theme_manager = ThemeManager(qapp, mode=ThemeMode.LIGHT)
    tray, _controller, _built = make_tray(available=False, theme_manager=theme_manager)
    theme_manager.set_mode(ThemeMode.DARK)
    assert tray.available is False
    theme_manager.set_mode(ThemeMode.SYSTEM)


# --- the icon file ---------------------------------------------------------------------------------------------


def _pretend_os(monkeypatch, os_):
    monkeypatch.setattr(platforms, "current_os", lambda *args, **kwargs: os_)


def test_macos_uses_the_template_icon_as_a_mask(qapp, monkeypatch):
    _pretend_os(monkeypatch, OperatingSystem.MACOS)
    icon = load_tray_icon()
    assert not icon.isNull()
    assert icon.isMask() is True


@pytest.mark.parametrize("os_", [OperatingSystem.WINDOWS, OperatingSystem.LINUX])
def test_other_platforms_use_the_coloured_icon(qapp, monkeypatch, os_):
    _pretend_os(monkeypatch, os_)
    icon = load_tray_icon()
    assert not icon.isNull()
    assert icon.isMask() is False


def test_the_bundled_tray_assets_exist():
    for name in ("tray-icon.png", "tray-icon-template.png", "icon.png"):
        assert asset_path(name).exists(), name


def test_a_missing_tray_icon_falls_back_to_the_app_icon(qapp, monkeypatch):
    _pretend_os(monkeypatch, OperatingSystem.LINUX)
    real_icon = asset_path("icon.png")
    monkeypatch.setattr(
        tray_module,
        "asset_path",
        lambda name: real_icon if name == "icon.png" else asset_path("definitely-missing.png"),
    )
    assert not load_tray_icon().isNull()


def test_an_unreadable_icon_never_raises_and_yields_an_empty_icon(qapp, monkeypatch, tmp_path):
    _pretend_os(monkeypatch, OperatingSystem.MACOS)
    garbage = tmp_path / "garbage.png"
    garbage.write_bytes(b"this is not a png")
    monkeypatch.setattr(tray_module, "asset_path", lambda name: garbage)

    icon = load_tray_icon()

    assert icon.isNull()


def test_a_failing_asset_lookup_never_raises(qapp, monkeypatch):
    def boom(name):
        raise OSError("assets directory unreadable")

    monkeypatch.setattr(tray_module, "asset_path", boom)
    assert load_tray_icon().isNull()
