"""Tests for :mod:`auto_essay_typer.gui.hotkeys`.

No test here ever starts a real global-hotkey listener. The service is driven
through a fake backend, and :class:`PynputHotkeyBackend`'s polling logic is
driven through a fake *listener* with a virtual clock (so even the timeout
paths take no real time). The one test that touches real pynput only builds a
``GlobalHotKeys`` object and checks it was not started.

Hotkey presses are fired from genuine background threads, as pynput's listener
thread would, and the tests assert the Qt signal is delivered on the GUI thread.

The backend (and the listener inside it) is *built on the main thread*: on macOS a
pynput listener built or started anywhere else kills the whole process once a GUI
is running, with a SIGTRAP and no Python error, so a regression there would not
show up as a failing test on any other platform -- only as a crash for a user.
The tests that pin it record ``threading.current_thread()`` where the thing is built.
"""

from __future__ import annotations

import contextlib
import logging
import sys
import threading
import types

import pytest
from PySide6.QtCore import QObject

from auto_essay_typer.gui import hotkeys
from auto_essay_typer.gui.hotkeys import (
    HotkeyError,
    HotkeyService,
    PynputHotkeyBackend,
    hotkey_problem,
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
    """A HotkeyBackend that records what the service does to it, and on which thread."""

    def __init__(self, *, start_error=None, stop_error=None, gate=None, note="", prepare_error=None):
        self.built_on = threading.current_thread()
        self.start_error = start_error
        self.stop_error = stop_error
        self.prepare_error = prepare_error
        self.gate = gate
        self.note = note
        self.bindings: dict | None = None
        self.prepared_bindings: dict | None = None
        self.prepare_thread: threading.Thread | None = None
        self.start_entered = threading.Event()
        self.start_thread: threading.Thread | None = None
        self.started = False
        self.stop_calls = 0

    def prepare(self, bindings):
        self.prepare_thread = threading.current_thread()
        if self.prepare_error is not None:
            raise self.prepare_error
        self.prepared_bindings = dict(bindings)

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


def test_enabling_builds_the_backend_on_the_gui_thread_and_starts_it_on_a_worker(qtbot, make_service):
    service, factory, recorder = make_service()
    service.apply(True, HOTKEYS)

    assert service.state == "starting"
    wait_for_state(qtbot, service, "active")

    (backend,) = factory.built
    assert backend.started
    # Building (and preparing) the backend happens on the GUI thread; only the blocking start
    # runs on a worker, so a slow or hanging start can never freeze the window.
    assert factory.call_threads == [threading.main_thread()]
    assert backend.prepare_thread is threading.main_thread()
    assert backend.start_thread is not threading.main_thread()
    assert set(backend.bindings) == set(HOTKEYS.values())
    assert recorder.statuses == [("starting", ""), ("active", "")]


# --- the backend is built on the main thread ------------------------------------------------
#
# The macOS crash this guards against: a pynput listener built or started off the main thread
# makes the OS kill the process (SIGTRAP) once a GUI is running. Nothing in a test run on any
# other OS (or on macOS under the offscreen platform) would notice a regression.


class ConstructionRecordingBackend(FakeBackend):
    """A backend *class* that notes the thread its ``__init__`` ran on, as a real one's would."""

    instances: list["ConstructionRecordingBackend"] = []

    def __init__(self):
        super().__init__()
        type(self).instances.append(self)


def test_the_hotkey_backend_is_constructed_on_the_main_thread(qtbot):
    ConstructionRecordingBackend.instances = []
    service = HotkeyService(backend_factory=ConstructionRecordingBackend)
    try:
        service.apply(True, HOTKEYS)
        wait_for_state(qtbot, service, "active")
    finally:
        service.stop()

    (backend,) = ConstructionRecordingBackend.instances
    assert backend.built_on is threading.main_thread()


def test_the_listener_is_prepared_on_the_main_thread_and_only_started_on_a_worker(qtbot):
    ConstructionRecordingBackend.instances = []
    service = HotkeyService(backend_factory=ConstructionRecordingBackend)
    try:
        service.apply(True, HOTKEYS)
        wait_for_state(qtbot, service, "active")
    finally:
        service.stop()

    (backend,) = ConstructionRecordingBackend.instances
    assert backend.prepare_thread is threading.main_thread()  # where a real listener is built
    assert backend.start_thread is not threading.main_thread()  # where it may block
    # The listener thread's callbacks (here: the bindings) are the ones the service made.
    assert backend.prepared_bindings == backend.bindings


def test_every_rebuild_also_happens_on_the_main_thread(qtbot, make_service):
    service, factory, _recorder = make_service()
    service.apply(True, HOTKEYS)
    wait_for_state(qtbot, service, "active")
    service.apply(True, OTHER_HOTKEYS)
    qtbot.waitUntil(lambda: len(factory.built) == 2 and service.state == "active", timeout=3000)
    service.retry()  # forgets the configuration, so this builds a third
    qtbot.waitUntil(lambda: len(factory.built) == 3 and service.state == "active", timeout=3000)
    service.apply(False, HOTKEYS)
    service.apply(True, HOTKEYS)  # and toggling off and on a fourth
    qtbot.waitUntil(lambda: len(factory.built) == 4 and service.state == "active", timeout=3000)

    assert factory.call_threads == [threading.main_thread()] * 4
    assert all(backend.built_on is threading.main_thread() for backend in factory.built)
    assert all(backend.prepare_thread is threading.main_thread() for backend in factory.built)


def test_apply_called_from_another_thread_still_builds_on_the_gui_thread(qtbot, make_service):
    service, factory, _recorder = make_service()

    fire_from_background_thread(lambda: service.apply(True, HOTKEYS))  # queued, not built here
    assert factory.built == []
    wait_for_state(qtbot, service, "active")

    assert factory.call_threads == [threading.main_thread()]
    assert factory.built[0].prepare_thread is threading.main_thread()


def test_a_backend_without_a_prepare_step_still_works(qtbot):
    class Plain:
        def __init__(self):
            self.started = False
            self.stopped = 0

        def start(self, bindings):
            self.started = True

        def stop(self):
            self.stopped += 1

    built: list[Plain] = []

    def factory():
        built.append(Plain())
        return built[-1]

    service = HotkeyService(backend_factory=factory)
    try:
        service.apply(True, HOTKEYS)
        wait_for_state(qtbot, service, "active")
    finally:
        service.stop()
    assert built[0].started and built[0].stopped == 1


def test_a_prepare_that_fails_makes_the_service_unavailable_without_starting_anything(make_service):
    failing = FakeBackend(prepare_error=HotkeyError("A hotkey is not a valid shortcut (<bogus>)"))
    service, factory, recorder = make_service(failing)

    service.apply(True, HOTKEYS)  # synchronous: nothing to wait for, no worker was started

    assert service.state == "unavailable"
    assert service.detail == "A hotkey is not a valid shortcut (<bogus>)"
    assert recorder.statuses == [("starting", ""), ("unavailable", service.detail)]
    assert failing.start_entered.is_set() is False
    assert failing.stop_calls == 1  # the half-built backend is cleaned up
    assert failing.prepare_thread is threading.main_thread()


def test_an_unexpected_prepare_exception_is_contained_and_logged(make_service, caplog):
    caplog.set_level(logging.DEBUG, logger="auto_essay_typer.gui.hotkeys")
    service, _factory, _recorder = make_service(FakeBackend(prepare_error=RuntimeError("layout lookup failed")))

    service.apply(True, HOTKEYS)

    assert service.state == "unavailable"
    assert "layout lookup failed" in service.detail
    assert any(record.levelno >= logging.ERROR for record in caplog.records)


def test_a_start_that_was_superseded_before_its_thread_began_never_starts_the_backend(
    qapp, make_service, monkeypatch
):
    service, factory, _recorder = make_service()
    release_worker = threading.Event()
    real_worker = service._start_worker

    def worker_held_back(generation, backend, bindings):
        assert release_worker.wait(5), "the test never released the worker"
        real_worker(generation, backend, bindings)

    monkeypatch.setattr(service, "_start_worker", worker_held_back)

    service.apply(True, HOTKEYS)
    service.stop()  # the worker has not run a single line yet
    release_worker.set()
    drain(qapp)

    (backend,) = factory.built
    assert backend.start_entered.is_set() is False  # it never started listening...
    assert backend.stop_calls == 1  # ...and the half-built backend was cleaned up
    assert service.state == "off"


def test_a_worker_thread_that_cannot_be_started_makes_the_service_unavailable_not_stuck(make_service, monkeypatch):
    service, factory, recorder = make_service()

    def cannot_start(*_args):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(service, "_spawn_start_worker", cannot_start)

    service.apply(True, HOTKEYS)  # must not raise

    assert service.state == "unavailable"  # and not left at "starting" forever
    assert "can't start new thread" in service.detail
    assert recorder.statuses[-1][0] == "unavailable"
    (backend,) = factory.built
    assert backend.stop_calls == 1  # the listener that was built for it is dropped


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


# --- hotkey_problem: a hotkey pynput can parse but that would fire while you type -----------------


@pytest.mark.parametrize(
    "text",
    [
        "<ctrl>+<alt>+<shift>+t",  # the defaults
        "<cmd>+<alt>+<shift>+p",
        "<esc>",  # the default Stop
        "<f9>",  # function keys are fine alone
        "<f20>",
        "<media_play_pause>",
        "<ctrl>+t",
        "<alt>+1",
        "<cmd>+<shift>+/",
        "<ctrl>+<shift>+<space>",
        "<ctrl>++",  # a literal plus
        "<shift>+<f5>",
    ],
)
def test_a_sensible_hotkey_has_no_problem(text):
    assert hotkey_problem(text) is None


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("t", "modifier key"),  # would fire on every t typed, in every app
        ("5", "modifier key"),
        ("<space>", "modifier key"),
        ("a+b", "modifier key"),
        ("<shift>+t", "Shift on its own"),  # that is how a capital T is typed
        ("<shift>+<space>", "Shift on its own"),
        ("<ctrl>+<alt>", "to go with the modifier keys"),
        ("<shift>", "to go with the modifier keys"),
        ("<ctrl_r>+t", "Write <ctrl> instead of <ctrl_r>"),
        ("<alt_gr>+t", "Write <alt> instead of <alt_gr>"),
        ("<cmd_l>+<shift>+t", "Write <cmd> instead of <cmd_l>"),
    ],
)
def test_a_hotkey_that_would_fire_while_typing_is_explained(text, fragment):
    assert fragment in hotkey_problem(text)


