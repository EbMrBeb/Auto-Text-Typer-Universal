"""Owns the run lifecycle: planning and driving, off the GUI thread.

Planning a long essay can take a noticeable moment and ``Driver.run`` sleeps
throughout the whole typed run, so both happen on one dedicated background
thread started by :meth:`RunController.start`. ``RunController`` itself is a
``QObject`` that lives on the GUI thread; its signals are emitted directly
from that background thread, which Qt delivers safely as queued calls to any
slot owned by a GUI-thread object (the pattern the module spec calls out
explicitly) -- so nothing here ever touches a widget, and no widget code
ever touches the background thread or the ``Driver`` directly.

``pause``/``resume``/``stop`` are called from the GUI thread and only ever
reach the running ``Driver`` through its own thread-safe ``pause()``/
``resume()``/``abort()`` methods (each just sets or clears a
``threading.Event``), the same cross-thread contract ``app.py`` and
``menubar_controller.py`` already relied on.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from auto_essay_typer.driver import Driver, RunResult
from auto_essay_typer.planner import plan
from auto_essay_typer.profiles import Profile
from auto_essay_typer.progress import cumulative_pause_ms_by_keypress, eta_seconds
from auto_essay_typer.simulator import total_duration_ms

#: Every value RunController.state / stateChanged can report.
STATES = (
    "idle",
    "planning",
    "countdown",
    "typing",
    "paused",
    "resuming",
    "finished",
    "stopped",
    "failed",
)

#: outcome strings the `finished` signal and the terminal post-run state use.
_TERMINAL_STATES = frozenset({"finished", "stopped", "failed"})


class RunController(QObject):
    """Drives one typing run at a time. See module docstring for the threading contract.

    Signals
    -------
    stateChanged(str)
        One of :data:`STATES`, emitted whenever the state actually changes.
    countdownTick(int)
        Seconds remaining, during both the initial start countdown (driven
        by ``Driver``) and the resume countdown (driven by this class).
    progressChanged(int, int, float, float)
        ``(done, total, eta_seconds, elapsed_seconds)`` -- ``done``/``total``
        are keypresses; ``eta_seconds`` is planned time remaining;
        ``elapsed_seconds`` is real wall-clock time since typing started.
    finished(str, str)
        ``(outcome, message)``; ``outcome`` is one of ``"finished"``,
        ``"stopped"``, ``"failed"``.
    """

    stateChanged = Signal(str)
    countdownTick = Signal(int)
    progressChanged = Signal(int, int, float, float)
    finished = Signal(str, str)

    def __init__(
        self,
        driver_factory: Callable[[], Driver] = Driver,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._driver_factory = driver_factory
        self._driver: Driver | None = None
        self._stop_event = threading.Event()
        self._state = "idle"
        self._busy = False
        self._thread: threading.Thread | None = None
        self._resume_timer: QTimer | None = None
        self._resume_remaining = 0

    # -- read-only state ----------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_busy(self) -> bool:
        """True from :meth:`start` until the run's ``finished`` signal has fired."""
        return self._busy

    # -- lifecycle ------------------------------------------------------------

    def start(self, text: str, profile: Profile, seed: int | None = None) -> None:
        """Begin planning ``text`` and, once planned, typing it. No-op if already busy."""
        if self._busy:
            return
        self._busy = True
        self._driver = None
        self._stop_event = threading.Event()
        self._set_state("planning")
        self._thread = threading.Thread(
            target=self._run_worker,
            args=(text, profile, seed, self._stop_event),
            daemon=True,
        )
        self._thread.start()

    def pause(self) -> None:
        """Pause a running typing session. No-op unless a run is actively typing."""
        if self._driver is not None and self._state == "typing":
            self._driver.pause()
            self._set_state("paused")

    def resume(self, countdown_seconds: int = 3) -> None:
        """Resume a paused session, after a countdown to give the user time to
        click back into their document (this window just had focus). No-op
        unless a run is currently paused."""
        if self._driver is None or self._state != "paused":
            return
        if countdown_seconds <= 0:
            self._do_resume()
            return
        self._set_state("resuming")
        self._resume_remaining = countdown_seconds
        self.countdownTick.emit(self._resume_remaining)
        self._resume_timer = QTimer(self)
        self._resume_timer.setInterval(1000)
        self._resume_timer.timeout.connect(self._tick_resume_countdown)
        self._resume_timer.start()

    def stop(self) -> None:
        """Stop as soon as possible. Safe to call at any time, including with
        nothing running, or racing a run that finishes naturally -- never raises."""
        self._cancel_resume_countdown()
        self._stop_event.set()
        if self._driver is not None:
            self._driver.abort()

    # -- internals: GUI-thread timer for the resume countdown -----------------

    def _tick_resume_countdown(self) -> None:
        self._resume_remaining -= 1
        if self._resume_remaining <= 0:
            self._cancel_resume_countdown()
            self._do_resume()
        else:
            self.countdownTick.emit(self._resume_remaining)

    def _cancel_resume_countdown(self) -> None:
        if self._resume_timer is not None:
            self._resume_timer.stop()
            self._resume_timer.deleteLater()
            self._resume_timer = None

    def _do_resume(self) -> None:
        if self._driver is not None:
            self._driver.resume()
        self._set_state("typing")

    def _set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        self.stateChanged.emit(state)

    # -- internals: the background thread's entire body ------------------------
    #
    # Everything below this point runs on the worker thread started by
    # `start()`. It must never touch a widget -- only emit this object's own
    # signals (safe: see module docstring) and read/write the plain
    # attributes `_driver`/`_busy`, which is safe under the GIL for the
    # simple assign-then-only-read-elsewhere pattern used here.

    def _run_worker(
        self, text: str, profile: Profile, seed: int | None, stop_event: threading.Event
    ) -> None:
        try:
            actions = plan(text, profile, seed)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user via `finished`
            self._finish_worker("failed", f"Planning failed: {exc}")
            return

        total_ms = total_duration_ms(actions)
        cumulative = cumulative_pause_ms_by_keypress(actions)

        driver = self._driver_factory()
        self._driver = driver
        if stop_event.is_set():
            # Stop was requested while still planning -- Driver.run() itself
            # would unconditionally clear any pre-set abort flag at the top
            # of run(), so the only correct way to honour an early stop is to
            # never call it at all.
            self._finish_worker("stopped", "Stopped.")
            return

        start_time = time.monotonic()

        def on_countdown(n: int) -> None:
            self._set_state("countdown")
            self.countdownTick.emit(n)

        def on_progress(done: int, total: int) -> None:
            self._set_state("typing")
            eta = eta_seconds(done, total_ms, cumulative)
            elapsed = time.monotonic() - start_time
            self.progressChanged.emit(done, total, eta, elapsed)

        try:
            result = driver.run(actions, on_progress=on_progress, on_countdown=on_countdown)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user via `finished`
            self._finish_worker("failed", str(exc))
            return

        if result is RunResult.COMPLETED:
            self._finish_worker("finished", "Done.")
        else:
            self._finish_worker("stopped", "Stopped.")

    def _finish_worker(self, outcome: str, message: str) -> None:
        assert outcome in _TERMINAL_STATES
        self._busy = False
        self._set_state(outcome)
        self.finished.emit(outcome, message)
