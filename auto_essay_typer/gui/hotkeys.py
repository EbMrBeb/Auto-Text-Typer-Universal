"""Global hotkeys: start, pause/resume and stop a typing run from any application.

Two layers, so the part that touches the operating system can be swapped out:

* :class:`PynputHotkeyBackend` wraps ``pynput.keyboard.GlobalHotKeys``, which
  runs its own listener thread. It is the only code here that knows about
  pynput, and it starts the listener *defensively*: pynput's own
  ``listener.wait()`` blocks forever if the platform thread dies before it
  reports ready (for example no X display on Linux), so this backend polls
  with a bounded timeout instead and turns every failure into a
  :class:`HotkeyError` carrying a plain-language reason.
* :class:`HotkeyService` is the Qt-facing object the rest of the GUI uses. It
  starts lazily (nothing is created until :meth:`HotkeyService.apply` is
  called with ``enabled=True``), *builds* the backend and its listener on the
  GUI thread (see below), *starts* it on a short-lived worker thread so a slow
  or hanging start can never block the GUI thread, and delivers hotkey presses
  as ordinary Qt signals. The listener thread only ever *emits signals*; it
  never touches a widget.

Why the listener is built on the main thread. On macOS, once a GUI event loop is
running, the Text Services call that reads the keyboard layout
(``TISCopyCurrentKeyboardInputSource``) asserts that it is on the main dispatch
queue, and the OS answers a call from any other thread with ``SIGTRAP`` -- no
Python exception, the whole process just dies (exit status 133). pynput's
``Listener._run`` makes that call, via ``keycode_context()``, *on the listener's
own thread*, so a stock ``GlobalHotKeys`` started under a running GUI kills the
app even when it was built on the main thread. :func:`_macos_global_hotkeys_class`
therefore reads the layout once, in the constructor (which runs on the GUI
thread because the service builds the listener there), and the listener thread
reuses that value instead of asking the OS again. Everything else the listener
thread does -- the event tap, the run loop, decoding key events -- is safe off
the main thread. (The same limit is described at ``driver.preload_keyboard_backend``,
which handles the other half of the problem: the ``Controller`` that types.)

Failure is always soft. If the listener cannot start (no permission, Wayland,
no display, pynput missing, ...) the service logs it, moves to the
``"unavailable"`` state with a short reason in :attr:`HotkeyService.detail`,
and keeps the application running -- it never raises and never shows a
dialog. The UI decides how quietly or loudly to surface that status.

The ``stop`` hotkey is Escape by default (see
``app_settings.default_hotkeys``). Escape is a key the user presses for many
other reasons, so :attr:`HotkeyService.stopRequested` fires on *every* press
while the service is active: **the consumer must ignore it unless a typing
run is actually in progress.** pynput never suppresses the key (the
listener only observes), so the press still reaches the focused application.
"""

from __future__ import annotations

import atexit
import functools
import logging
import re
import threading
import time
import weakref
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from PySide6.QtCore import QCoreApplication, QObject, Qt, Signal

from auto_essay_typer import platforms
from auto_essay_typer.platforms import LinuxSession, OperatingSystem

logger = logging.getLogger(__name__)

#: States :attr:`HotkeyService.state` can report.
STATE_OFF = "off"
STATE_STARTING = "starting"
STATE_ACTIVE = "active"
STATE_UNAVAILABLE = "unavailable"
STATES = (STATE_OFF, STATE_STARTING, STATE_ACTIVE, STATE_UNAVAILABLE)

#: The hotkey actions the service understands, in the order they are checked.
ACTIONS = ("start", "pause", "stop")
_ACTION_LABELS = {"start": "Start", "pause": "Pause", "stop": "Stop"}

# How long the pynput backend waits for its listener thread to report ready,
# how long it then waits to see whether the thread bails out right after
# (macOS without permission: the event tap is refused, ``_mark_ready()`` is
# still called, and ``_run()`` returns immediately), how often it polls, and
# how long ``stop()`` waits for the thread to finish.
_READY_TIMEOUT_SECONDS = 3.0
_SETTLE_SECONDS = 0.25
_POLL_INTERVAL_SECONDS = 0.02
_JOIN_TIMEOUT_SECONDS = 1.0

_MACOS_DETAIL = "Global hotkeys need Accessibility and Input Monitoring access on macOS"
_WAYLAND_DETAIL = "This session blocks global hotkeys (Wayland)"
_NO_DISPLAY_DETAIL = "No X display for hotkeys"
_WAYLAND_NOTE = "Under Wayland, hotkeys may only work while an X11 window has focus"
_GENERIC_DETAIL = "Global hotkeys could not start"
_IMPORT_DETAIL = "Global hotkeys are not available here (the keyboard library could not be loaded)"