@pytest.mark.parametrize(
    ("hotkeys", "message"),
    [
        ({**HOTKEYS, "start": "t"}, r"The Start hotkey \(t\): Add a modifier key"),
        ({**HOTKEYS, "pause": "<ctrl>+<alt>"}, r"The Pause hotkey .*go with the modifier keys"),
        ({**HOTKEYS, "stop": "<shift>+x"}, r"The Stop hotkey .*Shift on its own"),
        ({**HOTKEYS, "start": "<bogus>+t"}, r"The Start hotkey \(<bogus>\+t\) is not a valid shortcut"),
        # One hotkey containing all of another's keys would trigger both when pressed.
        (
            {"start": "<ctrl>+t", "pause": "<ctrl>+<shift>+t", "stop": "<esc>"},
            r"The Pause hotkey \(<ctrl>\+<shift>\+t\) contains every key of the Start hotkey",
        ),
        (
            {"start": "<f9>", "pause": "<ctrl>+<f9>", "stop": "<esc>"},
            r"The Pause hotkey \(<ctrl>\+<f9>\) contains every key of the Start hotkey \(<f9>\)",
        ),
    ],
)
def test_validated_hotkeys_refuses_hotkeys_that_would_misfire(hotkeys, message):
    with pytest.raises(ValueError, match=message):
        validated_hotkeys(hotkeys)


