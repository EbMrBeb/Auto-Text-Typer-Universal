"""Regression tests for the macOS "Start typing kills the app" crash.

``RunController`` used to build its ``Driver`` -- and with it the real
``PynputKeyboard`` and pynput ``Controller`` -- on the background worker thread.
On macOS, constructing a ``Controller`` off the main thread while a GUI is
running makes the OS kill the whole process (SIGTRAP, exit 133). The suite never
noticed because every test injected a ``RecordingKeyboard``, which touches no OS
API.

These tests make the thread observable: the driver factory records which thread
built the keyboard. They need no real keyboard, so they are safe and deterministic;
the ``fake_pynput`` fixture (``tests/conftest.py``) stands in for pynput's Controller.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QSettings

from auto_essay_typer import platforms
from auto_essay_typer.driver import Driver, RecordingKeyboard
from auto_essay_typer.gui.main_window import MainWindow
from auto_essay_typer.gui.run_controller import RunController
from auto_essay_typer.gui.theme import ThemeManager
from auto_essay_typer.platforms import OperatingSystem, PermissionStatus
from auto_essay_typer.profiles import Profile


class _ThreadNotingKeyboard(RecordingKeyboard):
    """A RecordingKeyboard that also notes the thread each keystroke arrived on."""

    def __init__(self) -> None:
        super().__init__()
        self.created_on = threading.current_thread()
        self.typed_on: list[threading.Thread] = []

    def press_char(self, char: str) -> None:
        self.typed_on.append(threading.current_thread())
        super().press_char(char)


def _noting_factory(keyboards: list[_ThreadNotingKeyboard]):
    def factory() -> Driver:
        keyboard = _ThreadNotingKeyboard()
        keyboards.append(keyboard)
        return Driver(keyboard=keyboard, sleep=lambda _seconds: None, countdown_seconds=0)

    return factory


def _granted() -> PermissionStatus:
    return PermissionStatus(granted=True, summary="ok", fix_steps=(), can_open_settings=False)


def test_start_builds_the_keyboard_on_the_main_thread_and_types_on_the_worker(qtbot):
    keyboards: list[_ThreadNotingKeyboard] = []
    controller = RunController(driver_factory=_noting_factory(keyboards))

    with qtbot.waitSignal(controller.finished, timeout=5000) as blocker:
        controller.start("Hello there.", Profile(), seed=1)

    assert blocker.args[0] == "finished"
    (keyboard,) = keyboards
    assert keyboard.created_on is threading.main_thread(), (
        "the keyboard was built off the main thread: on macOS that kills the process"
    )
    # Typing itself must stay off the GUI thread, or the window would freeze.
    assert keyboard.typed_on
    assert all(thread is not threading.main_thread() for thread in keyboard.typed_on)


def test_pressing_start_typing_builds_the_keyboard_on_the_main_thread(qtbot, qapp, tmp_path):
    keyboards: list[_ThreadNotingKeyboard] = []
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(
        ThemeManager(qapp),
        driver_factory=_noting_factory(keyboards),
        permission_checker=_granted,
        settings=settings,
    )
    qtbot.addWidget(window)
    page = window.page_stack.widget(0)
    page.set_text("Hello there.")

    with qtbot.waitSignal(window.run_controller.finished, timeout=8000) as blocker:
        page.start_button.click()

    assert blocker.args[0] == "finished"
    (keyboard,) = keyboards
    assert keyboard.created_on is threading.main_thread()


def test_default_driver_gets_its_pynput_controller_built_on_the_main_thread(
    qtbot, monkeypatch, fake_pynput
):
    """The crash scenario end to end with the real ``Driver`` and ``PynputKeyboard``.

    The OS is pinned to macOS and nothing is preloaded, so before the fix the worker
    thread built the Controller (fatal on a real Mac, here a refused build). Now the
    Controller must be built on the main thread, by ``start()``, and only *used* by
    the worker.
    """
    monkeypatch.setattr(platforms, "current_os", lambda: OperatingSystem.MACOS)
    controller = RunController(
        driver_factory=lambda: Driver(countdown_seconds=0, sleep=lambda _seconds: None)
    )

    with qtbot.waitSignal(controller.finished, timeout=5000) as blocker:
        controller.start("Hello there.", Profile(typo_rate_per_100=0.0), seed=1)

    assert blocker.args[0] == "finished", blocker.args
    (pynput_controller,) = fake_pynput.instances
    assert pynput_controller.built_on is threading.main_thread()
    assert pynput_controller.text == "Hello there."
    assert all(thread is not threading.main_thread() for thread in pynput_controller.used_on)


def test_a_driver_factory_that_raises_is_reported_as_failed(qtbot):
    def broken_factory() -> Driver:
        raise RuntimeError("keyboard backend exploded")

    controller = RunController(driver_factory=broken_factory)
    seen: list[tuple[str, str]] = []
    states: list[str] = []
    controller.finished.connect(lambda outcome, message: seen.append((outcome, message)))
    controller.stateChanged.connect(states.append)

    controller.start("Hello there.", Profile(), seed=1)

    # The failure is reported before start() returns -- the factory runs on the
    # calling thread now -- so there is nothing to wait for.
    assert [outcome for outcome, _message in seen] == ["failed"]
    assert "keyboard backend exploded" in seen[0][1]
    assert controller.state == "failed"
    assert states == ["failed"]
    assert controller.is_busy is False


def test_a_keyboard_setup_failure_is_shown_to_the_user_on_the_type_page(qtbot, qapp, tmp_path):
    """The guard rail's whole point: a message the user can read, not a dead process."""

    def broken_factory() -> Driver:
        raise RuntimeError("keyboard backend exploded")

    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(
        ThemeManager(qapp),
        driver_factory=broken_factory,
        permission_checker=_granted,
        settings=settings,
    )
    qtbot.addWidget(window)
    page = window.page_stack.widget(0)
    page.set_text("Hello there.")

    page.start_button.click()

    # isHidden(), not isVisible(): the window is deliberately never shown in unit tests.
    assert page.inline_message_label.isHidden() is False
    assert "keyboard backend exploded" in page.inline_message_label.text()
    assert window.run_controller.is_busy is False
    assert page.start_button.isEnabled()


def test_the_controller_can_start_again_after_a_factory_failure(qtbot):
    keyboards: list[_ThreadNotingKeyboard] = []
    good_factory = _noting_factory(keyboards)
    attempts: list[int] = []

    def flaky_factory() -> Driver:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("first attempt fails")
        return good_factory()

    controller = RunController(driver_factory=flaky_factory)
    controller.start("Hello there.", Profile(), seed=1)
    assert controller.state == "failed"

    with qtbot.waitSignal(controller.finished, timeout=5000) as blocker:
        controller.start("Hello there.", Profile(), seed=1)

    assert blocker.args[0] == "finished"
    assert keyboards[0].text == "Hello there."
