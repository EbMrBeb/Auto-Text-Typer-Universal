"""Entry point for the Qt GUI: ``python -m auto_essay_typer.gui``.

Also the target of the ``auto-text-typer-gui`` console entry point (see
``pyproject.toml``) and of ``packaging/launch_gui.py``, which is what the
PyInstaller build actually freezes (see ``packaging/pyinstaller/auto_text_typer.spec``).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from auto_essay_typer import __version__

APP_NAME = "Auto Text Typer"
ORG_NAME = "Auto Text Typer"

#: Sample text for `--self-test`'s end-to-end plan-and-type round trip.
_SELFTEST_TEXT = "The quick brown fox jumps over the lazy dog."
#: How long `--self-test` waits for the sample run to finish before giving up.
_SELFTEST_TIMEOUT_MS = 15_000


def _self_test() -> int:
    """Headless, no-window round trip: plan a sample sentence, type it with a
    RecordingKeyboard through the real MainWindow's own RunController, and
    verify the typed text matches exactly. Never shows a window."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    try:
        from PySide6.QtCore import QEventLoop, QSettings, QTimer
        from PySide6.QtWidgets import QApplication

        from auto_essay_typer.driver import Driver, RecordingKeyboard
        from auto_essay_typer.gui.main_window import MainWindow
        from auto_essay_typer.gui.theme import ThemeManager
        from auto_essay_typer.planner import plan
        from auto_essay_typer.profiles import PRESETS
        from auto_essay_typer.simulator import total_duration_ms
    except Exception as exc:  # noqa: BLE001 - must always print a clean one-liner
        print(f"SELFTEST FAILED: could not import the GUI: {exc}")
        return 1

    try:
        app = QApplication.instance() or QApplication(sys.argv[:1])
        app.setApplicationName(APP_NAME)
        app.setOrganizationName(ORG_NAME)
        theme_manager = ThemeManager(app)

        keyboard = RecordingKeyboard()

        def driver_factory() -> Driver:
            return Driver(keyboard=keyboard, sleep=lambda _seconds: None, countdown_seconds=0)

        with tempfile.TemporaryDirectory(prefix="auto_text_typer_selftest_") as tmpdir:
            settings = QSettings(str(Path(tmpdir) / "settings.ini"), QSettings.Format.IniFormat)
            window = MainWindow(theme_manager, driver_factory=driver_factory, settings=settings)
            # Deliberately never shown -- this proves the window builds and
            # runs headlessly, per the module spec.

            profile = PRESETS["natural"]
            controller = window.run_controller

            result: dict[str, str] = {}
            loop = QEventLoop()

            def on_finished(outcome: str, message: str) -> None:
                result["outcome"] = outcome
                result["message"] = message
                loop.quit()

            controller.finished.connect(on_finished)

            timeout_timer = QTimer()
            timeout_timer.setSingleShot(True)
            timeout_timer.timeout.connect(loop.quit)
            timeout_timer.start(_SELFTEST_TIMEOUT_MS)

            controller.start(_SELFTEST_TEXT, profile, seed=0)
            loop.exec()

            if "outcome" not in result:
                print("SELFTEST FAILED: timed out waiting for the sample run to finish")
                return 1
            if result["outcome"] != "finished":
                print(f"SELFTEST FAILED: run did not complete: {result['message']}")
                return 1
            if keyboard.text != _SELFTEST_TEXT:
                print("SELFTEST FAILED: typed text did not match the input text")
                return 1

            actions = plan(_SELFTEST_TEXT, profile, seed=0)
            duration_ms = total_duration_ms(actions)

            window.close()

        print(f"SELFTEST OK {len(actions)} actions, {duration_ms:.0f}ms estimated")
        return 0
    except Exception as exc:  # noqa: BLE001 - must always print a clean one-liner
        print(f"SELFTEST FAILED: {exc}")
        return 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)

    if "--version" in argv:
        print(f"{APP_NAME} {__version__}")
        return 0

    if "--self-test" in argv:
        return _self_test()

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from auto_essay_typer.gui.assets import asset_path
    from auto_essay_typer.gui.main_window import MainWindow
    from auto_essay_typer.gui.theme import ThemeManager

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    icon_path = asset_path("icon.png")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    theme_manager = ThemeManager(app)
    window = MainWindow(theme_manager)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