def test_validated_hotkeys_sees_the_same_keys_written_differently_as_the_same_hotkey():
    # Both are Ctrl+Alt+Shift+T: a different order and a different case.
    with pytest.raises(ValueError, match="Start and Pause use the same hotkey"):
        validated_hotkeys({**HOTKEYS, "pause": "T+<shift>+<alt>+<ctrl>"})


def test_a_service_given_a_hotkey_that_would_misfire_is_unavailable_and_starts_nothing(make_service):
    service, factory, _recorder = make_service()

    service.apply(True, {**HOTKEYS, "start": "t"})  # e.g. from a hand-edited settings file

    assert service.state == "unavailable"
    assert "Add a modifier key" in service.detail
    assert factory.built == []  # nothing was built, so nothing is listening to every t typed


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


def test_a_listener_that_cannot_be_built_on_macos_is_not_blamed_on_permissions(caplog):
    # Permission trouble shows up later, when the event tap is created. A listener that cannot even
    # be built is a different fault, and "needs Accessibility access" would send the user to fix
    # the wrong thing -- so the real reason is in the message, and the traceback in the log.
    def factory(bindings):
        raise RuntimeError("keyboard layout lookup failed")

    backend = PynputHotkeyBackend(factory, os_provider=lambda: OperatingSystem.MACOS)

    with caplog.at_level(logging.WARNING, logger="auto_essay_typer.gui.hotkeys"):
        with pytest.raises(HotkeyError) as excinfo:
            backend.prepare(BINDINGS)

    assert "RuntimeError: keyboard layout lookup failed" in str(excinfo.value)
    assert "Accessibility" not in str(excinfo.value)
    assert any(record.levelno == logging.WARNING and record.exc_info for record in caplog.records)


