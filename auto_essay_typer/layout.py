"""Static QWERTY geometry.

Fingers are numbered 0-9 left pinky to right pinky, with 5 reserved for the
thumbs (space). Columns carry the physical row stagger so that Euclidean
distance between two keys approximates real finger travel.

Characters outside this layout (accented letters, unicode punctuation) return
``None`` from :func:`key_info` and are treated as neutral everywhere: no shift
penalty, no neighbours, never chosen as a typo site.
"""

from dataclasses import dataclass
from enum import Enum
from functools import cache


class Hand(Enum):
    LEFT = "L"
    RIGHT = "R"


@dataclass(frozen=True, slots=True)
class KeyInfo:
    char: str
    row: int
    x: float
    finger: int
    hand: Hand


_ROWS: dict[int, str] = {
    0: "1234567890-=",
    1: "qwertyuiop[]\\",
    2: "asdfghjkl;'",
    3: "zxcvbnm,./",
}

# Horizontal offset of each row, in key widths, approximating the stagger.
_ROW_OFFSET: dict[int, float] = {0: 0.0, 1: 0.5, 2: 0.75, 3: 1.25}

_FINGER: dict[str, int] = {}
for _finger, _keys in {
    0: "1qaz`",
    1: "2wsx",
    2: "3edc",
    3: "45rfvtgb",
    6: "67yhnujm",
    7: "8ik,",
    8: "9ol.",
    9: "0p;/-=[]\\'",
}.items():
    for _key in _keys:
        _FINGER[_key] = _finger

_SHIFTED: dict[str, str] = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7",
    "*": "8", "(": "9", ")": "0", "_": "-", "+": "=", "{": "[", "}": "]",
    "|": "\\", ":": ";", '"': "'", "<": ",", ">": ".", "?": "/", "~": "`",
}

_SPACE = KeyInfo(char=" ", row=4, x=5.0, finger=5, hand=Hand.LEFT)

_BASE: dict[str, KeyInfo] = {" ": _SPACE}
for _row, _chars in _ROWS.items():
    for _col, _char in enumerate(_chars):
        _finger = _FINGER.get(_char, 3)
        _BASE[_char] = KeyInfo(
            char=_char,
            row=_row,
            x=_col + _ROW_OFFSET[_row],
            finger=_finger,
            hand=Hand.LEFT if _finger <= 4 else Hand.RIGHT,
        )

# The backtick is a real key to the left of "1", but it is deliberately not
# part of _ROWS[0]: prepending it there would shift every column index in the
# row by one (e.g. "1" would move to x=1.0 while "q" stays at x=0.5, putting
# "q" left of "1"), corrupting the row's neighbour geometry. It is added here
# instead, positioned explicitly.
_BASE["`"] = KeyInfo(char="`", row=0, x=-1.0, finger=0, hand=Hand.LEFT)

# Maximum centre-to-centre distance still counted as "adjacent".
_ADJACENCY_RADIUS = 1.2


def _base_char(char: str) -> str | None:
    """Return the unshifted key that produces ``char``, or None if unmodelled.

    Every candidate is validated against ``_BASE`` before being returned, so
    a mismatch between ``_FINGER``/``_SHIFTED`` and ``_ROWS`` (a key referenced
    by one but never actually added to ``_BASE``) degrades to ``None`` instead
    of surfacing as a ``KeyError`` in callers.
    """
    if char in _BASE:
        return char
    shifted = _SHIFTED.get(char)
    if shifted is not None and shifted in _BASE:
        return shifted
    lowered = char.lower()
    if lowered != char and lowered in _BASE:
        return lowered
    return None


def key_info(char: str) -> KeyInfo | None:
    """Return the physical key that produces ``char``, or None if unmodelled."""
    base = _base_char(char)
    return _BASE[base] if base is not None else None


def needs_shift(char: str) -> bool:
    """True if producing ``char`` requires holding shift."""
    if char in _SHIFTED:
        return True
    return char.isalpha() and char.isupper() and _base_char(char) is not None


@cache
def neighbors(char: str) -> list[str]:
    """Physically adjacent keys, in the same shift state as ``char``.

    Adjacency is Euclidean distance on the staggered grid, so it includes
    diagonals. Shift state is preserved because a slip while holding shift
    produces another shifted character.
    """
    info = key_info(char)
    if info is None or info.row == 4:
        return []
    shifted = needs_shift(char)
    result: list[str] = []
    for other in _BASE.values():
        if other.char == info.char or other.row == 4:
            continue
        distance = ((other.row - info.row) ** 2 + (other.x - info.x) ** 2) ** 0.5
        if distance <= _ADJACENCY_RADIUS:
            result.append(other.char.upper() if shifted else other.char)
    return result


def same_finger(first: str, second: str) -> bool:
    """True if both characters are struck by the same finger on different keys."""
    a, b = key_info(first), key_info(second)
    if a is None or b is None:
        return False
    return a.finger == b.finger and a.char != b.char


def same_hand(first: str, second: str) -> bool:
    """True if both characters are struck by the same hand."""
    a, b = key_info(first), key_info(second)
    if a is None or b is None:
        return False
    return a.hand is b.hand
