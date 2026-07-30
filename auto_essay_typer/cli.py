"""Headless entry point.

    python -m auto_essay_typer.cli essay.txt --wpm 65 --preset natural --dry-run
"""

import argparse
import sys
from pathlib import Path
from typing import Callable, Sequence

from auto_essay_typer.driver import Driver, Keyboard, RunResult
from auto_essay_typer.planner import plan
from auto_essay_typer.profiles import PRESETS, Profile, preset
from auto_essay_typer.simulator import simulate, total_duration_ms


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="auto-essay-typer",
        description="Type a text file into the focused application with human-like rhythm.",
    )
    parser.add_argument("file", help="path to the text file to type")
    parser.add_argument("--preset", choices=sorted(PRESETS), default="natural")
    parser.add_argument("--wpm", type=float, default=None, help="target words per minute")
    parser.add_argument(
        "--typo-rate", type=float, default=None, help="mistakes per 100 characters"
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
    return parser.parse_args(list(argv))


def build_profile(args: argparse.Namespace) -> Profile:
    """Preset first, then explicit flags override it."""
    profile = preset(args.preset)
    overrides: dict[str, object] = {}
    if args.wpm is not None:
        overrides["wpm"] = args.wpm
    if args.typo_rate is not None:
        overrides["typo_rate_per_100"] = args.typo_rate
    return profile.with_overrides(**overrides) if overrides else profile


def _format_duration(milliseconds: float) -> str:
    seconds = int(milliseconds / 1000)
    return f"{seconds // 60}m {seconds % 60}s"


def main(
    argv: Sequence[str] | None = None,
    keyboard: Keyboard | None = None,
    sleep: Callable[[float], None] | None = None,
) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    path = Path(args.file)
    if not path.is_file():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 1
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        print(f"error: file is empty: {path}", file=sys.stderr)
        return 1

    actions = plan(text, build_profile(args), args.seed)
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
