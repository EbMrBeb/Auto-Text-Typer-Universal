"""Suite-wide fixtures.

These keep pynput from ever being real in a test:

* ``_isolated_keyboard_backend`` (autouse) gives every test an empty cache for the shared
  ``Controller`` (``driver.preload_keyboard_backend``) and makes building a real one impossible.
* ``fake_pynput`` swaps ``pynput.keyboard.Controller`` for a recording fake, for
  the tests that exercise the cache, the thread guard and the default-keyboard
  paths without typing anything or touching macOS.
* ``_no_real_hotkey_listener`` (autouse) makes starting a real global hotkey listener
  impossible, and fails the test that tries.

And these keep the Qt tests from crashing the process: ``_only_the_main_thread_collects_garbage``
and ``_collect_garbage_after_each_test`` (both autouse), explained on the first.
"""

from __future__ import annotations

import contextlib
import gc
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


@pytest.fixture(autouse=True, scope="session")
def _only_the_main_thread_collects_garbage():
    """Keep Python's cycle collector off the worker threads, for the whole session.

    The collector runs on whichever thread happens to allocate when its threshold is
    crossed, and a typing run's worker thread allocates a lot. If it finds a Qt widget
    in a cycle (a page or dialog an earlier test abandoned) it destroys that widget *on
    the worker thread*; Qt then sends events through the application's Python event
    filter from the wrong thread and PySide segfaults inside its trampoline, taking the
    whole test process with it -- at a random moment, in a random test. Automatic
    collection is therefore off, and ``_collect_garbage_after_each_test`` collects on the
    main thread instead, where those objects belong.
    """
    was_enabled = gc.isenabled()
    gc.disable()
    yield
    if was_enabled:
        gc.enable()


@pytest.fixture(autouse=True)
def _collect_garbage_after_each_test():
    """Collect the test's cycles now, on the main thread (see the session fixture above).

    Function-scoped autouse fixtures are set up first and torn down last, so this runs
    after ``qtbot`` has closed the test's widgets.
    """
    yield
    gc.collect()


@pytest.fixture(autouse=True)
def _no_real_hotkey_listener(monkeypatch):
    """Make it impossible for a test to start a real global hotkey listener, and fail it if one tries.

    A real ``GlobalHotKeys`` captures every key the developer types, system-wide, and on macOS
    (without the main-thread handling in ``gui/hotkeys.py``) kills the process outright. The app
    starts one at launch, so any test that builds the window and starts the shell without
    injecting a fake hotkey backend would install a real one. *Constructing* a listener is
    harmless (a few tests do, to check it parses its hotkeys) -- it is ``start()`` that listens.

    The attempt is recorded and the listener's ``start`` raises, which the hotkey service
    treats as "hotkeys unavailable", so the test would otherwise carry on and pass quietly;
    the assertion at teardown is what makes it fail.
    """
    attempts: list[str] = []
    try:
        from pynput._util import AbstractListener
    except ImportError:  # pynput unusable here: nothing real can be started either
        yield attempts
        return

    def refuse(self, *args, **kwargs):
        attempts.append(type(self).__name__)
        raise RuntimeError("tests may not start a real global hotkey listener")

    monkeypatch.setattr(AbstractListener, "start", refuse)
    yield attempts
    assert attempts == [], f"a test started a real global hotkey listener: {attempts}; inject a fake backend"


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
