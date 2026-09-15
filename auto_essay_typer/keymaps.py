"""Keyboard shortcuts for Google Docs formatting commands.

``FormatOp`` (in ``actions.py``) is an abstract command -- "toggle bold",
"set heading 2". This module supplies the concrete key combination that
actually presses it in the browser, plus ``adapt_document``, which rewrites
a Document so every op it needs is one the target keymap can actually reach:
Google Docs has no keyboard shortcut for its Title or Subtitle paragraph
styles, so a document using either must be adapted to the closest heading
Docs *can* reach by shortcut before the planner ever sees it (section 5 of
the project's shared contract takes an already-adapted Document as input).
"""

import dataclasses
from collections.abc import Mapping

from auto_essay_typer.actions import FormatOp
from auto_essay_typer.document import (
    Alignment,
    Document,
    ListKind,
    Paragraph,
    ParagraphStyle,
    TextStyle,
)

#: A parsed keyboard shortcut: zero or more modifiers, then exactly one final
#: key. E.g. ``("cmd", "shift", "8")`` or ``("tab",)``.
Chord = tuple[str, ...]

_MODIFIERS = frozenset({"cmd", "alt", "shift", "ctrl"})
_NAMED_KEYS = frozenset({"tab", "enter"})


def parse_chord(text: str) -> Chord:
    """Parse a ``"+"``-joined shortcut like ``"cmd+shift+8"`` into a Chord.

    The grammar is zero or more modifiers (``cmd``/``alt``/``shift``/``ctrl``)
    followed by exactly one final key -- a single character, or a named key
    (``"tab"``, ``"enter"``). Note this means the literal plus key itself can
    never be expressed by this grammar; no shortcut here needs it, so that is
    fine. Raises ``ValueError`` with a clear message for anything that does
    not fit: an empty string, an unknown modifier, or a final segment that is
    empty, multi-character, or an unrecognised name.
    """
    if not text:
        raise ValueError("chord must not be empty")
    parts = text.split("+")
    if any(part == "" for part in parts):
        raise ValueError(f"chord {text!r} has an empty part (missing or trailing '+')")
    *modifiers, final = parts
    for mod in modifiers:
        if mod not in _MODIFIERS:
            raise ValueError(
                f"chord {text!r} has unknown modifier {mod!r}; "
                f"expected one of {sorted(_MODIFIERS)}"
            )
    if not (len(final) == 1 or final in _NAMED_KEYS):
        raise ValueError(
            f"chord {text!r} has an invalid final key {final!r}; expected a single "
            f"character or one of {sorted(_NAMED_KEYS)}"
        )
    return (*modifiers, final)


def format_chord(chord: Chord) -> str:
    """Render a Chord back to ``"+"``-joined shortcut text. Inverse of ``parse_chord``."""
    return "+".join(chord)


# Google Docs has no default shortcut for Title or Subtitle, so SET_TITLE and
# SET_SUBTITLE are deliberately absent -- adapt_document() is what handles
# documents that use them. INDENT/OUTDENT use plain Tab / Shift+Tab rather
# than Docs' cmd+]/cmd+[ list-indent shortcuts: those are Chrome's Forward/
# Back shortcuts too, and would navigate the browser away if focus ever slipped.
GOOGLE_DOCS_MAC: dict[FormatOp, Chord] = {
    FormatOp.CLEAR_FORMATTING: parse_chord("cmd+\\"),
    FormatOp.TOGGLE_BOLD: parse_chord("cmd+b"),
    FormatOp.TOGGLE_ITALIC: parse_chord("cmd+i"),
    FormatOp.TOGGLE_UNDERLINE: parse_chord("cmd+u"),
    FormatOp.TOGGLE_STRIKETHROUGH: parse_chord("cmd+shift+x"),
    FormatOp.SET_NORMAL: parse_chord("cmd+alt+0"),
    FormatOp.SET_HEADING_1: parse_chord("cmd+alt+1"),
    FormatOp.SET_HEADING_2: parse_chord("cmd+alt+2"),
    FormatOp.SET_HEADING_3: parse_chord("cmd+alt+3"),
    FormatOp.SET_HEADING_4: parse_chord("cmd+alt+4"),
    FormatOp.SET_HEADING_5: parse_chord("cmd+alt+5"),
    FormatOp.SET_HEADING_6: parse_chord("cmd+alt+6"),
    FormatOp.ALIGN_LEFT: parse_chord("cmd+shift+l"),
    FormatOp.ALIGN_CENTER: parse_chord("cmd+shift+e"),
    FormatOp.ALIGN_RIGHT: parse_chord("cmd+shift+r"),
    FormatOp.ALIGN_JUSTIFIED: parse_chord("cmd+shift+j"),
    FormatOp.TOGGLE_NUMBERED_LIST: parse_chord("cmd+shift+7"),
    FormatOp.TOGGLE_BULLET_LIST: parse_chord("cmd+shift+8"),
    FormatOp.INDENT: parse_chord("tab"),
    FormatOp.OUTDENT: parse_chord("shift+tab"),
}

KEYMAPS: dict[str, dict[FormatOp, Chord]] = {"google-docs-mac": GOOGLE_DOCS_MAC}