class HotkeyError(Exception):
    """A hotkey listener could not start. The message is fit to show a user as-is."""


class HotkeyBackend(Protocol):
    """What :class:`HotkeyService` needs from the thing that listens to the keyboard.

    The service builds a backend with its factory on the **GUI thread**, then
    calls :meth:`prepare` (if the backend has one) on that same thread, and only
    then hands the backend to a worker thread for :meth:`start`, which may block.
    ``prepare`` is where anything that must happen on the main thread belongs.
    """

    def start(self, bindings: Mapping[str, Callable[[], None]]) -> None:
        """Listen for ``{pynput hotkey text: callback}``; block until listening or raise :class:`HotkeyError`."""
        ...

    def stop(self) -> None:
        """Stop listening. Must be safe to call more than once."""
        ...


# --- the pynput backend --------------------------------------------------------


def _default_listener_factory(bindings: Mapping[str, Callable[[], None]]) -> Any:
    """Build (not start) a real ``pynput.keyboard.GlobalHotKeys`` listener.

    Imported lazily: ``pynput.keyboard`` can fail at import time on a headless
    Linux box, and nothing should pay for it until hotkeys are actually wanted.

    On macOS the listener is the :func:`_macos_global_hotkeys_class` variant
    that never asks the OS for the keyboard layout from its own thread. Call
    this on the main thread (``HotkeyService`` does): that is where the layout
    is read.
    """
    from pynput.keyboard import GlobalHotKeys

    if platforms.current_os() is OperatingSystem.MACOS:
        return _macos_global_hotkeys_class()(dict(bindings))
    return GlobalHotKeys(dict(bindings))


@functools.cache
def _macos_global_hotkeys_class() -> type:
    """``GlobalHotKeys`` for macOS, safe to start while a GUI event loop is running.

    pynput's darwin ``Listener._run`` is::

        with keycode_context() as context:   # TISCopyCurrentKeyboardInputSource ...
            self._context = context
            super()._run()                   # the event tap and its run loop

    and it runs on the listener's own thread. Under a running GUI that Text
    Services call is only allowed on the main thread, and anywhere else the OS
    kills the process with ``SIGTRAP`` (see the module docstring). The value it
    yields is a plain ``(keyboard type, layout bytes)`` tuple, so it can just as
    well be read once, on the main thread, and handed to the listener thread --
    which is what this subclass does: the constructor (main thread) reads it, and
    ``_run`` (listener thread) installs it and goes straight to the event tap
    without asking the OS again.

    The same layout is used for a second job, which has nothing to do with threads
    but decides whether the default hotkeys work at all. pynput names a pressed
    letter by the *character the key event carries*, and Option changes that
    character: with the default ``<cmd>+<alt>+<shift>+t`` held, the event says
    ``'ˇ'``, not ``'t'``, so the chord could never complete. :meth:`canonical` asks the
    layout which character the key makes with no modifiers held instead, so a chord
    means the same keys whatever is held with them. (Translating a key code with the
    layout bytes read here is plain arithmetic on those bytes; unlike reading the
    layout it is safe on the listener thread.)

    Built lazily because ``pynput._util.darwin`` imports the macOS frameworks.
    """
    from pynput._util.darwin import ListenerMixin, keycode_context, keycode_to_string
    from pynput.keyboard import GlobalHotKeys, KeyCode

    class MacOSGlobalHotKeys(GlobalHotKeys):
        def __init__(self, hotkeys: Mapping[str, Callable[[], None]], *args: Any, **kwargs: Any) -> None:
            super().__init__(hotkeys, *args, **kwargs)
            # Constructed on the GUI thread: this is the one place the OS is asked.
            with keycode_context() as context:
                self._main_thread_context = context

        def _run(self) -> None:
            self._context = self._main_thread_context
            try:
                ListenerMixin._run(self)  # what the stock _run wraps in keycode_context()
            finally:
                self._context = None

        def canonical(self, key: Any) -> Any:
            """A pressed letter, digit or symbol as the key it is on the keyboard, whatever modifiers are held.

            Called on the listener thread for every key press and release in every app, so it
            never raises (an exception there would end the listener for good): on any trouble
            it falls back to pynput's own normalisation.
            """
            vk = getattr(key, "vk", None)
            if isinstance(key, KeyCode) and vk is not None:
                try:
                    plain = self._unmodified_character(vk)
                except Exception:  # noqa: BLE001 - see the docstring
                    logger.debug("Could not translate key code %r through the layout", vk, exc_info=True)
                    plain = ""
                if plain:
                    return KeyCode.from_char(plain)
            return super().canonical(key)

        def _unmodified_character(self, vk: int) -> str:
            """The one printable character key code ``vk`` makes with no modifier held, or ``""``."""
            _keyboard_type, layout = self._main_thread_context
            if not layout:  # no layout data (nothing to translate with): leave the key as pynput names it
                return ""
            text = keycode_to_string(self._main_thread_context, vk)
            if len(text) == 1 and text.isprintable() and not text.isspace():
                return text.lower()
            return ""  # a dead key, a space, or nothing: not a character a hotkey can name

    return MacOSGlobalHotKeys


