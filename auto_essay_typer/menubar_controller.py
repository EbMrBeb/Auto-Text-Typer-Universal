"""State and orchestration for the macOS menu-bar mode.

Everything the menu-bar app needs to decide what to show and what to do
lives here, with **no** ``rumps`` import -- that is what makes this module
importable and unit-testable on a machine with no display, and independently
of whether PyObjC/rumps is even installed. The thin ``rumps`` shell in
``menubar.py`` only renders what :class:`MenubarController` reports; it owns
no state of its own.

Threading mirrors ``app.py``'s Tk front end: a run (``driver.run(...)``) can
take minutes and sleeps throughout it, so it always executes on a dedicated
worker thread, never on the caller's thread. Unlike ``app.py`` there is no
Tk main loop to marshal updates onto -- callers (the rumps ``@timer`` poll,
or a test) read state through the properties below, which are all guarded by
a single lock so a reader never observes a torn update.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from auto_essay_typer.actions import Action, KeyPress, Pause
from auto_essay_typer.config import resolve_profile
from auto_essay_typer.driver import Driver, RunResult
from auto_essay_typer.planner import plan
from auto_essay_typer.simulator import total_duration_ms

#: Every state MenubarController.state can report.
STATES = ("idle", "counting_down", "typing", "paused", "done", "aborted", "error")

_STATIC_TITLES = {
    "idle": "⌨",
    "paused": "⌨ ‖",
    "done": "⌨ ✓",
    "aborted": "⌨ ✕",
    "error": "⌨ ✕",
}


def _cumulative_pause_ms_by_keypress(actions: list[Action]) -> list[float]:
    """result[i] = planned Pause ms up to and including the i-th KeyPress.

    Same technique as ``app.py``'s helper of the same name: the driver's
    ``on_progress`` callback only reports a keypress *count*, not a time
    position, so this precomputed table is what turns "42 of 100 keys typed"
    into "about this many planned milliseconds remain".
    """
    cumulative = [0.0]
    running = 0.0
    for action in actions:
        if isinstance(action, Pause):
            running += action.ms
        elif isinstance(action, KeyPress):
            cumulative.append(running)
    return cumulative


def _format_elapsed(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    minutes, secs = divmod(total_seconds, 60)
    return f"{minutes}m {secs}s" if minutes else f"{secs}s"


class MenubarController:
    """Headless controller backing the menu-bar app.

    ``read_clipboard`` and ``driver_factory`` are injected so this class can
    be exercised without ``pbpaste`` or real keystroke injection: tests pass
    a fake clipboard reader and a factory that builds a ``Driver`` around a
    ``RecordingKeyboard``.
    """

    def __init__(
        self,
        read_clipboard: Callable[[], str],
        driver_factory: Callable[[], Driver],
        profile_name: str = "natural",
        seed: int | None = None,
    ) -> None:
        self._read_clipboard = read_clipboard
        self._driver_factory = driver_factory
        self._profile_name = profile_name
        self._seed = seed

        self._lock = threading.Lock()
        self._state = "idle"
        self._message = "Ready."
        self._done = 0
        self._total = 0
        self._countdown_n = 0
        self._cumulative_pause_ms: list[float] = [0.0]
        self._total_planned_ms = 0.0

        self._driver: Driver | None = None
        self._worker: threading.Thread | None = None

    # -- read-only state, for the shell's @timer poll ----------------------

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def last_message(self) -> str:
        with self._lock:
            return self._message

    @property
    def progress(self) -> tuple[int, int]:
        with self._lock:
            return self._done, self._total

    @property
    def profile_name(self) -> str:
        with self._lock:
            return self._profile_name

    @property
    def seed(self) -> int | None:
        with self._lock:
            return self._seed

    @property
    def eta_seconds(self) -> float:
        """Remaining time, in seconds, estimated from the plan's remaining pause time."""
        with self._lock:
            done = self._done
            cumulative = self._cumulative_pause_ms
            total_ms = self._total_planned_ms
        elapsed_planned_ms = cumulative[done] if done < len(cumulative) else total_ms
        return max(0.0, (total_ms - elapsed_planned_ms) / 1000.0)

    def title(self) -> str:
        """Short menu-bar text for the current state. Kept terse -- the macOS menu bar is narrow."""
        with self._lock:
            state = self._state
            done, total = self._done, self._total
            n = self._countdown_n
        if state == "counting_down":
            return f"⌨ {n}…"
        if state == "typing":
            pct = int((done / total) * 100) if total else 0
            return f"⌨ {pct}%"
        return _STATIC_TITLES.get(state, "⌨")

    # -- settings -----------------------------------------------------------

    def set_profile(self, name: str) -> None:
        """Switch the active profile. An unknown name surfaces as the "error" state, never raises."""
        try:
            resolve_profile(name)
        except KeyError as exc:
            with self._lock:
                self._state = "error"
                self._message = str(exc)
            return
        with self._lock:
            self._profile_name = name

    def set_seed(self, seed: int | None) -> None:
        with self._lock:
            self._seed = seed

    # -- starting a run -------------------------------------------------

    def start_from_clipboard(self) -> None:
        """Read the clipboard, plan, and run -- all on a worker thread.

        An empty or whitespace-only clipboard goes to the "error" state with
        an explanatory message and never starts a run. A run already in
        flight makes this a no-op rather than a second, competing thread.
        """
        self._start(self._read_clipboard, source="Clipboard")

    def start_from_text(self, text: str) -> None:
        """Same as :meth:`start_from_clipboard`, but for text already read (e.g. from a chosen file)."""
        self._start(lambda: text, source="File")

    def _start(self, get_text: Callable[[], str], source: str) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return  # already running -- no-op, not a second thread
            # Reserve the slot before releasing the lock, so a concurrent
            # start_from_* call sees a live worker and no-ops rather than
            # racing this one to start a second run.
            self._state = "counting_down"
            self._message = "Starting…"
            self._done = 0
            self._total = 0
            self._countdown_n = 0
            worker = threading.Thread(target=self._run, args=(get_text, source), daemon=True)
            self._worker = worker
        worker.start()

    def _run(self, get_text: Callable[[], str], source: str) -> None:
        text = get_text()
        if not text or not text.strip():
            with self._lock:
                self._state = "error"
                self._message = f"{source} is empty"
            return

        with self._lock:
            profile_name = self._profile_name
            seed = self._seed

        try:
            profile = resolve_profile(profile_name)
        except KeyError as exc:
            with self._lock:
                self._state = "error"
                self._message = str(exc)
            return

        try:
            actions = plan(text, profile, seed)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user, never a traceback
            with self._lock:
                self._state = "error"
                self._message = f"Planning failed: {exc}"
            return

        total_ms = total_duration_ms(actions)
        cumulative = _cumulative_pause_ms_by_keypress(actions)
        with self._lock:
            self._total_planned_ms = total_ms
            self._cumulative_pause_ms = cumulative

        driver = self._driver_factory()
        with self._lock:
            self._driver = driver

        def on_countdown(n: int) -> None:
            with self._lock:
                self._state = "counting_down"
                self._countdown_n = n

        def on_progress(done: int, total: int) -> None:
            with self._lock:
                self._state = "typing"
                self._done = done
                self._total = total
                self._message = f"Typing {done}/{total}…"

        start_time = time.monotonic()
        try:
            result = driver.run(actions, on_progress=on_progress, on_countdown=on_countdown)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user, never a traceback
            with self._lock:
                self._state = "error"
                self._message = f"Error: {exc}"
            return

        elapsed = time.monotonic() - start_time
        with self._lock:
            if result is RunResult.COMPLETED:
                self._state = "done"
                self._message = f"Typed {len(text):,} chars in {_format_elapsed(elapsed)}"
            else:
                self._state = "aborted"
                self._message = "Aborted."

    # -- controls -------------------------------------------------------

    def toggle_pause(self) -> None:
        """Pause a running typing session, or resume a paused one. No-op otherwise."""
        with self._lock:
            driver = self._driver
            state = self._state
            if state == "typing":
                self._state = "paused"
                new_state = "paused"
            elif state == "paused":
                self._state = "typing"
                new_state = "typing"
            else:
                return
        if driver is None:
            return
        if new_state == "paused":
            driver.pause()
        else:
            driver.resume()

    def abort(self) -> None:
        """Stop the current run as soon as possible. Safe to call with nothing running,
        and safe to call racing a run that finishes naturally at the same moment --
        ``Driver.abort()`` only ever sets a flag, so this never raises."""
        with self._lock:
            driver = self._driver
        if driver is not None:
            driver.abort()

    # -- test / scripting helper -----------------------------------------

    def wait(self, timeout: float | None = None) -> None:
        """Block until any in-flight run finishes. No-op if nothing is running."""
        with self._lock:
            worker = self._worker
        if worker is not None:
            worker.join(timeout)
