from PySide6.QtCore import Qt

from auto_essay_typer.gui.mini_window import MiniProgressWindow
from auto_essay_typer.gui.run_controller import RunController


def test_mini_window_never_accepts_focus_and_stays_on_top(qtbot, qapp):
    controller = RunController(driver_factory=lambda: None)
    window = MiniProgressWindow(controller)
    qtbot.addWidget(window)

    flags = window.windowFlags()
    assert flags & Qt.WindowType.WindowDoesNotAcceptFocus
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert flags & Qt.WindowType.Tool
    assert window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating) is True


def test_progress_updates_the_bar_and_labels(qtbot, qapp):
    controller = RunController(driver_factory=lambda: None)
    window = MiniProgressWindow(controller)
    qtbot.addWidget(window)

    controller.progressChanged.emit(5, 10, 12.0, 3.0)
    assert window.progress_bar.value() == 50
    assert window.percent_label.text() == "50%"
    assert "0:12" in window.eta_label.text()


def test_pause_button_label_follows_state(qtbot, qapp):
    controller = RunController(driver_factory=lambda: None)
    window = MiniProgressWindow(controller)
    qtbot.addWidget(window)

    controller.stateChanged.emit("typing")
    assert window.pause_button.text() == "Pause"
    controller.stateChanged.emit("paused")
    assert window.pause_button.text() == "Resume"


def test_pause_button_calls_controller_pause_and_resume(qtbot, qapp):
    calls = []

    class _Fake:
        state = "typing"

        def pause(self):
            calls.append("pause")

        def resume(self):
            calls.append("resume")

    controller = RunController(driver_factory=lambda: None)
    window = MiniProgressWindow(controller)
    qtbot.addWidget(window)
    window._controller = _Fake()

    window._on_pause_resume_clicked()
    assert calls == ["pause"]

    window._controller.state = "paused"
    window._on_pause_resume_clicked()
    assert calls == ["pause", "resume"]


def test_stop_button_calls_controller_stop(qtbot, qapp):
    calls = []
    controller = RunController(driver_factory=lambda: None)
    controller.stop = lambda: calls.append("stop")
    window = MiniProgressWindow(controller)
    qtbot.addWidget(window)

    window.stop_button.click()
    assert calls == ["stop"]


def test_position_near_corner_does_not_raise_without_a_real_screen(qtbot, qapp):
    controller = RunController(driver_factory=lambda: None)
    window = MiniProgressWindow(controller)
    qtbot.addWidget(window)
    window.position_near_corner()  # must not raise, even offscreen
