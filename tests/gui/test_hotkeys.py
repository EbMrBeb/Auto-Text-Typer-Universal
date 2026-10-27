"""Tests for :mod:`auto_essay_typer.gui.hotkeys`.

No test here ever starts a real global-hotkey listener. The service is driven
through a fake backend, and :class:`PynputHotkeyBackend`'s polling logic is
driven through a fake *listener* with a virtual clock (so even the timeout
paths take no real time). The one test that touches real pynput only builds a
``GlobalHotKeys`` object and checks it was not started.

Hotkey presses are fired from genuine background threads, as pynput's listener
thread would, and the tests assert the Qt signal is delivered on the GUI thread.
"""

from __future__ import annotations

import logging
import threading
import types

import pytest
from PySide6.QtCore import QObject

from auto_essay_typer.gui import hotkeys
from auto_essay_typer.gui.hotkeys import (
    HotkeyError,
    HotkeyService,
    PynputHotkeyBackend,
    validated_hotkeys,
)
from auto_essay_typer.platforms import LinuxSession, OperatingSystem

HOTKEYS = {
    "start": "<ctrl>+<alt>+<shift>+t",
    "pause": "<ctrl>+<alt>+<shift>+p",
    "stop": "<esc>",
}
OTHER_HOTKEYS = {**HOTKEYS, "start": "<ctrl>+<alt>+<shift>+k"}


# --- fakes -----------------------------------------------------------------------


class FakeBackend:
    """A HotkeyBackend that records what the service does to it."""

    def __init__(self, *, start_error=None, stop_error=None, gate=None, note=""):
        self.start_error = start_error
        self.stop_error = stop_error
        self.gate = gate
        self.note = note
        self.bindings: dict | None = None
        self.start_entered = threading.Event()
        self.start_thread: threading.Thread | None = None
        self.started = False
        self.stop_calls = 0

    def start(self, bindings):
        self.start_thread = threading.current_thread()
        self.start_entered.set()
        if self.gate is not None:
            assert self.gate.wait(5), "the test never released the start gate"
        if self.start_error is not None:
            raise self.start_error
        self.bindings = dict(bindings)
        self.started = True

    def stop(self):
        self.stop_calls += 1
        if self.stop_error is not None:
            raise self.stop_error


class BackendFactory:
    """Hands out prepared FakeBackends in order (then plain ones), recording each."""

    def __init__(self, *prepared, error=None):
        self._prepared = list(prepared)
        self.error = error
        self.built: list[FakeBackend] = []
        self.call_threads: list[threading.Thread] = []

    def __call__(self):
        self.call_threads.append(threading.current_thread())
        if self.error is not None:
            raise self.error
        backend = self._prepared.pop(0) if self._prepared else FakeBackend()
        self.built.append(backend)
        return backend


class Recorder(QObject):
    """Records the service's signals, and which thread each slot ran on."""

    def __init__(self, service):
        super().__init__()
        self.statuses: list[tuple[str, str]] = []
        self.calls: list[tuple[str, bool]] = []
        service.statusChanged.connect(self.on_status)
        service.startRequested.connect(self.on_start)
        service.pauseRequested.connect(self.on_pause)
        service.stopRequested.connect(self.on_stop)

    def _record(self, name):
        self.calls.append((name, threading.current_thread() is threading.main_thread()))

    def on_status(self, state, detail):
        self.statuses.append((state, detail))

    def on_start(self):
        self._record("start")

    def on_pause(self):
        self._record("pause")

    def on_stop(self):
        self._record("stop")


@pytest.fixture
def make_service(qtbot):
    """Build ``(service, factory, recorder)``; every service is stopped afterwards."""
    made: list[HotkeyService] = []

    def make(*prepared, error=None):
        factory = BackendFactory(*prepared, error=error)
        service = HotkeyService(backend_factory=factory)
        made.append(service)
        return service, factory, Recorder(service)

    yield make
    for service in made:
        service.stop()


def wait_for_state(qtbot, service, state, timeout=3000):
    qtbot.waitUntil(lambda: service.state == state, timeout=timeout)


def fire_from_background_thread(callback):
    """Call ``callback`` the way pynput's listener thread would: off the GUI thread."""
    thread = threading.Thread(target=callback, name="fake-listener-thread")
    thread.start()
    thread.join(5)
    assert not thread.is_alive()