class PynputHotkeyBackend:
    """Global hotkeys through ``pynput.keyboard.GlobalHotKeys``.

    ``listener_factory`` takes the ``{hotkey text: callback}`` mapping and
    returns an un-started listener object (the default builds a real
    ``GlobalHotKeys``); tests inject a fake so no real listener is ever
    installed. The listener only needs ``start()``, ``stop()``, ``is_alive()``
    and, optionally, ``join(timeout)`` and pynput's private ``_ready`` flag.

    ``clock``/``sleep`` and the timing keywords exist so the polling logic can
    be tested with virtual time. ``os_provider``/``session_provider`` let a
    test choose which platform's failure advice is produced.
    """

    def __init__(
        self,
        listener_factory: Callable[[Mapping[str, Callable[[], None]]], Any] | None = None,
        *,
        ready_timeout: float = _READY_TIMEOUT_SECONDS,
        settle_seconds: float = _SETTLE_SECONDS,
        poll_interval: float = _POLL_INTERVAL_SECONDS,
        join_timeout: float = _JOIN_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        os_provider: Callable[[], OperatingSystem] | None = None,
        session_provider: Callable[[], LinuxSession] | None = None,
    ) -> None:
        self._listener_factory = listener_factory or _default_listener_factory
        self._ready_timeout = ready_timeout
        self._settle_seconds = settle_seconds
        self._poll_interval = poll_interval
        self._join_timeout = join_timeout
        self._clock = clock
        self._sleep = sleep
        # Resolved through the `platforms` module at call time so a test's
        # monkeypatch of `platforms.current_os` is honoured.
        self._os_provider = os_provider or (lambda: platforms.current_os())
        self._session_provider = session_provider or (lambda: platforms.linux_session())
        self._lock = threading.Lock()
        self._listener: Any = None
        #: A listener built by :meth:`prepare` and not yet started, with the bindings it was built for.
        self._prepared: tuple[dict[str, Callable[[], None]], Any] | None = None
        self._note = ""

    @property
    def note(self) -> str:
        """A caveat about a *successful* start (empty when there is none).

        :class:`HotkeyService` shows it as the detail of the ``"active"`` state.
        """
        return self._note

    # -- lifecycle ------------------------------------------------------------------

    def prepare(self, bindings: Mapping[str, Callable[[], None]]) -> None:
        """Build the listener for ``bindings`` on the *calling* thread, without starting it.

        ``HotkeyService`` calls this on the GUI thread, because building the
        listener is what reads the keyboard layout on macOS, and macOS only allows
        that on the main thread (see the module docstring). It never blocks: the
        blocking part, :meth:`start`, then runs on a worker thread and uses the
        listener built here. Raises :class:`HotkeyError` for hotkeys pynput
        rejects or a keyboard library that cannot load.
        """
        if not bindings:
            raise HotkeyError("There are no hotkeys to listen for")
        listener = self._create_listener(bindings)
        with self._lock:
            self._prepared = (dict(bindings), listener)

    def start(self, bindings: Mapping[str, Callable[[], None]]) -> None:
        """Start listening; returns once the listener is up, or raises :class:`HotkeyError`.

        Blocks for up to ``ready_timeout`` plus ``settle_seconds``, so call it
        from a worker thread (``HotkeyService`` does). Calling it again first
        stops the previous listener. Uses the listener :meth:`prepare` built for
        these same ``bindings``; without one (or for different bindings) it
        builds the listener itself, on whichever thread it was called from.
        """
        if not bindings:
            raise HotkeyError("There are no hotkeys to listen for")
        with self._lock:
            prepared, self._prepared = self._prepared, None
        self.stop()
        self._note = ""

        if prepared is not None and prepared[0] == dict(bindings):
            listener = prepared[1]
        else:
            if self._os_provider() is OperatingSystem.MACOS and threading.current_thread() is not threading.main_thread():
                # Building the listener here would read the keyboard layout off the main thread, and
                # macOS answers that by killing the process. Refuse instead of taking the app down.
                raise HotkeyError(f"{_GENERIC_DETAIL} (the listener was not built on the main thread)")
            listener = self._create_listener(bindings)
        try:
            # pynput's listeners already are daemon threads; insist on it so a
            # listener can never keep the interpreter alive at exit.
            listener.daemon = True
        except Exception:  # noqa: BLE001 - a listener type that forbids it
            logger.debug("Could not mark the hotkey listener as a daemon thread", exc_info=True)
        try:
            listener.start()
        except Exception as exc:  # noqa: BLE001 - e.g. RuntimeError: can't start new thread
            raise HotkeyError(self._unavailable_detail()) from exc

        try:
            self._await_listening(listener)
        except BaseException:
            self._discard(listener)
            raise

        with self._lock:
            self._listener = listener
        self._note = self._running_note()

    def stop(self) -> None:
        """Stop listening. Idempotent; never raises.

        A listener that :meth:`prepare` built but nothing started is simply
        dropped: it has no thread to stop or join.
        """
        with self._lock:
            listener, self._listener = self._listener, None
            self._prepared = None
        if listener is not None:
            self._discard(listener)

    # -- internals --------------------------------------------------------------------

    def _create_listener(self, bindings: Mapping[str, Callable[[], None]]) -> Any:
        try:
            return self._listener_factory(dict(bindings))
        except HotkeyError:
            raise
        except ValueError as exc:  # pynput: an invalid hotkey description
            raise HotkeyError(f"A hotkey is not a valid shortcut ({exc})") from exc
        except ModuleNotFoundError as exc:
            if (exc.name or "").split(".")[0] == "pynput":
                raise HotkeyError("The pynput keyboard library is not installed") from exc
            raise HotkeyError(_IMPORT_DETAIL) from exc
        except Exception as exc:  # noqa: BLE001 - ImportError, Xlib's DisplayNameError at import, ...
            logger.warning("Could not create the hotkey listener", exc_info=True)
            if self._os_provider() is OperatingSystem.MACOS:
                # Permission problems show up when the event tap is created, after the listener has
                # been built. A listener that cannot even be built is something else, and saying
                # "needs Accessibility access" would send the user off to fix the wrong thing.
                raise HotkeyError(f"{_GENERIC_DETAIL} ({type(exc).__name__}: {exc})") from exc
            raise HotkeyError(self._unavailable_detail()) from exc

    def _await_listening(self, listener: Any) -> None:
        """Poll until ``listener`` is ready and still alive, or raise.

        Never calls ``listener.wait()``: pynput blocks there forever if the
        platform thread dies before reporting ready.
        """
        deadline = self._clock() + self._ready_timeout
        while True:
            alive = listener.is_alive()
            # Read AFTER `alive`: once the thread is dead the flag is final.
            ready = self._is_ready(listener)
            if not alive:
                # Dead before it ever reported ready: it never got going (no
                # display, ...). Dead right after: the OS refused it.
                raise HotkeyError(self._unavailable_detail())
            if ready:
                break
            if self._clock() >= deadline:
                raise HotkeyError("Global hotkeys did not start in time")
            self._sleep(self._poll_interval)

        # Ready -- but on macOS without permission the thread ends almost
        # immediately afterwards. Give it a moment to do so before trusting it.
        self._sleep(self._settle_seconds)
        if not listener.is_alive():
            raise HotkeyError(self._unavailable_detail())

    @staticmethod
    def _is_ready(listener: Any) -> bool:
        flag = getattr(listener, "_ready", None)
        if flag is None:  # a listener type without pynput's private flag
            return bool(getattr(listener, "running", True))
        return bool(flag)

    def _discard(self, listener: Any) -> None:
        try:
            listener.stop()
        except Exception:  # noqa: BLE001
            logger.debug("Stopping the hotkey listener failed", exc_info=True)
        join = getattr(listener, "join", None)
        if callable(join) and threading.current_thread() is not listener:
            try:
                join(self._join_timeout)
            except Exception:  # noqa: BLE001 - pynput's join re-raises listener exceptions
                logger.debug("Joining the hotkey listener failed", exc_info=True)

    def _unavailable_detail(self) -> str:
        os_ = self._os_provider()
        if os_ is OperatingSystem.MACOS:
            return _MACOS_DETAIL
        if os_ is OperatingSystem.LINUX:
            session = self._session_provider()
            if session is LinuxSession.WAYLAND:
                return _WAYLAND_DETAIL
            if session is LinuxSession.NONE:
                return _NO_DISPLAY_DETAIL
            return "Global hotkeys could not start (the X display refused the connection)"
        return _GENERIC_DETAIL

    def _running_note(self) -> str:
        if self._os_provider() is OperatingSystem.LINUX:
            if self._session_provider() is LinuxSession.WAYLAND:
                return _WAYLAND_NOTE
        return ""