def test_a_listener_is_never_built_off_the_main_thread_on_macos():
    # Without prepare() having built it on the main thread, start() would build it on whichever
    # thread called it, and on macOS building it anywhere but the main thread kills the process.
    backend, _clock, listeners = make_backend(os_=OperatingSystem.MACOS)
    errors: list[HotkeyError] = []

    def start_from_a_worker():
        try:
            backend.start(BINDINGS)
        except HotkeyError as exc:
            errors.append(exc)

    worker = threading.Thread(target=start_from_a_worker, name="fake-start-worker")
    worker.start()
    worker.join(5)

    assert len(errors) == 1 and "main thread" in str(errors[0])
    assert listeners == []  # nothing was built


def test_a_prepared_listener_may_be_started_from_a_worker_on_macos():
    backend, _clock, listeners = make_backend(os_=OperatingSystem.MACOS)
    backend.prepare(BINDINGS)  # on the main thread, as the service does
    failures: list[BaseException] = []

    def start_from_a_worker():
        try:
            backend.start(BINDINGS)
        except BaseException as exc:  # noqa: BLE001 - reported below
            failures.append(exc)

    worker = threading.Thread(target=start_from_a_worker, name="fake-start-worker")
    worker.start()
    worker.join(5)

    assert failures == [] and len(listeners) == 1 and listeners[0].started
    backend.stop()


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


# --- PynputHotkeyBackend: prepare() builds the listener, start() only starts it ---------------


def test_prepare_builds_the_listener_without_starting_it_and_start_reuses_it():
    backend, _clock, listeners = make_backend()

    backend.prepare(BINDINGS)
    assert len(listeners) == 1
    assert listeners[0].started is False  # built, not listening
    assert listeners[0].bindings == BINDINGS

    backend.start(BINDINGS)
    assert len(listeners) == 1  # no second listener was built
    assert listeners[0].started is True
    backend.stop()
    assert listeners[0].stop_calls == 1


def test_prepare_runs_the_listener_factory_on_the_calling_thread():
    threads: list[threading.Thread] = []
    clock = VirtualClock()

    def factory(bindings):
        threads.append(threading.current_thread())
        return FakeListener(bindings, clock)

    backend = PynputHotkeyBackend(factory, clock=clock, sleep=clock.sleep)

    backend.prepare(BINDINGS)  # called from the test's main thread, as the service does

    assert threads == [threading.main_thread()]


def test_start_builds_a_different_listener_when_the_bindings_changed_since_prepare():
    backend, _clock, listeners = make_backend()
    other = {**BINDINGS, HOTKEYS["stop"]: lambda: None}

    backend.prepare(BINDINGS)
    backend.start(other)

    assert len(listeners) == 2
    assert listeners[0].started is False  # the prepared one was never used
    assert listeners[1].started is True
    assert listeners[1].bindings == other
    backend.stop()


