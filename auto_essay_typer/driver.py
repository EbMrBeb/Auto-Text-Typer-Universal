"""Executes an action stream against a keyboard.

The keyboard is injected behind :class:`Keyboard` so the driver can be tested
against a recording sink instead of typing into the developer's own terminal.
``PynputKeyboard`` is only imported lazily, so the rest of the package -- and
the whole test suite -- works without accessibility permission.
"""

import threading
import time
from enum import Enum, auto
from typing import Callable, Iterable, Protocol

from auto_essay_typer.actions import Action, Backspace, KeyPress, Pause

# Long pauses are slept in slices no longer than this, so that pause and abort
# take effect promptly instead of after a five-second paragraph break.
_SLICE_SECONDS = 0.05


class RunResult(Enum):
    COMPLETED = auto()
    ABORTED = auto()


class Keyboard(Protocol):
    def press_char(self, char: str) -> None: ...
    def backspace(self, count: int) -> None: ...


class RecordingKeyboard:
    """In-memory keyboard for tests and dry runs."""

    def __init__(self) -> None:
        self._buffer: list[str] = []

    @property
    def text(self) -> str:
        return "".join(self._buffer)

    def press_char(self, char: str) -> None:
        self._buffer.append(char)

    def backspace(self, count: int) -> None:
        if count > len(self._buffer):
            raise ValueError(f"backspace({count}) past start of {len(self._buffer)} chars")
        del self._buffer[len(self._buffer) - count :]


class PynputKeyboard:
    """Real OS-level keystroke injection.

    Newline and tab are sent as named keys rather than typed characters,
    because ``Controller.type`` does not reliably produce Return in every
    application.
    """

    def __init__(self) -> None:
        from pynput.keyboard import Controller, Key

        self._controller = Controller()
        self._named = {"\n": Key.enter, "\t": Key.tab}
        self._backspace = Key.backspace

    def press_char(self, char: str) -> None:
        named = self._named.get(char)
        if named is not None:
            self._controller.tap(named)
        else:
            self._controller.type(char)

    def backspace(self, count: int) -> None:
        for _ in range(count):
            self._controller.tap(self._backspace)


class Driver:
    """Runs an action stream, with countdown, pause/resume, and abort."""

    def __init__(
        self,
        keyboard: Keyboard | None = None,
        sleep: Callable[[float], None] = time.sleep,
        countdown_seconds: int = 5,
    ) -> None:
        self._keyboard = keyboard if keyboard is not None else PynputKeyboard()
        self._sleep = sleep
        self._countdown_seconds = countdown_seconds
        self._abort = threading.Event()
        self._resumed = threading.Event()
        self._resumed.set()

    # -- controls ---------------------------------------------------------

    def abort(self) -> None:
        """Stop as soon as possible. Partial text is left in place."""
        self._abort.set()
        self._resumed.set()

    def pause(self) -> None:
        self._resumed.clear()

    def resume(self) -> None:
        self._resumed.set()

    @property
    def is_paused(self) -> bool:
        return not self._resumed.is_set()

    # -- execution --------------------------------------------------------

    def _wait(self, seconds: float) -> bool:
        """Sleep in slices. Returns False if aborted during the wait."""
        remaining = seconds
        while remaining > 0:
            if self._abort.is_set():
                return False
            slice_seconds = min(_SLICE_SECONDS, remaining)
            self._sleep(slice_seconds)
            remaining -= slice_seconds
        return not self._abort.is_set()

    def _await_resume(self) -> bool:
        while not self._resumed.is_set():
            if self._abort.is_set():
                return False
            self._sleep(_SLICE_SECONDS)
        return not self._abort.is_set()

    def run(
        self,
        actions: Iterable[Action],
        on_progress: Callable[[int, int], None] | None = None,
        on_countdown: Callable[[int], None] | None = None,
    ) -> RunResult:
        """Execute ``actions``. Returns COMPLETED or ABORTED."""
        actions = list(actions)
        self._abort.clear()
        self._resumed.set()

        for remaining in range(self._countdown_seconds, 0, -1):
            if on_countdown is not None:
                on_countdown(remaining)
            if not self._wait(1.0):
                return RunResult.ABORTED

        total = sum(1 for a in actions if isinstance(a, KeyPress))
        done = 0

        for action in actions:
            if not self._await_resume():
                return RunResult.ABORTED
            match action:
                case Pause(ms=ms):
                    if not self._wait(ms / 1000.0):
                        return RunResult.ABORTED
                case KeyPress(char=char):
                    self._keyboard.press_char(char)
                    done += 1
                    if on_progress is not None:
                        on_progress(done, total)
                case Backspace(count=count):
                    self._keyboard.backspace(count)
            if self._abort.is_set():
                return RunResult.ABORTED

        return RunResult.COMPLETED
