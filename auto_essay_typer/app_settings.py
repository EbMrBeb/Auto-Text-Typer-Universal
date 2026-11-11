"""Application preferences: which app to type into, theme, hotkeys, and UI toggles.

``profiles.Profile`` tunes the typing *model* itself (speed, mistakes,
pauses, ...); ``AppSettings`` instead holds everything about how the
application around that model behaves -- which ``targets.TargetApp`` to
type into, which ``profiles.PRESETS`` name to use by default, the global
hotkeys that start/pause/stop a run, and a handful of UI preferences
(theme, mini window, onboarding, confirmation prompts).

Persisted as JSON rather than the TOML ``config.py`` uses for typing
profiles: ``keymap_overrides`` (below) is a two-level nested mapping, which
this project's minimal hand-rolled TOML writer -- built only for
``Profile``'s flat schema of bools, numbers, and number-lists -- was never
meant to serialize. The stdlib ``json`` module both writes and reads that
shape without adding a dependency, so JSON is the better fit here.
"""

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from auto_essay_typer import platforms, targets
from auto_essay_typer.actions import FormatOp
from auto_essay_typer.keymaps import Chord, NO_OP, parse_chord
from auto_essay_typer.platforms import OperatingSystem

#: Full-path override for settings.json, mirroring config.py's
#: AUTO_TEXT_TYPER_CONFIG override for profiles.toml.
_SETTINGS_ENV_OVERRIDE = "AUTO_TEXT_TYPER_SETTINGS"
_SETTINGS_FILE_NAME = "settings.json"

_VALID_THEMES = frozenset({"system", "light", "dark"})


def default_hotkeys(os_: OperatingSystem) -> dict[str, str]:
    """The default global hotkeys for starting, pausing, and stopping a typing run.

    Written in ``pynput.keyboard.GlobalHotKeys`` syntax (e.g.
    ``"<cmd>+<alt>+<shift>+t"``), since that is the syntax
    ``pynput.keyboard.HotKey.parse`` -- see ``validate_hotkeys`` -- and
    whoever eventually wires up the listener both expect.

    Start/pause use three modifiers (``<mod>+<alt>+<shift>+<letter>``)
    rather than a more ordinary two-modifier combo: a two-modifier shortcut
    like Ctrl+Alt+T opens a brand new terminal window on Ubuntu (and similar
    collisions exist on other desktop environments), so a typing app that
    grabbed it globally would be fighting the OS for it. Three modifiers
    steers clear of the common two-modifier collisions.

    Stop is always plain Escape, on every OS, rather than following that
    same three-modifier pattern: Escape is the natural panic key -- the one
    reached for without thinking when something needs to stop right now --
    and a modifier-laden combo would work against that instinct. This
    module only defines the default; it does not run the hotkey listener
    itself, but whoever does should only act on this Escape while a typing
    run is actually in progress, so a stray Escape pressed anywhere else
    (closing a dialog, leaving a text field, ...) never does something
    surprising.

    ``cmd`` is used on macOS and ``ctrl`` everywhere else, matching
    ``platforms.primary_modifier``.
    """
    modifier = platforms.primary_modifier(os_)
    return {
        "start": f"<{modifier}>+<alt>+<shift>+t",
        "pause": f"<{modifier}>+<alt>+<shift>+p",
        "stop": "<esc>",
    }


@dataclass(frozen=True)
class AppSettings:
    """User-facing application preferences, persisted as JSON (see ``load_settings``/``save_settings``).

    Distinct from ``profiles.Profile``: a ``Profile`` is typing *behavior*
    (how fast, how many typos); ``AppSettings`` is everything else about how
    the app itself is configured to run.

    ``hotkeys`` defaults to ``default_hotkeys(platforms.current_os())``,
    wired through ``field(default_factory=...)`` rather than an ordinary
    default value. A plain ``= default_hotkeys(platforms.current_os())``
    default would be evaluated exactly once -- the moment this class body
    executes, i.e. whenever this module is first imported -- so it would
    freeze in whatever OS happened to be current at import time, and no
    amount of monkeypatching ``platforms.current_os`` afterward (the
    pattern the rest of this project's test suite relies on, e.g.
    ``tests/test_platforms.py``) could ever change it. ``default_factory``
    defers that call to each individual construction instead, so
    ``AppSettings()`` always reflects whatever OS is current *right then* --
    in production and in tests alike.

    ``keymap_overrides`` maps a target id (``targets.TargetApp.id``) to a
    mapping of ``FormatOp`` value -> chord text, letting a user customize or
    extend that target's built-in keymap -- see ``effective_keymap``.
    """

    target_app: str = "google-docs"
    profile_name: str = "natural"
    countdown_seconds: int = 5
    theme: str = "system"
    show_mini_window: bool = True
    minimize_on_start: bool = False
    global_hotkeys_enabled: bool = True
    hotkeys: Mapping[str, str] = field(
        default_factory=lambda: default_hotkeys(platforms.current_os())
    )
    keymap_overrides: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    onboarding_complete: bool = False
    confirm_before_typing: bool = True

    def __post_init__(self) -> None:
        if not 0 <= self.countdown_seconds <= 30:
            raise ValueError(
                f"countdown_seconds must be between 0 and 30 inclusive, got "
                f"{self.countdown_seconds}"
            )
        if self.theme not in _VALID_THEMES:
            raise ValueError(
                f"theme must be one of {sorted(_VALID_THEMES)}, got {self.theme!r}"
            )


