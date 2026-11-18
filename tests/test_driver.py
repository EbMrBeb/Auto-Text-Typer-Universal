import contextlib
from collections.abc import Iterator

import pytest
from pynput.keyboard import Key

from auto_essay_typer import platforms, targets
from auto_essay_typer.actions import Backspace, Format, FormatOp, KeyPress, Pause
from auto_essay_typer.driver import (
    Driver,
    PynputKeyboard,
    RecordingKeyboard,
    RunResult,
    UnsupportedFormatError,
)
from auto_essay_typer.keymaps import GOOGLE_DOCS_MAC, NO_OP
from auto_essay_typer.planner import plan
from auto_essay_typer.platforms import OperatingSystem
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
    # keymap pinned explicitly: this test is about interleaving order, not
    # keymap resolution, and the assertions below check against
    # GOOGLE_DOCS_MAC specifically.
    d = Driver(
        keyboard=keyboard,
        sleep=lambda _: None,
        countdown_seconds=0,
        keymap=GOOGLE_DOCS_MAC,
    )
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


def test_default_keymap_is_google_docs_mac(monkeypatch):
    # Pinned to macOS regardless of host OS, since this test is specifically
    # about what the default resolves to *on macOS* (as opposed to
    # test_default_keymap_resolves_per_os_at_construction_time, which checks
    # the general per-OS resolution behavior).
    monkeypatch.setattr(platforms, "current_os", lambda: OperatingSystem.MACOS)
    d, keyboard, _ = driver()
    d.run([Format(FormatOp.TOGGLE_ITALIC)])
    assert keyboard.chords == [GOOGLE_DOCS_MAC[FormatOp.TOGGLE_ITALIC]]


def test_default_keymap_resolves_per_os_at_construction_time(monkeypatch):
    """The default keymap must be resolved fresh when Driver() is constructed,
    using the OS current at that moment -- not frozen to macOS by a
    module-level default evaluated once at import time."""
    monkeypatch.setattr(platforms, "current_os", lambda: OperatingSystem.WINDOWS)
    d, keyboard, _ = driver()

    # TOGGLE_STRIKETHROUGH is alt+shift+5 on Windows -- a deliberate
    # non-substitution, not a cmd->ctrl swap of mac's cmd+shift+x (see
    # targets.py) -- so this can only pass if the Windows keymap was
    # genuinely resolved, not silently defaulted to GOOGLE_DOCS_MAC.
    d.run([Format(FormatOp.TOGGLE_STRIKETHROUGH)])

    expected = targets.keymap_for("google-docs", OperatingSystem.WINDOWS)[
        FormatOp.TOGGLE_STRIKETHROUGH
    ]
    assert keyboard.chords == [expected]


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


def test_format_action_with_no_op_chord_completes_without_pressing_keys():
    # A FormatOp mapped to keymaps.NO_OP is still "handled": _check_keymap_covers
    # checks key presence (`used_ops - self._keymap.keys()`), not chord
    # truthiness, so this must run to completion instead of raising
    # UnsupportedFormatError, and the recorded chord is the empty tuple itself.
    d, keyboard, _ = driver(keymap={FormatOp.CLEAR_FORMATTING: NO_OP})

    result = d.run([Format(FormatOp.CLEAR_FORMATTING)])

    assert result is RunResult.COMPLETED
    assert keyboard.chords == [()]


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
    # keymap is pinned explicitly (rather than left at the OS-resolved
    # default) since GOOGLE_DOCS_MAC is what the final assertion checks
    # against, and this test isn't about keymap resolution at all.
    d = Driver(
        keyboard=keyboard,
        sleep=lambda _: None,
        countdown_seconds=0,
        keymap=GOOGLE_DOCS_MAC,
    )
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


def test_pynput_keyboard_taps_space_with_modifier_held():
    controller = _FakeController()
    keyboard = PynputKeyboard(controller=controller)

    keyboard.press_chord(("ctrl", "space"))

    assert controller.events == [
        ("press", Key.ctrl),
        ("tap", Key.space),
        ("release", Key.ctrl),
    ]


def test_pynput_keyboard_press_chord_no_op_presses_nothing():
    controller = _FakeController()
    keyboard = PynputKeyboard(controller=controller)

    keyboard.press_chord(NO_OP)

    assert controller.events == []
