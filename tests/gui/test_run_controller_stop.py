"""A Stop pressed while a run is being prepared is never lost.

``Driver.run`` clears any abort that was requested before it started, so a Stop that arrives
after the controller last looked (``RunController._run_worker`` checks once, after planning) but
before ``run`` has cleared the flag used to vanish, and the whole run was typed anyway. The window
is a few milliseconds for a paragraph and grows with the size of a document. These tests put the
user's Stop exactly inside it and require the run to stop.
"""

from __future__ import annotations

from pathlib import Path

from auto_essay_typer import targets
from auto_essay_typer.driver import Driver, RecordingKeyboard
from auto_essay_typer.gui.document_session import DocumentSession
from auto_essay_typer.gui.run_controller import RunController
from auto_essay_typer.importers import load_document
from auto_essay_typer.platforms import OperatingSystem
from auto_essay_typer.profiles import Profile

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "gdocs_export_sample.html"
CLEAN = Profile(typo_rate_per_100=0.0, revision_rate_per_word=0.0)
TEXT = "A sentence long enough that a run which ignored its Stop would visibly type all of it."


class _StopPressedDuringPreflight(Driver):
    """A Driver whose pre-flight check is the moment the user happens to press Stop."""

    on_preflight = None

    def _check_keymap_covers(self, actions):
        super()._check_keymap_covers(actions)
        if self.on_preflight is not None:
            self.on_preflight()


def _controller_whose_user_presses_stop_during_preflight(countdown_seconds: int = 0):
    keyboard = RecordingKeyboard()
    box: dict[str, RunController] = {}

    def factory(keymap=None) -> Driver:
        driver = _StopPressedDuringPreflight(
            keyboard=keyboard, sleep=lambda _s: None, countdown_seconds=countdown_seconds, keymap=keymap
        )
        driver.on_preflight = lambda: box["controller"].stop()
        return driver

    controller = RunController(driver_factory=factory)
    box["controller"] = controller
    return controller, keyboard


def test_a_stop_that_arrives_while_a_plain_run_starts_up_still_stops_it(qtbot):
    controller, keyboard = _controller_whose_user_presses_stop_during_preflight()

    with qtbot.waitSignal(controller.finished, timeout=10000) as blocker:
        controller.start(TEXT, CLEAN, seed=1)

    assert blocker.args[0] == "stopped"
    assert len(keyboard.text) <= 1, "at most the key already on its way, never the whole text"


def test_a_stop_that_arrives_while_a_document_run_starts_up_still_stops_it(qtbot):
    document, report = load_document(str(FIXTURE))
    session = DocumentSession.create(
        document, str(FIXTURE), report.warnings, targets.get_target("google-docs"),
        targets.keymap_for("google-docs", OperatingSystem.MACOS),
    )
    controller, keyboard = _controller_whose_user_presses_stop_during_preflight()

    with qtbot.waitSignal(controller.finished, timeout=10000) as blocker:
        controller.start_document(session.adapted, CLEAN, session.keymap, seed=1)

    assert blocker.args[0] == "stopped"
    assert len(keyboard.text) <= 1


def test_a_stop_during_start_up_is_honoured_in_the_countdown_too(qtbot):
    controller, keyboard = _controller_whose_user_presses_stop_during_preflight(countdown_seconds=3)
    ticks: list[int] = []
    controller.countdownTick.connect(ticks.append)

    with qtbot.waitSignal(controller.finished, timeout=10000) as blocker:
        controller.start(TEXT, CLEAN, seed=1)

    assert blocker.args[0] == "stopped"
    assert keyboard.text == ""
    assert ticks == [3], "stopped on the first tick of the countdown, not after all three"


def test_a_run_nobody_stops_is_not_disturbed(qtbot):
    keyboard = RecordingKeyboard()
    controller = RunController(
        driver_factory=lambda keymap=None: Driver(
            keyboard=keyboard, sleep=lambda _s: None, countdown_seconds=0, keymap=keymap
        )
    )
    with qtbot.waitSignal(controller.finished, timeout=10000) as blocker:
        controller.start(TEXT, CLEAN, seed=1)
    assert blocker.args[0] == "finished"
    assert keyboard.text == TEXT
