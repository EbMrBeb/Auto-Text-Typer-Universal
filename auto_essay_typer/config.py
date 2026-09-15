"""Persistent, hand-editable TOML configuration for named typing profiles.

Reading uses the stdlib ``tomllib`` (Python 3.11+), which is read-only, so
this module also carries a small hand-rolled TOML writer (``_dump_toml``):
the schema Profile produces is flat and fully known ahead of time (every
value is a bool, a number, or a list of numbers), so a third-party
TOML-writing dependency would be more than the problem calls for.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path

from . import platforms
from .profiles import PRESETS, Profile, coerce_value, field_names

#: New, platform-neutral override -- takes precedence over everything else.
_CONFIG_ENV_OVERRIDE = "AUTO_TEXT_TYPER_CONFIG"
#: The pre-rename override name. Kept working indefinitely: anyone who set
#: this in a script or shell profile before the app was renamed should not
#: have it silently stop working.
_LEGACY_CONFIG_ENV_OVERRIDE = "AUTO_ESSAY_TYPER_CONFIG"
_CONFIG_FILE_NAME = "profiles.toml"


def config_path() -> Path:
    """Where the user's profiles.toml lives.

    Honours ``AUTO_TEXT_TYPER_CONFIG`` first (an explicit full-path
    override, used by tests to avoid ever touching the real config
    directory), then the older ``AUTO_ESSAY_TYPER_CONFIG`` (kept for
    backwards compatibility with configs/scripts set up before the app was
    renamed), then the platform-appropriate config directory
    (:func:`auto_essay_typer.platforms.config_dir`) -- macOS Application
    Support, Windows roaming AppData, or the Linux XDG config home.

    See :func:`load_profiles` for how a pre-rename config file sitting at
    the old default location (:func:`auto_essay_typer.platforms.legacy_config_file`)
    is picked up automatically the first time this file doesn't exist yet.
    """
    override = os.environ.get(_CONFIG_ENV_OVERRIDE) or os.environ.get(_LEGACY_CONFIG_ENV_OVERRIDE)
    if override:
        return Path(override)
    return platforms.config_dir() / _CONFIG_FILE_NAME


def load_profiles(path: Path | None = None) -> dict[str, Profile]:
    """Load every profile defined in the TOML file at ``path``.

    A missing file is normally just an empty starting state and returns
    ``{}`` -- except the very first time this runs after an upgrade: if the
    new-location file does not exist yet but the pre-rename legacy file
    (:func:`auto_essay_typer.platforms.legacy_config_file`) does, its
    profiles are read transparently instead, so upgrading never looks like
    losing every saved profile. The legacy file itself is never modified or
    deleted here -- ``save_profile``/``delete_profile`` are what actually
    copy its content into the new location, which falls out automatically
    the moment either of them reads through this function (getting the
    legacy profiles back) and then writes its result to the new path.

    Anything else wrong with the file (malformed TOML, an unknown field
    inside a profile table, a badly-sized tuple field) raises rather than
    being swallowed into ``{}`` -- silently ignoring a typo'd config would
    leave the user typing with settings they never asked for.
    """
    p = path if path is not None else config_path()
    if not p.exists():
        legacy = platforms.legacy_config_file()
        if not legacy.exists():
            return {}
        p = legacy

    with p.open("rb") as fh:
        try:
            raw = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"malformed TOML in {p}: {exc}") from exc

    profiles: dict[str, Profile] = {}
    for name, table in raw.items():
        if not isinstance(table, Mapping):
            raise ValueError(
                f"invalid profile {name!r} in {p}: expected a table of "
                f"fields, got {type(table).__name__}"
            )
        try:
            profiles[name] = Profile.from_dict(table)
        except (KeyError, ValueError) as exc:
            raise ValueError(f"invalid profile {name!r} in {p}: {exc}") from exc
    return profiles


def save_profile(name: str, profile: Profile, path: Path | None = None) -> None:
    """Write ``profile`` under ``name``, preserving every other profile already in the file.

    If the new-location file does not exist yet, ``load_profiles(p)`` below
    transparently returns the legacy file's profiles (see its docstring);
    writing that (plus this change) back out to ``p`` is what performs the
    one-time migration into the new location. The legacy file is never
    touched.
    """
    p = path if path is not None else config_path()
    existing = load_profiles(p)
    existing[name] = profile
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_dump_toml({n: prof.to_dict() for n, prof in existing.items()}), encoding="utf-8")


def delete_profile(name: str, path: Path | None = None) -> bool:
    """Remove profile ``name`` from the file. Returns False if it wasn't present.

    Like ``save_profile``, this migrates a legacy-only config forward to the
    new location as a side effect of reading through ``load_profiles`` and
    writing the result back out to ``p``.
    """
    p = path if path is not None else config_path()
    existing = load_profiles(p)
    if name not in existing:
        return False
    del existing[name]
    p.parent.mkdir(parents=True, exist_ok=True)
    remaining = {n: prof.to_dict() for n, prof in existing.items()}
    p.write_text(_dump_toml(remaining), encoding="utf-8")
    return True


def builtin_and_user_profiles(path: Path | None = None) -> dict[str, Profile]:
    """Built-in PRESETS merged with the user's saved profiles.

    A user profile shadows a builtin of the same name -- that shadowing is
    the entire point of letting someone customise a preset under its own
    name (e.g. tuning "natural" without inventing a new preset name).
    """
    merged = dict(PRESETS)
    merged.update(load_profiles(path))
    return merged


def resolve_profile(name: str, path: Path | None = None) -> Profile:
    """Look up ``name`` among builtin and user profiles, raising KeyError with the available names if absent."""
    profiles = builtin_and_user_profiles(path)
    if name not in profiles:
        raise KeyError(f"unknown profile {name!r}; available profiles are {sorted(profiles)}")
    return profiles[name]


def parse_override(text: str) -> tuple[str, object]:
    """Parse a CLI ``--set`` argument like ``"wpm=90"`` into ``(field_name, coerced_value)``.

    A tuple field accepts a comma-separated value, e.g. ``"burst_range=0.7,1.3"``.
    """
    if "=" not in text:
        raise ValueError(f"override {text!r} must be in key=value form")
    key, _, raw_value = text.partition("=")
    key = key.strip()
    raw_value = raw_value.strip()
    return key, coerce_value(key, raw_value)


def apply_overrides(profile: Profile, overrides: Iterable[str]) -> Profile:
    """Apply a batch of ``"key=value"`` overrides to ``profile``, returning a new Profile."""
    changes = dict(parse_override(text) for text in overrides)
    return profile.with_overrides(**changes)


def _format_toml_value(value: object) -> str:
    """Render one Profile field value (bool, number, or list of numbers) as a TOML literal.

    ``bool`` is checked before ``int``: ``bool`` is a subclass of ``int``, so
    testing ``isinstance(value, int)`` first would render ``True``/``False``
    as ``1``/``0`` instead of TOML's own ``true``/``false`` literals.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_format_toml_value(v) for v in value) + "]"
    raise TypeError(f"unsupported TOML value type: {type(value).__name__}")


def _format_toml_key(key: str) -> str:
    """Render a table name as a quoted TOML key so any characters in a profile name are safe."""
    escaped = key.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _dump_toml(data: Mapping[str, Mapping[str, object]]) -> str:
    """Serialize ``{profile_name: {field: value}}`` to TOML text.

    Deliberately minimal: Profile.to_dict() only ever produces booleans,
    numbers, and lists of numbers, so this needs none of the generality
    (strings, inline tables, dates, multi-line arrays) a general TOML writer
    would carry. Round-trip correctness through tomllib is what is verified;
    formatting is plain.
    """
    lines: list[str] = []
    for name, table in data.items():
        lines.append(f"[{_format_toml_key(name)}]")
        for key in field_names():
            if key in table:
                lines.append(f"{key} = {_format_toml_value(table[key])}")
        lines.append("")
    return "\n".join(lines)