def test_stopping_drops_a_prepared_listener_that_never_started():
    backend, _clock, listeners = make_backend()
    backend.prepare(BINDINGS)

    backend.stop()  # nothing is running: the unstarted listener has no thread to stop or join
    assert listeners[0].stop_calls == 0
    assert listeners[0].join_timeouts == []

    backend.start(BINDINGS)  # and a later start builds a fresh one
    assert len(listeners) == 2 and listeners[1].started
    backend.stop()


def test_prepare_reports_hotkeys_pynput_rejects_and_a_missing_library():
    def invalid(bindings):
        raise ValueError("'<bogus>' is not a key")

    def missing(bindings):
        raise ModuleNotFoundError("No module named 'pynput'", name="pynput")

    with pytest.raises(HotkeyError, match="not a valid shortcut"):
        PynputHotkeyBackend(invalid).prepare(BINDINGS)
    with pytest.raises(HotkeyError, match="not installed"):
        PynputHotkeyBackend(missing).prepare(BINDINGS)


def test_prepare_with_no_bindings_is_an_error():
    backend, _clock, listeners = make_backend()
    with pytest.raises(HotkeyError, match="no hotkeys"):
        backend.prepare({})
    assert listeners == []


def test_the_default_backend_through_the_service_builds_its_listener_on_the_main_thread(qtbot):
    """The whole chain -- service, real PynputHotkeyBackend, listener factory -- with a fake listener.

    Records where the listener is *constructed* (the thing macOS cares about) and where it is
    *started*, with nothing real installed.
    """
    constructed_on: list[threading.Thread] = []
    started_on: list[threading.Thread] = []
    clock = VirtualClock()

    class RecordingListener(FakeListener):
        def __init__(self, bindings):
            constructed_on.append(threading.current_thread())
            super().__init__(bindings, clock, ready_at=0.0)

        def start(self):
            started_on.append(threading.current_thread())
            super().start()

    service = HotkeyService(
        backend_factory=lambda: PynputHotkeyBackend(RecordingListener, clock=clock, sleep=clock.sleep)
    )
    try:
        service.apply(True, HOTKEYS)
        wait_for_state(qtbot, service, "active")
    finally:
        service.stop()

    assert constructed_on == [threading.main_thread()]
    assert started_on and threading.main_thread() not in started_on


# --- the macOS listener -------------------------------------------------------------------------
#
# pynput's darwin Listener._run reads the keyboard layout (Text Services) on the listener's own
# thread, which macOS answers with SIGTRAP once a GUI is running -- even for a listener that was
# built on the main thread. These tests replace the OS call and the event tap with recorders, so
# nothing real runs; they pin *where* the layout is read.

darwin_only = pytest.mark.skipif(
    sys.platform != "darwin", reason="the macOS listener needs pynput's Quartz backend"
)


#: What the fake keyboard layout makes of a few key codes with no modifier held (the US layout's).
FAKE_LAYOUT = {0x11: "t", 0x23: "p", 0x00: "a", 0x1D: "0"}


@pytest.fixture
def fake_layout(monkeypatch):
    """pynput's key-code-to-character lookup, replaced by a recorder over :data:`FAKE_LAYOUT`.

    ``calls`` collects ``(thread, context, key code, modifier state)``; set ``error`` to make it raise.
    """
    from pynput._util import darwin as pynput_darwin

    layout = types.SimpleNamespace(table=dict(FAKE_LAYOUT), calls=[], error=None)

    def fake_keycode_to_string(context, keycode, modifier_state=0):
        layout.calls.append((threading.current_thread(), context, keycode, modifier_state))
        if layout.error is not None:
            raise layout.error
        return layout.table.get(keycode, "")

    monkeypatch.setattr(pynput_darwin, "keycode_to_string", fake_keycode_to_string)
    return layout


