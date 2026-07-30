"""Turns text into an action stream.

Pure and seeded: no I/O, no clock, no keyboard. Given the same
``(text, profile, seed)`` this returns an identical list of actions, which is
what makes the core invariant checkable by pure computation and makes any
observed behaviour reproducible for debugging.
"""

import random

from auto_essay_typer.actions import Action, Backspace, KeyPress, Pause
from auto_essay_typer.errors import make_typo, plan_correction, sample_notice_distance
from auto_essay_typer.profiles import Profile
from auto_essay_typer.timing import TimingModel


def _word_at(text: str, index: int) -> str:
    """The whole word starting at ``index``, or '' if mid-word.

    Used to decide whether an upcoming word is long enough to hesitate before.
    """
    if index > 0 and not text[index - 1].isspace():
        return ""
    end = index
    while end < len(text) and not text[end].isspace():
        end += 1
    return text[index:end]


def plan(
    text: str,
    profile: Profile | None = None,
    seed: int | None = None,
) -> list[Action]:
    """Return the action stream that types ``text`` in a human-like way."""
    profile = profile or Profile()
    rng = random.Random(seed)
    timing = TimingModel(profile, rng)
    actions: list[Action] = []

    previous: str | None = None
    typo_probability = profile.typo_rate_per_100 / 100.0

    def emit(char: str, care: float = 1.0) -> None:
        """Append the pause-then-keypress pair for one character."""
        nonlocal previous
        interval = timing.keystroke_interval_ms(previous, char) * care
        actions.append(Pause(interval))
        actions.append(KeyPress(char))
        previous = char

    index = 0
    while index < len(text):
        thinking = timing.thinking_pause_ms(previous, _word_at(text, index))
        if thinking > 0.0:
            actions.append(Pause(thinking))

        typo = None
        if rng.random() < typo_probability:
            typo = make_typo(text, index, rng)

        if typo is None:
            emit(text[index])
            index += 1
            continue

        for char in typo.wrong:
            emit(char)

        # Keep typing correctly until the mistake is noticed. The tail stops at
        # a line break -- nobody starts a new paragraph without re-reading.
        consumed = len(typo.correct)
        distance = sample_notice_distance(rng)
        tail = text[index + consumed : index + consumed + distance]
        if "\n" in tail:
            tail = tail[: tail.index("\n")]
        for char in tail:
            emit(char)

        actions.append(Pause(rng.uniform(*profile.oh_moment_ms)))

        backspaces, retype = plan_correction(typo, tail)
        for _ in range(backspaces):
            actions.append(Pause(timing.backspace_interval_ms()))
            actions.append(Backspace(1))

        # Backspacing resets the digraph context: the last key struck was not a
        # character key.
        previous = None
        for char in retype:
            emit(char, care=profile.retype_care_factor)

        index += consumed + len(tail)

    return actions