_SET_OP_BY_STYLE: dict[ParagraphStyle, FormatOp] = {
    ParagraphStyle.NORMAL: FormatOp.SET_NORMAL,
    ParagraphStyle.TITLE: FormatOp.SET_TITLE,
    ParagraphStyle.SUBTITLE: FormatOp.SET_SUBTITLE,
    ParagraphStyle.HEADING_1: FormatOp.SET_HEADING_1,
    ParagraphStyle.HEADING_2: FormatOp.SET_HEADING_2,
    ParagraphStyle.HEADING_3: FormatOp.SET_HEADING_3,
    ParagraphStyle.HEADING_4: FormatOp.SET_HEADING_4,
    ParagraphStyle.HEADING_5: FormatOp.SET_HEADING_5,
    ParagraphStyle.HEADING_6: FormatOp.SET_HEADING_6,
}

_ALIGN_OP_BY_ALIGNMENT: dict[Alignment, FormatOp] = {
    Alignment.LEFT: FormatOp.ALIGN_LEFT,
    Alignment.CENTER: FormatOp.ALIGN_CENTER,
    Alignment.RIGHT: FormatOp.ALIGN_RIGHT,
    Alignment.JUSTIFIED: FormatOp.ALIGN_JUSTIFIED,
}

_LIST_TOGGLE_OP_BY_KIND: dict[ListKind, FormatOp] = {
    ListKind.BULLET: FormatOp.TOGGLE_BULLET_LIST,
    ListKind.NUMBERED: FormatOp.TOGGLE_NUMBERED_LIST,
}

_TOGGLE_OP_BY_TEXT_STYLE: dict[TextStyle, FormatOp] = {
    TextStyle.BOLD: FormatOp.TOGGLE_BOLD,
    TextStyle.ITALIC: FormatOp.TOGGLE_ITALIC,
    TextStyle.UNDERLINE: FormatOp.TOGGLE_UNDERLINE,
    TextStyle.STRIKETHROUGH: FormatOp.TOGGLE_STRIKETHROUGH,
}

# The only paragraph styles Google Docs has no shortcut for, and what they
# fall back to when a keymap lacks their SET_* op.
_STYLE_SUBSTITUTIONS: dict[ParagraphStyle, ParagraphStyle] = {
    ParagraphStyle.TITLE: ParagraphStyle.HEADING_1,
    ParagraphStyle.SUBTITLE: ParagraphStyle.HEADING_2,
}
_STYLE_LABEL: dict[ParagraphStyle, str] = {
    ParagraphStyle.TITLE: "Title",
    ParagraphStyle.SUBTITLE: "Subtitle",
    ParagraphStyle.HEADING_1: "Heading 1",
    ParagraphStyle.HEADING_2: "Heading 2",
}


def adapt_document(
    doc: Document, keymap: Mapping[FormatOp, Chord]
) -> tuple[Document, tuple[str, ...]]:
    """Rewrite ``doc`` so every op it needs exists in ``keymap``.

    Title and Subtitle paragraphs are rewritten to Heading 1 / Heading 2 when
    the keymap has no shortcut for them (true of every keymap this project
    ships, since Google Docs itself has none), and the substitution is
    reported back as one aggregated warning per kind -- never applied
    silently, since it does change how the typed document looks. Every other
    op the document needs (its other paragraph styles, its alignments, its
    list kinds, indentation, and every run's character styles) must already
    be in ``keymap``; if one is not, this raises ``ValueError`` naming every
    missing op at once rather than stopping at the first.
    """
    substitution_counts: dict[ParagraphStyle, int] = {}
    missing_ops: set[FormatOp] = set()
    new_paragraphs: list[Paragraph] = []

    for paragraph in doc.paragraphs:
        style = paragraph.style
        style_op = _SET_OP_BY_STYLE[style]
        if style_op not in keymap and style in _STYLE_SUBSTITUTIONS:
            substitution_counts[style] = substitution_counts.get(style, 0) + 1
            style = _STYLE_SUBSTITUTIONS[style]
            style_op = _SET_OP_BY_STYLE[style]
            paragraph = dataclasses.replace(paragraph, style=style)
        if style_op not in keymap:
            missing_ops.add(style_op)

        align_op = _ALIGN_OP_BY_ALIGNMENT[paragraph.alignment]
        if align_op not in keymap:
            missing_ops.add(align_op)

        if paragraph.list_kind is not None:
            list_op = _LIST_TOGGLE_OP_BY_KIND[paragraph.list_kind]
            if list_op not in keymap:
                missing_ops.add(list_op)
            if paragraph.list_level > 0 and FormatOp.INDENT not in keymap:
                missing_ops.add(FormatOp.INDENT)

        for run in paragraph.runs:
            for text_style in run.styles:
                toggle_op = _TOGGLE_OP_BY_TEXT_STYLE[text_style]
                if toggle_op not in keymap:
                    missing_ops.add(toggle_op)

        new_paragraphs.append(paragraph)

    if missing_ops:
        names = ", ".join(sorted(op.value for op in missing_ops))
        raise ValueError(f"keymap is missing required op(s): {names}")

    if not substitution_counts:
        return doc, ()

    warnings = tuple(
        f"{count} {_STYLE_LABEL[style]} paragraph{'' if count == 1 else 's'} typed as "
        f"{_STYLE_LABEL[_STYLE_SUBSTITUTIONS[style]]} "
        f"(Google Docs has no {_STYLE_LABEL[style]} shortcut)"
        for style, count in sorted(substitution_counts.items(), key=lambda kv: kv[0].value)
    )
    return Document(tuple(new_paragraphs)), warnings
