"""Fakes for the tests that wire the window, the tray and the hotkeys together.

Nothing here touches the OS: the system tray, the hotkey listener, the clipboard, the
permission store and the confirmation dialogs are all replaced, and every fake records
*which thread* it was called on, because that is what the macOS crash this wiring must
avoid was about (see ``gui/hotkeys.py``).
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QIcon

from auto_essay_typer.gui.hotkeys import HotkeyError, HotkeyService
from auto_essay_typer.gui.run_controller import RunController
from auto_essay_typer.gui.tray import AppTray
from auto_essay_typer.platforms import PermissionStatus

MAIN = threading.main_thread


def granted() -> PermissionStatus:
    return PermissionStatus(granted=True, summary="ok", fix_steps=(), can_open_settings=False)


def not_granted() -> PermissionStatus:
    return PermissionStatus(
        granted=False,
        summary="Auto Text Typer needs Accessibility access to type into other apps.",
        fix_steps=("Open System Settings.", "Turn on Auto Text Typer."),
        can_open_settings=True,
    )


class FakeSystemTray(QObject):
    """Stands in for ``QSystemTrayIcon``: records what the ``AppTray`` does to it."""

    activated = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.icons: list[QIcon] = []
        self.tooltip: str | None = None
        self.menu = None
        self.visible = False
        self.messages: list[tuple[str, str]] = []

    def setIcon(self, icon):  # noqa: N802 - mirrors Qt's API
        self.icons.append(icon)

    def setToolTip(self, text):  # noqa: N802
        self.tooltip = text

    def setContextMenu(self, menu):  # noqa: N802
        self.menu = menu

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False

    def isVisible(self):  # noqa: N802
        return self.visible

    def showMessage(self, title, message, icon=None, msecs=10000):  # noqa: N802
        self.messages.append((title, message))


def build_tray(
    controller: RunController, *, available: bool = True, theme_manager=None
) -> tuple[AppTray, list[FakeSystemTray]]:
    """``(tray, built_fakes)``: an ``AppTray`` over a fake system tray (or none, when unavailable)."""
    built: list[FakeSystemTray] = []

    def factory() -> FakeSystemTray:
        fake = FakeSystemTray()
        built.append(fake)
        return fake

    tray = AppTray(
        controller, is_available=lambda: available, tray_factory=factory, theme_manager=theme_manager
    )
    return tray, built


class FakeHotkeyBackend:
    """A hotkey backend that records the thread it was built, prepared and started on."""

    def __init__(self, *, prepare_error=None, start_error=None) -> None:
        self.built_on = threading.current_thread()
        self.prepare_error = prepare_error
        self.start_error = start_error
        self.prepare_thread: threading.Thread | None = None
        self.start_thread: threading.Thread | None = None
        self.bindings: dict[str, Callable[[], None]] | None = None
        self.started = False
        self.stop_calls = 0

    def prepare(self, bindings) -> None:
        self.prepare_thread = threading.current_thread()
        if self.prepare_error is not None:
            raise self.prepare_error

    def start(self, bindings) -> None:
        self.start_thread = threading.current_thread()
        if self.start_error is not None:
            raise self.start_error
        self.bindings = dict(bindings)
        self.started = True

    def stop(self) -> None:
        self.stop_calls += 1

    def press(self, hotkey_text: str) -> None:
        """Press ``hotkey_text`` from a real background thread, the way pynput's listener would."""
        assert self.bindings is not None, "the backend never started"
        thread = threading.Thread(target=self.bindings[hotkey_text], name="fake-listener-thread")
        thread.start()
        thread.join(5)
        assert not thread.is_alive()


class FakeHotkeyBackends:
    """A backend factory: hands out ``FakeHotkeyBackend``s (one prepared, then plain ones)."""

    def __init__(self, *prepared: FakeHotkeyBackend) -> None:
        self._prepared = list(prepared)
        self.built: list[FakeHotkeyBackend] = []

    def __call__(self) -> FakeHotkeyBackend:
        backend = self._prepared.pop(0) if self._prepared else FakeHotkeyBackend()
        self.built.append(backend)
        return backend

    @property
    def live(self) -> FakeHotkeyBackend | None:
        """The most recently built backend that is started and not stopped."""
        for backend in reversed(self.built):
            if backend.started and backend.stop_calls == 0:
                return backend
        return None


def hotkey_service(*prepared: FakeHotkeyBackend) -> tuple[HotkeyService, FakeHotkeyBackends]:
    """``(service, backends)``: a real ``HotkeyService`` over fake backends -- no real listener, ever."""
    backends = FakeHotkeyBackends(*prepared)
    return HotkeyService(backend_factory=backends), backends


class Recorder:
    """A callable that records its calls (``calls``) and answers with ``answer``."""

    def __init__(self, answer=None) -> None:
        self.answer = answer
        self.calls: list[tuple] = []
        self.threads: list[threading.Thread] = []

    def __call__(self, *args):
        self.calls.append(args)
        self.threads.append(threading.current_thread())
        return self.answer(*args) if callable(self.answer) else self.answer


def failing_backend(reason: str = "Global hotkeys need Accessibility and Input Monitoring access on macOS"):
    return FakeHotkeyBackend(start_error=HotkeyError(reason))