def _hotkey_tokens(text: str) -> frozenset[str]:
    """Split pynput hotkey text like ``"<cmd>+<alt>+t"`` into a lowercase, order-independent token set."""
    return frozenset(part.strip("<>").lower() for part in text.split("+"))


def _chord_tokens(chord: Chord) -> frozenset[str]:
    """The same kind of token set as ``_hotkey_tokens``, for a ``keymaps.Chord`` tuple."""
    return frozenset(part.lower() for part in chord)


def validate_hotkeys(hotkeys: Mapping[str, str], keymap: Mapping[FormatOp, Chord]) -> None:
    """Raise ``ValueError`` if any hotkey is unusable; return nothing meaningful otherwise.

    Two checks apply to every hotkey, independently of each other -- a
    hotkey that does not collide with anything still has its text checked
    for validity, and vice versa:

    1. The text must actually parse as a ``pynput`` ``GlobalHotKeys``
       shortcut (via ``pynput.keyboard.HotKey.parse``). Its exception is
       caught and re-raised as a ``ValueError`` naming both the action
       (``"start"``, ``"pause"``, ...) and the offending text, since
       pynput's own message does not reliably mention either.
    2. The shortcut must not equal any formatting chord in ``keymap``: this
       app presses those chords itself while typing, so a hotkey that
       matched one would fire in the middle of a run.

    Comparing (2) is not a plain text comparison: pynput's own notation
    wraps named/modifier keys in angle brackets and leaves literal
    character keys bare (``"<cmd>+<alt>+t"``), while this project's
    ``Chord`` tuples are all-lowercase with no brackets at all
    (``("cmd", "alt", "t")``) -- and either notation can list its modifiers
    in any order. ``_hotkey_tokens``/``_chord_tokens`` normalize both sides
    to a lowercase, bracket-free, order-independent token set before
    comparing, so ``"<alt>+<cmd>+t"`` and ``("cmd", "alt", "t")`` are
    correctly recognized as the same shortcut. ``keymaps.NO_OP`` (``()``)
    entries are skipped explicitly: an empty token set could never
    legitimately equal a real hotkey's non-empty one, but there is no
    reason to run the comparison at all.
    """
    from pynput.keyboard import HotKey

    chord_tokens_by_op = {
        op: _chord_tokens(chord) for op, chord in keymap.items() if chord != NO_OP
    }

    for action, text in hotkeys.items():
        try:
            HotKey.parse(text)
        except Exception as exc:
            raise ValueError(
                f"hotkey for {action!r} is not a valid shortcut ({text!r}): {exc}"
            ) from exc

        tokens = _hotkey_tokens(text)
        for op, op_tokens in chord_tokens_by_op.items():
            if tokens == op_tokens:
                raise ValueError(
                    f"hotkey for {action!r} ({text!r}) collides with the "
                    f"{op.value!r} formatting shortcut -- this app presses "
                    "that chord itself while typing"
                )


