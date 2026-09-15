#!/usr/bin/env python3
"""Packaging spike: a minimal PySide6 app that touches every dependency the
real GUI will need bundled -- PySide6 widgets, a system-tray icon, and
auto_essay_typer's pure planning core (which in turn imports pynput).

This exists to de-risk PyInstaller packaging *before* auto_essay_typer.gui
exists: if this builds, runs, and self-tests cleanly on all three platforms,
the same spec (packaging/pyinstaller/auto_text_typer.spec) will work for the
real GUI later -- just repoint ATT_ENTRY at packaging/launch_gui.py.

Run directly, for a normal window:

    .venv/bin/python packaging/spike_app.py

Or headlessly, to verify a *built* executable with no display/window at
all (used by build_macos.sh and the release workflow's post-build check):

    .venv/bin/python packaging/spike_app.py --self-test
    "dist/Auto Text Typer.app/Contents/MacOS/Auto Text Typer" --self-test
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from auto_essay_typer.actions import Pause
from auto_essay_typer.planner import plan
from auto_essay_typer.profiles import PRESETS
import pynput.keyboard  # noqa: F401 -- proves the frozen pynput backend loads

SAMPLE_TEXT = (
    "The quick brown fox jumps over the lazy dog, then stops to consider "
    "whether it really needed to."
)


def assets_dir() -> Path:
    """Where bundled data lives: ``sys._MEIPASS`` once PyInstaller has
    frozen this into an app (set for both onedir and onefile builds), or
    the repo's assets/ directory -- one level up from packaging/ -- when
    running from source."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "assets"
    return Path(__file__).resolve().parent.parent / "assets"


def tray_icon() -> QIcon:
    """The best tray icon for this platform: macOS gets the template image
    marked as a mask, so AppKit recolours it for the active light/dark menu
    bar; every other platform gets the colour glyph."""
    if sys.platform == "darwin":
        icon = QIcon(str(assets_dir() / "tray-icon-template.png"))
        icon.setIsMask(True)
        return icon
    return QIcon(str(assets_dir() / "tray-icon.png"))


def plan_sample(seed: int = 0) -> tuple[list, float]:
    """Plan SAMPLE_TEXT with the 'natural' preset.

    Returns ``(actions, estimated_duration_ms)``. The duration is the sum
    of every planned Pause -- the only source of elapsed time in the action
    stream (see auto_essay_typer.actions.Pause / auto_essay_typer.planner).
    """
    actions = plan(SAMPLE_TEXT, PRESETS["natural"], seed=seed)
    duration_ms = sum(a.ms for a in actions if isinstance(a, Pause))
    return actions, duration_ms


class SpikeWindow(QMainWindow):
    """The spike's one window: a button that plans the sample sentence and
    reports how many actions and how much simulated time it came out to."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Auto Text Typer - packaging spike")
        self.resize(480, 220)

        central = QWidget(self)
        layout = QVBoxLayout(central)

        self._result_label = QLabel(
            "Click “Plan Sample” to run auto_essay_typer.planner.plan() "
            "on a sample sentence."
        )
        self._result_label.setWordWrap(True)
        layout.addWidget(self._result_label)

        plan_button = QPushButton("Plan Sample")
        plan_button.clicked.connect(self._on_plan_clicked)
        layout.addWidget(plan_button)

        layout.addStretch(1)
        self.setCentralWidget(central)

        self._tray: QSystemTrayIcon | None = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray = QSystemTrayIcon(tray_icon(), self)
            self._tray.setToolTip("Auto Text Typer")
            menu = QMenu()
            quit_action = QAction("Quit", self)
            quit_action.triggered.connect(QApplication.instance().quit)
            menu.addAction(quit_action)
            self._tray.setContextMenu(menu)
            self._tray.show()

    def _on_plan_clicked(self) -> None:
        actions, duration_ms = plan_sample()
        self._result_label.setText(
            f"Planned {len(actions)} actions for {len(SAMPLE_TEXT)} characters "
            f"of sample text -- estimated typing duration {duration_ms / 1000:.1f}s."
        )


def run_self_test() -> int:
    # QApplication must exist before touching QIcon/QSystemTrayIcon at all,
    # even though nothing is ever shown here -- constructing it is exactly
    # what proves Qt's native libraries loaded correctly out of the frozen
    # bundle, on top of proving auto_essay_typer + pynput did too.
    app = QApplication.instance() or QApplication(sys.argv[:1])
    _ = tray_icon()  # exercises the bundled-asset-loading path
    actions, duration_ms = plan_sample()
    print(f"SELFTEST OK {len(actions)} actions, {duration_ms:.0f}ms estimated")
    del app
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--self-test" in argv:
        return run_self_test()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    window = SpikeWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
