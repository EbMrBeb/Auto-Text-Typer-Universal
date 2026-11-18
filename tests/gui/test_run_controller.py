"""Tests for RunController's lifecycle, threading, and pause/resume/stop.

Two styles of test are used deliberately:

* A **fake driver double** (``_FakeDriver``) for the pause/resume/stop
  *control logic* -- state transitions, the resume countdown, cancelling a
  pending countdown -- so those are deterministic and fast, with no real
  background thread or timing race involved.
* A **real Driver + RecordingKeyboard** (via the ``recording_driver_factory``
  fixture) for end-to-end tests that prove the actual threading and Driver
  integration work: planning, typing, progress, and a real pause/resume and
  a real stop. Where a test needs the run to still be in flight when it
  intervenes, the injected ``sleep`` does a small *real* sleep per slice
  (independent of the planned, randomly-sampled pause durations) so the run
  is reliably still "typing" when the test's main thread acts -- bounded and
  fast, never an open-ended block.
"""

from __future__ import annotations

import time

import pytest

from auto_essay_typer.gui.run_controller import RunController
from auto_essay_typer.profiles import Profile


class _FakeDriver:
    """A minimal Driver double for testing RunController's own control logic."""

    def __init__(self) -> None:
        self.paused = False
        self.resumed_calls = 0
        self.aborted = False

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.resumed_calls += 1
        self.paused = False

    def abort(self) -> None:
        self.aborted = True


def _controller_with_fake_driver(state: str) -> tuple[RunController, _FakeDriver]:
    controller = RunController(driver_factory=lambda: _FakeDriver())
    fake = _FakeDriver()
    controller._driver = fake  # simulate "a run is in flight" without a real thread
    controller._state = state
    return controller, fake


# --- real end-to-end runs (planning + Driver + RecordingKeyboard) ------------


def test_start_runs_to_completion_and_types_the_text(qtbot, recording_driver_factory):
    factory, keyboards = recording_driver_factory
    controller = RunController(driver_factory=factory)

    states: list[str] = []
    controller.stateChanged.connect(states.append)

    with qtbot.waitSignal(controller.finished, timeout=5000) as blocker:
        controller.start("Hello there.", Profile(), seed=1)

    outcome, message = blocker.args
    assert outcome == "finished"
    assert message
    assert keyboards[0].text == "Hello there."
    assert controller.state == "finished"
    assert controller.is_busy is False

    # planning -> ... -> typing -> finished, in that relative order (countdown
    # is skipped entirely with countdown_seconds=0, so it must never appear).
    assert "planning" in states
    assert "typing" in states
    assert states.index("planning") < states.index("typing") < states.index("finished")
    assert "countdown" not in states


def test_progress_reaches_100_percent(qtbot, recording_driver_factory):
    factory, _keyboards = recording_driver_factory
    controller = RunController(driver_factory=factory)

    progress_updates: list[tuple[int, int, float, float]] = []
    controller.progressChanged.connect(lambda *args: progress_updates.append(args))

    with qtbot.waitSignal(controller.finished, timeout=5000):
        controller.start("abcdefghij", Profile(), seed=2)

    assert progress_updates, "expected at least one progressChanged signal"
    last_done, last_total, last_eta, _elapsed = progress_updates[-1]
    assert last_done == last_total == 10
    assert last_eta == pytest.approx(0.0, abs=1e-6)


def test_start_is_a_no_op_while_busy(qtbot, recording_driver_factory):
    factory, keyboards = recording_driver_factory
    controller = RunController(driver_factory=factory)

    controller._busy = True  # simulate "a run is already in flight"
    controller.start("should be ignored", Profile(), seed=3)
    qtbot.wait(50)
    assert not keyboards


def test_stop_before_anything_started_does_not_raise(qtbot):
    controller = RunController(driver_factory=lambda: None)  # never called
    controller.stop()  # must not raise


