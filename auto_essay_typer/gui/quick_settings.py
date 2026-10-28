"""Pure mapping helpers for the Type page's quick-settings controls.

Kept free of any Qt import so the mapping logic itself -- the part most
worth getting exactly right -- is testable without constructing a single
widget. ``pages/type_page.py`` is the only importer.
"""

from __future__ import annotations

from collections.abc import Sequence

from auto_essay_typer.targets import TargetApp

#: Speed slider bounds, in words per minute.
SPEED_MIN = 20
SPEED_MAX = 160

#: (upper bound exclusive, label) pairs for the speed word, checked in order;
#: a value at or above every threshold falls through to _SPEED_WORD_ABOVE.
_SPEED_WORDS: tuple[tuple[float, str], ...] = (
    (35, "Slow"),
    (50, "Relaxed"),
    (75, "Natural"),
    (100, "Fast"),
)
_SPEED_WORD_ABOVE = "Very fast"


def speed_label(wpm: float) -> str:
    """The descriptive word for a WPM value.

    under 35 Slow, 35-49 Relaxed, 50-74 Natural, 75-99 Fast, 100+ Very fast.
    """
    for threshold, word in _SPEED_WORDS:
        if wpm < threshold:
            return word
    return _SPEED_WORD_ABOVE


#: (typo_rate_per_100, label) stops for the Mistakes slider, in order --
#: the slider's integer value is an index into this tuple.
MISTAKE_STOPS: tuple[tuple[float, str], ...] = (
    (0.0, "None"),
    (0.3, "Rare"),
    (1.2, "Natural"),
    (3.0, "Frequent"),
    (6.0, "Sloppy"),
)


def nearest_mistake_stop_index(typo_rate_per_100: float) -> int:
    """The MISTAKE_STOPS index whose rate is closest to ``typo_rate_per_100``.

    Used when a chosen profile's own ``typo_rate_per_100`` does not land
    exactly on one of the five canonical stops, so the Mistakes slider still
    snaps to a sensible position instead of raising.
    """
    return min(
        range(len(MISTAKE_STOPS)),
        key=lambda i: abs(MISTAKE_STOPS[i][0] - typo_rate_per_100),
    )


# -- the "Typing into" editor picker ---------------------------------------------------------------


def initial_target_id(saved_id: str, choices: Sequence[TargetApp]) -> str:
    """The editor the picker opens on: the saved choice if this system offers it, else the first.

    ``choices`` is ``targets.targets_for(current_os())`` -- Microsoft Word has
    no Linux release, TextEdit exists only on macOS -- so a settings file
    carried over from another machine may name an editor that is not listed here.
    """
    ids = [target.id for target in choices]
    return saved_id if saved_id in ids else ids[0]


def target_caution(target: TargetApp) -> str:
    """A reminder shown under an editor whose shortcuts have not been checked, or ``""``.

    Only the Google Docs shortcuts are transcribed from the vendor's own
    reference (``TargetApp.verified``); the rest are best-effort, so the person
    is told they may need adjusting and where.
    """
    if target.verified:
        return ""
    return (
        f"The {target.name} shortcuts are a best effort and haven't been checked against a "
        "live copy. If something doesn't format as expected, you can change them on the "
        "Advanced page."
    )
