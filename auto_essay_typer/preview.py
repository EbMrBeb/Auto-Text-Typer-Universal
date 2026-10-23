"""Turns an action stream into a human-readable trace.

Pure module: no Tk dependency, no clock, no I/O. The GUI's Dry Run button
calls :func:`describe` to render what a plan will actually do before any key
is pressed for real, but everything here is plain data transformation and is
exercised directly by ``tests/test_preview.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

from auto_essay_typer.actions import Action, Backspace, Format, KeyPress, Pause
from auto_essay_typer.simulator import simulate, total_duration_ms

#: Trailing characters kept in an event's on-screen snapshot.
_SNAPSHOT_CHARS = 40
#: A Pause longer than this (but not a break) is reported as "pause".
_PAUSE_THRESHOLD_MS = 800.0
#: A Pause longer than this is reported as "break" instead of "pause".
_BREAK_THRESHOLD_MS = 15_000.0
#: Cap on the number of events returned, so a very long essay does not build
#: an enormous list. Summary counts below are always computed over the full
#: stream regardless of this cap.
_MAX_EVENTS = 200


@dataclass(frozen=True)
class PreviewEvent:
    """One notable moment in a typing plan, in the order it occurs."""

    kind: str  # "typo" | "revision" | "pause" | "break"
    at_char: int  # index in the final text where it happens
    detail: str  # e.g. "typed 'tI' for 'It', caught 18 chars later"
    snapshot: str  # trailing ~40 chars on screen at that moment


@dataclass(frozen=True)
class PreviewSummary:
    characters: int
    duration_ms: float
    effective_wpm: float
    typo_count: int
    revision_count: int
    break_count: int
    longest_pause_ms: float
    matches_input: bool
    format_count: int = 0


def _snapshot(buffer: list[str]) -> str:
    return "".join(buffer[-_SNAPSHOT_CHARS:])


def _common_suffix_len(a: str, b: str) -> int:
    """Length of the longest common suffix shared by ``a`` and ``b``."""
    n = 0
    limit = min(len(a), len(b))
    while n < limit and a[len(a) - 1 - n] == b[len(b) - 1 - n]:
        n += 1
    return n


def _classify_run(deleted: str, retyped: str) -> str:
    """Classify a finished backspace-and-retype run.

    Exact, not a heuristic: the planner guarantees a revision retypes the
    deleted span identically (it never alters wording), while a typo
    correction retypes something different (that's the whole point of the
    correction). So ``retyped == deleted`` is a revision; anything else --
    including a retype that came up short because the stream ended or
    another correction began before it finished -- is a typo.
    """
    return "revision" if retyped == deleted else "typo"


def _describe_run(kind: str, deleted: str, retyped: str) -> str:
    """Human-readable detail string for one finished backspace run.

    For a typo, the deleted span (what was actually typed) and the retyped
    span (what replaced it) share a common trailing "tail" -- the
    correctly-typed characters that got swept up in the correction before
    the mistake was noticed. Stripping that shared suffix isolates the
    minimal wrong/correct fragment, e.g. "tI" -> "It".
    """
    if kind == "revision":
        words = len(deleted.split())
        plural = "" if words == 1 else "s"
        return f"backed up and rewrote {deleted!r} ({words} word{plural})"

    if not retyped:
        # Truncated stream: the correction's retype was never captured.
        return f"typed {deleted!r}, then the stream ends before the correction"

    tail_len = _common_suffix_len(deleted, retyped)
    wrong = deleted[: len(deleted) - tail_len]
    correct = retyped[: len(retyped) - tail_len]
    if tail_len:
        unit = "char" if tail_len == 1 else "chars"
        return f"typed {wrong!r} for {correct!r}, caught {tail_len} {unit} later"
    return f"typed {wrong!r} for {correct!r}"


def describe(actions: list[Action], text: str) -> tuple[list[PreviewEvent], PreviewSummary]:
    """Turn an action stream into a readable trace plus a summary.

    Replays ``actions`` into a buffer exactly as
    :func:`auto_essay_typer.simulator.simulate` does, while watching for two
    kinds of noteworthy moment:

    * A run of ``Backspace`` actions -- a *typo* or a *revision*, decided by
      comparing the deleted span against the span retyped immediately after
      it: identical means *revision*, different means *typo*. (A deleted
      span that happens to cross a word boundary is not by itself evidence
      of anything -- a mid-word typo noticed several characters later,
      after the typist has kept typing across a space, deletes a span that
      contains a space too.) A run may be interrupted by ``Pause`` actions
      (that is normal -- the correction timing model sleeps between
      backspaces) without being split; only a ``KeyPress`` (or the end of
      the stream) ends the deleting phase of a run.
    * A long standalone ``Pause`` -- a *break* past 15s, otherwise a *pause*
      past 800ms. Pauses that occur inside a backspace run are absorbed into
      that run rather than reported separately.

    A ``Format`` action (a Google Docs formatting shortcut) never produces an
    event of its own and never ends a backspace run -- it is invisible to
    this trace the same way a ``Pause`` is, since the planner may need to
    re-apply a run's styles mid-correction (right after the backspaces, right
    before the retype) and that must still read as one uninterrupted
    correction. It is only tallied into ``format_count``.

    The returned event list is capped at ``_MAX_EVENTS``; the summary counts
    (``typo_count``, ``revision_count``, ``break_count``, ``format_count``)
    are always tallied over the *entire* stream, so they stay accurate even
    when the list of events shown is truncated.
    """
    buffer: list[str] = []
    events: list[PreviewEvent] = []

    typo_count = 0
    revision_count = 0
    break_count = 0
    format_count = 0
    longest_pause_ms = 0.0

    in_run = False
    deleted_chars: list[str] = []

    def record(kind: str, at_char: int, detail: str) -> None:
        if len(events) < _MAX_EVENTS:
            events.append(
                PreviewEvent(kind=kind, at_char=at_char, detail=detail, snapshot=_snapshot(buffer))
            )

    def retyped_span(start_index: int, deleted_len: int) -> str:
        """Reconstruct what was retyped after a correction, from ``actions``.

        Collects ``KeyPress`` characters starting at ``actions[start_index]``
        until ``deleted_len`` of them have been gathered, or a ``Backspace``
        begins the next correction, whichever comes first. ``Pause`` and
        ``Format`` actions are skipped over -- neither affects the retype
        comparison. Running off the end of ``actions`` (a stream truncated
        mid-correction) just stops early and returns whatever was collected,
        which is always shorter than ``deleted_len`` and therefore never
        equal to the deleted span -- correctly falling out as a typo rather
        than crashing or guessing.
        """
        collected: list[str] = []
        i = start_index
        n = len(actions)
        while i < n and len(collected) < deleted_len:
            step = actions[i]
            if isinstance(step, KeyPress):
                collected.append(step.char)
                i += 1
            elif isinstance(step, Backspace):
                break
            else:  # Pause or Format -- doesn't count toward the retype, doesn't end it
                i += 1
        return "".join(collected)

    def finish_run(next_index: int) -> None:
        nonlocal in_run, deleted_chars, typo_count, revision_count
        if not in_run:
            return
        deleted = "".join(deleted_chars)
        at_char = len(buffer)
        retyped = retyped_span(next_index, len(deleted))
        kind = _classify_run(deleted, retyped)
        if kind == "revision":
            revision_count += 1
        else:
            typo_count += 1
        record(kind, at_char, _describe_run(kind, deleted, retyped))
        in_run = False
        deleted_chars = []

    for index, action in enumerate(actions):
        match action:
            case KeyPress(char=char):
                finish_run(index)
                buffer.append(char)
            case Backspace(count=count):
                if count > len(buffer):
                    raise ValueError(
                        f"Backspace({count}) runs past the start of a "
                        f"{len(buffer)}-character buffer"
                    )
                if not in_run:
                    in_run = True
                    deleted_chars = []
                removed = buffer[len(buffer) - count :]
                del buffer[len(buffer) - count :]
                deleted_chars = removed + deleted_chars
            case Pause(ms=ms):
                longest_pause_ms = max(longest_pause_ms, ms)
                if in_run:
                    continue
                if ms > _BREAK_THRESHOLD_MS:
                    break_count += 1
                    record("break", len(buffer), f"took a break for {ms / 1000:.0f}s")
                elif ms > _PAUSE_THRESHOLD_MS:
                    record("pause", len(buffer), f"paused for {ms / 1000:.1f}s")
            case Format():
                # Never ends a run (a correction may need to re-apply a run's
                # styles between its backspaces and its retype) and never
                # produces an event of its own -- only tallied below.
                format_count += 1

    finish_run(len(actions))

    duration_ms = total_duration_ms(actions)
    characters = len(text)
    effective_wpm = (
        (characters / 5.0) / (duration_ms / 60_000.0) if duration_ms > 0 else 0.0
    )
    matches_input = simulate(actions) == text

    summary = PreviewSummary(
        characters=characters,
        duration_ms=duration_ms,
        effective_wpm=effective_wpm,
        typo_count=typo_count,
        revision_count=revision_count,
        break_count=break_count,
        longest_pause_ms=longest_pause_ms,
        matches_input=matches_input,
        format_count=format_count,
    )
    return events, summary
