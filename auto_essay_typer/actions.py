"""The three primitive actions a typing plan is made of.

A plan is a flat list of these. Delays are carried as explicit ``Pause``
actions rather than as attributes on ``KeyPress`` so that the simulator and
the driver share one uniform interpretation of the stream.
"""

from dataclasses import dataclass


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


Action = KeyPress | Backspace | Pause
