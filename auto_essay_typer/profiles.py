"""Tunable parameters for the typing model, plus three presets."""

from collections.abc import Mapping
from dataclasses import Field, dataclass, fields, replace
from typing import get_args, get_origin


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

    def to_dict(self) -> dict[str, object]:
        """Serialize every field to a TOML-safe plain type.

        TOML has no tuple type, so the ``tuple[float, float]``/``tuple[int,
        int]`` range fields are widened to lists here; ``from_dict`` narrows
        them back on the way in.
        """
        result: dict[str, object] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            result[f.name] = list(value) if isinstance(value, tuple) else value
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "Profile":
        """Rebuild a Profile from a plain mapping (typically loaded TOML).

        Config files get hand-edited, so mistakes must fail loudly rather
        than silently constructing a profile that ignores the typo: unknown
        keys raise ``KeyError`` naming the bad key plus the valid ones, and
        a mis-sized tuple field raises a ``ValueError`` naming the field.
        """
        kwargs = {key: coerce_value(key, value) for key, value in data.items()}
        return cls(**kwargs)


def field_names() -> tuple[str, ...]:
    """Sorted Profile field names, derived from dataclass metadata.

    Never hardcode this list elsewhere: a 20th knob added to the dataclass
    above should not require touching this function or any of its callers.
    """
    return tuple(sorted(f.name for f in fields(Profile)))


def _field_map() -> dict[str, Field]:
    """Field objects keyed by name, used to look up each field's declared type."""
    return {f.name: f for f in fields(Profile)}


def _coerce_scalar(py_type: type, raw: object) -> object:
    """Coerce one value (a TOML literal or a CLI override string) to ``py_type``."""
    try:
        return py_type(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"cannot parse {raw!r} as {py_type.__name__}") from exc


def _coerce_tuple(f: Field, raw: object) -> tuple:
    """Coerce a range field's value, accepting a list/tuple (TOML) or a
    comma-separated string (CLI override) as long as the arity matches."""
    elem_types = get_args(f.type)
    if isinstance(raw, str):
        parts: list[object] = [p.strip() for p in raw.split(",")]
    elif isinstance(raw, (list, tuple)):
        parts = list(raw)
    else:
        raise ValueError(f"expects {len(elem_types)} comma-separated values, got {raw!r}")
    if len(parts) != len(elem_types):
        raise ValueError(
            f"expects exactly {len(elem_types)} values, got {len(parts)}: {raw!r}"
        )
    return tuple(_coerce_scalar(t, v) for t, v in zip(elem_types, parts))


def coerce_value(name: str, raw: object) -> object:
    """Coerce ``raw`` to the type declared for Profile field ``name``.

    Shared by ``Profile.from_dict`` (TOML values) and the CLI override
    parser (strings like ``"90"`` or ``"0.7,1.3"``) so both entry points
    reject unknown fields and malformed values identically.
    """
    field_map = _field_map()
    if name not in field_map:
        raise KeyError(
            f"unknown Profile field {name!r}; valid fields are {list(field_names())}"
        )
    f = field_map[name]
    try:
        if get_origin(f.type) is tuple:
            return _coerce_tuple(f, raw)
        return _coerce_scalar(f.type, raw)
    except ValueError as exc:
        raise ValueError(f"{name}: {exc}") from exc


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
