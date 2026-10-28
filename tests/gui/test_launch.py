"""Tests for ``launch()`` (``gui/__main__.py``): everything the app does at startup bar the event loop.

``main()`` itself blocks in ``app.exec()`` and builds its own ``QApplication``, which cannot share a
process with the session's, so ``launch()`` was split out of it: it builds the theme manager, the
window and the shell, shows the window and queues the shell's start. These tests drive it with the
same fakes as the shell tests (a tray over a fake system tray, hotkeys over a fake backend) and a
recording keyboard, so nothing is installed, shown to the OS or typed.

One test goes further and runs the *real* ``main()`` in a subprocess on the offscreen platform, with
hotkeys switched off in its (temporary) settings, a timer that quits after a moment, and the check
that it starts, stays up and exits cleanly without a traceback.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import threading

from auto_essay_typer.gui.__main__ import launch
from auto_essay_typer.gui.settings_store import SettingsStore
from auto_essay_typer.gui.theme import ThemeMode
from tests.gui.fakes import build_tray, failing_backend, granted, hotkey_service


def _launch(qtbot, qapp, tmp_path, recording_driver_factory, *, store_changes=None, backends=(), tray_available=True):
    store = SettingsStore(tmp_path / "settings.json")
    if store_changes:
        store.update(**store_changes)
    service, backends_made = hotkey_service(*backends)
    factory, keyboards = recording_driver_factory
    trays: dict = {}
    quits: list[int] = []

    def tray_factory(window):
        tray, fakes = build_tray(window.run_controller, available=tray_available, theme_manager=window.theme_manager)
        trays.update(tray=tray, fakes=fakes)
        return tray

    window, shell = launch(
        qapp,
        settings_store=store,
        tray_factory=tray_factory,
        hotkey_service=service,
        driver_factory=factory,
        permission_checker=granted,
        quit_app=lambda: quits.append(1),
    )
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: shell.started, timeout=3000)  # queued: runs once the event loop turns
    return window, shell, service, backends_made, trays, quits, store


def test_launch_builds_the_window_from_the_saved_settings_and_starts_the_shell(
    qtbot, qapp, tmp_path, recording_driver_factory
):
    window, shell, service, backends, trays, _quits, store = _launch(
        qtbot, qapp, tmp_path, recording_driver_factory, store_changes={"theme": "dark", "onboarding_complete": True}
    )
    try:
        assert window.settings_store is store
        assert window.theme_manager.mode is ThemeMode.DARK  # from the saved theme, not a default
        assert window.isVisible()
        assert trays["fakes"][0].visible  # the tray icon is up
        qtbot.waitUntil(lambda: service.state == "active", timeout=3000)  # and the hotkeys are on
        assert window.onboarding_dialog is None  # setup was already done
    finally:
        shell.shutdown()


def test_launch_builds_the_hotkey_listener_on_the_main_thread(qtbot, qapp, tmp_path, recording_driver_factory):
    window, shell, service, backends, _trays, _quits, _store = _launch(
        qtbot, qapp, tmp_path, recording_driver_factory, store_changes={"onboarding_complete": True}
    )
    try:
        qtbot.waitUntil(lambda: service.state == "active", timeout=3000)
        (backend,) = backends.built
        # The macOS crash this guards against kills the process; it cannot show up as a failure there.
        assert backend.built_on is threading.main_thread()
        assert backend.prepare_thread is threading.main_thread()
        assert backend.start_thread is not threading.main_thread()
    finally:
        shell.shutdown()


def test_a_first_launch_opens_the_walkthrough_before_the_hotkeys(qtbot, qapp, tmp_path, recording_driver_factory):
    window, shell, service, backends, _trays, _quits, store = _launch(qtbot, qapp, tmp_path, recording_driver_factory)
    try:
        assert store.settings.onboarding_complete is False
        assert window.onboarding_dialog is not None and window.onboarding_dialog.isVisible()
        assert backends.built == []

        window.onboarding_dialog.skip_button.click()

        assert store.settings.onboarding_complete is True
        qtbot.waitUntil(lambda: service.state == "active", timeout=3000)
    finally:
        shell.shutdown()


def test_hotkeys_that_cannot_start_do_not_stop_the_app_launching(qtbot, qapp, tmp_path, recording_driver_factory):
    window, shell, service, _backends, trays, _quits, _store = _launch(
        qtbot,
        qapp,
        tmp_path,
        recording_driver_factory,
        store_changes={"onboarding_complete": True},
        backends=(failing_backend(),),
    )
    try:
        qtbot.waitUntil(lambda: service.state == "unavailable", timeout=3000)
        assert window.isVisible() and trays["fakes"][0].visible
        assert window.hotkey_status_button.isHidden() is False
    finally:
        shell.shutdown()


def test_launch_without_a_tray_is_quiet(qtbot, qapp, tmp_path, recording_driver_factory):
    window, shell, service, _backends, trays, _quits, _store = _launch(
        qtbot, qapp, tmp_path, recording_driver_factory, store_changes={"onboarding_complete": True}, tray_available=False
    )
    try:
        assert trays["tray"].available is False
        assert trays["fakes"] == []
        qtbot.waitUntil(lambda: service.state == "active", timeout=3000)  # the rest still works
        assert window.isVisible()
    finally:
        shell.shutdown()


def test_the_application_quitting_takes_the_tray_and_the_listener_down(qtbot, qapp, tmp_path, recording_driver_factory):
    window, shell, service, backends, trays, _quits, _store = _launch(
        qtbot, qapp, tmp_path, recording_driver_factory, store_changes={"onboarding_complete": True}
    )
    qtbot.waitUntil(lambda: service.state == "active", timeout=3000)

    qapp.aboutToQuit.emit()  # launch() connected the shell's shutdown to this

    assert not trays["fakes"][0].visible
    assert service.state == "off"
    assert backends.built[0].stop_calls >= 1


def test_the_tray_quit_entry_ends_the_application(qtbot, qapp, tmp_path, recording_driver_factory):
    window, shell, service, _backends, trays, quits, _store = _launch(
        qtbot, qapp, tmp_path, recording_driver_factory, store_changes={"onboarding_complete": True}
    )
    try:
        trays["tray"].quit_action.trigger()
        assert quits == [1]
    finally:
        shell.shutdown()


def test_launch_defaults_to_the_isolated_settings_file(qtbot, qapp, tmp_path, recording_driver_factory):
    # With no store passed, launch() makes its own -- which must resolve to the test's temp file,
    # never the user's real one (AUTO_TEXT_TYPER_SETTINGS, set by the autouse fixture in conftest.py).
    service, _backends = hotkey_service()
    window, shell = launch(qapp, hotkey_service=service, driver_factory=recording_driver_factory[0], permission_checker=granted)
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: shell.started, timeout=3000)
    try:
        assert window.settings_store.path == tmp_path / "settings.json"
    finally:
        if window.onboarding_dialog is not None:
            window.onboarding_dialog.reject()
        shell.shutdown()


# --- the real main(), in a subprocess ------------------------------------------------------------------------------------


_RUN_MAIN_THEN_QUIT = textwrap.dedent(
    """
    import sys
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    real_exec = QApplication.exec

    def exec_then_quit_soon(*args, **kwargs):
        # Quit through the normal path (aboutToQuit and all) once the app has been up a moment.
        QTimer.singleShot(1500, QApplication.quit)
        return real_exec()

    QApplication.exec = staticmethod(exec_then_quit_soon)

    from auto_essay_typer.gui.__main__ import main
    raise SystemExit(main([]))
    """
)


def test_the_real_main_starts_stays_up_and_quits_cleanly(tmp_path):
    settings = tmp_path / "settings.json"
    # Setup already done, so no walkthrough; hotkeys off, so no real listener in a test process.
    settings.write_text(
        '{"onboarding_complete": true, "global_hotkeys_enabled": false, "theme": "dark"}', encoding="utf-8"
    )
    env = dict(os.environ)
    env.update(
        QT_QPA_PLATFORM="offscreen",
        AUTO_TEXT_TYPER_SETTINGS=str(settings),
        AUTO_TEXT_TYPER_CONFIG=str(tmp_path / "profiles.toml"),
    )

    result = subprocess.run(
        [sys.executable, "-c", _RUN_MAIN_THEN_QUIT],
        capture_output=True,
        text=True,
        timeout=90,
        env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Traceback" not in result.stderr, result.stderr
