"""Entry point for the Qt GUI: ``python -m auto_essay_typer.gui``.

Also the target of the ``auto-text-typer-gui`` console entry point (see
``pyproject.toml``) and of ``packaging/launch_gui.py``, which is what the
PyInstaller build actually freezes (see ``packaging/pyinstaller/auto_text_typer.spec``).
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from auto_essay_typer import __version__
from auto_essay_typer.driver import preload_keyboard_backend

if TYPE_CHECKING:  # the Qt classes are imported inside the functions that use them
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from auto_essay_typer.driver import Driver, Keyboard
    from auto_essay_typer.gui.hotkeys import HotkeyService
    from auto_essay_typer.gui.main_window import MainWindow
    from auto_essay_typer.gui.settings_store import SettingsStore
    from auto_essay_typer.gui.shell import AppShell
    from auto_essay_typer.gui.tray import AppTray
    from auto_essay_typer.platforms import PermissionStatus

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
        from auto_essay_typer.gui.settings_store import SettingsStore
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
            # Its own settings file: the self-test must never read or write the user's real one.
            settings_store = SettingsStore(Path(tmpdir) / "settings.json")
            window = MainWindow(
                theme_manager,
                driver_factory=driver_factory,
                settings=settings,
                settings_store=settings_store,
            )
            # Deliberately never shown -- this proves the window builds and
            # runs headlessly, per the module spec. No tray, hotkeys or
            # first-run dialog either: those belong to launch().

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


def collect_garbage_on_the_gui_thread(app: QApplication, interval_ms: int = 5000) -> QTimer:
    """Keep Python's cycle collector off every thread but the GUI thread.

    Python collects cycles on whichever thread allocates when its threshold is crossed, and a
    typing run's worker thread allocates constantly. A Qt widget or object that Python owns and
    that sits in a cycle (a dialog that has been closed, say) would then be destroyed *on the
    worker thread*: Qt posts events about its destruction through the application's Python event
    filters from the wrong thread, and PySide segfaults in its trampoline -- no Python error,
    the process just dies, in the middle of a run. So automatic collection is turned off and a
    timer on the GUI thread collects instead, where those objects belong. A few seconds' delay in
    freeing a closed dialog costs nothing; a full collection takes a few milliseconds.

    Returns the timer, which the caller must keep alive (``app`` owns it too).
    """
    import gc

    from PySide6.QtCore import QTimer

    gc.disable()
    timer = QTimer(app)
    timer.setInterval(interval_ms)
    timer.timeout.connect(gc.collect)
    timer.start()
    return timer


def launch(
    app: QApplication,
    *,
    settings_store: SettingsStore | None = None,
    tray_factory: Callable[[MainWindow], AppTray] | None = None,
    hotkey_service: HotkeyService | None = None,
    driver_factory: Callable[..., Driver] | None = None,
    keyboard_factory: Callable[[], Keyboard] | None = None,
    permission_checker: Callable[[], PermissionStatus] | None = None,
    quit_app: Callable[[], None] | None = None,
) -> tuple[MainWindow, AppShell]:
    """Everything the app does on launch except enter the event loop.

    Builds the theme manager from the saved theme, the window and the application shell;
    shows the window; and queues ``AppShell.start`` so that the tray comes up, the first-run
    walkthrough opens (on a first run) and the global hotkeys start (when they are enabled)
    once the event loop is running and the window is on screen. Returns ``(window, shell)``.

    The keyword arguments exist so tests can supply fakes (a tray built over the window's run
    controller, a hotkey service built on a fake backend, a recording keyboard, a permission
    probe, a ``quit_app`` that does not quit the test runner); the real app passes none of them.

    Must be called on the main thread, after ``driver.preload_keyboard_backend()``: it is where
    the hotkey listener is built, and macOS kills the process if that is anywhere else.
    """
    from PySide6.QtCore import QTimer

    from auto_essay_typer import platforms
    from auto_essay_typer.gui.main_window import MainWindow
    from auto_essay_typer.gui.settings_store import SettingsStore
    from auto_essay_typer.gui.shell import AppShell
    from auto_essay_typer.gui.theme import ThemeManager, ThemeMode

    store = settings_store if settings_store is not None else SettingsStore()
    theme_manager = ThemeManager(app, mode=ThemeMode(store.settings.theme))
    window_kwargs: dict[str, object] = {"settings_store": store}
    if hotkey_service is not None:
        window_kwargs["hotkey_service"] = hotkey_service
    if driver_factory is not None:
        window_kwargs["driver_factory"] = driver_factory
    if keyboard_factory is not None:
        window_kwargs["keyboard_factory"] = keyboard_factory
    window_kwargs["permission_checker"] = (
        permission_checker if permission_checker is not None else platforms.check_input_permission
    )
    window = MainWindow(theme_manager, **window_kwargs)

    shell_kwargs: dict[str, object] = {}
    if tray_factory is not None:
        shell_kwargs["tray"] = tray_factory(window)
    if quit_app is not None:
        shell_kwargs["quit_app"] = quit_app
    shell = AppShell(window, **shell_kwargs)
    app.aboutToQuit.connect(shell.shutdown)  # the tray icon, the hotkey listener and any run stop with the app

    window.show()
    QTimer.singleShot(0, shell.start)
    return window, shell


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)

    if "--version" in argv:
        print(f"{APP_NAME} {__version__}")
        return 0

    preload_keyboard_backend()  # main thread, before any window: macOS kills the app otherwise

    if "--self-test" in argv:
        return _self_test()

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from auto_essay_typer.gui.assets import asset_path

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    icon_path = asset_path("icon.png")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    _gc_timer = collect_garbage_on_the_gui_thread(app)  # before any worker thread exists
    _window, shell = launch(app)
    try:
        return app.exec()
    finally:
        shell.shutdown()  # also run from aboutToQuit; stopping twice is harmless


if __name__ == "__main__":
    raise SystemExit(main())
