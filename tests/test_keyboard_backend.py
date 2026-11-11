"""The shared pynput Controller: built on the main thread, used from anywhere.

Background (see ``driver.preload_keyboard_backend``): on macOS, *constructing* a
``pynput.keyboard.Controller`` calls ``TSMGetInputSourceProperty``, and once a GUI
event loop is running the OS kills the whole process (SIGTRAP, exit 133) if that
happens off the main thread. *Using* a Controller that already exists from a
worker thread is fine.

Nothing here builds a real Controller. Every test substitutes the ``fake_pynput``
fixture for ``pynput.keyboard.Controller`` and pins the OS where it matters, so a
regression shows up as a failed assertion instead of a dead test process.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from typing import Any

import pytest

from auto_essay_typer import driver as driver_module
from auto_essay_typer import platforms
from auto_essay_typer.actions import KeyPress
from auto_essay_typer.cli import main as cli_main
from auto_essay_typer.driver import Driver, PynputKeyboard, preload_keyboard_backend
from auto_essay_typer.platforms import OperatingSystem


class _RecordingController:
    """A hand-made controller to inject, distinct from the shared (fake) one."""

    def __init__(self) -> None:
        self.typed: list[str] = []

    def type(self, text: str) -> None:
        self.typed.append(text)

    def press(self, key: object) -> None: ...
    def release(self, key: object) -> None: ...
    def tap(self, key: object) -> None: ...
    def pressed(self, *keys: object): ...


def _run_on_worker(fn: Callable[[], Any]) -> dict[str, Any]:
    """Run ``fn`` on a fresh non-main thread; return ``{"value": ...}`` or ``{"error": ...}``."""
    outcome: dict[str, Any] = {}

    def target() -> None:
        try:
            outcome["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - reported to the test, never lost
            outcome["error"] = exc

    thread = threading.Thread(target=target)
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive(), "worker thread did not finish"
    return outcome


def _pin_os(monkeypatch, os_: OperatingSystem) -> None:
    monkeypatch.setattr(platforms, "current_os", lambda: os_)


# --- preload_keyboard_backend -----------------------------------------------------


def test_every_test_starts_with_an_empty_shared_controller_cache():
    # Guaranteed by the suite-wide autouse fixture in tests/conftest.py; the tests
    # below that populate the cache would otherwise leak it into each other.
    assert driver_module._shared_controller is None


def test_the_suite_cannot_build_a_real_controller_by_accident():
    # Without the fake_pynput fixture, building a keyboard must fail loudly rather
    # than touch the real OS (reading the macOS layout, or typing into a window).
    with pytest.raises(AssertionError, match="real pynput Controller"):
        PynputKeyboard()


def test_preload_in_a_test_without_the_fixture_reports_and_returns_none(caplog):
    # This is what the in-process `main(["--self-test"])` test runs into.
    with caplog.at_level(logging.WARNING, logger="auto_essay_typer.driver"):
        assert preload_keyboard_backend() is None
    assert "unavailable" in caplog.text


def test_preload_builds_one_controller_and_returns_the_same_object_every_time(fake_pynput):
    first = preload_keyboard_backend()
    second = preload_keyboard_backend()

    assert isinstance(first, fake_pynput)
    assert second is first
    assert len(fake_pynput.instances) == 1


def test_preload_builds_the_controller_on_the_calling_thread(fake_pynput):
    controller = preload_keyboard_backend()

    assert controller.built_on is threading.main_thread()


def test_preload_reports_and_returns_none_when_the_controller_cannot_be_built(monkeypatch, caplog):
    import pynput.keyboard

    class _Broken:
        def __init__(self) -> None:
            raise OSError("no display available")

    monkeypatch.setattr(pynput.keyboard, "Controller", _Broken)

    with caplog.at_level(logging.WARNING, logger="auto_essay_typer.driver"):
        assert preload_keyboard_backend() is None

    assert "no display available" in caplog.text
    assert "unavailable" in caplog.text


def test_preload_survives_pynput_not_being_installed(monkeypatch, caplog):
    # A None entry makes `from pynput.keyboard import Controller` raise ImportError,
    # exactly as on a machine where pynput cannot be imported at all.
    monkeypatch.setitem(sys.modules, "pynput.keyboard", None)

    with caplog.at_level(logging.WARNING, logger="auto_essay_typer.driver"):
        assert preload_keyboard_backend() is None

    assert "unavailable" in caplog.text


def test_a_failed_preload_is_not_remembered(monkeypatch):
    import pynput.keyboard

    attempts: list[int] = []

    class _FailsOnce:
        def __init__(self) -> None:
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError("display not ready yet")

    monkeypatch.setattr(pynput.keyboard, "Controller", _FailsOnce)

    assert preload_keyboard_backend() is None
    second = preload_keyboard_backend()

    assert isinstance(second, _FailsOnce)
    assert len(attempts) == 2


def test_preload_does_not_require_accessibility_permission(monkeypatch, fake_pynput):
    # Construction must never consult the permission probe: on macOS the Controller
    # builds without Accessibility and only the later keystrokes are dropped.
    def _boom(*_args, **_kwargs):
        raise AssertionError("preload must not check or request input permission")

    monkeypatch.setattr(platforms, "check_input_permission", _boom)
    monkeypatch.setattr(platforms, "request_input_permission_prompt", _boom)
    monkeypatch.setattr(platforms, "_ax_is_process_trusted", _boom)

    assert preload_keyboard_backend() is not None


# --- PynputKeyboard picks the controller ----------------------------------------


def test_pynput_keyboard_uses_the_preloaded_controller(fake_pynput):
    preloaded = preload_keyboard_backend()

    PynputKeyboard().press_char("a")

    assert preloaded.events == [("type", "a")]
    assert len(fake_pynput.instances) == 1


def test_pynput_keyboard_builds_and_caches_the_controller_on_first_use(fake_pynput):
    PynputKeyboard()
    PynputKeyboard()

    assert len(fake_pynput.instances) == 1
    assert preload_keyboard_backend() is fake_pynput.instances[0]


def test_an_injected_controller_is_used_and_builds_nothing(fake_pynput):
    injected = _RecordingController()

    PynputKeyboard(controller=injected).press_char("x")

    assert injected.typed == ["x"]
    assert fake_pynput.instances == []
    assert driver_module._shared_controller is None


def test_an_injected_controller_wins_over_the_preloaded_one(fake_pynput):
    preloaded = preload_keyboard_backend()
    injected = _RecordingController()

    PynputKeyboard(controller=injected).press_char("x")

    assert injected.typed == ["x"]
    assert preloaded.events == []


# --- the guard rail: a Python error instead of the OS killing the process --------


@pytest.mark.parametrize("build", [PynputKeyboard, Driver], ids=["PynputKeyboard", "Driver"])
def test_building_off_the_main_thread_on_macos_without_a_preload_raises_a_clear_error(
    monkeypatch, fake_pynput, build
):
    _pin_os(monkeypatch, OperatingSystem.MACOS)

    outcome = _run_on_worker(build)

    error = outcome["error"]
    assert isinstance(error, RuntimeError)
    assert "main thread" in str(error)
    assert "preload_keyboard_backend" in str(error)
    assert fake_pynput.instances == [], "the guard must refuse before touching pynput"
    assert driver_module._shared_controller is None


def test_off_the_main_thread_on_macos_a_preloaded_controller_is_used(monkeypatch, fake_pynput):
    _pin_os(monkeypatch, OperatingSystem.MACOS)
    preloaded = preload_keyboard_backend()  # on the main thread, as the app does at startup

    def build_and_type() -> None:
        PynputKeyboard().press_char("a")

    outcome = _run_on_worker(build_and_type)

    assert "error" not in outcome
    assert len(fake_pynput.instances) == 1
    assert preloaded.built_on is threading.main_thread()
    assert preloaded.events == [("type", "a")]
    assert preloaded.used_on, "the worker should have typed with it"
    assert all(thread is not threading.main_thread() for thread in preloaded.used_on)


@pytest.mark.parametrize("os_", [OperatingSystem.WINDOWS, OperatingSystem.LINUX])
def test_off_the_main_thread_outside_macos_building_is_allowed(monkeypatch, fake_pynput, os_):
    _pin_os(monkeypatch, os_)

    outcome = _run_on_worker(PynputKeyboard)

    assert "error" not in outcome
    assert len(fake_pynput.instances) == 1


def test_preload_called_off_the_main_thread_on_macos_reports_instead_of_raising(
    monkeypatch, fake_pynput, caplog
):
    _pin_os(monkeypatch, OperatingSystem.MACOS)

    with caplog.at_level(logging.WARNING, logger="auto_essay_typer.driver"):
        outcome = _run_on_worker(preload_keyboard_backend)

    assert outcome == {"value": None}
    assert fake_pynput.instances == []
    assert "main thread" in caplog.text


# --- paths that build a Driver without an explicit keyboard ---------------------


def test_a_driver_with_no_keyboard_types_through_the_preloaded_controller(fake_pynput):
    preloaded = preload_keyboard_backend()
    driver = Driver(countdown_seconds=0, sleep=lambda _seconds: None)

    driver.run([KeyPress("h"), KeyPress("i")])

    assert preloaded.text == "hi"
    assert len(fake_pynput.instances) == 1


def test_the_cli_still_types_through_the_default_keyboard_on_the_main_thread(
    tmp_path, fake_pynput
):
    essay = tmp_path / "essay.txt"
    essay.write_text("Hello there.")

    exit_code = cli_main(
        [str(essay), "--preset", "clean", "--seed", "1", "--countdown", "0"],
        sleep=lambda _seconds: None,
    )

    assert exit_code == 0
    (controller,) = fake_pynput.instances
    assert controller.built_on is threading.main_thread()
    assert controller.text == "Hello there."