# --- helpers --------------------------------------------------------------------


def _stop_backend(backend: HotkeyBackend) -> None:
    """Stop ``backend``, tolerating one whose ``stop()`` raises."""
    try:
        backend.stop()
    except Exception:  # noqa: BLE001
        logger.warning("Stopping the global hotkey backend failed", exc_info=True)


#: Spellings of a modifier that name one particular key: ``<ctrl_r>``, ``<alt_gr>``. A listener reports
#: either Ctrl key as plain ``<ctrl>``, so a hotkey written with the side-specific name never matches
#: (or, on some platforms, is just another spelling of ``<ctrl>``). Either way the plain name is right.
_SIDE_SPECIFIC_MODIFIER = re.compile(r"<(?:ctrl|alt|shift|cmd)_[lr]>|<alt_gr>", re.IGNORECASE)

#: Keys that are safe to use as a hotkey on their own, because nobody types with them:
#: function keys, Escape (the default Stop), and the keys that never produce text.
_STANDALONE_KEY_NAMES = (
    "esc",
    "pause",
    "scroll_lock",
    "print_screen",
    *(f"f{number}" for number in range(1, 21)),
    "media_play_pause",
    "media_volume_mute",
    "media_volume_down",
    "media_volume_up",
    "media_previous",
    "media_next",
    "media_eject",
)


