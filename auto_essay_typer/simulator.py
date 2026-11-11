"""Replays an action stream into a text buffer, or into a formatted Document.

``simulate()`` is the referee for the core invariant:
``simulate(plan(text)) == text``. It deliberately contains no cleverness --
if this module is wrong, every correctness guarantee in the project is
worthless. It ignores ``Format`` entirely: formatting shortcuts never change
what characters are on screen, only how they look.

``simulate_document()`` is the second referee, for the formatted-document
feature: it is the single source of truth for how a blank document actually
responds to keystrokes and formatting commands, and
``planner.plan_document`` must track this exact same state machine so that a
plan it builds is guaranteed reproducible. It replays abstract ``FormatOp``s
and never sees a keymap, so one model answers for every target editor. That
model is the Google Docs editing model: Google Docs is the editor whose
behaviour it was written from and the reference the document format was
designed against, not the only editor it describes. Its rules are the code in
this module, not a separate document. Narrowing a document to what a given
target can actually express happens earlier, in ``keymaps.adapt_document``,
not here.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field

from auto_essay_typer.actions import Action, Backspace, Format, FormatOp, KeyPress, Pause
from auto_essay_typer.document import (
    Alignment,
    Document,
    ListKind,
    Paragraph,
    ParagraphStyle,
    Run,
    TextStyle,
)


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
            case Format():
                pass
            case _:
                raise TypeError(f"action {index}: unknown action {action!r}")
    return "".join(buffer)


def total_duration_ms(actions: Iterable[Action]) -> float:
    """Return the total time an action stream would take, in milliseconds."""
    return sum(action.ms for action in actions if isinstance(action, Pause))


class SimulationError(ValueError):
    """An action stream is not valid to execute inside a Google Doc.

    Raised for the two moves real Google Docs cannot undo by simply typing
    more text -- backspacing across a paragraph boundary (which would merge
    two paragraphs or delete a bullet) and indenting/outdenting a paragraph
    that is not a list item -- so the planner must simply never emit them.
    """


# Ops whose only effect is to toggle membership of one TextStyle in `insertion`.
_TOGGLE_STYLE_OPS: dict[FormatOp, TextStyle] = {
    FormatOp.TOGGLE_BOLD: TextStyle.BOLD,
    FormatOp.TOGGLE_ITALIC: TextStyle.ITALIC,
    FormatOp.TOGGLE_UNDERLINE: TextStyle.UNDERLINE,
    FormatOp.TOGGLE_STRIKETHROUGH: TextStyle.STRIKETHROUGH,
}

# Ops that set the current paragraph's style outright.
_SET_STYLE_OPS: dict[FormatOp, ParagraphStyle] = {
    FormatOp.SET_NORMAL: ParagraphStyle.NORMAL,
    FormatOp.SET_TITLE: ParagraphStyle.TITLE,
    FormatOp.SET_SUBTITLE: ParagraphStyle.SUBTITLE,
    FormatOp.SET_HEADING_1: ParagraphStyle.HEADING_1,
    FormatOp.SET_HEADING_2: ParagraphStyle.HEADING_2,
    FormatOp.SET_HEADING_3: ParagraphStyle.HEADING_3,
    FormatOp.SET_HEADING_4: ParagraphStyle.HEADING_4,
    FormatOp.SET_HEADING_5: ParagraphStyle.HEADING_5,
    FormatOp.SET_HEADING_6: ParagraphStyle.HEADING_6,
}

# Ops that set the current paragraph's alignment outright.
_ALIGN_OPS: dict[FormatOp, Alignment] = {
    FormatOp.ALIGN_LEFT: Alignment.LEFT,
    FormatOp.ALIGN_CENTER: Alignment.CENTER,
    FormatOp.ALIGN_RIGHT: Alignment.RIGHT,
    FormatOp.ALIGN_JUSTIFIED: Alignment.JUSTIFIED,
}

_MAX_LIST_LEVEL = 8


@dataclass
class _ParagraphState:
    """Mutable per-paragraph state tracked while replaying an action stream.

    Unlike ``document.Paragraph`` this is intentionally mutable and carries
    each character's style alongside it (rather than already-grouped Runs):
    grouping into Runs only happens once, at the end, in ``_finish_paragraph``.
    """

    chars: list[tuple[str, frozenset[TextStyle]]] = field(default_factory=list)
    style: ParagraphStyle = ParagraphStyle.NORMAL
    alignment: Alignment = Alignment.LEFT
    list_kind: ListKind | None = None
    list_level: int = 0


def _toggle_list(
    kind: ListKind | None, level: int, target: ListKind
) -> tuple[ListKind | None, int]:
    """Apply TOGGLE_BULLET_LIST / TOGGLE_NUMBERED_LIST to ``(kind, level)``.

    Toggling the kind that is already active turns the list off (back to
    level 0); toggling the *other* kind switches to ``target`` but keeps the
    current nesting level; toggling from no list at all starts a fresh list
    at level 0. The same rule produces both ops' behaviour depending only on
    which ``target`` the caller passes, since Docs treats them symmetrically.
    """
    if kind == target:
        return None, 0
    if kind is not None:
        return target, level
    return target, 0


def _apply_format(
    op: FormatOp, paragraph: _ParagraphState, insertion: frozenset[TextStyle]
) -> frozenset[TextStyle]:
    """Apply one Format op to the model, mutating ``paragraph`` in place.

    Returns the (possibly unchanged) insertion style set -- the one piece of
    state a Format can change that lives outside the current paragraph.
    """
    if op is FormatOp.CLEAR_FORMATTING:
        return frozenset()

    style_toggle = _TOGGLE_STYLE_OPS.get(op)
    if style_toggle is not None:
        return insertion.symmetric_difference({style_toggle})

    paragraph_style = _SET_STYLE_OPS.get(op)
    if paragraph_style is not None:
        paragraph.style = paragraph_style
        return insertion

    alignment = _ALIGN_OPS.get(op)
    if alignment is not None:
        paragraph.alignment = alignment
        return insertion

    if op is FormatOp.TOGGLE_BULLET_LIST:
        paragraph.list_kind, paragraph.list_level = _toggle_list(
            paragraph.list_kind, paragraph.list_level, ListKind.BULLET
        )
        return insertion
    if op is FormatOp.TOGGLE_NUMBERED_LIST:
        paragraph.list_kind, paragraph.list_level = _toggle_list(
            paragraph.list_kind, paragraph.list_level, ListKind.NUMBERED
        )
        return insertion

    if op is FormatOp.INDENT:
        if paragraph.list_kind is None:
            raise SimulationError("INDENT requires the current paragraph to be a list item")
        paragraph.list_level = min(paragraph.list_level + 1, _MAX_LIST_LEVEL)
        return insertion
    if op is FormatOp.OUTDENT:
        if paragraph.list_kind is None:
            raise SimulationError("OUTDENT requires the current paragraph to be a list item")
        paragraph.list_level = max(paragraph.list_level - 1, 0)
        return insertion

    raise AssertionError(f"unhandled FormatOp {op!r}")  # pragma: no cover


def _finish_paragraph(state: _ParagraphState) -> Paragraph:
    """Group a paragraph's (char, styles) pairs into Runs of equal style."""
    runs: list[Run] = []
    for char, styles in state.chars:
        if runs and runs[-1].styles == styles:
            runs[-1] = Run(runs[-1].text + char, styles)
        else:
            runs.append(Run(char, styles))
    return Paragraph(
        runs=tuple(runs),
        style=state.style,
        alignment=state.alignment,
        list_kind=state.list_kind,
        list_level=state.list_level,
    )


