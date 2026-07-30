"""Tunable parameters for the typing model, plus three presets."""

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class Profile:
    """All knobs for the typing model.

    Timing values are milliseconds. Ranges are inclusive ``(low, high)`` pairs
    sampled uniformly unless stated otherwise.
    """

    # --- speed -----------------------------------------------------------
    wpm: float = 65.0
    lognormal_sigma: float = 0.35
    burst_sigma: float = 0.06
    burst_reversion: float = 0.08
    burst_range: tuple[float, float] = (0.8, 1.25)
    fatigue_max: float = 0.15
    fatigue_tau_ms: float = 1_200_000.0

    # --- thinking pauses -------------------------------------------------
    sentence_pause_ms: tuple[float, float] = (400.0, 1200.0)
    clause_pause_ms: tuple[float, float] = (120.0, 350.0)
    paragraph_pause_ms: tuple[float, float] = (1500.0, 5000.0)
    hesitation_probability: float = 0.02
    hesitation_ms: tuple[float, float] = (300.0, 1400.0)
    long_word_threshold: int = 9

    # --- mistakes --------------------------------------------------------
    typo_rate_per_100: float = 1.2
    oh_moment_ms: tuple[float, float] = (150.0, 400.0)
    backspace_ms: tuple[float, float] = (40.0, 60.0)
    retype_care_factor: float = 1.15

    # --- revision --------------------------------------------------------
    revision_rate_per_word: float = 1.0 / 500.0
    revision_span_words: tuple[int, int] = (2, 5)

    def __post_init__(self) -> None:
        if self.wpm <= 0:
            raise ValueError(f"wpm must be positive, got {self.wpm}")
        if self.typo_rate_per_100 < 0:
            raise ValueError(
                f"typo_rate_per_100 must be non-negative, got {self.typo_rate_per_100}"
            )
        if not 0.0 <= self.hesitation_probability <= 1.0:
            raise ValueError(
                f"hesitation_probability must be in [0, 1], got {self.hesitation_probability}"
            )

    def with_overrides(self, **changes: object) -> "Profile":
        """Return a copy with the given fields replaced."""
        return replace(self, **changes)


PRESETS: dict[str, Profile] = {
    "clean": Profile(typo_rate_per_100=0.3, revision_rate_per_word=1.0 / 800.0),
    "natural": Profile(typo_rate_per_100=1.2, revision_rate_per_word=1.0 / 500.0),
    "sloppy": Profile(typo_rate_per_100=3.0, revision_rate_per_word=1.0 / 300.0),
}


def preset(name: str) -> Profile:
    """Look up a preset by name, case-insensitively."""
    key = name.strip().lower()
    if key not in PRESETS:
        raise KeyError(f"unknown preset {name!r}; choose one of {sorted(PRESETS)}")
    return PRESETS[key]
