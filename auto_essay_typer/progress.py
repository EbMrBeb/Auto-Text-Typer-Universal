"""Pure progress/ETA math shared by the GUI's run controller.

Moved out of the old Tk ``app.py`` (see git history) so this arithmetic has
no GUI dependency and is unit-testable on its own. ``Driver.run``'s
``on_progress`` callback only ever reports a keypress *count* (``done`` of
``total``), never a time position, so the table built by
:func:`cumulative_pause_ms_by_keypress` is what turns that count into a
"planned time elapsed" figure -- and, via :func:`eta_seconds`, a "planned
time remaining" estimate.
"""

from __future__ import annotations

from auto_essay_typer.actions import Action, KeyPress, Pause


def cumulative_pause_ms_by_keypress(actions: list[Action]) -> list[float]:
    """``result[i]`` = planned ``Pause`` milliseconds elapsed up to and including the i-th ``KeyPress``.

    ``result[0] == 0.0`` (no time has elapsed before the first key is
    pressed), and ``len(result) == 1 + (number of KeyPress actions)``.
    """
    cumulative = [0.0]
    running = 0.0
    for action in actions:
        if isinstance(action, Pause):
            running += action.ms
        elif isinstance(action, KeyPress):
            cumulative.append(running)
    return cumulative


def eta_seconds(done: int, total_planned_ms: float, cumulative_pause_ms: list[float]) -> float:
    """Estimated seconds remaining after ``done`` keys have been typed.

    ``cumulative_pause_ms`` is the table :func:`cumulative_pause_ms_by_keypress`
    built for the plan currently running. ``done`` past the end of that table
    (which should not happen, but a driver mid-abort or a stale table must
    never raise here) is treated as "no time remaining" rather than indexing
    out of range.
    """
    elapsed_planned_ms = (
        cumulative_pause_ms[done] if 0 <= done < len(cumulative_pause_ms) else total_planned_ms
    )
    return max(0.0, (total_planned_ms - elapsed_planned_ms) / 1000.0)


def format_mmss(ms: float) -> str:
    """Render milliseconds as ``M:SS``, floored at ``0:00`` for negative input."""
    minutes, seconds = divmod(max(0, int(ms / 1000.0)), 60)
    return f"{minutes}:{seconds:02d}"


def format_duration_words(seconds: float) -> str:
    """Render seconds as a friendly estimate like ``"~3 min 40 s"`` or ``"~12 s"``.

    Used by the Type page's live character/word/duration line. Always
    prefixed with ``~`` -- this is an estimate from a planned action stream,
    sampled with a fixed seed for stability while the user is still typing,
    not a guarantee of the exact final duration.
    """
    total = max(0, int(round(seconds)))
    minutes, secs = divmod(total, 60)
    if minutes and secs:
        return f"~{minutes} min {secs} s"
    if minutes:
        return f"~{minutes} min"
    return f"~{secs} s"