def effective_keymap(settings: AppSettings, os_: OperatingSystem) -> dict[FormatOp, Chord]:
    """The keymap actually in effect for ``settings``: its target's base keymap plus its overrides.

    Starts from ``targets.keymap_for(settings.target_app, os_)`` and applies
    ``settings.keymap_overrides.get(settings.target_app, {})`` on top,
    entry by entry. An override can replace an op the base keymap already
    defines, or add one it lacks entirely -- e.g. teaching ``microsoft-word``
    a shortcut for ``TOGGLE_STRIKETHROUGH``, which its built-in keymap has no
    binding for at all.

    An override chord text of the empty string is handled specially:
    ``keymaps.parse_chord("")`` raises rather than returning
    ``keymaps.NO_OP`` (a deliberate asymmetry -- see that function's
    docstring), but the empty string is exactly how ``keymaps.format_chord``
    renders ``NO_OP`` back to text. So an override of ``""`` is mapped to
    ``keymaps.NO_OP`` directly here, letting a user's "no shortcut for this
    op" choice round-trip correctly instead of raising.

    Raises ``ValueError`` naming the bad text for an override whose op text
    is not a valid ``FormatOp`` or whose chord text does not parse.
    """
    keymap: dict[FormatOp, Chord] = dict(targets.keymap_for(settings.target_app, os_))

    overrides = settings.keymap_overrides.get(settings.target_app, {})
    for op_text, chord_text in overrides.items():
        try:
            op = FormatOp(op_text)
        except ValueError as exc:
            raise ValueError(
                f"keymap override for target {settings.target_app!r} has an "
                f"unknown formatting op {op_text!r}: {exc}"
            ) from exc

        if chord_text == "":
            chord = NO_OP
        else:
            try:
                chord = parse_chord(chord_text)
            except ValueError as exc:
                raise ValueError(
                    f"keymap override for target {settings.target_app!r}, op "
                    f"{op_text!r}, has an invalid chord {chord_text!r}: {exc}"
                ) from exc

        keymap[op] = chord

    return keymap


def settings_path() -> Path:
    """Where the user's settings.json lives.

    Honours ``AUTO_TEXT_TYPER_SETTINGS`` first -- an explicit full-path
    override, the same pattern ``config.py``'s ``AUTO_TEXT_TYPER_CONFIG``
    uses for profiles.toml, letting tests redirect this without ever
    touching the real config directory -- then falls back to the
    platform-appropriate config directory (``platforms.config_dir``).
    """
    override = os.environ.get(_SETTINGS_ENV_OVERRIDE)
    if override:
        return Path(override)
    return platforms.config_dir() / _SETTINGS_FILE_NAME


def load_settings(path: Path | None = None) -> tuple[AppSettings, str | None]:
    """Load settings from ``path`` (default: ``settings_path()``), never raising.

    Returns ``(settings, warning)``: ``warning`` is ``None`` on a clean
    load, including a missing file, which is just the normal empty-state
    case -- ``(AppSettings(), None)`` -- or a human-readable message when
    anything went wrong, in which case ``settings`` is always
    ``AppSettings()`` (defaults), never a partially-applied result.

    Unknown top-level JSON keys are silently ignored: filtered out against
    ``AppSettings``'s actual field names, derived from ``dataclasses.fields``
    rather than hardcoded, so a newly added field never requires touching
    this filter. A forward-compatible settings file from a newer version of
    this app should not make an older one refuse to start.

    Everything else that can go wrong -- malformed JSON, a JSON value of the
    wrong shape, an out-of-range ``countdown_seconds``/bad ``theme`` (caught
    by ``AppSettings.__post_init__``), or a ``hotkeys``/``keymap_overrides``
    entry that fails ``validate_hotkeys`` -- degrades to defaults plus a
    warning instead of raising. The broad ``except Exception`` below is
    deliberate, not a lazy catch-all: the whole point of this function is
    that a corrupt or foreign settings file can never crash the app on
    startup, which means every failure mode must degrade the same way,
    including ones nobody has thought to enumerate yet.
    """
    p = path if path is not None else settings_path()
    if not p.exists():
        return AppSettings(), None

    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(
                f"expected a JSON object at the top level, got {type(raw).__name__}"
            )

        valid_fields = {f.name for f in fields(AppSettings)}
        filtered = {key: value for key, value in raw.items() if key in valid_fields}
        candidate = AppSettings(**filtered)

        validate_hotkeys(candidate.hotkeys, effective_keymap(candidate, platforms.current_os()))
        return candidate, None
    except Exception as exc:
        return AppSettings(), f"could not load settings from {p}: {exc}"


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    """Write ``settings`` to ``path`` (default: ``settings_path()``) as JSON, atomically.

    Unlike ``config.py``'s TOML writer, which writes the destination file
    directly, this writes to a temp file created in the *same* directory as
    the destination -- so it is guaranteed to share a filesystem with it --
    and then ``os.replace``s it into place, which is atomic: a reader (or a
    concurrent save) never observes a partially-written file. The parent
    directory is created first if missing. If anything fails before the
    replace, the temp file is cleaned up rather than left behind.

    ``dataclasses.asdict`` plus ``json.dumps`` is sufficient here -- unlike
    ``profiles.Profile.to_dict``, ``AppSettings`` has no tuple fields that
    need widening to lists, since JSON (unlike TOML) already round-trips
    plain ``str``-keyed dicts natively.
    """
    p = path if path is not None else settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(asdict(settings), indent=2, sort_keys=True)

    fd, tmp_name = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
        os.replace(tmp_name, p)
    except BaseException:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise
