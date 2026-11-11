"""Chord parsing and the document-adaptation engine shared by every target keymap.

``FormatOp`` (in ``actions.py``) is an abstract command -- "toggle bold",
"set heading 2". A concrete keymap (a ``dict[FormatOp, Chord]``) supplies the
actual key combination that presses each one in a specific (target app, OS)
pair; ``targets.py`` is where those per-editor keymaps now live. This module
supplies the shared pieces every one of them relies on: ``parse_chord`` /
``format_chord`` for reading and writing chords as text, and
``adapt_document``, which rewrites a Document so every op it needs is one a
given keymap can actually reach -- falling back to the closest thing an
editor *can* express (see ``adapt_document``'s docstring for the full list)
rather than requiring every target to support everything Google Docs does.
``GOOGLE_DOCS_MAC`` and ``KEYMAPS["google-docs-mac"]`` remain here, unchanged,
since other code already depends on their exact names and location; they are
also available as ``targets.TARGET_APPS["google-docs"]``.
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
    Run,
    TextStyle,
)

#: A parsed keyboard shortcut: zero or more modifiers, then exactly one final
#: key. E.g. ``("cmd", "shift", "8")`` or ``("tab",)``.
Chord = tuple[str, ...]

#: An explicit "this op presses nothing" chord, for a FormatOp a target editor
#: has no way to express at all. Only ever valid for CLEAR_FORMATTING -- see
#: adapt_document's own docstring -- since starting a blank
#: document with no formatting to clear is the one case where doing nothing
#: is truly equivalent to the op. driver.Driver treats it as "handled" but
#: presses no keys (see driver.py).
NO_OP: Chord = ()

_MODIFIERS = frozenset({"cmd", "alt", "shift", "ctrl"})
_NAMED_KEYS = frozenset({"tab", "enter", "space"})


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

# The only paragraph styles Google Docs has no shortcut for anywhere, and
# what they fall back to first (before the general heading-representability
# fallback below gets a chance to run on the substituted style too).
_STYLE_SUBSTITUTIONS: dict[ParagraphStyle, ParagraphStyle] = {
    ParagraphStyle.TITLE: ParagraphStyle.HEADING_1,
    ParagraphStyle.SUBTITLE: ParagraphStyle.HEADING_2,
}
_STYLE_LABEL: dict[ParagraphStyle, str] = {
    ParagraphStyle.TITLE: "Title",
    ParagraphStyle.SUBTITLE: "Subtitle",
    ParagraphStyle.HEADING_1: "Heading 1",
    ParagraphStyle.HEADING_2: "Heading 2",
    ParagraphStyle.HEADING_3: "Heading 3",
    ParagraphStyle.HEADING_4: "Heading 4",
    ParagraphStyle.HEADING_5: "Heading 5",
    ParagraphStyle.HEADING_6: "Heading 6",
}
_ALIGNMENT_LABEL: dict[Alignment, str] = {
    Alignment.LEFT: "Left",
    Alignment.CENTER: "Center",
    Alignment.RIGHT: "Right",
    Alignment.JUSTIFIED: "Justified",
}
_TEXT_STYLE_LABEL: dict[TextStyle, str] = {
    TextStyle.BOLD: "bold",
    TextStyle.ITALIC: "italic",
    TextStyle.UNDERLINE: "underline",
    TextStyle.STRIKETHROUGH: "strikethrough",
}
_LIST_KIND_LABEL: dict[ListKind, str] = {
    ListKind.BULLET: "bullet-list",
    ListKind.NUMBERED: "numbered-list",
}
#: Four spaces per nesting level, matching the task's fallback spec.
_LIST_INDENT_UNIT = "    "


def required_ops(doc: Document) -> frozenset[FormatOp]:
    """The FormatOps ``plan_document`` will need in order to reproduce ``doc``.

    Always includes CLEAR_FORMATTING. A NORMAL paragraph style and a LEFT
    alignment never need their own op: a blank document -- and every fresh
    paragraph immediately after Enter -- already starts NORMAL and LEFT (see
    ``simulator.simulate_document``'s docstring), so nothing needs to *set*
    either one just because a paragraph happens to have that value. SET_NORMAL
    is still included whenever any *other* paragraph style is used, since the
    keymap must then be able to return to Normal at all. Every other
    paragraph style used, every non-LEFT alignment used, every run text style
    used, and every list kind/nesting level used each require their own op.
    """
    ops: set[FormatOp] = {FormatOp.CLEAR_FORMATTING}
    non_normal_style_used = False

    for paragraph in doc.paragraphs:
        if paragraph.style is not ParagraphStyle.NORMAL:
            non_normal_style_used = True
            ops.add(_SET_OP_BY_STYLE[paragraph.style])

        if paragraph.alignment is not Alignment.LEFT:
            ops.add(_ALIGN_OP_BY_ALIGNMENT[paragraph.alignment])

        if paragraph.list_kind is not None:
            ops.add(_LIST_TOGGLE_OP_BY_KIND[paragraph.list_kind])
            if paragraph.list_level > 0:
                ops.add(FormatOp.INDENT)
                ops.add(FormatOp.OUTDENT)

        for run in paragraph.runs:
            for text_style in run.styles:
                ops.add(_TOGGLE_OP_BY_TEXT_STYLE[text_style])

    if non_normal_style_used:
        ops.add(FormatOp.SET_NORMAL)

    return frozenset(ops)


def _heading_representable(style: ParagraphStyle, keymap: Mapping[FormatOp, Chord]) -> bool:
    """Whether ``style`` (assumed non-NORMAL) can actually be set and later left.

    Representing a heading needs two things from the keymap: a shortcut for
    the heading itself, and -- since something used it -- SET_NORMAL, so
    there is a way back out of it. Missing either one means this style can't
    survive adaptation as itself.
    """
    return _SET_OP_BY_STYLE[style] in keymap and FormatOp.SET_NORMAL in keymap


def _list_kind_needs_fallback(kind: ListKind, keymap: Mapping[FormatOp, Chord]) -> bool:
    """Whether every paragraph of list kind ``kind`` must be flattened to marker text.

    Checked once per kind for the whole document rather than per paragraph:
    mixing "real" Docs-style list items with typed-marker ones within the
    same list, depending on which particular paragraph happens to be nested,
    would look inconsistent and make numbering impossible to keep coherent.
    So if this kind's own toggle is missing, or indenting/outdenting isn't
    available at all, every paragraph of that kind falls back -- regardless
    of whether any single paragraph's own nesting level would have needed
    indentation.
    """
    return (
        _LIST_TOGGLE_OP_BY_KIND[kind] not in keymap
        or FormatOp.INDENT not in keymap
        or FormatOp.OUTDENT not in keymap
    )


def _flatten_list_paragraph(paragraph: Paragraph, marker: str) -> Paragraph:
    """Convert one list paragraph to a plain NORMAL paragraph with a typed marker.

    The marker is its own unstyled leading run, per the task spec; the
    paragraph's original runs (already adapted for style-toggle availability
    by the time this runs) follow it unchanged, keeping whatever character
    styles the target keymap could actually represent.
    """
    marker_run = Run(marker, frozenset())
    return Paragraph(
        runs=(marker_run, *paragraph.runs),
        style=ParagraphStyle.NORMAL,
        alignment=paragraph.alignment,
        list_kind=None,
        list_level=0,
    )


def adapt_document(
    doc: Document, keymap: Mapping[FormatOp, Chord]
) -> tuple[Document, tuple[str, ...]]:
    """Rewrite ``doc`` so every op it needs (see ``required_ops``) exists in ``keymap``.

    CLEAR_FORMATTING must be in every keymap (as a real chord, or
    ``keymaps.NO_OP`` when the target genuinely has nothing to clear) --
    its absence is a programming error in the keymap itself, not something
    any document can be adapted around, so this raises ``ValueError``
    immediately rather than attempting anything else.

    Everything else about the document is instead made to fit the keymap,
    applying fallbacks in this order, and reporting each fallback kind
    actually used back as one aggregated warning (with a count) rather than
    applying any of them silently, since every one of them changes how the
    typed document will look:

    1. Title/Subtitle paragraphs substitute to Heading 1/Heading 2 when their
       own op is missing (true of every keymap this project ships, since no
       target editor has a Title/Subtitle shortcut).
    2. A paragraph style (after step 1) that still can't be set -- its own
       SET_* op is missing, or SET_NORMAL itself is, so there would be no way
       back out of it -- becomes a NORMAL paragraph instead, with every run
       marked bold when TOGGLE_BOLD is available (so the heading is at least
       still visually distinguished) or left plain when it is not.
    3. A non-LEFT alignment whose op is missing becomes LEFT.
    4. A run text style (bold/italic/underline/strikethrough) whose toggle op
       is missing is dropped from that run.
    5. A list kind that cannot be fully represented (see
       ``_list_kind_needs_fallback``) has every one of its paragraphs
       rewritten to a plain NORMAL paragraph whose text starts with a typed
       marker -- ``"• "`` for bullets, ``"1. "``, ``"2. "``, ... for numbered
       lists -- indented four spaces per nesting level. Numbered counters run
       per level and reset when a non-list paragraph breaks the run, or when
       a shallower level resumes (ending whatever deeper sub-list was live).

    Applying this to an already-adapted document is idempotent: every
    fallback either leaves its input alone (when the op is available) or
    produces output nothing here would touch again (NORMAL/LEFT text with no
    unavailable run styles and no list attributes), so a second pass changes
    nothing and reports no warnings.
    """
    if FormatOp.CLEAR_FORMATTING not in keymap:
        raise ValueError(
            "keymap is missing required op(s): clear_formatting -- every "
            "keymap must define it, even as keymaps.NO_OP"
        )

    substitution_counts: dict[ParagraphStyle, int] = {}
    heading_fallback_counts: dict[ParagraphStyle, int] = {}
    alignment_fallback_counts: dict[Alignment, int] = {}
    style_removed_counts: dict[TextStyle, int] = {}
    bold_available = FormatOp.TOGGLE_BOLD in keymap

    # --- stage 1: paragraph style (Title/Subtitle substitution, then heading
    # representability) and alignment ---------------------------------------
    stage1: list[Paragraph] = []
    for paragraph in doc.paragraphs:
        style = paragraph.style
        if style in _STYLE_SUBSTITUTIONS and _SET_OP_BY_STYLE[style] not in keymap:
            substitution_counts[style] = substitution_counts.get(style, 0) + 1
            style = _STYLE_SUBSTITUTIONS[style]

        runs = paragraph.runs
        if style is not ParagraphStyle.NORMAL and not _heading_representable(style, keymap):
            heading_fallback_counts[style] = heading_fallback_counts.get(style, 0) + 1
            if bold_available:
                runs = tuple(Run(run.text, run.styles | {TextStyle.BOLD}) for run in runs)
            style = ParagraphStyle.NORMAL

        alignment = paragraph.alignment
        if alignment is not Alignment.LEFT and _ALIGN_OP_BY_ALIGNMENT[alignment] not in keymap:
            alignment_fallback_counts[alignment] = alignment_fallback_counts.get(alignment, 0) + 1
            alignment = Alignment.LEFT

        stage1.append(dataclasses.replace(paragraph, runs=runs, style=style, alignment=alignment))

    # --- stage 2: run text-style-toggle availability ------------------------
    stage2: list[Paragraph] = []
    for paragraph in stage1:
        new_runs = []
        for run in paragraph.runs:
            keep = frozenset(s for s in run.styles if _TOGGLE_OP_BY_TEXT_STYLE[s] in keymap)
            for removed_style in run.styles - keep:
                style_removed_counts[removed_style] = style_removed_counts.get(removed_style, 0) + 1
            new_runs.append(run if keep == run.styles else Run(run.text, keep))
        stage2.append(dataclasses.replace(paragraph, runs=tuple(new_runs)))

    # --- stage 3: list fallback (sequential -- numbering needs run state) --
    bullet_needs_fallback = _list_kind_needs_fallback(ListKind.BULLET, keymap)
    numbered_needs_fallback = _list_kind_needs_fallback(ListKind.NUMBERED, keymap)
    bullet_fallback_count = 0
    numbered_fallback_count = 0
    counters: dict[int, int] = {}
    last_numbered_level: int | None = None

    stage3: list[Paragraph] = []
    for paragraph in stage2:
        kind = paragraph.list_kind
        if kind is ListKind.BULLET and bullet_needs_fallback:
            # A bullet item is still a list paragraph, just a different kind
            # -- per the spec, only a genuine *non-list* paragraph breaks a
            # numbered run, so last_numbered_level/counters are deliberately
            # left untouched here.
            bullet_fallback_count += 1
            marker = _LIST_INDENT_UNIT * paragraph.list_level + "• "
            stage3.append(_flatten_list_paragraph(paragraph, marker))
        elif kind is ListKind.NUMBERED and numbered_needs_fallback:
            numbered_fallback_count += 1
            level = paragraph.list_level
            if last_numbered_level is None:
                counters = {}
            elif level < last_numbered_level:
                counters = {lvl: n for lvl, n in counters.items() if lvl <= level}
            counters[level] = counters.get(level, 0) + 1
            last_numbered_level = level
            marker = _LIST_INDENT_UNIT * level + f"{counters[level]}. "
            stage3.append(_flatten_list_paragraph(paragraph, marker))
        else:
            if kind is None:
                last_numbered_level = None
            stage3.append(paragraph)

    # --- assemble warnings, one aggregated line per fallback kind ----------
    warnings: list[str] = []

    for style, count in sorted(substitution_counts.items(), key=lambda kv: kv[0].value):
        target = _STYLE_SUBSTITUTIONS[style]
        warnings.append(
            f"{count} {_STYLE_LABEL[style]} paragraph{'' if count == 1 else 's'} typed as "
            f"{_STYLE_LABEL[target]} (no {_STYLE_LABEL[style]} shortcut in this keymap)"
        )

    bold_suffix = ", bold" if bold_available else ""
    for style, count in sorted(heading_fallback_counts.items(), key=lambda kv: kv[0].value):
        warnings.append(
            f"{count} {_STYLE_LABEL[style]} paragraph{'' if count == 1 else 's'} typed as "
            f"Normal{bold_suffix} (no {_STYLE_LABEL[style]} shortcut in this keymap)"
        )

    for alignment, count in sorted(alignment_fallback_counts.items(), key=lambda kv: kv[0].value):
        warnings.append(
            f"{count} paragraph{'' if count == 1 else 's'} typed as left-aligned (no "
            f"{_ALIGNMENT_LABEL[alignment]} shortcut in this keymap)"
        )

    for text_style, count in sorted(style_removed_counts.items(), key=lambda kv: kv[0].value):
        label = _TEXT_STYLE_LABEL[text_style]
        warnings.append(
            f"{count} run{'' if count == 1 else 's'} lost {label} formatting "
            f"(no {label} shortcut in this keymap)"
        )

    if bullet_fallback_count:
        label = _LIST_KIND_LABEL[ListKind.BULLET]
        warnings.append(
            f"{bullet_fallback_count} {label} paragraph{'' if bullet_fallback_count == 1 else 's'} "
            f'typed as plain text with a "•" marker (no {label} shortcut in this keymap)'
        )
    if numbered_fallback_count:
        label = _LIST_KIND_LABEL[ListKind.NUMBERED]
        warnings.append(
            f"{numbered_fallback_count} {label} paragraph"
            f"{'' if numbered_fallback_count == 1 else 's'} typed as plain text with typed "
            f"numbers (no {label} shortcut in this keymap)"
        )

    return Document(tuple(stage3)), tuple(warnings)