@pytest.fixture
def macos_listener(monkeypatch, fake_layout):
    """``(listener_class, layout_reads, run_calls)`` with pynput's OS-touching parts faked."""
    from pynput._util import darwin as pynput_darwin

    layout_reads: list[threading.Thread] = []
    run_calls: list[tuple[threading.Thread, object]] = []

    @contextlib.contextmanager
    def fake_keycode_context():
        layout_reads.append(threading.current_thread())
        yield ("fake keyboard type", b"fake layout bytes")

    def fake_event_tap_run(self):
        run_calls.append((threading.current_thread(), self._context))

    monkeypatch.setattr(pynput_darwin, "keycode_context", fake_keycode_context)
    monkeypatch.setattr(pynput_darwin.ListenerMixin, "_run", fake_event_tap_run)
    hotkeys._macos_global_hotkeys_class.cache_clear()  # rebuild against the fakes above
    try:
        yield hotkeys._macos_global_hotkeys_class(), layout_reads, run_calls
    finally:
        hotkeys._macos_global_hotkeys_class.cache_clear()  # never leave a class bound to fakes


@darwin_only
def test_the_macos_listener_reads_the_keyboard_layout_where_it_is_built(macos_listener):
    listener_class, layout_reads, _run_calls = macos_listener

    listener_class({HOTKEYS["start"]: noop})  # built on the main thread, as the service does

    assert layout_reads == [threading.main_thread()]


@darwin_only
def test_the_macos_listener_thread_never_asks_the_os_for_the_keyboard_layout(macos_listener):
    listener_class, layout_reads, run_calls = macos_listener
    listener = listener_class({HOTKEYS["start"]: noop})  # one read, on this thread

    runner = threading.Thread(target=listener._run, name="fake-listener-thread")  # its own thread
    runner.start()
    runner.join(5)
    assert not runner.is_alive()

    assert layout_reads == [threading.main_thread()]  # still only the construction-time read
    (ran_on, context_during_run) = run_calls[0]
    assert ran_on is not threading.main_thread()
    # ...and the run used the layout read at construction, exactly as the stock _run would have.
    assert context_during_run == ("fake keyboard type", b"fake layout bytes")
    assert listener._context is None  # cleared again afterwards, like the stock _run


@darwin_only
def test_the_macos_listener_is_a_global_hotkeys_listener(macos_listener):
    from pynput.keyboard import GlobalHotKeys

    listener_class, _reads, _runs = macos_listener
    listener = listener_class({HOTKEYS["start"]: noop, HOTKEYS["stop"]: noop})
    assert isinstance(listener, GlobalHotKeys)
    assert not listener.is_alive()  # nothing was started


@darwin_only
def test_the_default_factory_uses_the_macos_listener_on_macos_and_the_stock_one_elsewhere(
    macos_listener, monkeypatch
):
    from pynput.keyboard import GlobalHotKeys

    from auto_essay_typer import platforms

    listener_class, _reads, _runs = macos_listener
    bindings = {HOTKEYS["start"]: noop}

    monkeypatch.setattr(platforms, "current_os", lambda *a, **k: OperatingSystem.MACOS)
    assert type(hotkeys._default_listener_factory(bindings)) is listener_class

    monkeypatch.setattr(platforms, "current_os", lambda *a, **k: OperatingSystem.LINUX)
    assert type(hotkeys._default_listener_factory(bindings)) is GlobalHotKeys


@darwin_only
def test_the_macos_workaround_still_targets_what_pynputs_listener_does():
    """A tripwire: the override exists because pynput's ``_run`` wraps the event tap in ``keycode_context``.

    If a pynput upgrade stops doing that, the override is dead weight (or, worse, skips something
    new) -- this fails so someone looks, instead of the app silently diverging from pynput.
    """
    import inspect

    from pynput._util.darwin import ListenerMixin
    from pynput.keyboard import GlobalHotKeys, Listener

    assert "keycode_context" in inspect.getsource(Listener._run)
    assert GlobalHotKeys._run is Listener._run  # GlobalHotKeys inherits it unchanged
    assert callable(ListenerMixin._run)


# --- the macOS listener names a pressed letter by its key, not by what Option made of it ------------------
#
# pynput names a pressed letter by the character the key *event* carries, and Option changes that:
# Cmd+Option+Shift+T arrives as the key code of T with the character 'ˇ'. The default hotkeys are of that
# shape, so without the override in hotkeys.py they could never fire. These tests feed the listener the
# events it would receive (never starting it) and check which chord completes.