def _hotkey_signature(text: str) -> frozenset[str]:
    """An order-independent, case-insensitive token set for a pynput hotkey text.

    ``"<cmd>+<alt>+t"`` and ``"<ALT>+<cmd>+T"`` press the same keys, so they
    share a signature. (The same normalisation ``app_settings`` uses.) Only a stand-in for
    :func:`_parse_hotkey` when pynput cannot be loaded.
    """
    return frozenset(part.strip().strip("<>").lower() for part in text.split("+") if part.strip())


def _parse_hotkey(text: str) -> list[Any] | None:
    """The keys of ``text`` as pynput reads them (``ValueError`` if it cannot), or ``None`` without pynput.

    ``None`` means the keyboard library cannot be loaded here -- the backend reports that
    when hotkeys are started -- so there is nothing to check a hotkey against.
    """
    try:
        from pynput.keyboard import HotKey
    except Exception:  # noqa: BLE001 - ImportError, or an X display error at import on Linux
        return None
    return HotKey.parse(text)


def hotkey_problem(text: str) -> str | None:
    """Why ``text`` -- which pynput can parse -- would still be a bad global hotkey, or ``None`` if it is fine.

    A global hotkey sees every key press in every app, so one that is made of keys people
    type with would fire constantly:

    * a letter, digit or other typing key on its own (``t``), or with only Shift
      (``<shift>+t`` is how a capital T is typed);
    * nothing but modifiers (``<ctrl>+<alt>``).

    Function keys, Escape and the media keys are fine alone. A modifier spelled for one side
    (``<ctrl_r>``) never matches what the listener reports; ``<ctrl>`` covers both.
    The message is written for the person who typed the hotkey.
    """
    side_specific = _SIDE_SPECIFIC_MODIFIER.search(text)
    if side_specific:
        name = side_specific.group(0).lower()
        plain = name.strip("<>").split("_")[0]
        return f"Write <{plain}> instead of {name}. <{plain}> covers both {plain} keys, and {name} may never trigger."

    keys = _parse_hotkey(text)
    if keys is None:
        return None
    from pynput.keyboard import Key

    modifiers = [key for key in keys if isinstance(key, Key)]
    typing_keys = [key for key in keys if not isinstance(key, Key) and not _is_standalone_key(key)]
    if all(isinstance(key, Key) for key in keys):
        return "Add a letter, a digit or a key such as <f9> to go with the modifier keys."
    if typing_keys and not any(key in (Key.ctrl, Key.alt, Key.cmd) for key in modifiers):
        if modifiers:  # only Shift
            return (
                "Shift on its own is not enough: add <ctrl>, <alt> or <cmd> as well, "
                "or it would trigger whenever you type a capital letter."
            )
        return (
            "Add a modifier key such as <ctrl>, <alt> or <cmd>: a hotkey of just a typing key "
            "would trigger every time you press it, in any app."
        )
    return None


def _is_standalone_key(key: Any) -> bool:
    """Whether ``key`` (a parsed ``KeyCode``) is one that is safe to use as a hotkey with no modifier."""
    from pynput.keyboard import Key

    vk = getattr(key, "vk", None)
    if getattr(key, "char", None) is not None or vk is None:
        return False
    return any(
        name in Key.__members__ and Key[name].value.vk == vk for name in _STANDALONE_KEY_NAMES
    )


