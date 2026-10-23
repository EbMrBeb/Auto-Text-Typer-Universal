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

``PynputKeyboard`` types through one process-wide pynput ``Controller`` that
must be *built* on the main thread (see :func:`preload_keyboard_backend` for
the macOS reason) but can then be *used* from any thread, such as the worker
that runs a typing session.
"""

import logging
import threading
import time
from collections.abc import Mapping
from contextlib import AbstractContextManager
from enum import Enum, auto
from typing import Callable, Iterable, Protocol

from auto_essay_typer import platforms, targets
from auto_essay_typer.actions import Action, Backspace, Format, FormatOp, KeyPress, Pause
from auto_essay_typer.keymaps import Chord

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


_log = logging.getLogger(__name__)

#: The process-wide pynput ``Controller`` every ``PynputKeyboard`` shares unless
#: it is handed one explicitly. Written only by :func:`_shared_keyboard_controller`.
_shared_controller: _PynputController | None = None
_shared_controller_lock = threading.Lock()

_BUILD_OFF_MAIN_THREAD_MESSAGE = (
    "The keyboard backend has not been built, and macOS only allows it to be "
    "built on the main thread: pynput's Controller reads the keyboard layout "
    "through TSMGetInputSourceProperty, and once a GUI is running macOS kills "
    "the whole process if that is called from any other thread. Call "
    "auto_essay_typer.driver.preload_keyboard_backend() on the main thread "
    "during startup, before creating a window or starting worker threads."
)


def _build_controller() -> _PynputController:
    """Build a brand-new pynput ``Controller``.

    Raises ``RuntimeError`` instead of building one when that would be fatal:
    on macOS, off the main thread. A Python exception the user can see beats
    the process being killed by the OS with no message at all.
    """
    if platforms.current_os() is platforms.OperatingSystem.MACOS and (
        threading.current_thread() is not threading.main_thread()
    ):
        raise RuntimeError(_BUILD_OFF_MAIN_THREAD_MESSAGE)

    from pynput.keyboard import Controller

    return Controller()


def _shared_keyboard_controller() -> _PynputController:
    """The shared ``Controller``, built first if nothing has built it yet.

    Raises whatever :func:`_build_controller` raises: ``RuntimeError`` where
    building is forbidden, or pynput's own error where it is unusable.
    """
    global _shared_controller
    with _shared_controller_lock:
        if _shared_controller is None:
            _shared_controller = _build_controller()
        return _shared_controller


def preload_keyboard_backend() -> _PynputController | None:
    """Build the process-wide keyboard backend now, and return it.

    **Call this once, on the main thread, during application startup, before
    any window or worker thread exists.**

    Why it exists: on macOS, ``pynput.keyboard.Controller.__init__`` reads the
    current keyboard layout through ``TSMGetInputSourceProperty`` (via ctypes).
    Once a GUI event loop is running -- AppKit/HIToolbox is active, e.g. a
    visible Qt window -- that API asserts it is on the main dispatch queue, and
    when it is called from any other thread the OS does not raise anything
    Python could catch: it kills the whole process with ``SIGTRAP`` (exit
    status 133). Only *constructing* a ``Controller`` is main-thread-only.
    Typing with one that already exists (``type``, ``tap``, ``pressed``) just
    posts CoreGraphics events and is fine from any thread, which is why the
    worker thread that runs a typing session can use the controller built here.

    Idempotent: the first successful call builds the Controller and every later
    call returns that same object. Never raises: if pynput is missing or
    unusable (no pyobjc on macOS, no display on Linux, or this was called off
    the main thread on macOS) the failure is logged as a warning and ``None``
    is returned, so a broken backend cannot stop the application from starting.
    The failure is not remembered; a later ``PynputKeyboard()`` tries again and
    raises the real error where it can be shown to the user. Building the
    Controller does not need Accessibility permission -- without it the OS
    silently drops the keystrokes instead, which is what
    :func:`~auto_essay_typer.platforms.check_input_permission` reports.

    Two limits to know about:

    * pynput pins the character-to-keycode map to the keyboard layout current
      when the Controller is built, so a layout switched after startup is not
      picked up until the next launch.
    * It does nothing for pynput's own ``Listener`` / ``GlobalHotKeys``: in
      pynput 1.8.2 ``Listener._run()`` calls the same Text Services function
      (``keycode_context()``) on the listener's *own* thread, so starting one
      under a running GUI kills the process on macOS even when this was called
      first. A listener needs its own fix.
    """
    try:
        return _shared_keyboard_controller()
    except Exception as exc:  # noqa: BLE001 - app startup must survive a broken backend
        _log.warning("Keyboard backend unavailable (%s): %s", type(exc).__name__, exc)
        return None


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
    When ``controller`` is omitted, the process-wide Controller shared through
    :func:`preload_keyboard_backend` is used, and built first only if nothing
    has built it yet. Building is refused with a ``RuntimeError`` when the OS
    would kill the process for it (macOS, off the main thread). Either way
    ``pynput`` is imported lazily rather than at module scope, so importing
    ``driver.py`` never requires pynput to be installed.
    """

    def __init__(self, controller: _PynputController | None = None) -> None:
        if controller is None:
            controller = _shared_keyboard_controller()

        from pynput.keyboard import Key

        self._controller = controller
        self._named = {"\n": Key.enter, "\t": Key.tab}
        self._backspace = Key.backspace
        self._modifier_keys = {
            "cmd": Key.cmd,
            "alt": Key.alt,
            "shift": Key.shift,
            "ctrl": Key.ctrl,
        }
        self._chord_final_keys = {"tab": Key.tab, "enter": Key.enter, "space": Key.space}

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

        An empty chord (``keymaps.NO_OP``) presses nothing at all -- some
        keymaps deliberately map a FormatOp to it when the target app has no
        usable shortcut for that op (see ``keymaps.NO_OP``'s docstring), and
        ``Driver`` already treats that as "handled" rather than unsupported.

        Modifiers are held with one nested ``controller.pressed(...)``
        context manager per modifier -- rather than a single call given every
        key at once -- so that if tapping the final key raises, every
        modifier already held is still released while the exception unwinds
        back out through the nested ``with`` blocks. No manual try/finally is
        needed for that: it falls out of plain context-manager nesting.
        """
        if not keys:
            return
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
        keymap: Mapping[FormatOp, Chord] | None = None,
    ) -> None:
        self._keyboard = keyboard if keyboard is not None else PynputKeyboard()
        self._sleep = sleep
        self._countdown_seconds = countdown_seconds
        # Resolved fresh here (not a literal default evaluated once at import
        # time) so it reflects the OS this process is actually running on.
        # Goes through the `platforms`/`targets` *modules* rather than
        # importing `current_os`/`keymap_for` as bare names, so a test's
        # `monkeypatch.setattr(platforms, "current_os", ...)` is seen here too.
        self._keymap = (
            keymap
            if keymap is not None
            else targets.keymap_for("google-docs", platforms.current_os())
        )
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
