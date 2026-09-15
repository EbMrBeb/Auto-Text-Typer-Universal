import contextlib
from collections.abc import Iterator

import pytest
from pynput.keyboard import Key

from auto_essay_typer.actions import Backspace, Format, FormatOp, KeyPress, Pause
from auto_essay_typer.driver import (
    Driver,
    PynputKeyboard,
    RecordingKeyboard,
    RunResult,
    UnsupportedFormatError,
)
from auto_essay_typer.keymaps import GOOGLE_DOCS_MAC
from auto_essay_typer.planner import plan
from auto_essay_typer.profiles import Profile


def driver(**kwargs):
    """Countdown defaults to 0 here so it does not pollute sleep assertions."""
    kwargs.setdefault("countdown_seconds", 0)
    keyboard = RecordingKeyboard()
    slept: list[float] = []
    return Driver(keyboard=keyboard, sleep=slept.append, **kwargs), keyboard, slept


def test_driver_reproduces_planned_text():
    d, keyboard, _ = driver()
    text = "The quick brown fox. " * 5
    d.run(plan(text, Profile(typo_rate_per_100=6.0), seed=3))
    assert keyboard.text == text


def test_driver_reports_completion():
    d, _, _ = driver()
    assert d.run([KeyPress("a")]) is RunResult.COMPLETED


def test_driver_sleeps_for_pauses():
    d, _, slept = driver()
    d.run([Pause(120.0), KeyPress("a")])
    assert sum(slept) == pytest.approx(0.120, abs=1e-6)


def test_long_pauses_are_sliced_for_responsiveness():
    d, _, slept = driver()
    d.run([Pause(1000.0)])
    assert len(slept) > 1
    assert max(slept) <= 0.051


def test_abort_stops_mid_stream():
    keyboard = RecordingKeyboard()
    # Countdown must be 0, otherwise the abort fires during the countdown and
    # nothing is ever typed.
    d = Driver(keyboard=keyboard, sleep=lambda _: None, countdown_seconds=0)
    d._sleep = lambda _: d.abort()
    result = d.run([KeyPress("a"), Pause(50.0), KeyPress("b")])
    assert result is RunResult.ABORTED
    assert keyboard.text == "a"


def test_countdown_invokes_the_callback_each_second():
    d, _, _ = driver(countdown_seconds=3)
    ticks: list[int] = []
    d.run([KeyPress("a")], on_countdown=ticks.append)
    assert ticks == [3, 2, 1]


def test_progress_callback_reports_characters_typed():
    d, _, _ = driver()
    seen: list[tuple[int, int]] = []
    d.run([KeyPress("a"), KeyPress("b")], on_progress=lambda done, total: seen.append((done, total)))
    assert seen[-1] == (2, 2)


def test_recording_keyboard_rejects_backspace_underflow():
    keyboard = RecordingKeyboard()
    with pytest.raises(ValueError):
        keyboard.backspace(1)


# --- Format / keymap ---------------------------------------------------------


class _LoggingKeyboard(RecordingKeyboard):
    """Records every call -- chars, backspaces, and chords -- in one ordered log.

    The plain ``RecordingKeyboard`` keeps chars (``text``) and chords
    (``chords``) in two separate places, which cannot show how they
    interleave. This subclass adds one ordered event log so a test can
    assert a Format action lands exactly where it belongs relative to the
    surrounding keystrokes, not just that it happened at some point.
    """

    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple[str, object]] = []

    def press_char(self, char: str) -> None:
        super().press_char(char)
        self.events.append(("char", char))

    def backspace(self, count: int) -> None:
        super().backspace(count)
        self.events.append(("backspace", count))

    def press_chord(self, keys: tuple[str, ...]) -> None:
        super().press_chord(keys)
        self.events.append(("chord", keys))


def test_format_presses_mapped_chord_interleaved_with_characters():
    keyboard = _LoggingKeyboard()
    d = Driver(keyboard=keyboard, sleep=lambda _: None, countdown_seconds=0)
    actions = [
        KeyPress("h"),
        KeyPress("i"),
        Format(FormatOp.TOGGLE_BOLD),
        KeyPress("b"),
        Format(FormatOp.ALIGN_CENTER),
        KeyPress("!"),
    ]

    assert d.run(actions) is RunResult.COMPLETED

    assert keyboard.events == [
        ("char", "h"),
        ("char", "i"),
        ("chord", GOOGLE_DOCS_MAC[FormatOp.TOGGLE_BOLD]),
        ("char", "b"),
        ("chord", GOOGLE_DOCS_MAC[FormatOp.ALIGN_CENTER]),
        ("char", "!"),
    ]
    assert keyboard.text == "hib!"
    assert keyboard.chords == [
        GOOGLE_DOCS_MAC[FormatOp.TOGGLE_BOLD],
        GOOGLE_DOCS_MAC[FormatOp.ALIGN_CENTER],
    ]