def drain(qapp):
    """Wait for every in-flight start worker to finish, then deliver whatever it queued.

    Deterministic replacement for "sleep a bit and hope": once the worker
    threads are joined, anything they emitted is already in the event queue,
    and ``processEvents`` delivers it -- so a "this did NOT happen" assertion
    made afterwards is meaningful.
    """
    for thread in threading.enumerate():
        if thread.name == "auto-text-typer-hotkeys-start":
            thread.join(5)
            assert not thread.is_alive()
    qapp.processEvents()


# --- the service: laziness, going active, thread-safety --------------------------------


def test_the_default_backend_is_pynput_and_nothing_starts_on_construction(make_service):
    assert HotkeyService()._backend_factory is PynputHotkeyBackend

    service, factory, recorder = make_service()
    assert factory.built == []
    assert service.state == "off"
    assert service.detail == ""
    assert recorder.statuses == []


def test_applying_disabled_never_starts_a_backend(make_service):
    service, factory, recorder = make_service()
    service.apply(False, HOTKEYS)
    assert factory.built == []
    assert service.state == "off"
    assert recorder.statuses == []  # already off: nothing to announce


def test_enabling_starts_the_backend_on_a_worker_thread_and_goes_active(qtbot, make_service):
    service, factory, recorder = make_service()
    service.apply(True, HOTKEYS)

    assert service.state == "starting"
    wait_for_state(qtbot, service, "active")

    (backend,) = factory.built
    assert backend.started
    # Both building and starting the backend happen off the GUI thread.
    assert factory.call_threads and threading.main_thread() not in factory.call_threads
    assert backend.start_thread is not threading.main_thread()
    assert set(backend.bindings) == set(HOTKEYS.values())
    assert recorder.statuses == [("starting", ""), ("active", "")]


def test_a_slow_start_never_blocks_the_caller(qtbot, make_service):
    gate = threading.Event()
    service, factory, recorder = make_service(FakeBackend(gate=gate))

    service.apply(True, HOTKEYS)  # returns at once even though start() is blocked
    assert service.state == "starting"
    qtbot.waitUntil(lambda: bool(factory.built) and factory.built[0].start_entered.is_set(), timeout=3000)
    assert service.state == "starting"

    gate.set()
    wait_for_state(qtbot, service, "active")


@pytest.mark.parametrize(
    ("action", "hotkey"),
    [("start", HOTKEYS["start"]), ("pause", HOTKEYS["pause"]), ("stop", HOTKEYS["stop"])],
)
def test_a_hotkey_press_from_the_listener_thread_arrives_on_the_gui_thread(
    qtbot, make_service, action, hotkey
):
    service, factory, recorder = make_service()
    service.apply(True, HOTKEYS)
    wait_for_state(qtbot, service, "active")

    fire_from_background_thread(factory.built[0].bindings[hotkey])
    qtbot.waitUntil(lambda: bool(recorder.calls), timeout=3000)

    assert recorder.calls == [(action, True)]  # exactly one signal, delivered on the main thread


def test_each_hotkey_maps_to_its_own_signal(qtbot, make_service):
    service, factory, recorder = make_service()
    service.apply(True, HOTKEYS)
    wait_for_state(qtbot, service, "active")

    for hotkey in (HOTKEYS["pause"], HOTKEYS["start"], HOTKEYS["stop"]):
        fire_from_background_thread(factory.built[0].bindings[hotkey])
    qtbot.waitUntil(lambda: len(recorder.calls) == 3, timeout=3000)

    assert [name for name, _ in recorder.calls] == ["pause", "start", "stop"]


# --- the service: apply semantics ---------------------------------------------------------


def test_applying_the_same_configuration_twice_starts_one_backend(qtbot, make_service):
    service, factory, recorder = make_service()
    service.apply(True, HOTKEYS)
    service.apply(True, dict(HOTKEYS))  # same content, different dict, while still starting
    wait_for_state(qtbot, service, "active")
    service.apply(True, HOTKEYS)

    assert len(factory.built) == 1
    assert recorder.statuses == [("starting", ""), ("active", "")]


