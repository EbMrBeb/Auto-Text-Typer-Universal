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
  called with ``enabled=True``), builds and starts the backend on a
  short-lived worker thread so a slow or hanging start can never block the GUI
  thread, and delivers hotkey presses as ordinary Qt signals. The listener
  thread only ever *emits signals*; it never touches a widget.

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
    """What :class:`HotkeyService` needs from the thing that listens to the keyboard."""

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
    """
    from pynput.keyboard import GlobalHotKeys

    return GlobalHotKeys(dict(bindings))


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
        self._note = ""

    @property
    def note(self) -> str:
        """A caveat about a *successful* start (empty when there is none).

        :class:`HotkeyService` shows it as the detail of the ``"active"`` state.
        """
        return self._note

    # -- lifecycle ------------------------------------------------------------------

    def start(self, bindings: Mapping[str, Callable[[], None]]) -> None:
        """Start listening; returns once the listener is up, or raises :class:`HotkeyError`.

        Blocks for up to ``ready_timeout`` plus ``settle_seconds``, so call it
        from a worker thread (``HotkeyService`` does). Calling it again first
        stops the previous listener.
        """
        if not bindings:
            raise HotkeyError("There are no hotkeys to listen for")
        self.stop()
        self._note = ""

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
        """Stop listening. Idempotent; never raises."""
        with self._lock:
            listener, self._listener = self._listener, None
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
            logger.debug("Could not create the hotkey listener", exc_info=True)
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


def _hotkey_signature(text: str) -> frozenset[str]:
    """An order-independent, case-insensitive token set for a pynput hotkey text.

    ``"<cmd>+<alt>+t"`` and ``"<ALT>+<cmd>+T"`` press the same keys, so they
    share a signature. (The same normalisation ``app_settings`` uses.)
    """
    return frozenset(part.strip().strip("<>").lower() for part in text.split("+") if part.strip())


def validated_hotkeys(hotkeys: Mapping[str, str]) -> dict[str, str]:
    """``{action: hotkey text}`` for every action in :data:`ACTIONS`, or ``ValueError``.

    Raises with a plain-language message when an action has no hotkey, the
    text is empty, or two actions would share one hotkey (only one of them
    could ever fire).
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

    seen: dict[frozenset[str], str] = {}
    for action, text in result.items():
        signature = _hotkey_signature(text)
        if signature in seen:
            raise ValueError(
                f"{_ACTION_LABELS[seen[signature]]} and {_ACTION_LABELS[action]} use the same hotkey ({text})"
            )
        seen[signature] = action
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

    ``backend_factory`` builds a :class:`HotkeyBackend`; it is called on the
    worker thread, so even constructing a backend never blocks the GUI.
    """

    startRequested = Signal()
    pauseRequested = Signal()
    stopRequested = Signal()
    statusChanged = Signal(str, str)
    # (generation, ok, detail) from the worker thread back to the GUI thread.
    _startFinished = Signal(int, bool, str)

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
        """
        self._hook_shutdown()
        hotkeys = dict(hotkeys)
        self._requested = (bool(enabled), hotkeys)

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
        threading.Thread(
            target=self._start_worker,
            args=(generation, bindings),
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

    def _start_worker(self, generation: int, bindings: Mapping[str, Callable[[], None]]) -> None:
        """Build and start the backend off the GUI thread, then report back.

        The backend is *published* to ``self._backend`` under the lock only if
        this start's generation is still current. If ``stop()`` (or a newer
        ``apply()``) got there first, this thread stops its own backend
        instead -- so no listener can outlive a stop, whichever finishes first.
        """
        backend: HotkeyBackend | None = None
        detail = ""
        ok = False
        try:
            backend = self._backend_factory()
            backend.start(bindings)
            ok = True
            detail = str(getattr(backend, "note", "") or "")
        except HotkeyError as exc:
            detail = str(exc) or _GENERIC_DETAIL
            logger.warning("Global hotkeys unavailable: %s", detail)
        except ImportError as exc:
            detail = _IMPORT_DETAIL
            logger.warning("Global hotkeys unavailable: %s (%s)", detail, exc)
        except Exception as exc:  # noqa: BLE001 - anything at all must degrade, not crash
            detail = f"{_GENERIC_DETAIL}: {exc}" if str(exc) else _GENERIC_DETAIL
            logger.exception("Global hotkeys failed to start")

        if ok:
            with self._lock:
                current = generation == self._generation
                if current:
                    self._backend = backend
            if not current:
                assert backend is not None
                _stop_backend(backend)
        elif backend is not None:
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
