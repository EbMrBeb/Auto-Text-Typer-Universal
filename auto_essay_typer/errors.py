"""Typo construction and correction planning.

Every typo is a ``(wrong, correct)`` pair where ``correct`` is the exact span
of source text the typo consumes. Correction is therefore pure arithmetic:
backspace ``len(wrong) + len(tail)`` characters and retype ``correct + tail``.
No typo can survive into the output, because the planner always emits the
correction alongside the mistake.
"""

import random
from dataclasses import dataclass
from enum import StrEnum

from auto_essay_typer import layout


class TypoKind(StrEnum):
    TRANSPOSITION = "transposition"
    SUBSTITUTION = "substitution"
    DOUBLING = "doubling"
    OMISSION = "omission"


# Weights approximate observed real-world typo frequencies.
_KIND_WEIGHTS: dict[TypoKind, float] = {
    TypoKind.TRANSPOSITION: 0.35,
    TypoKind.SUBSTITUTION: 0.30,
    TypoKind.DOUBLING: 0.20,
    TypoKind.OMISSION: 0.15,
}


@dataclass(frozen=True, slots=True)
class Typo:
    """A mistake, and the source span it stands in for."""

    kind: TypoKind
    wrong: str
    correct: str


def _is_typo_site(text: str, index: int) -> bool:
    """True if a typo may be placed at ``index``.

    Whitespace is excluded because slips on the space bar read as unnatural,
    and the final character is excluded because nothing follows it to make a
    delayed notice plausible.
    """
    if index >= len(text) - 1:
        return False
    char = text[index]
    return not char.isspace() and layout.key_info(char) is not None


def _build(kind: TypoKind, text: str, index: int, rng: random.Random) -> Typo | None:
    """Construct one typo of the given kind, or None if infeasible here."""
    char = text[index]
    match kind:
        case TypoKind.TRANSPOSITION:
            nxt = text[index + 1]
            if nxt.isspace() or nxt == char or layout.key_info(nxt) is None:
                return None
            return Typo(kind, wrong=nxt + char, correct=char + nxt)
        case TypoKind.SUBSTITUTION:
            options = layout.neighbors(char)
            if not options:
                return None
            return Typo(kind, wrong=rng.choice(options), correct=char)
        case TypoKind.DOUBLING:
            return Typo(kind, wrong=char * 2, correct=char)
        case TypoKind.OMISSION:
            return Typo(kind, wrong="", correct=char)
    return None


def make_typo(
    text: str,
    index: int,
    rng: random.Random,
    forced_kind: TypoKind | None = None,
) -> Typo | None:
    """Build a typo at ``index``, or None if no kind is feasible there.

    When ``forced_kind`` is given only that kind is attempted, which keeps
    tests deterministic. Otherwise kinds are tried in weighted random order so
    that an infeasible first choice degrades to a plausible alternative rather
    than skipping the mistake entirely.
    """
    if not _is_typo_site(text, index):
        return None
    if forced_kind is not None:
        return _build(forced_kind, text, index, rng)

    remaining = dict(_KIND_WEIGHTS)
    while remaining:
        kinds = list(remaining)
        chosen = rng.choices(kinds, weights=[remaining[k] for k in kinds], k=1)[0]
        typo = _build(chosen, text, index, rng)
        if typo is not None:
            return typo
        del remaining[chosen]
    return None


def sample_notice_distance(rng: random.Random) -> int:
    """How many more source characters get typed before the error is spotted.

    This distribution is what makes correction read as human rather than
    scripted: a bot fixes errors instantly, a person types on for a beat first.
    Weighted toward short distances -- most slips are caught within the word --
    with a thin tail for errors noticed a phrase later.

    Because correction is unconditional, this affects only *how much* text gets
    backspaced, never whether the mistake survives.
    """
    roll = rng.random()
    if roll < 0.45:
        return 0
    if roll < 0.80:
        return rng.randint(1, 4)
    if roll < 0.95:
        return rng.randint(5, 10)
    return rng.randint(11, 25)


def plan_correction(typo: Typo, tail: str) -> tuple[int, str]:
    """Return ``(backspace_count, retype_text)`` to repair ``typo``.

    ``tail`` is the correctly typed source text that followed the mistake
    before it was noticed. Everything from the cursor back through the error is
    deleted, then the whole span is retyped correctly.
    """
    return len(typo.wrong) + len(tail), typo.correct + tail