OPTION_SHIFT_T = "ˇ"  # what Option+Shift+T types on a US layout


def _press(listener, *keys):
    for key in keys:
        listener._on_press(key, False)


def _release(listener, *keys):
    for key in keys:
        listener._on_release(key, False)


@darwin_only
def test_a_chord_with_option_completes_although_the_event_carries_the_option_character(macos_listener):
    from pynput.keyboard import Key, KeyCode

    listener_class, _reads, _runs = macos_listener
    fired: list[str] = []
    listener = listener_class({"<cmd>+<alt>+<shift>+t": lambda: fired.append("start")})

    _press(listener, Key.cmd, Key.alt, Key.shift, KeyCode.from_char(OPTION_SHIFT_T, vk=0x11))

    assert fired == ["start"]


@darwin_only
def test_stock_pynput_would_miss_that_chord(macos_listener):
    # The reason the override exists, kept as a test so the reason is not forgotten.
    from pynput.keyboard import GlobalHotKeys, Key, KeyCode

    fired: list[str] = []
    stock = GlobalHotKeys({"<cmd>+<alt>+<shift>+t": lambda: fired.append("start")})

    _press(stock, Key.cmd, Key.alt, Key.shift, KeyCode.from_char(OPTION_SHIFT_T, vk=0x11))

    assert fired == []


@darwin_only
def test_the_chord_can_be_pressed_again_after_it_is_let_go_in_any_order(macos_listener):
    from pynput.keyboard import Key, KeyCode

    listener_class, _reads, _runs = macos_listener
    fired: list[str] = []
    listener = listener_class({"<cmd>+<alt>+<shift>+t": lambda: fired.append("start")})
    letter = KeyCode.from_char(OPTION_SHIFT_T, vk=0x11)

    _press(listener, Key.cmd, Key.alt, Key.shift, letter)
    _release(listener, Key.alt, KeyCode.from_vk(0x11), Key.cmd, Key.shift)  # the key-up may carry no character
    _press(listener, Key.cmd, Key.alt, Key.shift, letter)

    assert fired == ["start", "start"]


@darwin_only
def test_two_chords_on_the_same_modifiers_each_fire_only_their_own_callback(macos_listener):
    from pynput.keyboard import Key, KeyCode

    listener_class, _reads, _runs = macos_listener
    fired: list[str] = []
    listener = listener_class(
        {
            "<cmd>+<alt>+<shift>+t": lambda: fired.append("start"),
            "<cmd>+<alt>+<shift>+p": lambda: fired.append("pause"),
        }
    )

    _press(listener, Key.cmd, Key.alt, Key.shift, KeyCode.from_char("∏", vk=0x23))  # Option+Shift+P

    assert fired == ["pause"]


@darwin_only
def test_a_key_is_translated_with_the_layout_read_at_construction_and_no_modifiers(macos_listener, fake_layout):
    from pynput.keyboard import KeyCode

    listener_class, _reads, _runs = macos_listener
    listener = listener_class({HOTKEYS["start"]: noop})

    named = listener.canonical(KeyCode.from_char(OPTION_SHIFT_T, vk=0x11))

    assert named == KeyCode.from_char("t")
    ((_thread, context, key_code, modifier_state),) = fake_layout.calls
    assert context == ("fake keyboard type", b"fake layout bytes")
    assert (key_code, modifier_state) == (0x11, 0)


@darwin_only
def test_the_translation_works_on_the_listener_thread(macos_listener, fake_layout):
    from pynput.keyboard import KeyCode

    listener_class, _reads, _runs = macos_listener
    listener = listener_class({HOTKEYS["start"]: noop})
    named: list[object] = []

    thread = threading.Thread(
        target=lambda: named.append(listener.canonical(KeyCode.from_char(OPTION_SHIFT_T, vk=0x11))),
        name="fake-listener-thread",
    )
    thread.start()
    thread.join(5)

    assert named == [KeyCode.from_char("t")]
    assert fake_layout.calls[0][0] is not threading.main_thread()


