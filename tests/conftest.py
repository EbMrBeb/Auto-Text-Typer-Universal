"""Suite-wide fixtures.

The two here keep the shared pynput ``Controller`` (``driver.preload_keyboard_backend``)
from ever being a real one in a test:

* ``_isolated_keyboard_backend`` (autouse) gives every test an empty cache and makes
  building a real ``Controller`` impossible.
* ``fake_pynput`` swaps ``pynput.keyboard.Controller`` for a recording fake, for
  the tests that exercise the cache, the thread guard and the default-keyboard
  paths without typing anything or touching macOS.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator

import pytest

from auto_essay_typer import driver

try:
    import pynput.keyboard as _pynput_keyboard
except ImportError:  # pynput unusable here: tests that need it fail on their own import
    _pynput_keyboard = None


class _NoRealController:
    """Refuses to be built, so no test can create a real pynput ``Controller``."""

    def __init__(self) -> None:
        raise AssertionError(
            "a test tried to build a real pynput Controller; inject a keyboard or "
            "controller, or use the fake_pynput fixture"
        )


@pytest.fixture(autouse=True)
def _isolated_keyboard_backend(monkeypatch):
    """Start every test with no shared Controller, and no way to build a real one.

    ``preload_keyboard_backend()`` -- and any ``PynputKeyboard()`` built without an
    explicit controller -- caches one process-wide ``Controller``. Without this
    reset, the first test to build one would hand it to every later test in the
    process. And a real one would post real keystrokes into whatever window the
    developer has focused, or on macOS read the live keyboard layout, from a unit
    test. ``monkeypatch`` restores the original values afterwards even when a test
    replaced them.
    """
    monkeypatch.setattr(driver, "_shared_controller", None)
    if _pynput_keyboard is not None:
        monkeypatch.setattr(_pynput_keyboard, "Controller", _NoRealController)


class FakeController:
    """Records how it was built and used, instead of posting any keystroke.

    ``instances`` collects every one built since the fixture was created, and each
    remembers the thread it was built on (``built_on``) and every thread that
    typed with it (``used_on``) -- the two facts the macOS crash was about.
    """

    instances: list[FakeController]

    def __init__(self) -> None:
        self.built_on = threading.current_thread()
        self.used_on: list[threading.Thread] = []
        self.events: list[tuple[str, object]] = []
        type(self).instances.append(self)

    @property
    def text(self) -> str:
        """The text the recorded events would leave in a document."""
        from pynput.keyboard import Key

        buffer: list[str] = []
        for kind, value in self.events:
            if kind == "type":
                buffer.append(str(value))
            elif kind == "tap":
                if value is Key.backspace:
                    buffer.pop()
                elif value is Key.enter:
                    buffer.append("\n")
                elif value is Key.tab:
                    buffer.append("\t")
                elif isinstance(value, str):
                    buffer.append(value)
        return "".join(buffer)

    def _record(self, kind: str, value: object) -> None:
        self.used_on.append(threading.current_thread())
        self.events.append((kind, value))

    def press(self, key: object) -> None:
        self._record("press", key)

    def release(self, key: object) -> None:
        self._record("release", key)

    def tap(self, key: object) -> None:
        self._record("tap", key)

    def type(self, text: str) -> None:
        self._record("type", text)

    @contextlib.contextmanager
    def pressed(self, *keys: object) -> Iterator[None]:
        for key in keys:
            self.press(key)
        try:
            yield
        finally:
            for key in reversed(keys):
                self.release(key)


@pytest.fixture
def fake_pynput(monkeypatch):
    """Replace ``pynput.keyboard.Controller`` with a fresh recording fake class.

    Returns the class; its ``instances`` list is empty at the start of each test.
    """
    import pynput.keyboard

    class _Fake(FakeController):
        instances: list[FakeController] = []

    monkeypatch.setattr(pynput.keyboard, "Controller", _Fake)
    return _Fake