def test_real_pause_and_resume_round_trip(qtbot, recording_driver_factory):
    from auto_essay_typer.driver import Driver, RecordingKeyboard

    keyboard = RecordingKeyboard()
    # A small *real* sleep per slice, independent of the profile's own
    # randomly-sampled pause durations, so the run is slow enough to reliably
    # still be "typing" when this test calls pause() -- bounded (a few
    # hundred slices at most for this short text) rather than open-ended.
    driver = Driver(keyboard=keyboard, sleep=lambda _s: time.sleep(0.003), countdown_seconds=0)
    controller = RunController(driver_factory=lambda: driver)

    controller.start("hello world", Profile(wpm=65.0, typo_rate_per_100=0.0), seed=6)
    qtbot.waitUntil(lambda: controller.state == "typing", timeout=5000)

    controller.pause()
    assert controller.state == "paused"
    typed_at_pause = keyboard.text
    qtbot.wait(30)
    assert keyboard.text == typed_at_pause, "no more keys should land while paused"

    ticks: list[int] = []
    controller.countdownTick.connect(ticks.append)
    controller.resume(countdown_seconds=2)
    assert controller.state == "resuming"

    qtbot.waitUntil(lambda: controller.state == "typing", timeout=5000)
    assert ticks == [2, 1]

    qtbot.waitSignal(controller.finished, timeout=5000).wait()
    assert keyboard.text == "hello world"


def test_stop_mid_run_aborts_and_leaves_a_valid_prefix(qtbot):
    from auto_essay_typer.driver import Driver, RecordingKeyboard

    keyboard = RecordingKeyboard()
    full_text = "a fairly long piece of text to type out slowly enough to interrupt"
    driver = Driver(keyboard=keyboard, sleep=lambda _s: time.sleep(0.003), countdown_seconds=0)
    controller = RunController(driver_factory=lambda: driver)

    # No typos/revisions: guarantees whatever is on screen at an arbitrary
    # abort point is a clean prefix of the input, not a not-yet-corrected typo.
    clean_profile = Profile(typo_rate_per_100=0.0, revision_rate_per_word=0.0)

    with qtbot.waitSignal(controller.finished, timeout=5000) as blocker:
        controller.start(full_text, clean_profile, seed=7)
        qtbot.waitUntil(lambda: controller.state == "typing", timeout=5000)
        controller.stop()

    outcome, _message = blocker.args
    assert outcome == "stopped"
    assert controller.state == "stopped"
    assert full_text.startswith(keyboard.text)


# --- pause/resume/stop control logic (fake driver, no real thread) -----------


def test_pause_sets_state_and_calls_driver_pause(qtbot):
    controller, fake = _controller_with_fake_driver("typing")
    controller.pause()
    assert fake.paused is True
    assert controller.state == "paused"


def test_pause_is_a_no_op_unless_currently_typing(qtbot):
    controller, fake = _controller_with_fake_driver("paused")
    controller.pause()
    assert fake.paused is False
    assert controller.state == "paused"


def test_resume_runs_a_countdown_then_calls_driver_resume(qtbot):
    controller, fake = _controller_with_fake_driver("paused")

    ticks: list[int] = []
    controller.countdownTick.connect(ticks.append)
    controller.resume(countdown_seconds=2)

    assert controller.state == "resuming"
    assert fake.resumed_calls == 0  # countdown still running -- not resumed yet

    qtbot.waitUntil(lambda: controller.state == "typing", timeout=3000)
    assert fake.resumed_calls == 1
    assert ticks == [2, 1]


def test_resume_with_zero_countdown_resumes_immediately(qtbot):
    controller, fake = _controller_with_fake_driver("paused")
    controller.resume(countdown_seconds=0)
    assert fake.resumed_calls == 1
    assert controller.state == "typing"


def test_resume_is_a_no_op_unless_currently_paused(qtbot):
    controller, fake = _controller_with_fake_driver("typing")
    controller.resume()
    assert fake.resumed_calls == 0
    assert controller.state == "typing"


def test_stop_cancels_a_pending_resume_countdown(qtbot):
    controller, fake = _controller_with_fake_driver("paused")
    controller.resume(countdown_seconds=5)
    assert controller.state == "resuming"

    controller.stop()
    assert fake.aborted is True

    qtbot.wait(1100)  # longer than one tick interval, if the timer were still running
    assert fake.resumed_calls == 0, "the countdown must have been cancelled, not merely raced"


def test_statechanged_does_not_repeat_the_same_state(qtbot):
    controller, _fake = _controller_with_fake_driver("typing")
    states: list[str] = []
    controller.stateChanged.connect(states.append)
    controller.pause()
    controller.pause()  # already paused -- must not emit "paused" again
    assert states == ["paused"]
