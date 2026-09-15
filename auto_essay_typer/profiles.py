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

    # --- session breaks ---------------------------------------------------
    session_breaks: bool = True
    break_interval_ms: tuple[float, float] = (420_000.0, 900_000.0)   # 7-15 min of typing
    short_break_ms: tuple[float, float] = (20_000.0, 90_000.0)        # 20-90 s away
    long_break_probability: float = 0.2
    long_break_ms: tuple[float, float] = (120_000.0, 480_000.0)       # 2-8 min away
    # Burst-walk value set on return from a break. This is an INTERVAL
    # multiplier, not a speed: values ABOVE 1.0 make typing SLOWER, values
    # BELOW 1.0 make it FASTER. Default sits above the burst walk's neutral
    # 1.0 so a resumed session starts with a re-entry dip (re-reading,
    # re-orienting) rather than an instant speed-up; the mean-reverting walk
    # then carries the multiplier back toward 1.0 over the following
    # keystrokes, while break_fatigue_recovery (below) supplies the real
    # recovery by partially clearing the fatigue clock.
    post_break_burst: float = 1.15
    break_fatigue_recovery: float = 0.6  # fraction of the fatigue clock a break clears

    # --- formatting --------------------------------------------------------
    # How long a Format action (a Google Docs keyboard shortcut like cmd+b)
    # takes to press -- reaching for a chord is slower than a plain keystroke.
    format_shortcut_ms: tuple[float, float] = (180.0, 520.0)

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
        if not 0.0 <= self.long_break_probability <= 1.0:
            raise ValueError(
                f"long_break_probability must be in [0, 1], got {self.long_break_probability}"
            )
        if not 0.0 <= self.break_fatigue_recovery <= 1.0:
            raise ValueError(
                f"break_fatigue_recovery must be in [0, 1], got {self.break_fatigue_recovery}"
            )
        if self.post_break_burst <= 0:
            raise ValueError(
                f"post_break_burst must be positive, got {self.post_break_burst}"
            )
        format_low, format_high = self.format_shortcut_ms
        if format_low < 0:
            raise ValueError(
                f"format_shortcut_ms low must be non-negative, got {format_low}"
            )
        if format_low > format_high:
            raise ValueError(
                f"format_shortcut_ms low must be <= high, got {self.format_shortcut_ms}"
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


_TRUE_STRINGS = frozenset({"true", "yes", "1"})
_FALSE_STRINGS = frozenset({"false", "no", "0"})


def _coerce_bool(raw: object) -> bool:
    """Coerce a TOML boolean or a CLI override string to ``bool``.

    ``bool`` is a subclass of ``int`` in Python, so a naive ``bool(raw)`` on
    a non-empty string like ``"false"`` would silently return ``True``.
    Real booleans (as ``tomllib`` produces for a TOML ``true``/``false``
    literal) pass through unchanged; strings are parsed explicitly and
    case-insensitively, accepting ``true/false``, ``yes/no``, ``1/0``.
    """
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        key = raw.strip().lower()
        if key in _TRUE_STRINGS:
            return True
        if key in _FALSE_STRINGS:
            return False
    raise ValueError(f"cannot parse {raw!r} as bool")


def _coerce_scalar(py_type: type, raw: object) -> object:
    """Coerce one value (a TOML literal or a CLI override string) to ``py_type``.

    ``bool`` is checked before the generic path: ``bool`` is a subclass of
    ``int``, so letting a bool-typed field fall through to ``py_type(raw)``
    would silently mangle it (see ``_coerce_bool``).
    """
    if py_type is bool:
        return _coerce_bool(raw)
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
