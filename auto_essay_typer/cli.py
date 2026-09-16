"""Headless entry point.

    python -m auto_essay_typer.cli essay.txt --wpm 65 --preset natural --dry-run
    python -m auto_essay_typer.cli essay.txt --profile natural --set wpm=90 --set session_breaks=false
    python -m auto_essay_typer.cli essay.txt --preset natural --wpm 80 --save-profile my-fast
    python -m auto_essay_typer.cli essay.txt --profile my-fast
"""

import argparse
import sys
from pathlib import Path
from typing import Callable, Sequence

from auto_essay_typer.config import (
    apply_overrides,
    builtin_and_user_profiles,
    config_path,
    delete_profile,
    load_profiles,
    resolve_profile,
    save_profile,
)
from auto_essay_typer.driver import Driver, Keyboard, RunResult
from auto_essay_typer.planner import plan
from auto_essay_typer.profiles import Profile, field_names
from auto_essay_typer.simulator import simulate, total_duration_ms

_EPILOG = """\
precedence (lowest to highest): --preset/--profile  <  --wpm/--typo-rate  <  --set

examples:
  auto-essay-typer essay.txt --preset sloppy --dry-run
  auto-essay-typer essay.txt --profile natural --set wpm=90 --set session_breaks=false
  auto-essay-typer essay.txt --preset natural --wpm 80 --save-profile my-fast
  auto-essay-typer essay.txt --profile my-fast

management flags (no "file" argument needed):
  auto-essay-typer --list-profiles
  auto-essay-typer --show-profile --profile my-fast
  auto-essay-typer --config-path
"""


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="auto-essay-typer",
        description="Type a text file into the focused application with human-like rhythm.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG,
    )
    parser.add_argument(
        "file", nargs="?", default=None, help="path to the text file to type"
    )
    parser.add_argument(
        "--preset",
        default="natural",
        help=(
            "named typing profile: a builtin (clean, natural, sloppy) or a "
            "saved user profile; see --list-profiles"
        ),
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="alias for --preset; whichever of the two is given wins",
    )
    parser.add_argument("--wpm", type=float, default=None, help="target words per minute")
    parser.add_argument(
        "--typo-rate", type=float, default=None, help="mistakes per 100 characters"
    )
    parser.add_argument(
        "--set",
        dest="set",
        action="append",
        default=None,
        metavar="KEY=VALUE",
        help=(
            "override a single profile field, e.g. --set wpm=90 "
            "(repeatable; applied after --preset/--profile and --wpm/--typo-rate, "
            "so --set always wins)"
        ),
    )
    parser.add_argument("--seed", type=int, default=None, help="reproduce an exact run")
    parser.add_argument(
        "--countdown", type=int, default=5, help="seconds to focus the target window"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be typed and how long it would take, then exit",
    )
    parser.add_argument(
        "--list-profiles",
        action="store_true",
        help="list every available profile (builtin and user-saved) and exit",
    )
    parser.add_argument(
        "--show-profile",
        action="store_true",
        help="print the fully resolved profile as TOML to stdout, then exit",
    )
    parser.add_argument(
        "--save-profile",
        metavar="NAME",
        default=None,
        help="save the fully resolved profile under NAME for later reuse via --profile, then exit",
    )
    parser.add_argument(
        "--delete-profile",
        metavar="NAME",
        default=None,
        help="delete a previously saved user profile, then exit",
    )
    parser.add_argument(
        "--config-path",
        dest="config_path_flag",
        action="store_true",
        help="print the path to the user profiles config file, then exit",
    )
    return parser.parse_args(list(argv))


def build_profile(args: argparse.Namespace) -> Profile:
    """Resolve the profile named by --preset/--profile, then layer on --wpm/--typo-rate,
    then --set overrides.

    Precedence (lowest to highest): --preset/--profile < --wpm/--typo-rate < --set.

    Raises ``KeyError`` for an unknown profile name, and ``KeyError``/``ValueError``
    for a bad ``--set`` key or value -- callers that need a clean CLI error message
    (rather than a traceback) must catch these; see ``_build_profile_or_none``.
    """
    name = args.profile if args.profile is not None else args.preset
    profile = resolve_profile(name)

    overrides: dict[str, object] = {}
    if args.wpm is not None:
        overrides["wpm"] = args.wpm
    if args.typo_rate is not None:
        overrides["typo_rate_per_100"] = args.typo_rate
    if overrides:
        profile = profile.with_overrides(**overrides)

    if args.set:
        profile = apply_overrides(profile, args.set)

    return profile


def _error_message(exc: Exception) -> str:
    """The exception's message, without KeyError's extra repr-quoting of its args."""
    if isinstance(exc, KeyError) and exc.args:
        return str(exc.args[0])
    return str(exc)


def _build_profile_or_none(args: argparse.Namespace) -> Profile | None:
    """build_profile(args), printing a clean stderr message (never a traceback) on failure."""
    try:
        return build_profile(args)
    except (KeyError, ValueError) as exc:
        print(f"error: {_error_message(exc)}", file=sys.stderr)
        return None


def _format_toml_scalar(value: object) -> str:
    """Render one Profile field value (bool, number, or list of numbers) as a TOML literal."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_format_toml_scalar(v) for v in value) + "]"
    raise TypeError(f"unsupported TOML value type: {type(value).__name__}")


def _profile_to_toml(profile: Profile) -> str:
    """Render a fully-resolved profile as flat TOML.

    Flat (no table header) so it parses straight back via ``tomllib`` into a
    mapping usable with ``Profile.from_dict`` -- handy for piping into a
    config file under a chosen profile name.
    """
    data = profile.to_dict()
    return "\n".join(f"{key} = {_format_toml_scalar(data[key])}" for key in field_names())


def _print_profile_list() -> None:
    merged = builtin_and_user_profiles()
    user_names = set(load_profiles())
    for name in sorted(merged):
        source = "user" if name in user_names else "builtin"
        profile = merged[name]
        print(
            f"{name:<20} {source:<8} wpm={profile.wpm} typo_rate_per_100={profile.typo_rate_per_100}"
        )


def _format_duration(milliseconds: float) -> str:
    seconds = int(milliseconds / 1000)
    return f"{seconds // 60}m {seconds % 60}s"


def main(
    argv: Sequence[str] | None = None,
    keyboard: Keyboard | None = None,
    sleep: Callable[[float], None] | None = None,
) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    if args.list_profiles:
        _print_profile_list()
        return 0

    if args.config_path_flag:
        print(config_path())
        return 0

    if args.delete_profile is not None:
        if delete_profile(args.delete_profile):
            print(f"deleted profile {args.delete_profile!r} from {config_path()}")
            return 0
        print(f"error: no profile named {args.delete_profile!r}", file=sys.stderr)
        return 1

    if args.show_profile or args.save_profile is not None:
        profile = _build_profile_or_none(args)
        if profile is None:
            return 1
        if args.save_profile is not None:
            save_profile(args.save_profile, profile)
            print(f"saved profile {args.save_profile!r} to {config_path()}")
        if args.show_profile:
            print(_profile_to_toml(profile))
        return 0

    if args.file is None:
        print(
            "error: the 'file' argument is required unless using --list-profiles, "
            "--show-profile, --save-profile, --delete-profile, or --config-path",
            file=sys.stderr,
        )
        return 2

    path = Path(args.file)
    if not path.is_file():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 1
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        print(f"error: file is empty: {path}", file=sys.stderr)
        return 1

    profile = _build_profile_or_none(args)
    if profile is None:
        return 1

    actions = plan(text, profile, args.seed)
    estimate = _format_duration(total_duration_ms(actions))

    if args.dry_run:
        print(simulate(actions))
        print(f"\n-- estimated typing time: {estimate}", file=sys.stdout)
        return 0

    driver_kwargs: dict[str, object] = {"countdown_seconds": args.countdown}
    if keyboard is not None:
        driver_kwargs["keyboard"] = keyboard
    if sleep is not None:
        driver_kwargs["sleep"] = sleep
    driver = Driver(**driver_kwargs)

    print(f"Typing {len(text)} characters, estimated {estimate}. Ctrl-C to stop.")
    try:
        result = driver.run(
            actions, on_countdown=lambda n: print(f"  starting in {n}...", flush=True)
        )
    except KeyboardInterrupt:
        driver.abort()
        print("\naborted", file=sys.stderr)
        return 130
    print("done" if result is RunResult.COMPLETED else "aborted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
