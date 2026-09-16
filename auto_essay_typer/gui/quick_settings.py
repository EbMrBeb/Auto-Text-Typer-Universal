"""Pure mapping helpers for the Type page's quick-settings controls.

Kept free of any Qt import so the mapping logic itself -- the part most
worth getting exactly right -- is testable without constructing a single
widget. ``pages/type_page.py`` is the only importer.
"""

from __future__ import annotations

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