def test_a_changed_configuration_restarts_and_stops_the_old_backend(qtbot, make_service):
    service, factory, recorder = make_service()
    service.apply(True, HOTKEYS)
    wait_for_state(qtbot, service, "active")
    first = factory.built[0]

    service.apply(True, OTHER_HOTKEYS)
    assert first.stop_calls == 1  # stopped synchronously by apply()
    qtbot.waitUntil(lambda: len(factory.built) == 2 and service.state == "active", timeout=3000)

    assert factory.built[1].started
    assert set(factory.built[1].bindings) == set(OTHER_HOTKEYS.values())


def test_disabling_stops_the_running_backend(qtbot, make_service):
    service, factory, recorder = make_service()
    service.apply(True, HOTKEYS)
    wait_for_state(qtbot, service, "active")

    service.apply(False, HOTKEYS)

    assert factory.built[0].stop_calls == 1
    assert service.state == "off"
    assert recorder.statuses[-1] == ("off", "")


def test_toggling_off_and_on_starts_afresh(qtbot, make_service):
    service, factory, recorder = make_service()
    service.apply(True, HOTKEYS)
    wait_for_state(qtbot, service, "active")
    service.apply(False, HOTKEYS)
    service.apply(True, HOTKEYS)
    qtbot.waitUntil(lambda: len(factory.built) == 2 and service.state == "active", timeout=3000)


def test_a_note_from_the_backend_becomes_the_active_detail(qtbot, make_service):
    service, _factory, recorder = make_service(FakeBackend(note="Only works in X11 windows"))
    service.apply(True, HOTKEYS)
    wait_for_state(qtbot, service, "active")
    assert service.detail == "Only works in X11 windows"
    assert recorder.statuses[-1] == ("active", "Only works in X11 windows")


# --- the service: failing soft ---------------------------------------------------------------


def test_a_backend_that_cannot_start_makes_the_service_unavailable(qtbot, make_service, caplog):
    caplog.set_level(logging.DEBUG, logger="auto_essay_typer.gui.hotkeys")
    failing = FakeBackend(start_error=HotkeyError("Needs Accessibility access"))
    service, factory, recorder = make_service(failing)

    service.apply(True, HOTKEYS)  # must not raise
    wait_for_state(qtbot, service, "unavailable")

    assert service.detail == "Needs Accessibility access"
    assert recorder.statuses == [("starting", ""), ("unavailable", "Needs Accessibility access")]
    assert failing.stop_calls == 1  # the half-built backend is cleaned up
    assert any("Needs Accessibility access" in record.getMessage() for record in caplog.records)


def test_an_unexpected_backend_exception_is_contained_and_logged(qtbot, make_service, caplog):
    caplog.set_level(logging.DEBUG, logger="auto_essay_typer.gui.hotkeys")
    service, _factory, _recorder = make_service(FakeBackend(start_error=RuntimeError("kaboom")))

    service.apply(True, HOTKEYS)
    wait_for_state(qtbot, service, "unavailable")

    assert "kaboom" in service.detail
    assert any(record.levelno >= logging.ERROR for record in caplog.records)


def test_a_backend_factory_that_raises_import_error_is_unavailable(qtbot, make_service):
    service, factory, _recorder = make_service(error=ImportError("no keyboard backend"))
    service.apply(True, HOTKEYS)
    wait_for_state(qtbot, service, "unavailable")
    assert "keyboard library" in service.detail


def test_a_backend_factory_that_raises_anything_is_unavailable(qtbot, make_service):
    service, _factory, _recorder = make_service(error=OSError("display exploded"))
    service.apply(True, HOTKEYS)
    wait_for_state(qtbot, service, "unavailable")
    assert "display exploded" in service.detail


def test_duplicate_hotkeys_are_unavailable_without_starting_a_backend(make_service):
    service, factory, recorder = make_service()
    # The same keys in a different order and case are the same hotkey.
    service.apply(True, {**HOTKEYS, "pause": "<ALT>+<ctrl>+<shift>+T"})

    assert service.state == "unavailable"
    assert "Start and Pause" in service.detail
    assert factory.built == []
    assert recorder.statuses == [("unavailable", service.detail)]


def test_a_missing_action_is_unavailable(make_service):
    service, factory, _recorder = make_service()
    service.apply(True, {"start": HOTKEYS["start"], "stop": HOTKEYS["stop"]})
    assert service.state == "unavailable"
    assert "Pause" in service.detail
    assert factory.built == []


