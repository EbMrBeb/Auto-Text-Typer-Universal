"""Tests for the headless menu-bar controller.

No ``rumps`` import anywhere in this file -- ``MenubarController`` is plain
Python plus a worker thread, so it is exercised the same way ``driver.py``
is: a ``RecordingKeyboard`` standing in for real keystrokes, and either a
no-op sleep (for tests that just want the run to finish) or a controllable
blocking sleep/keyboard (for tests that need to catch the controller
mid-run, deterministically -- never via a bare ``time.sleep`` race).
"""

from __future__ import annotations

import threading

from auto_essay_typer.driver import Driver, RecordingKeyboard
from auto_essay_typer.menubar_controller import MenubarController

_TEXT = "Hello there. This is a test."


def _controller(text: str = _TEXT, **ctor_kwargs) -> tuple[MenubarController, RecordingKeyboard]:
    """A controller wired to a RecordingKeyboard and a no-op, non-blocking sleep."""
    keyboard = RecordingKeyboard()
    driver_factory = lambda: Driver(keyboard=keyboard, sleep=lambda _: None, countdown_seconds=0)
    controller = MenubarController(
        read_clipboard=lambda: text, driver_factory=driver_factory, **ctor_kwargs
    )
    return controller, keyboard


class _BlockingKeyboard:
    """Wraps a RecordingKeyboard; blocks inside ``press_char`` the Nth time it's called.

    This lets a test pause the worker thread at a precise, deterministic
    point: by the time call N blocks, the (N-1)th character's
    ``on_progress`` callback has already fired (so the controller is
    verifiably in the "typing" state), but the Nth character's has not.
    Call ``release.set()`` to let the run continue.
    """

    def __init__(self, block_at_call: int = 2) -> None:
        self.inner = RecordingKeyboard()
        self.started = threading.Event()
        self.release = threading.Event()
        self._block_at_call = block_at_call
        self._count = 0

    @property
    def text(self) -> str:
        return self.inner.text

    def press_char(self, char: str) -> None:
        self.inner.press_char(char)
        self._count += 1
        if self._count == self._block_at_call:
            self.started.set()
            self.release.wait(timeout=5.0)

    def backspace(self, count: int) -> None:
        self.inner.backspace(count)


# -- idle / title ------------------------------------------------------------


def test_idle_title():
    controller, _ = _controller()
    assert controller.state == "idle"
    assert controller.title() == "⌨"


def test_title_strings_for_every_state():
    # Reaches into the private state directly: this test's job is to pin down
    # the exact title string for each state, and every state *reachable*
    # through the public API is already exercised by a transition test below.
    controller, _ = _controller()
    assert controller.title() == "⌨"

    with controller._lock:
        controller._state = "counting_down"
        controller._countdown_n = 3
    assert controller.title() == "⌨ 3…"

    with controller._lock:
        controller._state = "typing"
        controller._done, controller._total = 42, 100
    assert controller.title() == "⌨ 42%"

    with controller._lock:
        controller._state = "paused"
    assert controller.title() == "⌨ ‖"

    with controller._lock:
        controller._state = "done"
    assert controller.title() == "⌨ ✓"

    with controller._lock:
        controller._state = "aborted"
    assert controller.title() == "⌨ ✕"

    with controller._lock:
        controller._state = "error"
    assert controller.title() == "⌨ ✕"


# -- empty / whitespace clipboard --------------------------------------------


def test_empty_clipboard_goes_to_error_without_typing():
    controller, keyboard = _controller(text="")
    controller.start_from_clipboard()
    controller.wait(timeout=5.0)
    assert controller.state == "error"
    assert "empty" in controller.last_message.lower()
    assert keyboard.text == ""


def test_whitespace_only_clipboard_goes_to_error_without_typing():
    controller, keyboard = _controller(text="   \n\t  ")
    controller.start_from_clipboard()
    controller.wait(timeout=5.0)
    assert controller.state == "error"
    assert "empty" in controller.last_message.lower()
    assert keyboard.text == ""


# -- successful run -----------------------------------------------------------


def test_successful_run_types_the_clipboard_text_exactly():
    controller, keyboard = _controller(_TEXT)
    controller.start_from_clipboard()
    controller.wait(timeout=5.0)
    assert keyboard.text == _TEXT
    assert controller.state == "done"


