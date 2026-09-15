"""Executes an action stream against a keyboard.

The keyboard is injected behind :class:`Keyboard` so the driver can be tested
against a recording sink instead of typing into the developer's own terminal.
``PynputKeyboard`` is only imported lazily, so the rest of the package -- and
the whole test suite -- works without accessibility permission.

``Format`` actions do not type text at all: they press a Google Docs
keyboard shortcut (:meth:`Keyboard.press_chord`), looked up in a ``keymap``
supplied to :class:`Driver`. ``Driver.run`` validates every ``Format`` op the
action stream uses against that keymap up front, before the countdown or any
keystroke, so an unreachable formatting command is reported once and clearly
rather than discovered mid-run by silently skipping it.
"""

import threading
import time
from collections.abc import Mapping
from contextlib import AbstractContextManager
from enum import Enum, auto
from typing import Callable, Iterable, Protocol

from auto_essay_typer.actions import Action, Backspace, Format, FormatOp, KeyPress, Pause
from auto_essay_typer.keymaps import GOOGLE_DOCS_MAC, Chord

# Long pauses are slept in slices no longer than this, so that pause and abort
# take effect promptly instead of after a five-second paragraph break.
_SLICE_SECONDS = 0.05


class RunResult(Enum):
    COMPLETED = auto()
    ABORTED = auto()


class UnsupportedFormatError(Exception):
    """Raised when an action stream uses a FormatOp absent from the driver's keymap.

    Raised eagerly -- ``Driver.run`` checks the whole stream against the
    keymap before the countdown starts -- rather than discovered mid-run by
    silently skipping the unreachable ``Format`` action, which would leave
    the typed document looking different from what was planned with no
    explanation why.
    """


class Keyboard(Protocol):
    def press_char(self, char: str) -> None: ...
    def backspace(self, count: int) -> None: ...
    def press_chord(self, keys: Chord) -> None: ...


class RecordingKeyboard:
    """In-memory keyboard for tests and dry runs."""

    def __init__(self) -> None:
        self._buffer: list[str] = []
        self.chords: list[Chord] = []

    @property
    def text(self) -> str:
        return "".join(self._buffer)

    def press_char(self, char: str) -> None:
        self._buffer.append(char)

    def backspace(self, count: int) -> None:
        if count > len(self._buffer):
            raise ValueError(f"backspace({count}) past start of {len(self._buffer)} chars")
        del self._buffer[len(self._buffer) - count :]

    def press_chord(self, keys: Chord) -> None:
        """Record the chord. Text is untouched: unlike a character, a shortcut
        has no predictable effect on the buffer, so tests assert on ``chords``."""
        self.chords.append(keys)


class _PynputController(Protocol):
    """The slice of ``pynput.keyboard.Controller``'s interface ``PynputKeyboard`` uses.

    Spelled out as a Protocol (like :class:`Keyboard` above) purely so
    ``PynputKeyboard.__init__`` can type its ``controller`` parameter
    precisely without importing ``pynput`` at module scope: a real
    ``pynput.keyboard.Controller`` satisfies this structurally, and so does
    the fake controller the test suite injects in its place.
    """

    def press(self, key: object) -> None: ...
    def release(self, key: object) -> None: ...
    def tap(self, key: object) -> None: ...
    def type(self, text: str) -> None: ...
    def pressed(self, *keys: object) -> AbstractContextManager[None]: ...


class PynputKeyboard:
    """Real OS-level keystroke injection.

    Newline and tab are sent as named keys rather than typed characters,
    because ``Controller.type`` does not reliably produce Return in every
    application.

    ``controller`` can be injected -- anything satisfying
    :class:`_PynputController`, which a real ``pynput.keyboard.Controller``
    does structurally -- so tests can exercise the chord-holding logic in
    :meth:`press_chord` without pynput touching the real OS (and, on macOS,
    without needing Accessibility permission just to run the test suite).
    Either way ``pynput`` is imported lazily right here rather than at module
    scope, so importing ``driver.py`` never requires pynput to be installed;
    when ``controller`` is omitted, a real ``Controller`` is constructed in
    that same lazy import.
    """

    def __init__(self, controller: _PynputController | None = None) -> None:
        from pynput.keyboard import Key

        if controller is None:
            from pynput.keyboard import Controller

            controller = Controller()
        self._controller = controller
        self._named = {"\n": Key.enter, "\t": Key.tab}
        self._backspace = Key.backspace
        self._modifier_keys = {
            "cmd": Key.cmd,
            "alt": Key.alt,
            "shift": Key.shift,
            "ctrl": Key.ctrl,
        }
        self._chord_final_keys = {"tab": Key.tab, "enter": Key.enter}

    def press_char(self, char: str) -> None:
        named = self._named.get(char)
        if named is not None:
            self._controller.tap(named)
        else:
            self._controller.type(char)

    def backspace(self, count: int) -> None:
        for _ in range(count):
            self._controller.tap(self._backspace)

    def press_chord(self, keys: Chord) -> None:
        """Press one keyboard shortcut: hold every modifier, tap the final key.

        Modifiers are held with one nested ``controller.pressed(...)``
        context manager per modifier -- rather than a single call given every
        key at once -- so that if tapping the final key raises, every
        modifier already held is still released while the exception unwinds
        back out through the nested ``with`` blocks. No manual try/finally is
        needed for that: it falls out of plain context-manager nesting.
        """
        *modifiers, final = keys
        self._hold_and_tap(list(modifiers), final)

    def _hold_and_tap(self, modifiers: list[str], final: str) -> None:
        if not modifiers:
            self._controller.tap(self._chord_final_keys.get(final, final))
            return
        with self._controller.pressed(self._modifier_keys[modifiers[0]]):
            self._hold_and_tap(modifiers[1:], final)


class Driver:
    """Runs an action stream, with countdown, pause/resume, and abort."""

    def __init__(
        self,
        keyboard: Keyboard | None = None,
        sleep: Callable[[float], None] = time.sleep,
        countdown_seconds: int = 5,
        keymap: Mapping[FormatOp, Chord] = GOOGLE_DOCS_MAC,
    ) -> None:
        self._keyboard = keyboard if keyboard is not None else PynputKeyboard()
        self._sleep = sleep
        self._countdown_seconds = countdown_seconds
        self._keymap = keymap
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

    def _check_keymap_covers(self, actions: list[Action]) -> None:
        """Raise UnsupportedFormatError naming every FormatOp the keymap can't reach.

        Checked once, up front, against the whole stream -- so a document
        that needs (say) a strikethrough run fails loudly before the
        countdown even starts, instead of typing most of the document and
        only then discovering the one shortcut it has no chord for.
        """
        used_ops = {action.op for action in actions if isinstance(action, Format)}
        missing = used_ops - self._keymap.keys()
        if missing:
            names = ", ".join(sorted(op.value for op in missing))
            raise UnsupportedFormatError(f"keymap has no chord for: {names}")

    def run(
        self,
        actions: Iterable[Action],
        on_progress: Callable[[int, int], None] | None = None,
        on_countdown: Callable[[int], None] | None = None,
    ) -> RunResult:
        """Execute ``actions``. Returns COMPLETED or ABORTED."""
        actions = list(actions)
        self._check_keymap_covers(actions)
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
                case Format(op=op):
                    self._keyboard.press_chord(self._keymap[op])
                case _:
                    raise TypeError(f"unknown action type: {type(action).__name__}")
            if self._abort.is_set():
                return RunResult.ABORTED

        return RunResult.COMPLETED
