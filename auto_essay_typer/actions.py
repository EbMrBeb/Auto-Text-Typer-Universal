"""The primitive actions a typing plan is made of.

A plan is a flat list of these. Delays are carried as explicit ``Pause``
actions rather than as attributes on ``KeyPress`` so that the simulator and
the driver share one uniform interpretation of the stream. ``Format`` is the
one action that does not touch on-screen text at all: it presses a Google
Docs keyboard shortcut (bold, a heading style, a list toggle, ...), which is
why ``simulate()`` -- the plain-text replay used by the core invariant --
ignores it entirely while ``simulate_document()`` tracks its effect.
"""

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class KeyPress:
    """Emit exactly one character."""

    char: str

    def __post_init__(self) -> None:
        if len(self.char) != 1:
            raise ValueError(f"KeyPress takes exactly one character, got {self.char!r}")


@dataclass(frozen=True, slots=True)
class Backspace:
    """Delete ``count`` characters backwards from the cursor."""

    count: int

    def __post_init__(self) -> None:
        if self.count < 1:
            raise ValueError(f"Backspace count must be >= 1, got {self.count}")


@dataclass(frozen=True, slots=True)
class Pause:
    """Wait ``ms`` milliseconds."""

    ms: float

    def __post_init__(self) -> None:
        if self.ms < 0:
            raise ValueError(f"Pause must be non-negative, got {self.ms}")


class FormatOp(StrEnum):
    """A Google Docs formatting command, applied via its keyboard shortcut.

    Every member's value is its lowercased name (enforced by
    ``tests/test_actions.py``), so the enum can be round-tripped through
    plain strings (config files, CLI args) without a separate lookup table.
    """

    CLEAR_FORMATTING = "clear_formatting"
    TOGGLE_BOLD = "toggle_bold"
    TOGGLE_ITALIC = "toggle_italic"
    TOGGLE_UNDERLINE = "toggle_underline"
    TOGGLE_STRIKETHROUGH = "toggle_strikethrough"
    SET_NORMAL = "set_normal"
    SET_TITLE = "set_title"
    SET_SUBTITLE = "set_subtitle"
    SET_HEADING_1 = "set_heading_1"
    SET_HEADING_2 = "set_heading_2"
    SET_HEADING_3 = "set_heading_3"
    SET_HEADING_4 = "set_heading_4"
    SET_HEADING_5 = "set_heading_5"
    SET_HEADING_6 = "set_heading_6"
    ALIGN_LEFT = "align_left"
    ALIGN_CENTER = "align_center"
    ALIGN_RIGHT = "align_right"
    ALIGN_JUSTIFIED = "align_justified"
    TOGGLE_BULLET_LIST = "toggle_bullet_list"
    TOGGLE_NUMBERED_LIST = "toggle_numbered_list"
    INDENT = "indent"
    OUTDENT = "outdent"


@dataclass(frozen=True, slots=True)
class Format:
    """Press the keyboard shortcut for one formatting command."""

    op: FormatOp


Action = KeyPress | Backspace | Pause | Format
