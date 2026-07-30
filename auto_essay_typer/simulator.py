"""Replays an action stream into a text buffer.

This is the referee for the core invariant: ``simulate(plan(text)) == text``.
It deliberately contains no cleverness -- if this module is wrong, every
correctness guarantee in the project is worthless.
"""

from collections.abc import Iterable

from auto_essay_typer.actions import Action, Backspace, KeyPress, Pause


def simulate(actions: Iterable[Action]) -> str:
    """Return the text an action stream would leave on screen."""
    buffer: list[str] = []
    for index, action in enumerate(actions):
        match action:
            case KeyPress(char=char):
                buffer.append(char)
            case Backspace(count=count):
                if count > len(buffer):
                    raise ValueError(
                        f"action {index}: Backspace({count}) runs past the start "
                        f"of a {len(buffer)}-character buffer"
                    )
                del buffer[len(buffer) - count :]
            case Pause():
                pass
            case _:
                raise TypeError(f"action {index}: unknown action {action!r}")
    return "".join(buffer)


def total_duration_ms(actions: Iterable[Action]) -> float:
    """Return the total time an action stream would take, in milliseconds."""
    return sum(action.ms for action in actions if isinstance(action, Pause))