def test_an_empty_hotkey_is_unavailable(make_service):
    service, factory, _recorder = make_service()
    service.apply(True, {**HOTKEYS, "stop": "  "})
    assert service.state == "unavailable"
    assert "Stop" in service.detail
    assert factory.built == []


def test_a_failed_configuration_is_not_retried_automatically_but_retry_works(qtbot, make_service):
    service, factory, _recorder = make_service(FakeBackend(start_error=HotkeyError("nope")))
    service.apply(True, HOTKEYS)
    wait_for_state(qtbot, service, "unavailable")

    service.apply(True, HOTKEYS)  # an unrelated settings change re-applies: no new attempt
    assert len(factory.built) == 1

    service.retry()
    qtbot.waitUntil(lambda: len(factory.built) == 2 and service.state == "active", timeout=3000)


def test_retry_with_nothing_requested_is_a_no_op(make_service):
    service, factory, _recorder = make_service()
    service.retry()
    assert factory.built == []
    assert service.state == "off"


# --- the service: stopping reliably ---------------------------------------------------------------


def test_a_start_that_finishes_after_stop_is_discarded_and_its_backend_stopped(
    qtbot, qapp, make_service
):
    gate = threading.Event()
    slow = FakeBackend(gate=gate)
    service, factory, recorder = make_service(slow)

    service.apply(True, HOTKEYS)
    qtbot.waitUntil(slow.start_entered.is_set, timeout=3000)
    service.stop()
    assert service.state == "off"

    gate.set()  # the start now completes, too late
    qtbot.waitUntil(lambda: slow.stop_calls == 1, timeout=3000)
    drain(qapp)  # deliver any (wrongly) queued completion signal

    assert service.state == "off"
    assert ("active", "") not in recorder.statuses
    fire_from_background_thread(slow.bindings[HOTKEYS["start"]])
    qapp.processEvents()
    assert recorder.calls == []


def test_a_start_superseded_by_a_newer_apply_is_discarded(qtbot, qapp, make_service):
    gate = threading.Event()
    stale = FakeBackend(gate=gate)
    service, factory, recorder = make_service(stale, FakeBackend())

    service.apply(True, HOTKEYS)
    qtbot.waitUntil(stale.start_entered.is_set, timeout=3000)
    service.apply(True, OTHER_HOTKEYS)
    wait_for_state(qtbot, service, "active")
    live = factory.built[1]

    gate.set()
    qtbot.waitUntil(lambda: stale.stop_calls == 1, timeout=3000)
    drain(qapp)

    assert service.state == "active"
    assert live.stop_calls == 0
    # The superseded backend's callbacks are ignored; the live one's work.
    fire_from_background_thread(stale.bindings[HOTKEYS["start"]])
    fire_from_background_thread(live.bindings[OTHER_HOTKEYS["start"]])
    qapp.processEvents()
    assert recorder.calls == [("start", True)]


def test_stop_is_repeatable_and_tolerates_a_backend_whose_stop_raises(qtbot, make_service, caplog):
    caplog.set_level(logging.DEBUG, logger="auto_essay_typer.gui.hotkeys")
    backend = FakeBackend(stop_error=RuntimeError("stop failed"))
    service, _factory, recorder = make_service(backend)
    service.apply(True, HOTKEYS)
    wait_for_state(qtbot, service, "active")

    service.stop()  # must not raise
    service.stop()
    service.stop()

    assert service.state == "off"
    assert backend.stop_calls == 1  # nothing left to stop after the first
    assert any(
        record.exc_info and "stop failed" in str(record.exc_info[1]) for record in caplog.records
    )


def test_stop_without_ever_starting_is_harmless(make_service):
    service, _factory, recorder = make_service()
    service.stop()
    service.stop()
    assert service.state == "off"
    assert recorder.statuses == []


def test_hotkey_presses_after_stop_are_ignored(qtbot, qapp, make_service):
    service, factory, recorder = make_service()
    service.apply(True, HOTKEYS)
    wait_for_state(qtbot, service, "active")
    callback = factory.built[0].bindings[HOTKEYS["stop"]]

    service.stop()
    fire_from_background_thread(callback)
    qapp.processEvents()

    assert recorder.calls == []