def validated_hotkeys(hotkeys: Mapping[str, str]) -> dict[str, str]:
    """``{action: hotkey text}`` for every action in :data:`ACTIONS`, or ``ValueError``.

    Raises with a plain-language message when an action has no hotkey, the text is empty or
    not a hotkey pynput can read, a hotkey would trigger on ordinary typing (see
    :func:`hotkey_problem`), or two actions would clash: the same keys, or one hotkey's keys
    being all of another's plus more (pressing the longer one would also trigger the shorter).
    """
    result: dict[str, str] = {}
    for action in ACTIONS:
        label = _ACTION_LABELS[action]
        text = hotkeys.get(action)
        if text is None:
            raise ValueError(f"No hotkey is set for {label}")
        text = str(text).strip()
        if not text:
            raise ValueError(f"The {label} hotkey is empty")
        result[action] = text

    keysets: dict[str, frozenset[Any]] = {}
    for action, text in result.items():
        label = _ACTION_LABELS[action]
        try:
            parsed = _parse_hotkey(text)
        except ValueError as exc:
            raise ValueError(f"The {label} hotkey ({text}) is not a valid shortcut") from exc
        problem = hotkey_problem(text)
        if problem is not None:
            raise ValueError(f"The {label} hotkey ({text}): {problem}")
        keysets[action] = frozenset(parsed) if parsed is not None else _hotkey_signature(text)

    actions = list(result)
    for index, first in enumerate(actions):
        for second in actions[index + 1 :]:
            if keysets[first] == keysets[second]:
                raise ValueError(
                    f"{_ACTION_LABELS[first]} and {_ACTION_LABELS[second]} use the same hotkey ({result[second]})"
                )
            for shorter, longer in ((first, second), (second, first)):
                if keysets[shorter] < keysets[longer]:
                    raise ValueError(
                        f"The {_ACTION_LABELS[longer]} hotkey ({result[longer]}) contains every key of the "
                        f"{_ACTION_LABELS[shorter]} hotkey ({result[shorter]}), so it would trigger both"
                    )
    return result


# Services still alive at interpreter exit get their backend stopped by an
# atexit hook, as a last line of defence for a process that ends without
# QApplication.aboutToQuit having run (a crash handler, os._exit paths aside).
_LIVE_SERVICES: weakref.WeakSet[HotkeyService] = weakref.WeakSet()
_atexit_registered = False


def _ensure_atexit_hook() -> None:
    global _atexit_registered
    if _atexit_registered:
        return
    _atexit_registered = True
    atexit.register(_stop_all_at_exit)


def _stop_all_at_exit() -> None:
    """Stop every live service's backend. Touches no Qt object: Qt may already be gone."""
    for service in list(_LIVE_SERVICES):
        try:
            service._shutdown(forget_config=True)
        except Exception:  # noqa: BLE001 - at exit there is nobody to tell
            pass


# --- the service ----------------------------------------------------------------


