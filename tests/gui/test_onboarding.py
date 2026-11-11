"""Tests for the first-run onboarding dialog.

Nothing here touches the real keyboard, the OS permission store, the network,
or the real settings file: the permission probe, the settings opener and the
demo's driver are all injected, and every :class:`SettingsStore` lives under
``tmp_path``. Dialogs are only ever ``show()``n (never ``exec()``ed), and runs
are awaited with ``qtbot.waitSignal`` -- the one place a test has to hold a run
mid-flight it uses a gate the worker thread parks on, not a sleep.
"""

from __future__ import annotations

import json
import threading

import pytest
import shiboken6
from PySide6.QtWidgets import QDialog, QLabel, QPlainTextEdit

from auto_essay_typer import driver as driver_module
from auto_essay_typer.actions import Backspace
from auto_essay_typer.driver import Driver, RunResult
from auto_essay_typer.gui import onboarding
from auto_essay_typer.gui.onboarding import (
    DEMO_PROFILE,
    DEMO_SEED,
    SAMPLE_TEXT,
    BoxKeyboard,
    OnboardingDialog,
    default_demo_driver,
)
from auto_essay_typer.gui.settings_store import SettingsStore
from auto_essay_typer.gui.theme import DARK_TOKENS, LIGHT_TOKENS, ThemeManager, ThemeMode
from auto_essay_typer.planner import plan
from auto_essay_typer.platforms import PermissionStatus
from auto_essay_typer.preview import describe
from auto_essay_typer.simulator import simulate, total_duration_ms

# --- helpers -----------------------------------------------------------------------------------


def _status(
    granted: bool = True,
    *,
    summary: str = "Everything is fine.",
    steps: tuple[str, ...] = (),
    can_open: bool = False,
) -> PermissionStatus:
    return PermissionStatus(
        granted=granted, summary=summary, fix_steps=steps, can_open_settings=can_open
    )


def _instant_driver(keyboard) -> Driver:
    return Driver(keyboard=keyboard, sleep=lambda _seconds: None, countdown_seconds=0)


class _Gate:
    """A ``sleep`` for the demo driver: lets some waits through, then parks the worker.

    The worker thread blocks inside ``sleep`` (so the run is genuinely
    mid-flight) until the test sets :attr:`release`; ``blocked`` is set the
    moment it parks.
    """

    def __init__(self, free_calls: int = 30) -> None:
        self.free_calls = free_calls
        self.calls = 0
        self.blocked = threading.Event()
        self.release = threading.Event()

    def sleep(self, _seconds: float) -> None:
        self.calls += 1
        if self.calls > self.free_calls:
            self.blocked.set()
            self.release.wait(timeout=10)

    def factory(self, keyboard) -> Driver:
        return Driver(keyboard=keyboard, sleep=self.sleep, countdown_seconds=0)