@darwin_only
def test_a_key_the_layout_has_no_character_for_is_named_the_way_pynput_names_it(macos_listener, fake_layout):
    from pynput.keyboard import KeyCode

    listener_class, _reads, _runs = macos_listener
    listener = listener_class({HOTKEYS["start"]: noop})

    assert listener.canonical(KeyCode.from_char("Z", vk=0x7F)) == KeyCode.from_char("z")  # just lower-cased
    assert listener.canonical(KeyCode.from_vk(0x7F)) == KeyCode.from_vk(0x7F)


@darwin_only
def test_a_layout_lookup_that_fails_never_reaches_the_listener_thread(macos_listener, fake_layout):
    from pynput.keyboard import KeyCode

    listener_class, _reads, _runs = macos_listener
    listener = listener_class({HOTKEYS["start"]: noop})
    fake_layout.error = RuntimeError("the layout went away")

    # An exception here would end the listener for good; the key is named as pynput would instead.
    assert listener.canonical(KeyCode.from_char("T", vk=0x11)) == KeyCode.from_char("t")


@darwin_only
def test_without_layout_data_nothing_is_translated(macos_listener, fake_layout):
    from pynput.keyboard import KeyCode

    listener_class, _reads, _runs = macos_listener
    listener = listener_class({HOTKEYS["start"]: noop})
    listener._main_thread_context = ("fake keyboard type", None)

    assert listener.canonical(KeyCode.from_char("T", vk=0x11)) == KeyCode.from_char("t")
    assert fake_layout.calls == []


@darwin_only
def test_special_keys_and_modifiers_are_left_to_pynput(macos_listener, fake_layout):
    from pynput.keyboard import GlobalHotKeys, Key

    listener_class, _reads, _runs = macos_listener
    listener = listener_class({HOTKEYS["start"]: noop})
    stock = GlobalHotKeys({HOTKEYS["start"]: noop})

    for key in (Key.esc, Key.f9, Key.space, Key.cmd, Key.alt_r, Key.shift_r, Key.ctrl):
        assert listener.canonical(key) == stock.canonical(key), key
    assert fake_layout.calls == []  # none of them is a character key


@darwin_only
def test_the_chord_fix_still_targets_how_pynput_names_a_pressed_letter():
    """A tripwire, like the one above: ``canonical`` relies on the key event's key code being kept.

    pynput builds the pressed key from the character in the event and keeps the key code beside it
    (``vk=vk``). If an upgrade changes that, the override would translate nothing, quietly.
    """
    import inspect

    from pynput.keyboard import Listener

    source = inspect.getsource(Listener._event_to_key)
    assert "CGEventKeyboardGetUnicodeString" in source  # the character comes from the event, Option and all
    assert "vk=vk" in source  # and the key code is kept alongside it


@darwin_only
def test_with_the_real_keyboard_layout_every_character_key_is_named_by_its_unmodified_character():
    """No fakes: this machine's layout bytes and the real ``UCKeyTranslate``.

    Whatever character an event carries (here the Option+Shift+T one, for every key), the key is named
    by what it types with nothing held -- so this holds on any layout, not just the US one.
    """
    from pynput._util.darwin import keycode_to_string
    from pynput.keyboard import KeyCode

    hotkeys._macos_global_hotkeys_class.cache_clear()
    try:
        listener = hotkeys._macos_global_hotkeys_class()({HOTKEYS["start"]: noop})  # reads the real layout, here
        _keyboard_type, layout = listener._main_thread_context
        if not layout:
            pytest.skip("this machine has no keyboard layout data to translate with")

        checked = 0
        for key_code in range(128):
            plain = keycode_to_string(listener._main_thread_context, key_code)
            if len(plain) == 1 and plain.isprintable() and not plain.isspace():
                carried = KeyCode.from_char(OPTION_SHIFT_T, vk=key_code)
                assert listener.canonical(carried) == KeyCode.from_char(plain.lower()), key_code
                checked += 1
        assert checked >= 26  # at the very least the letters
    finally:
        hotkeys._macos_global_hotkeys_class.cache_clear()