def test_default_keymap_is_google_docs_mac():
    d, keyboard, _ = driver()
    d.run([Format(FormatOp.TOGGLE_ITALIC)])
    assert keyboard.chords == [GOOGLE_DOCS_MAC[FormatOp.TOGGLE_ITALIC]]


def test_custom_keymap_is_used_instead_of_the_default():
    custom_chord = ("ctrl", "alt", "z")
    assert custom_chord != GOOGLE_DOCS_MAC[FormatOp.TOGGLE_BOLD]  # actually exercises the override

    d, keyboard, _ = driver(keymap={FormatOp.TOGGLE_BOLD: custom_chord})
    d.run([Format(FormatOp.TOGGLE_BOLD)])

    assert keyboard.chords == [custom_chord]


def test_missing_format_op_raises_before_countdown_and_keystrokes():
    d, keyboard, _ = driver(countdown_seconds=3, keymap={})
    ticks: list[int] = []

    with pytest.raises(UnsupportedFormatError, match="toggle_bold"):
        d.run([KeyPress("a"), Format(FormatOp.TOGGLE_BOLD)], on_countdown=ticks.append)

    assert ticks == []
    assert keyboard.text == ""
    assert keyboard.chords == []


def test_missing_format_op_error_names_every_missing_op():
    d, _, _ = driver(keymap={})
    with pytest.raises(UnsupportedFormatError) as excinfo:
        d.run([Format(FormatOp.TOGGLE_BOLD), Format(FormatOp.ALIGN_CENTER)])
    message = str(excinfo.value)
    assert "toggle_bold" in message
    assert "align_center" in message


class _UnknownAction:
    """Stands in for a future Action variant the driver's match statement doesn't know."""


def test_unknown_action_type_raises_type_error():
    d, _, _ = driver()
    with pytest.raises(TypeError):
        d.run([_UnknownAction()])


def test_abort_stops_mid_stream_with_format_actions_present():
    keyboard = RecordingKeyboard()
    # Countdown must be 0, otherwise the abort fires during the countdown and
    # nothing is ever typed (same reasoning as test_abort_stops_mid_stream).
    d = Driver(keyboard=keyboard, sleep=lambda _: None, countdown_seconds=0)
    d._sleep = lambda _: d.abort()
    result = d.run([KeyPress("a"), Format(FormatOp.TOGGLE_BOLD), Pause(50.0), KeyPress("b")])
    assert result is RunResult.ABORTED
    assert keyboard.text == "a"
    assert keyboard.chords == [GOOGLE_DOCS_MAC[FormatOp.TOGGLE_BOLD]]


# --- PynputKeyboard.press_chord (fake controller) -----------------------------


class _FakeController:
    """Mimics pynput.keyboard.Controller's press/release/tap/pressed interface.

    ``pressed`` mirrors the real Controller's own guarantee (see
    ``pynput.keyboard._base.Controller.pressed``, a ``@contextmanager`` built
    on try/finally): every key it pressed is released on the way out,
    exception or not. That guarantee is what ``PynputKeyboard`` leans on to
    release modifiers when a tap fails, so the fake must honor it too, or the
    "released when tap raises" test would pass for the wrong reason.
    """

    def __init__(self, *, raise_on_tap: bool = False) -> None:
        self.events: list[tuple[str, object]] = []
        self._raise_on_tap = raise_on_tap

    def press(self, key: object) -> None:
        self.events.append(("press", key))

    def release(self, key: object) -> None:
        self.events.append(("release", key))

    def tap(self, key: object) -> None:
        self.events.append(("tap", key))
        if self._raise_on_tap:
            raise RuntimeError("boom")

    @contextlib.contextmanager
    def pressed(self, *keys: object) -> Iterator[None]:
        for key in keys:
            self.press(key)
        try:
            yield
        finally:
            for key in reversed(keys):
                self.release(key)


def test_pynput_keyboard_holds_both_modifiers_and_taps_final_character():
    controller = _FakeController()
    keyboard = PynputKeyboard(controller=controller)

    keyboard.press_chord(("cmd", "shift", "8"))

    assert controller.events == [
        ("press", Key.cmd),
        ("press", Key.shift),
        ("tap", "8"),
        ("release", Key.shift),
        ("release", Key.cmd),
    ]


def test_pynput_keyboard_taps_named_key_with_modifier_held():
    controller = _FakeController()
    keyboard = PynputKeyboard(controller=controller)

    keyboard.press_chord(("shift", "tab"))

    assert controller.events == [
        ("press", Key.shift),
        ("tap", Key.tab),
        ("release", Key.shift),
    ]


def test_pynput_keyboard_releases_modifiers_when_tap_raises():
    controller = _FakeController(raise_on_tap=True)
    keyboard = PynputKeyboard(controller=controller)

    with pytest.raises(RuntimeError):
        keyboard.press_chord(("cmd", "shift", "8"))

    assert controller.events == [
        ("press", Key.cmd),
        ("press", Key.shift),
        ("tap", "8"),
        ("release", Key.shift),
        ("release", Key.cmd),
    ]