class _SpyKeyboard:
    """Forwards to the real sink while recording every call the driver makes."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.calls: list[tuple[str, object]] = []

    def press_char(self, char: str) -> None:
        self.calls.append(("char", char))
        self.inner.press_char(char)

    def backspace(self, count: int) -> None:
        self.calls.append(("backspace", count))
        self.inner.backspace(count)

    def press_chord(self, keys) -> None:
        self.calls.append(("chord", keys))
        self.inner.press_chord(keys)


@pytest.fixture
def store(tmp_path):
    return SettingsStore(tmp_path / "settings.json")


@pytest.fixture
def gate():
    gate = _Gate()
    yield gate
    gate.release.set()  # never leave a worker parked, whatever the test did


@pytest.fixture
def make_dialog(qtbot, qapp, store):
    def build(
        *,
        status: PermissionStatus | None = None,
        checker=None,
        opener=None,
        driver_factory=_instant_driver,
        theme_manager: ThemeManager | None = None,
        show: bool = True,
        register: bool = True,
    ) -> OnboardingDialog:
        probe = checker if checker is not None else (lambda: status or _status(True))
        dialog = OnboardingDialog(
            theme_manager or ThemeManager(qapp),
            store,
            permission_checker=probe,
            open_settings=opener or (lambda: True),
            driver_factory=driver_factory,
        )
        if register:
            qtbot.addWidget(dialog)
        if show:
            dialog.show()
        return dialog

    return build


def _go_to_demo(dialog: OnboardingDialog) -> None:
    dialog.next_button.click()
    dialog.next_button.click()
    assert dialog.step_index == 2


def _start_parked_demo(qtbot, dialog: OnboardingDialog, gate: _Gate) -> None:
    """Start the demo and wait until it is parked mid-run with some text typed."""
    _go_to_demo(dialog)
    dialog.try_button.click()
    assert gate.blocked.wait(timeout=5), "the demo never reached its gate"
    qtbot.waitUntil(lambda: dialog.demo_edit.toPlainText() != "", timeout=5000)


# --- the plan the demo runs -------------------------------------------------------------------------


def test_the_demo_plan_shows_exactly_one_slip_and_ends_on_the_sample():
    actions = plan(SAMPLE_TEXT, DEMO_PROFILE, DEMO_SEED)

    assert simulate(actions) == SAMPLE_TEXT
    assert any(isinstance(action, Backspace) for action in actions)  # a correction is visible
    events, summary = describe(actions, SAMPLE_TEXT)
    assert summary.typo_count == 1
    assert [event.kind for event in events if event.kind == "typo"] == ["typo"]
    assert total_duration_ms(actions) < 10_000  # short enough to watch in full


def test_the_sample_is_a_sentence_or_two():
    assert 20 <= len(SAMPLE_TEXT) <= 100
    assert OnboardingDialog.SAMPLE_TEXT == SAMPLE_TEXT


# --- the welcome step ------------------------------------------------------------------------------------


def test_the_dialog_starts_on_the_welcome_step(make_dialog):
    dialog = make_dialog()

    assert dialog.step_index == 0
    assert dialog.isModal()
    assert dialog.step_bar.label.text() == "Step 1 of 3"
    assert dialog.back_button.isEnabled() is False
    assert dialog.next_button.isHidden() is False
    assert dialog.finish_button.isHidden() is True
    assert dialog.skip_button.isHidden() is False
    assert dialog.app_icon_label.pixmap().isNull() is False


def test_the_welcome_step_explains_the_app_in_a_sentence_and_three_points(make_dialog):
    dialog = make_dialog()
    texts = [label.text() for label in dialog.findChildren(QLabel)]

    assert "types text into whatever app you click into" in dialog.intro_label.text()
    for lead in ("Works in any app", "Types like a person", "Keeps your formatting"):
        assert lead in texts


def test_the_step_indicator_follows_the_current_step(make_dialog):
    dialog = make_dialog()
    for step in range(3):
        dialog._go_to_step(step)
        assert dialog.step_bar.label.text() == f"Step {step + 1} of 3"
        reached = [segment.property("reached") for segment in dialog.step_bar.segments]
        assert reached == [position <= step for position in range(3)]


# --- navigation ----------------------------------------------------------------------------------------------


def test_next_and_back_walk_through_all_three_steps(make_dialog):
    dialog = make_dialog()

    dialog.next_button.click()
    assert dialog.step_index == 1
    assert dialog.back_button.isEnabled() is True
    assert dialog.next_button.isHidden() is False
    assert dialog.finish_button.isHidden() is True
    assert dialog.skip_button.isHidden() is False

    dialog.next_button.click()
    assert dialog.step_index == 2
    assert dialog.next_button.isHidden() is True
    assert dialog.finish_button.isHidden() is False
    assert dialog.skip_button.isHidden() is True  # Finish is the way out on the last step

    dialog.back_button.click()
    assert dialog.step_index == 1
    assert dialog.next_button.isHidden() is False
    assert dialog.finish_button.isHidden() is True

    dialog.back_button.click()
    assert dialog.step_index == 0
    assert dialog.back_button.isEnabled() is False


def test_stepping_never_runs_off_either_end(make_dialog):
    dialog = make_dialog()
    dialog._go_to_step(-5)
    assert dialog.step_index == 0
    dialog._go_to_step(99)
    assert dialog.step_index == 2


def test_enter_does_the_obvious_thing_on_each_step(make_dialog):
    dialog = make_dialog()
    assert dialog.next_button.isDefault() is True
    dialog.next_button.click()
    assert dialog.next_button.isDefault() is True
    dialog.next_button.click()
    assert dialog.try_button.isDefault() is True  # the demo is encouraged, not forced


# --- the permission step -------------------------------------------------------------------------------------------


def test_missing_permission_shows_a_warning_the_steps_and_a_hint(make_dialog):
    status = _status(
        False,
        summary="Auto Text Typer needs access.",
        steps=("Open the settings.", "Turn it on.", "Reopen the app."),
        can_open=True,
    )
    dialog = make_dialog(status=status)
    dialog.next_button.click()

    assert dialog.permission_banner.tone() == "warning"
    assert dialog.permission_status_label.text() == "Auto Text Typer needs access."
    assert dialog.permission_steps_card.isHidden() is False
    steps_html = dialog.permission_steps_label.text()
    assert steps_html.count("<li") == 3
    for step in status.fix_steps:
        assert step in steps_html
    assert dialog.permission_hint_label.isHidden() is False
    assert dialog.open_settings_button.isHidden() is False
    assert dialog.check_again_button.isHidden() is False
    assert dialog.next_button.isEnabled() is True  # a missing permission never blocks setup


def test_fix_steps_are_escaped_not_interpreted_as_markup(make_dialog):
    dialog = make_dialog(status=_status(False, steps=("Pick <b>Xorg</b> & log in.",)))
    steps_html = dialog.permission_steps_label.text()
    assert "&lt;b&gt;Xorg&lt;/b&gt; &amp; log in." in steps_html


def test_granted_permission_shows_a_success_banner_and_no_fix_steps(make_dialog):
    dialog = make_dialog(status=_status(True, summary="Accessibility access is granted."))
    dialog.next_button.click()

    assert dialog.permission_banner.tone() == "success"
    assert dialog.permission_status_label.text() == "Accessibility access is granted."
    assert dialog.permission_steps_card.isHidden() is True
    assert dialog.permission_hint_label.isHidden() is True


def test_open_settings_is_offered_only_where_the_os_has_a_settings_pane(make_dialog):
    with_pane = make_dialog(status=_status(False, steps=("Step.",), can_open=True))
    without_pane = make_dialog(status=_status(False, steps=("Step.",), can_open=False))

    assert with_pane.open_settings_button.isHidden() is False
    assert without_pane.open_settings_button.isHidden() is True
    assert without_pane.check_again_button.isHidden() is False


def test_open_settings_calls_the_injected_opener(make_dialog):
    opened: list[str] = []

    def opener() -> bool:
        opened.append("settings")
        return True

    dialog = make_dialog(status=_status(False, steps=("Step.",), can_open=True), opener=opener)
    dialog.open_settings_button.click()

    assert opened == ["settings"]
    assert dialog.open_settings_note_label.isHidden() is True


@pytest.mark.parametrize("failure", ["returns_false", "raises"])
def test_a_settings_pane_that_will_not_open_points_at_the_written_steps(make_dialog, failure):
    def opener() -> bool:
        if failure == "raises":
            raise OSError("no such app")
        return False

    dialog = make_dialog(status=_status(False, steps=("Step.",), can_open=True), opener=opener)
    dialog.open_settings_button.click()  # must not raise

    assert dialog.open_settings_note_label.isHidden() is False


def test_check_again_reruns_the_checker_and_flips_the_display_to_success(make_dialog):
    state = {"granted": False}
    calls: list[bool] = []

    def checker() -> PermissionStatus:
        calls.append(state["granted"])
        if state["granted"]:
            return _status(True, summary="Now it works.")
        return _status(False, summary="Not yet.", steps=("Do the thing.",), can_open=True)

    dialog = make_dialog(checker=checker)
    dialog.next_button.click()
    assert dialog.permission_banner.tone() == "warning"
    assert dialog.permission_status_label.text() == "Not yet."
    calls_before = len(calls)

    state["granted"] = True
    dialog.check_again_button.click()

    assert len(calls) == calls_before + 1
    assert dialog.permission_banner.tone() == "success"
    assert dialog.permission_status_label.text() == "Now it works."
    assert dialog.permission_steps_card.isHidden() is True
    assert dialog.permission_hint_label.isHidden() is True


def test_entering_the_permission_step_checks_again(make_dialog):
    calls: list[int] = []

    def checker() -> PermissionStatus:
        calls.append(1)
        return _status(True)

    dialog = make_dialog(checker=checker)
    after_construction = len(calls)

    dialog.next_button.click()
    assert len(calls) == after_construction + 1
    dialog.back_button.click()
    dialog.next_button.click()
    assert len(calls) == after_construction + 2


def test_a_permission_probe_that_raises_is_reported_not_propagated(make_dialog):
    def checker() -> PermissionStatus:
        raise RuntimeError("probe exploded")

    dialog = make_dialog(checker=checker)  # construction must survive it

    assert dialog.permission_banner.tone() == "warning"
    assert "probe exploded" in dialog.permission_status_label.text()
    assert dialog.next_button.isEnabled() is True


# --- the demo: typing into its own box -------------------------------------------------------------------------------------


def test_the_demo_box_starts_out_holding_the_sample_text(make_dialog):
    dialog = make_dialog()
    assert dialog.demo_edit.toPlainText() == SAMPLE_TEXT
    assert dialog.demo_edit.isReadOnly() is True  # a display surface, not an input
    assert dialog.stop_button.isEnabled() is False


def test_the_rules_are_spelled_out_in_plain_language(make_dialog):
    dialog = make_dialog()
    rules = dialog.rules_label.text()
    assert "Click into your document during the countdown." in rules
    assert "Keep your hands off the mouse and keyboard while it types." in rules
    assert "Press Stop in this window or Esc to stop at any time." in rules
    assert rules.count("<li") == 3


def test_try_it_here_types_the_sample_into_its_own_box(qtbot, make_dialog, store, monkeypatch):
    spied: list[tuple[BoxKeyboard, _SpyKeyboard]] = []

    def factory(keyboard) -> Driver:
        spy = _SpyKeyboard(keyboard)
        spied.append((keyboard, spy))
        return Driver(keyboard=spy, sleep=lambda _seconds: None, countdown_seconds=0)

    # Nothing may reach the real keyboard: constructing it at all is a failure.
    def forbidden(*_args, **_kwargs):
        raise AssertionError("the demo must never create a real keyboard")

    monkeypatch.setattr(driver_module, "PynputKeyboard", forbidden)

    dialog = make_dialog(driver_factory=factory)
    _go_to_demo(dialog)

    with qtbot.waitSignal(dialog.demoFinished, timeout=5000) as blocker:
        dialog.try_button.click()

    assert blocker.args == ["finished"]
    assert dialog.demo_edit.toPlainText() == SAMPLE_TEXT

    # The keystrokes went through the sink handed to the factory...
    ((sink, spy),) = spied
    assert isinstance(sink, BoxKeyboard)
    typed = "".join(str(arg) for kind, arg in spy.calls if kind == "char")
    # ...including the slip: more characters were typed than the sample holds,
    # and some were backspaced away again.
    assert len(typed) > len(SAMPLE_TEXT)
    assert any(kind == "backspace" for kind, _arg in spy.calls)

    # The UI settled into its "done" state and nothing else changed.
    assert dialog.demo_completed is True
    assert dialog.demo_running is False
    assert dialog.demo_progress.value() == 100
    assert dialog.demo_status_label.text().startswith("Done")
    assert dialog.try_button.isEnabled() is True
    assert dialog.try_button.text() == "Try again"
    assert dialog.stop_button.isEnabled() is False
    assert store.settings.onboarding_complete is False
    assert not store.path.exists()


def test_the_demo_can_be_run_again(qtbot, make_dialog):
    dialog = make_dialog()
    _go_to_demo(dialog)
    for _ in range(2):
        with qtbot.waitSignal(dialog.demoFinished, timeout=5000) as blocker:
            dialog.try_button.click()
        assert blocker.args == ["finished"]
        assert dialog.demo_edit.toPlainText() == SAMPLE_TEXT


def test_the_demo_clears_the_box_before_typing(qtbot, make_dialog, gate):
    dialog = make_dialog(driver_factory=gate.factory)
    _go_to_demo(dialog)
    assert dialog.demo_edit.toPlainText() == SAMPLE_TEXT

    dialog.try_button.click()
    assert gate.blocked.wait(timeout=5)
    qtbot.waitUntil(lambda: dialog.demo_edit.toPlainText() != "", timeout=5000)

    # Parked mid-run: what is in the box is what has been "typed" so far, not the old sample.
    assert dialog.demo_edit.toPlainText() != SAMPLE_TEXT
    gate.release.set()
    qtbot.waitUntil(lambda: dialog.demo_running is False, timeout=5000)


def test_try_it_here_is_disabled_while_the_demo_runs(qtbot, make_dialog, gate):
    dialog = make_dialog(driver_factory=gate.factory)
    _start_parked_demo(qtbot, dialog, gate)

    assert dialog.demo_running is True
    assert dialog.try_button.isEnabled() is False
    assert dialog.stop_button.isEnabled() is True
    assert dialog.back_button.isEnabled() is False  # don't wander off mid-demo
    assert dialog.finish_button.isEnabled() is True

    with qtbot.waitSignal(dialog.demoFinished, timeout=5000) as blocker:
        gate.release.set()

    assert blocker.args == ["finished"]
    assert dialog.demo_running is False
    assert dialog.try_button.isEnabled() is True
    assert dialog.back_button.isEnabled() is True
    assert dialog.demo_edit.toPlainText() == SAMPLE_TEXT


def test_the_stop_button_ends_the_demo_early(qtbot, make_dialog, gate):
    dialog = make_dialog(driver_factory=gate.factory)
    _start_parked_demo(qtbot, dialog, gate)

    dialog.stop_button.click()
    assert dialog.stop_button.isEnabled() is False  # asked once; waiting for it to wind down
    with qtbot.waitSignal(dialog.demoFinished, timeout=5000) as blocker:
        gate.release.set()

    assert blocker.args == ["stopped"]
    assert dialog.demo_edit.toPlainText() != SAMPLE_TEXT  # cut short
    assert dialog.demo_completed is False
    assert dialog.try_button.isEnabled() is True
    assert dialog.try_button.objectName() == "PrimaryButton"  # still the thing to press
    assert dialog.finish_button.objectName() == "SecondaryButton"
    assert "Stopped" in dialog.demo_status_label.text()


def test_escape_stops_a_running_demo_instead_of_closing_the_dialog(
    qtbot, make_dialog, store, gate
):
    from PySide6.QtCore import Qt

    dialog = make_dialog(driver_factory=gate.factory)
    _start_parked_demo(qtbot, dialog, gate)

    qtbot.keyClick(dialog.demo_edit, Qt.Key.Key_Escape)  # focus is in the box while it types

    assert dialog.isVisible() is True
    assert dialog.stop_button.isEnabled() is False  # the demo was told to stop
    with qtbot.waitSignal(dialog.demoFinished, timeout=5000) as blocker:
        gate.release.set()
    assert blocker.args == ["stopped"]
    assert dialog.isVisible() is True
    assert store.settings.onboarding_complete is False


def test_escape_dismisses_an_idle_dialog_without_completing_setup(qtbot, make_dialog, store):
    from PySide6.QtCore import Qt

    dialog = make_dialog()
    qtbot.keyClick(dialog, Qt.Key.Key_Escape)

    assert dialog.isVisible() is False
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert store.settings.onboarding_complete is False


def test_closing_the_dialog_mid_run_stops_the_run_cleanly(qtbot, make_dialog, gate, monkeypatch):
    thread_errors: list[object] = []
    monkeypatch.setattr(threading, "excepthook", thread_errors.append)

    dialog = make_dialog(driver_factory=gate.factory)
    _start_parked_demo(qtbot, dialog, gate)
    controller = dialog._controller

    with qtbot.waitSignal(controller.finished, timeout=5000) as blocker:
        dialog.close()
        gate.release.set()

    assert blocker.args[0] == "stopped"
    assert thread_errors == []
    assert controller.is_busy is False


def test_a_run_winding_down_after_the_dialog_is_destroyed_does_no_harm(
    qtbot, make_dialog, gate, monkeypatch
):
    thread_errors: list[object] = []
    monkeypatch.setattr(threading, "excepthook", thread_errors.append)

    # Not registered with qtbot: the test deletes the dialog itself.
    dialog = make_dialog(driver_factory=gate.factory, register=False)
    _start_parked_demo(qtbot, dialog, gate)
    controller = dialog._controller  # the worker thread holds this too

    dialog.close()
    shiboken6.delete(dialog)  # the dialog, its box and its sink are gone for real
    assert shiboken6.isValid(dialog) is False

    with qtbot.waitSignal(controller.finished, timeout=5000) as blocker:
        gate.release.set()

    assert blocker.args[0] == "stopped"
    assert thread_errors == []


def test_a_worker_that_keeps_typing_after_the_dialog_is_destroyed_does_no_harm(
    qtbot, make_dialog, monkeypatch
):
    """The worst case: a driver that ignores the stop request and types into a box that is gone."""
    thread_errors: list[object] = []
    monkeypatch.setattr(threading, "excepthook", thread_errors.append)
    parked = threading.Event()
    release = threading.Event()

    def factory(keyboard) -> Driver:
        class _LateTypist(Driver):
            def run(self, actions, on_progress=None, on_countdown=None):
                parked.set()
                release.wait(timeout=10)
                keyboard.press_char("x")  # all three land on a deleted widget
                keyboard.backspace(1)
                keyboard.press_chord(("cmd", "b"))
                return RunResult.ABORTED

        return _LateTypist(keyboard=keyboard, sleep=lambda _seconds: None, countdown_seconds=0)

    dialog = make_dialog(driver_factory=factory, register=False)
    _go_to_demo(dialog)
    dialog.try_button.click()
    assert parked.wait(timeout=5)
    controller = dialog._controller

    dialog.close()
    shiboken6.delete(dialog)
    try:
        with qtbot.waitSignal(controller.finished, timeout=5000) as blocker:
            release.set()
    finally:
        release.set()

    assert blocker.args[0] == "stopped"
    assert thread_errors == []


def test_the_real_default_driver_path_types_into_the_box_end_to_end(
    qtbot, make_dialog, monkeypatch
):
    # No injected driver factory: the dialog builds its own Driver, with real
    # sleeping. Make the plan quick so the test is too.
    fast = DEMO_PROFILE.with_overrides(
        wpm=4000.0,
        typo_rate_per_100=0.0,
        sentence_pause_ms=(0.0, 0.0),
        clause_pause_ms=(0.0, 0.0),
        paragraph_pause_ms=(0.0, 0.0),
    )
    monkeypatch.setattr(onboarding, "DEMO_PROFILE", fast)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("the demo must never create a real keyboard")

    monkeypatch.setattr(driver_module, "PynputKeyboard", forbidden)

    dialog = make_dialog(driver_factory=None)
    assert dialog._driver_factory is default_demo_driver
    _go_to_demo(dialog)

    with qtbot.waitSignal(dialog.demoFinished, timeout=15000) as blocker:
        dialog.try_button.click()

    assert blocker.args == ["finished"]
    assert dialog.demo_edit.toPlainText() == SAMPLE_TEXT


def test_the_default_driver_types_through_the_sink_with_no_countdown(qtbot, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("the demo must never create a real keyboard")

    monkeypatch.setattr(driver_module, "PynputKeyboard", forbidden)

    edit = QPlainTextEdit()
    qtbot.addWidget(edit)
    sink = BoxKeyboard(edit)
    driver = default_demo_driver(sink, sleep=lambda _seconds: None)

    result = driver.run(plan(SAMPLE_TEXT, DEMO_PROFILE, DEMO_SEED))  # no countdown ticks

    assert result is RunResult.COMPLETED
    assert edit.toPlainText() == SAMPLE_TEXT


def test_a_failing_demo_run_is_reported_in_the_dialog(qtbot, make_dialog):
    def factory(keyboard) -> Driver:
        class _Broken(Driver):
            def run(self, *args, **kwargs):
                raise RuntimeError("the typist tripped")

        return _Broken(keyboard=keyboard, sleep=lambda _seconds: None, countdown_seconds=0)

    dialog = make_dialog(driver_factory=factory)
    _go_to_demo(dialog)
    with qtbot.waitSignal(dialog.demoFinished, timeout=5000) as blocker:
        dialog.try_button.click()

    assert blocker.args == ["failed"]
    assert "the typist tripped" in dialog.demo_status_label.text()
    assert LIGHT_TOKENS.danger in dialog.demo_status_label.styleSheet()  # shown as an error
    assert dialog.try_button.isEnabled() is True
    assert dialog.demo_completed is False


# --- Finish / Skip / closing -----------------------------------------------------------------------------------------------------


def test_finish_is_available_without_trying_the_demo_and_starts_secondary(make_dialog):
    dialog = make_dialog()
    _go_to_demo(dialog)

    assert dialog.finish_button.isEnabled() is True
    assert dialog.finish_button.objectName() == "SecondaryButton"
    assert dialog.try_button.objectName() == "PrimaryButton"


def test_finish_becomes_the_primary_action_once_the_demo_has_run(qtbot, make_dialog):
    dialog = make_dialog()
    _go_to_demo(dialog)
    with qtbot.waitSignal(dialog.demoFinished, timeout=5000):
        dialog.try_button.click()

    assert dialog.finish_button.objectName() == "PrimaryButton"
    assert dialog.try_button.objectName() == "SecondaryButton"
    assert dialog.finish_button.isDefault() is True


def test_finish_marks_setup_complete_and_saves_it(qtbot, make_dialog, store, tmp_path):
    dialog = make_dialog()
    _go_to_demo(dialog)

    with qtbot.waitSignal(dialog.completed, timeout=1000):
        dialog.finish_button.click()

    assert store.settings.onboarding_complete is True
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved["onboarding_complete"] is True
    assert SettingsStore(tmp_path / "settings.json").settings.onboarding_complete is True
    assert dialog.finished_setup is True
    assert dialog.save_error is None
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.isVisible() is False


def test_completed_is_emitted_exactly_once(qapp, make_dialog):
    dialog = make_dialog()
    _go_to_demo(dialog)
    emitted: list[int] = []
    dialog.completed.connect(lambda: emitted.append(1))

    dialog.finish_button.click()
    qapp.processEvents()

    assert emitted == [1]


@pytest.mark.parametrize("step", [0, 1])
def test_skip_marks_setup_complete_and_closes(qtbot, make_dialog, store, step):
    dialog = make_dialog()
    dialog._go_to_step(step)
    completed: list[int] = []
    dialog.completed.connect(lambda: completed.append(1))

    with qtbot.waitSignal(dialog.skipped, timeout=1000):
        dialog.skip_button.click()

    assert store.settings.onboarding_complete is True  # it must not nag again
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.isVisible() is False
    assert dialog.finished_setup is False  # skipped, not finished
    assert completed == []


def test_closing_without_finishing_leaves_setup_to_be_offered_again(make_dialog, store):
    dialog = make_dialog()
    dialog.next_button.click()

    dialog.close()

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert store.settings.onboarding_complete is False
    assert not store.path.exists()  # nothing was written at all


def test_rejecting_the_dialog_does_not_set_the_flag(make_dialog, store):
    dialog = make_dialog()
    _go_to_demo(dialog)
    dialog.reject()
    assert store.settings.onboarding_complete is False
    assert dialog.finished_setup is False


def test_a_settings_file_that_cannot_be_written_does_not_trap_the_user(
    make_dialog, store, monkeypatch
):
    def refuse(**_changes):
        raise OSError("read-only file system")

    monkeypatch.setattr(store, "update", refuse)
    dialog = make_dialog()
    _go_to_demo(dialog)

    dialog.finish_button.click()

    assert dialog.finished_setup is True
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert "read-only file system" in dialog.save_error


# --- theming ----------------------------------------------------------------------------------------------------------------------------


def test_the_dialog_restyles_when_the_theme_changes(qapp, make_dialog):
    theme_manager = ThemeManager(qapp, mode=ThemeMode.LIGHT)
    dialog = make_dialog(theme_manager=theme_manager)

    assert LIGHT_TOKENS.border in dialog.styleSheet()
    assert LIGHT_TOKENS.text in dialog.permission_banner.styleSheet()
    assert LIGHT_TOKENS.muted_text in dialog.skip_button.styleSheet()

    theme_manager.set_mode(ThemeMode.DARK)

    assert DARK_TOKENS.border in dialog.styleSheet()
    assert LIGHT_TOKENS.border not in dialog.styleSheet()
    assert DARK_TOKENS.text in dialog.permission_banner.styleSheet()
    assert LIGHT_TOKENS.text not in dialog.permission_banner.styleSheet()
    assert DARK_TOKENS.muted_text in dialog.skip_button.styleSheet()

    theme_manager.set_mode(ThemeMode.SYSTEM)


def test_the_dialog_works_without_a_theme_manager(qtbot, store):
    dialog = OnboardingDialog(None, store, permission_checker=lambda: _status(True))
    qtbot.addWidget(dialog)
    dialog.show()
    assert LIGHT_TOKENS.border in dialog.styleSheet()


# --- the keystroke sink --------------------------------------------------------------------------------------------------------------------


@pytest.fixture
def box(qtbot):
    edit = QPlainTextEdit()
    qtbot.addWidget(edit)
    return edit, BoxKeyboard(edit)


def test_the_sink_types_and_backspaces_in_its_box(box):
    edit, sink = box
    for char in "helo":
        sink.press_char(char)
    assert edit.toPlainText() == "helo"

    sink.backspace(1)
    assert edit.toPlainText() == "hel"
    sink.press_char("l")
    sink.press_char("o")
    assert edit.toPlainText() == "hello"

    sink.backspace(2)
    assert edit.toPlainText() == "hel"


def test_the_sink_tolerates_a_backspace_past_the_start(box):
    edit, sink = box
    sink.press_char("a")
    sink.backspace(50)
    assert edit.toPlainText() == ""


def test_the_sink_handles_line_breaks(box):
    edit, sink = box
    for char in "a\nb":
        sink.press_char(char)
    assert edit.toPlainText() == "a\nb"
    sink.backspace(2)  # the "b" and the line break
    assert edit.toPlainText() == "a"


def test_pressing_a_chord_in_the_sink_is_a_safe_no_op(box):
    edit, sink = box
    sink.press_char("x")

    sink.press_chord(("cmd", "b"))
    sink.press_chord(())  # keymaps.NO_OP

    assert edit.toPlainText() == "x"
    assert sink.chords == [("cmd", "b"), ()]


def test_the_sink_delivers_a_worker_threads_keystrokes_in_order_on_the_gui_thread(qtbot):
    gui_thread = threading.current_thread()
    edited_on: list[threading.Thread] = []

    class RecordingEdit(QPlainTextEdit):
        """Notes which thread each edit is made on."""

        def insertPlainText(self, text: str) -> None:  # noqa: N802 - Qt override
            edited_on.append(threading.current_thread())
            super().insertPlainText(text)

    edit = RecordingEdit()
    qtbot.addWidget(edit)
    sink = BoxKeyboard(edit)

    def worker() -> None:
        for char in "hey!":
            sink.press_char(char)
        sink.backspace(1)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=5)
    assert edited_on == []  # the worker itself never touched the widget

    qtbot.waitUntil(lambda: edit.toPlainText() == "hey", timeout=5000)
    assert len(edited_on) == 4
    assert all(t is gui_thread for t in edited_on)


def test_the_sink_ignores_calls_after_its_box_is_gone(qtbot):
    edit = QPlainTextEdit()
    sink = BoxKeyboard(edit)
    shiboken6.delete(edit)  # takes the sink (a child of the box) with it

    sink.press_char("x")
    sink.backspace(1)
    sink.press_chord(("ctrl", "b"))  # nothing raises