def test_quitting_the_app_stops_the_service(qtbot, qapp, make_service):
    service, factory, _recorder = make_service()
    service.apply(True, HOTKEYS)
    wait_for_state(qtbot, service, "active")

    qapp.aboutToQuit.emit()

    assert service.state == "off"
    assert factory.built[0].stop_calls == 1


def test_the_atexit_hook_stops_backends_without_touching_qt(qtbot, make_service):
    service, factory, recorder = make_service()
    service.apply(True, HOTKEYS)
    wait_for_state(qtbot, service, "active")
    announced = list(recorder.statuses)

    hotkeys._stop_all_at_exit()

    assert factory.built[0].stop_calls == 1
    assert recorder.statuses == announced  # no Qt signal at interpreter exit


def test_the_atexit_hook_is_registered_once_on_first_use(monkeypatch, make_service):
    registered = []
    fake_atexit = types.SimpleNamespace(register=lambda fn: registered.append(fn))
    monkeypatch.setattr(hotkeys, "atexit", fake_atexit)
    monkeypatch.setattr(hotkeys, "_atexit_registered", False)

    first, _f1, _r1 = make_service()
    second, _f2, _r2 = make_service()
    first.apply(False, HOTKEYS)
    second.apply(False, HOTKEYS)
    first.apply(False, HOTKEYS)

    assert registered == [hotkeys._stop_all_at_exit]


# --- validated_hotkeys -------------------------------------------------------------------------


def test_validated_hotkeys_returns_the_three_actions_stripped():
    result = validated_hotkeys({**HOTKEYS, "start": f"  {HOTKEYS['start']} ", "extra": "ignored"})
    assert result == HOTKEYS


@pytest.mark.parametrize(
    ("hotkeys", "message"),
    [
        ({"start": "<f9>", "stop": "<esc>"}, "No hotkey is set for Pause"),
        ({**HOTKEYS, "start": ""}, "The Start hotkey is empty"),
        ({**HOTKEYS, "pause": "<esc>"}, "Pause and Stop use the same hotkey"),
    ],
)
def test_validated_hotkeys_explains_what_is_wrong(hotkeys, message):
    with pytest.raises(ValueError, match=message):
        validated_hotkeys(hotkeys)


# --- PynputHotkeyBackend, driven by a fake listener and a virtual clock ----------------------------


class VirtualClock:
    """Time that only advances when the backend sleeps, and ticks fake listeners as it does."""

    def __init__(self):
        self.now = 0.0
        self.listeners: list[FakeListener] = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        for listener in self.listeners:
            listener.tick(self.now)


class FakeListener:
    """The slice of a pynput listener the backend uses, with a scripted life cycle.

    ``ready_at``/``dies_at`` are virtual times (``None`` = never). ``start()``
    counts as the thread beginning at time 0.
    """

    def __init__(self, bindings, clock, *, ready_at=0.0, dies_at=None, with_ready_flag=True,
                 start_error=None, stop_error=None, join_error=None):
        self.bindings = bindings
        self.clock = clock
        self.ready_at = ready_at
        self.dies_at = dies_at
        self.start_error = start_error
        self.stop_error = stop_error
        self.join_error = join_error
        self.daemon = False
        self.running = True
        self.started = False
        self.stopped = False
        self.stop_calls = 0
        self.join_timeouts: list[float | None] = []
        self.with_ready_flag = with_ready_flag
        if with_ready_flag:
            self._ready = False
        clock.listeners.append(self)

    def tick(self, now):
        if self.with_ready_flag and self.started and self.ready_at is not None and now >= self.ready_at:
            self._ready = True

    def start(self):
        if self.start_error is not None:
            raise self.start_error
        self.started = True
        self.tick(self.clock.now)

    def is_alive(self):
        if not self.started or self.stopped:
            return False
        return self.dies_at is None or self.clock.now < self.dies_at

    def stop(self):
        self.stop_calls += 1
        self.stopped = True
        if self.stop_error is not None:
            raise self.stop_error

    def join(self, timeout=None):
        self.join_timeouts.append(timeout)
        if self.join_error is not None:
            raise self.join_error