class HotkeyService(QObject):
    """Starts and stops global hotkeys and delivers their presses as Qt signals.

    Signals
    -------
    startRequested(), pauseRequested(), stopRequested()
        The matching hotkey was pressed. Always emitted on the GUI thread (they
        are queued from the listener thread). ``stopRequested`` fires on every
        press of the stop hotkey (Escape by default), so the consumer must
        ignore it when no run is in progress.
    statusChanged(str, str)
        ``(state, detail)`` whenever either changes. ``state`` is one of
        :data:`STATES`; ``detail`` is a short plain-language reason when the
        state is ``"unavailable"`` (and an optional caveat when ``"active"``).

    The service is lazy: building one starts nothing. Call :meth:`apply` with
    the settings' ``global_hotkeys_enabled`` and ``hotkeys``; call it again
    whenever either changes. Call :meth:`stop` when finished -- it is also run
    automatically from ``QCoreApplication.aboutToQuit`` and, as a fallback,
    at interpreter exit.

    ``backend_factory`` builds a :class:`HotkeyBackend`. It is called on the
    **GUI thread**, followed by the backend's ``prepare(bindings)`` when it has
    one, because building a pynput listener reads the keyboard layout and macOS
    kills the process if that happens anywhere else (see the module docstring).
    Both are quick and never block; only the backend's ``start`` -- which waits
    for the listener to come up -- runs on a worker thread. ``apply`` called from
    another thread hands the work over to the GUI thread rather than building
    the listener where it was called.

    Connect the request signals to methods of objects that live in the GUI
    thread (or with a queued connection): a plain function or lambda connected
    the default way is called directly on the listener's thread.
    """

    startRequested = Signal()
    pauseRequested = Signal()
    stopRequested = Signal()
    statusChanged = Signal(str, str)
    # (generation, ok, detail) from the worker thread back to the GUI thread.
    _startFinished = Signal(int, bool, str)
    # (enabled, hotkeys) from any thread to the GUI thread, which does the building.
    _applyRequested = Signal(bool, object)

    def __init__(
        self,
        backend_factory: Callable[[], HotkeyBackend] = PynputHotkeyBackend,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._backend_factory = backend_factory
        # Guards _generation and _backend, which the worker thread, the GUI
        # thread and the atexit hook all touch.
        self._lock = threading.Lock()
        self._generation = 0
        self._backend: HotkeyBackend | None = None
        self._config: tuple[bool, tuple[tuple[str, str], ...]] | None = None
        self._requested: tuple[bool, dict[str, str]] | None = None
        self._state = STATE_OFF
        self._detail = ""
        self._quit_hooked = False
        self._signals = {
            "start": self.startRequested,
            "pause": self.pauseRequested,
            "stop": self.stopRequested,
        }
        self._startFinished.connect(self._on_start_finished, Qt.ConnectionType.QueuedConnection)
        self._applyRequested.connect(self._apply_on_gui_thread, Qt.ConnectionType.QueuedConnection)
        _LIVE_SERVICES.add(self)

    # -- read-only status -----------------------------------------------------------

    @property
    def state(self) -> str:
        """One of :data:`STATES`."""
        return self._state

    @property
    def detail(self) -> str:
        """The reason for ``"unavailable"`` (or a caveat for ``"active"``); otherwise empty."""
        return self._detail

    # -- control ---------------------------------------------------------------------

    def apply(self, enabled: bool, hotkeys: Mapping[str, str]) -> None:
        """Make the service match ``enabled``/``hotkeys`` (``AppSettings.global_hotkeys_enabled``/``.hotkeys``).

        Idempotent: the same configuration already starting or active is left
        alone, and a configuration that already failed is *not* retried
        automatically (toggle it off and on, or call :meth:`retry`) so an
        unrelated settings change never re-attempts a start the OS refused. A
        changed configuration stops the old listener and starts a new one.
        ``enabled=False`` stops everything. Never raises.

        The backend (and, on macOS, the listener inside it) is built here, on
        the GUI thread; called from any other thread this queues itself onto
        the GUI thread instead of building where it was called.
        """
        if threading.current_thread() is not threading.main_thread():
            self._applyRequested.emit(bool(enabled), dict(hotkeys))
            return
        self._apply_on_gui_thread(bool(enabled), dict(hotkeys))

    def _apply_on_gui_thread(self, enabled: bool, hotkeys: Mapping[str, str]) -> None:
        self._hook_shutdown()
        hotkeys = dict(hotkeys)
        self._requested = (enabled, hotkeys)

        if not enabled:
            self._shutdown(forget_config=True)
            self._set_status(STATE_OFF, "")
            return

        config = (True, tuple(sorted((str(k), str(v)) for k, v in hotkeys.items())))
        if config == self._config and self._state in (STATE_STARTING, STATE_ACTIVE, STATE_UNAVAILABLE):
            return

        self._shutdown(forget_config=True)
        self._config = config
        try:
            by_action = validated_hotkeys(hotkeys)
        except ValueError as exc:
            logger.warning("Global hotkeys not started: %s", exc)
            self._set_status(STATE_UNAVAILABLE, str(exc))
            return

        generation = self._generation
        bindings = {
            text: functools.partial(self._on_hotkey, generation, action)
            for action, text in by_action.items()
        }
        self._set_status(STATE_STARTING, "")

        # Built on this (the GUI) thread -- see the module docstring for why.
        backend: HotkeyBackend | None = None
        try:
            backend = self._backend_factory()
            prepare = getattr(backend, "prepare", None)
            if callable(prepare):
                prepare(bindings)
        except Exception as exc:  # noqa: BLE001 - anything at all must degrade, not crash
            if backend is not None:
                _stop_backend(backend)
            self._set_status(STATE_UNAVAILABLE, self._failure_detail(exc))
            return

        try:
            self._spawn_start_worker(generation, backend, bindings)
        except Exception as exc:  # noqa: BLE001 - e.g. RuntimeError: can't start new thread
            _stop_backend(backend)
            self._set_status(STATE_UNAVAILABLE, self._failure_detail(exc))

    def _spawn_start_worker(
        self,
        generation: int,
        backend: HotkeyBackend,
        bindings: Mapping[str, Callable[[], None]],
    ) -> None:
        """Run :meth:`_start_worker` on a short-lived thread (its own method so a test can make it fail)."""
        threading.Thread(
            target=self._start_worker,
            args=(generation, backend, bindings),
            name="auto-text-typer-hotkeys-start",
            daemon=True,
        ).start()

    def retry(self) -> None:
        """Try again with the last configuration given to :meth:`apply` (a no-op if hotkeys are off)."""
        if self._requested is None:
            return
        enabled, hotkeys = self._requested
        self._config = None  # forget the remembered failure so apply() starts afresh
        self.apply(enabled, hotkeys)

    def stop(self) -> None:
        """Stop listening and move to ``"off"``. Synchronous, repeatable, never raises."""
        self._shutdown(forget_config=True)
        self._set_status(STATE_OFF, "")

    # -- internals: GUI thread ----------------------------------------------------------

    def _hook_shutdown(self) -> None:
        """On first use, make sure the listener is stopped when the app quits."""
        if self._quit_hooked:
            return
        self._quit_hooked = True
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.stop)
        _ensure_atexit_hook()

    def _shutdown(self, *, forget_config: bool) -> None:
        """Invalidate any in-flight start and stop the running backend.

        Bumping the generation under the lock is what makes a start that is
        still in progress harmless: when it finishes it finds a newer
        generation and stops its own backend (see :meth:`_start_worker`). It
        touches no Qt object, so it is safe from the atexit hook.
        """
        with self._lock:
            self._generation += 1
            backend, self._backend = self._backend, None
        if forget_config:
            self._config = None
        if backend is not None:
            _stop_backend(backend)

    def _set_status(self, state: str, detail: str) -> None:
        if state == self._state and detail == self._detail:
            return
        self._state = state
        self._detail = detail
        try:
            self.statusChanged.emit(state, detail)
        except RuntimeError:  # the C++ object is already gone (shutdown)
            pass

    def _on_start_finished(self, generation: int, ok: bool, detail: str) -> None:
        if generation != self._generation:
            return  # superseded by stop() or a newer apply(): already cleaned up
        if ok:
            self._set_status(STATE_ACTIVE, detail)
        else:
            self._set_status(STATE_UNAVAILABLE, detail)

    # -- internals: worker and listener threads -----------------------------------------

    @staticmethod
    def _failure_detail(exc: BaseException) -> str:
        """The plain-language reason for ``exc``, logged on the way. Any thread."""
        if isinstance(exc, HotkeyError):
            detail = str(exc) or _GENERIC_DETAIL
            # The user-facing reason is plain language; what actually went wrong, if there is more
            # to it, is the exception this one was raised from, so keep it in the log.
            logger.warning("Global hotkeys unavailable: %s", detail, exc_info=exc if exc.__cause__ else None)
        elif isinstance(exc, ImportError):
            detail = _IMPORT_DETAIL
            logger.warning("Global hotkeys unavailable: %s (%s)", detail, exc)
        else:
            detail = f"{_GENERIC_DETAIL}: {exc}" if str(exc) else _GENERIC_DETAIL
            logger.error("Global hotkeys failed to start", exc_info=exc)
        return detail

    def _start_worker(
        self,
        generation: int,
        backend: HotkeyBackend,
        bindings: Mapping[str, Callable[[], None]],
    ) -> None:
        """Start the (already built) ``backend`` off the GUI thread, then report back.

        The backend is *published* to ``self._backend`` under the lock only if
        this start's generation is still current. If ``stop()`` (or a newer
        ``apply()``) got there first, this thread stops its own backend
        instead -- so no listener can outlive a stop, whichever finishes first.
        """
        with self._lock:
            superseded = generation != self._generation
        if superseded:  # stopped or re-applied before this thread got going: nothing to start
            _stop_backend(backend)
            return

        detail = ""
        ok = False
        try:
            backend.start(bindings)
            ok = True
            detail = str(getattr(backend, "note", "") or "")
        except Exception as exc:  # noqa: BLE001 - anything at all must degrade, not crash
            detail = self._failure_detail(exc)

        if ok:
            with self._lock:
                current = generation == self._generation
                if current:
                    self._backend = backend
            if not current:
                _stop_backend(backend)
        else:
            _stop_backend(backend)

        try:
            self._startFinished.emit(generation, ok, detail)
        except RuntimeError:  # the service was destroyed while starting
            pass

    def _on_hotkey(self, generation: int, action: str) -> None:
        """Called by the listener thread: turn a hotkey press into a Qt signal. Never raises.

        A callback that raises would make pynput stop the whole listener, and
        this thread must never touch a widget, so all it does is emit.
        """
        if generation != self._generation:
            return  # a stale listener still draining events after stop()/re-apply
        try:
            self._signals[action].emit()
        except Exception:  # noqa: BLE001 - e.g. RuntimeError once the C++ object is gone
            logger.debug("Dropping hotkey %r: the service is gone", action, exc_info=True)
