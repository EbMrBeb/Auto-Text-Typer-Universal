"""Owns the run lifecycle: planning and driving, off the GUI thread.

Planning a long essay can take a noticeable moment and ``Driver.run`` sleeps
throughout the whole typed run, so both happen on one dedicated background
thread started by :meth:`RunController.start`. ``RunController`` itself is a
``QObject`` that lives on the GUI thread; its signals are emitted directly
from that background thread, which Qt delivers safely as queued calls to any
slot owned by a GUI-thread object (the pattern the module spec calls out
explicitly) -- so nothing here ever touches a widget, and no widget code
ever touches the background thread or the ``Driver`` directly.

Only the *running* happens there: the ``Driver`` is built by :meth:`start` (or
:meth:`start_document`) on the GUI thread, because building the real keyboard
backend off the main thread kills the whole process on macOS (see
``driver.preload_keyboard_backend``).

Two ways to start a run share everything else: :meth:`start` types plain text,
and :meth:`start_document` types a formatted document by also pressing the
target editor's formatting shortcuts, which is why it builds its ``Driver``
with that editor's keymap and checks the plan against the document before a
single key is pressed.

``pause``/``resume``/``stop`` are called from the GUI thread and only ever
reach the running ``Driver`` through its own thread-safe ``pause()``/
``resume()``/``abort()`` methods (each just sets or clears a
``threading.Event``), the same cross-thread contract ``app.py`` and
``menubar_controller.py`` already relied on.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from auto_essay_typer.actions import Action, FormatOp
from auto_essay_typer.document import Document
from auto_essay_typer.driver import Driver, RunResult, UnsupportedFormatError
from auto_essay_typer.gui.document_session import PlanVerificationError, verify_document_plan
from auto_essay_typer.keymaps import Chord
from auto_essay_typer.planner import plan, plan_document
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

#: How ``UnsupportedFormatError`` begins its message, followed by the missing ops.
_MISSING_CHORDS_PREFIX = "keymap has no chord for: "


def _unsupported_format_message(error: UnsupportedFormatError) -> str:
    """``error`` as something to tell a person: which steps lack a shortcut, and what to do.

    ``Driver`` refuses a plan it has no shortcut for before pressing anything,
    so this only reaches the user when an editor's shortcuts have been edited
    (on the Advanced page) to leave out something the document needs.
    """
    text = str(error)
    if not text.startswith(_MISSING_CHORDS_PREFIX):
        return text
    missing = [name.strip().replace("_", " ") for name in text[len(_MISSING_CHORDS_PREFIX) :].split(",")]
    return (
        "The editor's shortcuts don't cover everything this document needs "
        f"(missing: {', '.join(missing)}). Nothing was typed. Add the missing shortcuts on the "
        "Advanced page, or choose another editor."
    )


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
        driver_factory: Callable[..., Driver] = Driver,
        parent: QObject | None = None,
    ) -> None:
        """``driver_factory`` builds the run's ``Driver``, on the GUI thread.

        It is called with no arguments to type plain text, and with a single
        ``keymap=`` keyword to type a document, so a factory only needs to
        accept ``keymap`` if documents are to be typed through it (``Driver``
        itself does).
        """
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
        self._begin(lambda: plan(text, profile, seed))

    def start_document(
        self,
        document: Document,
        profile: Profile,
        keymap: Mapping[FormatOp, Chord],
        seed: int | None = None,
    ) -> None:
        """Begin planning ``document`` and, once planned, typing it. No-op if already busy.

        ``document`` must already be adapted to ``keymap``
        (:func:`auto_essay_typer.keymaps.adapt_document`), which is also the
        keymap the run's ``Driver`` is built with, so every shortcut the plan
        presses is one the editor being typed into really has.

        Before the countdown starts, the plan is replayed through the Google
        Docs editing model and must come back as exactly ``document``
        (:func:`~auto_essay_typer.gui.document_session.verify_document_plan`).
        If it does not, nothing is typed and the run ends as ``"failed"``: a
        document typed wrongly is worse than one not typed at all.
        """

        def make_actions() -> list[Action]:
            actions = plan_document(document, profile, seed)
            check = verify_document_plan(actions, document)
            if not check.ok:
                raise PlanVerificationError(
                    f"the typing plan did not reproduce your document exactly, so nothing "
                    f"was typed ({check.detail})"
                )
            return actions

        self._begin(make_actions, keymap=keymap)

    def _begin(self, make_actions: Callable[[], list[Action]], **driver_kwargs: object) -> None:
        """Shared start-up for every kind of run: build the driver, then plan and type off-thread.

        ``driver_kwargs`` go to the driver factory. A plain-text run passes
        none, so its factory is called exactly as it always was.
        """
        if self._busy:
            return
        self._busy = True
        self._driver = None
        self._stop_event = threading.Event()
        try:
            # Built here on the GUI thread, never on the worker: constructing the real
            # keyboard backend off the main thread kills the whole process on macOS.
            driver = self._driver_factory(**driver_kwargs)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user via `finished`
            self._finish_worker("failed", f"Keyboard setup failed: {exc}")
            return
        self._set_state("planning")
        self._thread = threading.Thread(
            target=self._run_worker,
            args=(make_actions, self._stop_event, driver),
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
        self,
        make_actions: Callable[[], list[Action]],
        stop_event: threading.Event,
        driver: Driver,
    ) -> None:
        try:
            actions = make_actions()
        except Exception as exc:  # noqa: BLE001 - surfaced to the user via `finished`
            self._finish_worker("failed", f"Planning failed: {exc}")
            return

        total_ms = total_duration_ms(actions)
        cumulative = cumulative_pause_ms_by_keypress(actions)

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
        except UnsupportedFormatError as exc:
            self._finish_worker("failed", _unsupported_format_message(exc))
            return
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