def make_backend(os_=OperatingSystem.LINUX, session=LinuxSession.X11, **listener_kwargs):
    """``(backend, clock, listeners)`` with a fake listener factory and virtual time."""
    clock = VirtualClock()
    listeners: list[FakeListener] = []

    def factory(bindings):
        listener = FakeListener(bindings, clock, **listener_kwargs)
        listeners.append(listener)
        return listener

    backend = PynputHotkeyBackend(
        factory,
        clock=clock,
        sleep=clock.sleep,
        os_provider=lambda: os_,
        session_provider=lambda: session,
    )
    return backend, clock, listeners


def noop():
    return None


BINDINGS = {HOTKEYS["start"]: noop, HOTKEYS["pause"]: noop, HOTKEYS["stop"]: noop}


def test_the_backend_starts_a_listener_for_the_bindings_and_stops_it_cleanly():
    backend, _clock, listeners = make_backend(ready_at=0.04)

    backend.start(BINDINGS)

    (listener,) = listeners
    assert listener.started
    assert listener.bindings == BINDINGS  # {hotkey text: callback}, handed through untouched
    assert all(listener.bindings[text] is BINDINGS[text] for text in BINDINGS)
    assert backend.note == ""

    backend.stop()
    assert listener.stop_calls == 1
    assert listener.join_timeouts  # joined (bounded) so the thread is really gone
    assert all(timeout is not None for timeout in listener.join_timeouts)


def test_the_backend_stop_is_idempotent_and_safe_before_start():
    backend, _clock, listeners = make_backend()
    backend.stop()  # never started
    backend.start(BINDINGS)
    backend.stop()
    backend.stop()
    backend.stop()
    assert listeners[0].stop_calls == 1


def test_the_backend_stop_tolerates_a_listener_that_raises():
    backend, _clock, listeners = make_backend(
        stop_error=RuntimeError("stop"), join_error=RuntimeError("join")
    )
    backend.start(BINDINGS)
    backend.stop()  # must not raise
    assert listeners[0].stop_calls == 1


def test_the_backend_forces_the_listener_thread_to_be_a_daemon():
    backend, _clock, listeners = make_backend()
    backend.start(BINDINGS)
    assert listeners[0].daemon is True  # the fake began as a non-daemon
    backend.stop()


def test_a_listener_that_dies_before_it_is_ready_raises_instead_of_hanging():
    backend, clock, listeners = make_backend(
        os_=OperatingSystem.LINUX, session=LinuxSession.NONE, ready_at=None, dies_at=0.02
    )
    with pytest.raises(HotkeyError, match="No X display for hotkeys"):
        backend.start(BINDINGS)
    assert listeners[0].stop_calls == 1  # the dead listener is cleaned up
    assert clock.now < 1.0  # bailed out promptly, nowhere near the timeout


def test_a_listener_that_is_ready_but_dies_right_after_means_macos_refused_it():
    # pynput on macOS without Accessibility/Input Monitoring: ready is reported,
    # then the thread ends. Without the settle check this would look like success.
    backend, _clock, listeners = make_backend(
        os_=OperatingSystem.MACOS, ready_at=0.0, dies_at=0.1
    )
    with pytest.raises(HotkeyError, match="Accessibility and Input Monitoring"):
        backend.start(BINDINGS)
    assert listeners[0].stop_calls == 1


def test_a_listener_that_never_becomes_ready_times_out_and_is_stopped():
    backend, clock, listeners = make_backend(ready_at=None, dies_at=None)
    with pytest.raises(HotkeyError, match="did not start in time"):
        backend.start(BINDINGS)
    assert listeners[0].stop_calls == 1
    assert clock.now >= 3.0  # waited the full (virtual) timeout, no longer


def test_a_listener_without_pynputs_ready_flag_is_judged_by_running_and_alive():
    backend, _clock, listeners = make_backend(with_ready_flag=False)
    backend.start(BINDINGS)
    assert listeners[0].started
    backend.stop()


