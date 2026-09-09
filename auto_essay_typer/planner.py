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


def _at_sentence_or_paragraph_boundary(
    text: str, index: int, previous: str | None
) -> bool:
    """True at the start of a new sentence or paragraph -- the only place a
    session break may land, so a break never lands mid-word.

    Mirrors the boundary test the revision block uses to find a safe place
    to back up over already-typed text: ``previous`` is the space (or the
    ``None`` used right after a reset) that follows sentence-ending
    punctuation. A paragraph break is simpler -- any newline qualifies,
    the same boundary ``thinking_pause_ms`` already treats specially.
    """
    if previous == "\n":
        return True
    return (
        previous in (" ", None)
        and index > 0
        and text[index - 2 : index - 1] in (".", "?", "!")
    )


def _revision_span_start(text: str, end: int, words: int) -> int:
    """Index of the start of the last ``words`` words ending at ``end``.

    Walks backwards over word/space pairs. Never crosses a line break, so a
    revision stays within the paragraph being written.
    """
    index = end
    for _ in range(words):
        while index > 0 and text[index - 1].isspace():
            if text[index - 1] == "\n":
                return index
            index -= 1
        while index > 0 and not text[index - 1].isspace():
            index -= 1
    return index


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

    next_break_at = rng.uniform(*profile.break_interval_ms) if profile.session_breaks else None

    index = 0
    while index < len(text):
        thinking = timing.thinking_pause_ms(previous, _word_at(text, index))
        if thinking > 0.0:
            actions.append(Pause(thinking))

        # A session break fires only once enough simulated typing time has
        # elapsed, and only at a sentence/paragraph boundary -- never
        # mid-word. It appends nothing but a Pause, so it cannot affect the
        # simulate(plan(text)) == text invariant.
        if (
            next_break_at is not None
            and timing.elapsed_ms >= next_break_at
            and _at_sentence_or_paragraph_boundary(text, index, previous)
        ):
            if rng.random() < profile.long_break_probability:
                break_ms = rng.uniform(*profile.long_break_ms)
            else:
                break_ms = rng.uniform(*profile.short_break_ms)
            actions.append(Pause(break_ms))
            timing.rest(break_ms * profile.break_fatigue_recovery)
            timing.burst = profile.post_break_burst
            previous = None
            next_break_at = timing.elapsed_ms + rng.uniform(*profile.break_interval_ms)
            continue

        # A revision fires only at a sentence boundary, once enough text exists
        # to back over. The span is retyped identically -- wording never changes.
        if (
            previous in (" ", None)
            and index > 0
            and text[index - 2 : index - 1] in (".", "?", "!")
            and rng.random() < profile.revision_rate_per_word
        ):
            words = rng.randint(*profile.revision_span_words)
            start = _revision_span_start(text, index, words)
            span = text[start:index]
            if span.strip():
                actions.append(Pause(rng.uniform(*profile.sentence_pause_ms)))
                for _ in range(len(span)):
                    actions.append(Pause(timing.backspace_interval_ms()))
                    actions.append(Backspace(1))
                previous = None
                for char in span:
                    emit(char, care=profile.retype_care_factor)

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