def simulate_document(actions: Iterable[Action]) -> Document:
    """Replay an action stream through the Google Docs editing model.

    A blank Google Doc starts as one empty NORMAL, left-aligned paragraph
    with no list and no insertion styles, and the cursor never moves except
    to follow the last character typed -- nothing in this project's action
    stream ever repositions it. The code in this module is the complete rule
    set. In short:
    ``KeyPress`` inserts (or, for ``"\\n"``, starts a new paragraph that
    inherits alignment and list from the one before it), ``Backspace``
    deletes within the current paragraph only, and ``Format`` changes either
    the insertion style set or the current paragraph's attributes.
    """
    paragraphs: list[_ParagraphState] = [_ParagraphState()]
    insertion: frozenset[TextStyle] = frozenset()

    for index, action in enumerate(actions):
        current = paragraphs[-1]
        match action:
            case KeyPress(char="\n"):
                paragraphs.append(
                    _ParagraphState(
                        style=ParagraphStyle.NORMAL,
                        alignment=current.alignment,
                        list_kind=current.list_kind,
                        list_level=current.list_level,
                    )
                )
            case KeyPress(char=char):
                current.chars.append((char, insertion))
            case Backspace(count=count):
                for _ in range(count):
                    if not current.chars:
                        raise SimulationError(
                            "backspace across a paragraph boundary is unsupported"
                        )
                    current.chars.pop()
                if current.chars:
                    insertion = current.chars[-1][1]
                # else: paragraph is now empty -- insertion is left unchanged.
            case Format(op=op):
                insertion = _apply_format(op, current, insertion)
            case Pause():
                pass
            case _:
                raise TypeError(f"action {index}: unknown action {action!r}")

    return Document(tuple(_finish_paragraph(p) for p in paragraphs))