def test_progress_reaches_n_n_on_completion():
    controller, _ = _controller(_TEXT)
    controller.start_from_clipboard()
    controller.wait(timeout=5.0)
    done, total = controller.progress
    assert done > 0
    assert done == total


def test_start_from_text_types_the_given_text_exactly():
    text = "Text supplied directly, e.g. from a chosen file."
    keyboard = RecordingKeyboard()
    driver_factory = lambda: Driver(keyboard=keyboard, sleep=lambda _: None, countdown_seconds=0)
    controller = MenubarController(read_clipboard=lambda: "", driver_factory=driver_factory)
    controller.start_from_text(text)
    controller.wait(timeout=5.0)
    assert keyboard.text == text
    assert controller.state == "done"


# -- state transitions, pause, and abort (deterministic via _BlockingKeyboard) ---


def test_state_transitions_idle_then_typing_then_done():
    keyboard = _BlockingKeyboard(block_at_call=2)
    driver_factory = lambda: Driver(keyboard=keyboard, sleep=lambda _: None, countdown_seconds=0)
    controller = MenubarController(read_clipboard=lambda: _TEXT, driver_factory=driver_factory)

    assert controller.state == "idle"
    controller.start_from_clipboard()

    assert keyboard.started.wait(timeout=5.0)
    assert controller.state == "typing"

    keyboard.release.set()
    controller.wait(timeout=5.0)
    assert controller.state == "done"


def test_toggle_pause_flips_to_paused_and_back():
    keyboard = _BlockingKeyboard(block_at_call=2)
    driver_factory = lambda: Driver(keyboard=keyboard, sleep=lambda _: None, countdown_seconds=0)
    controller = MenubarController(read_clipboard=lambda: _TEXT, driver_factory=driver_factory)

    controller.start_from_clipboard()
    assert keyboard.started.wait(timeout=5.0)
    assert controller.state == "typing"

    controller.toggle_pause()
    assert controller.state == "paused"

    controller.toggle_pause()
    assert controller.state == "typing"

    keyboard.release.set()
    controller.wait(timeout=5.0)
    assert controller.state == "done"


def test_abort_reaches_aborted():
    keyboard = _BlockingKeyboard(block_at_call=2)
    driver_factory = lambda: Driver(keyboard=keyboard, sleep=lambda _: None, countdown_seconds=0)
    controller = MenubarController(read_clipboard=lambda: _TEXT, driver_factory=driver_factory)

    controller.start_from_clipboard()
    assert keyboard.started.wait(timeout=5.0)
    assert controller.state == "typing"

    controller.abort()
    keyboard.release.set()
    controller.wait(timeout=5.0)
    assert controller.state == "aborted"


def test_abort_racing_a_naturally_finishing_run_does_not_raise():
    controller, _ = _controller(_TEXT)
    controller.start_from_clipboard()
    controller.wait(timeout=5.0)
    assert controller.state == "done"
    controller.abort()  # must not raise, even though nothing is running any more


def test_abort_with_nothing_running_does_not_raise():
    controller, _ = _controller(_TEXT)
    controller.abort()
    assert controller.state == "idle"


# -- no-op re-entrancy --------------------------------------------------------


def test_start_from_clipboard_while_running_is_a_noop():
    keyboard = _BlockingKeyboard(block_at_call=1)
    call_count = {"n": 0}

    def driver_factory():
        call_count["n"] += 1
        return Driver(keyboard=keyboard, sleep=lambda _: None, countdown_seconds=0)

    controller = MenubarController(read_clipboard=lambda: _TEXT, driver_factory=driver_factory)

    controller.start_from_clipboard()
    assert keyboard.started.wait(timeout=5.0)

    controller.start_from_clipboard()  # should be a no-op: no second thread, no second run
    controller.start_from_clipboard()

    keyboard.release.set()
    controller.wait(timeout=5.0)

    assert call_count["n"] == 1
    assert controller.state == "done"


# -- profile ------------------------------------------------------------------


def test_set_profile_unknown_name_surfaces_error_rather_than_raising():
    controller, _ = _controller(_TEXT)
    controller.set_profile("definitely-not-a-real-profile")
    assert controller.state == "error"
    assert "definitely-not-a-real-profile" in controller.last_message


def test_set_profile_known_name_is_used_for_the_next_run():
    controller, _ = _controller(_TEXT)
    controller.set_profile("sloppy")
    assert controller.profile_name == "sloppy"
    assert controller.state == "idle"  # unaffected by a valid switch