@pytest.mark.parametrize(
    ("os_", "session", "expected"),
    [
        (OperatingSystem.MACOS, LinuxSession.X11, "Accessibility and Input Monitoring"),
        (OperatingSystem.LINUX, LinuxSession.NONE, "No X display for hotkeys"),
        (OperatingSystem.LINUX, LinuxSession.WAYLAND, "Wayland"),
        (OperatingSystem.LINUX, LinuxSession.X11, "X display"),
        (OperatingSystem.WINDOWS, LinuxSession.X11, "Global hotkeys could not start"),
    ],
)
def test_failure_advice_depends_on_the_platform(os_, session, expected):
    backend, _clock, _listeners = make_backend(os_=os_, session=session, ready_at=None, dies_at=0.0)
    with pytest.raises(HotkeyError) as excinfo:
        backend.start(BINDINGS)
    assert expected in str(excinfo.value)


def test_an_invalid_hotkey_from_pynput_is_reported_as_such():
    def factory(bindings):
        raise ValueError("'<bogus>' is not a key")

    backend = PynputHotkeyBackend(factory)
    with pytest.raises(HotkeyError, match="not a valid shortcut"):
        backend.start(BINDINGS)


def test_a_missing_pynput_is_reported_plainly():
    def factory(bindings):
        raise ModuleNotFoundError("No module named 'pynput'", name="pynput")

    backend = PynputHotkeyBackend(factory)
    with pytest.raises(HotkeyError, match="pynput keyboard library is not installed"):
        backend.start(BINDINGS)


def test_an_import_failure_gets_platform_advice():
    # pynput raises a bare ImportError on a headless Linux box.
    def factory(bindings):
        raise ImportError("this platform is not supported: failed to acquire X connection")

    backend = PynputHotkeyBackend(
        factory,
        os_provider=lambda: OperatingSystem.LINUX,
        session_provider=lambda: LinuxSession.NONE,
    )
    with pytest.raises(HotkeyError, match="No X display for hotkeys"):
        backend.start(BINDINGS)


def test_a_listener_that_cannot_start_its_thread_is_reported():
    backend, _clock, _listeners = make_backend(start_error=RuntimeError("can't start new thread"))
    with pytest.raises(HotkeyError):
        backend.start(BINDINGS)


def test_starting_with_no_bindings_is_an_error():
    backend, _clock, listeners = make_backend()
    with pytest.raises(HotkeyError, match="no hotkeys"):
        backend.start({})
    assert listeners == []


def test_starting_again_stops_the_previous_listener_first():
    backend, _clock, listeners = make_backend()
    backend.start(BINDINGS)
    backend.start(BINDINGS)
    assert len(listeners) == 2
    assert listeners[0].stop_calls == 1
    assert listeners[1].stop_calls == 0


def test_a_wayland_session_that_does_start_carries_a_caveat():
    backend, _clock, _listeners = make_backend(session=LinuxSession.WAYLAND, ready_at=0.0)
    backend.start(BINDINGS)
    assert "Wayland" in backend.note
    backend.stop()


def test_the_pynput_backend_drives_the_service_signals_end_to_end(qtbot, make_service):
    """Service + PynputHotkeyBackend + a fake listener: a press becomes a GUI-thread signal."""
    listeners: list[FakeListener] = []
    clock = VirtualClock()

    def listener_factory(bindings):
        listener = FakeListener(bindings, clock, ready_at=0.0)
        listeners.append(listener)
        return listener

    def backend_factory():
        return PynputHotkeyBackend(listener_factory, clock=clock, sleep=clock.sleep)

    service = HotkeyService(backend_factory=backend_factory)
    recorder = Recorder(service)
    try:
        service.apply(True, HOTKEYS)
        wait_for_state(qtbot, service, "active")

        fire_from_background_thread(listeners[0].bindings[HOTKEYS["stop"]])
        qtbot.waitUntil(lambda: bool(recorder.calls), timeout=3000)
        assert recorder.calls == [("stop", True)]
    finally:
        service.stop()
    assert listeners[0].stop_calls == 1


def test_the_default_factory_builds_a_real_but_unstarted_listener():
    # Constructing pynput's GlobalHotKeys only parses the hotkeys; nothing
    # touches the OS until start(). Skipped where pynput.keyboard cannot import.
    try:
        listener = hotkeys._default_listener_factory(
            {HOTKEYS["start"]: noop, HOTKEYS["pause"]: noop, HOTKEYS["stop"]: noop}
        )
    except ImportError as exc:  # pragma: no cover - a headless Linux box
        pytest.skip(f"pynput.keyboard is not importable here: {exc}")
    assert not listener.is_alive()
    assert not listener.running
