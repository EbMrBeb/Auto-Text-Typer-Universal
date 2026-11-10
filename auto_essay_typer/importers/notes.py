"""Every kind of loss an importer reports, and the one place each is worded.

An importer cannot turn a whole document into keystrokes. What it has to leave out
it says so in ``ImportReport.warnings``, and the Type page then words each of those
messages for the person using the app (``gui/document_warnings.py``). The two sides
meet at a string, which is how a kind of loss can be reported by one and never worded
by the other: text boxes, tracked changes and symbols reached the panel in the
importers' own developer wording because nothing said a rule was owed.

This module is the list both sides work from.

* :class:`NoteKind` names every kind of loss. An importer counts a loss under one of
  its members and hands the lot to :func:`render`, so a kind is worded once however
  many importers report it, and a count under a name that is not a member raises
  instead of being left out of the report.
* The panel must have a rule for every member. ``tests/gui/test_import_note_rules.py``
  fails when it does not, and says what to add and where.

To add a kind: add a member below, word it in :func:`messages_by_kind`, count it in the
importer that finds it, then write its rule in ``gui/document_warnings.py``.

The messages here are worded for a developer ("1 image not reproduced (dropped)"), not
for the person reading the panel, and they are regular on purpose: the panel's rules
recognise each one by an anchored pattern and turn it into a sentence about the
person's document.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum


class NoteKind(StrEnum):
    """One kind of loss an importer reports. The order is the order messages are listed in."""

    #: A hyperlink: its words are typed, but not as a clickable link.
    LINK = "link"
    #: A picture, or a shape with no text in it.
    IMAGE = "image"
    #: A table. The grid is dropped; what is in the cells is typed, in order.
    TABLE = "table"
    #: A horizontal rule (a Google Docs export only).
    RULE = "rule"
    #: A soft line break, typed as a paragraph break.
    BREAK = "break"
    #: A footnote, endnote or comment: the marker in the text *and* the note's own text.
    FOOTNOTE = "footnote"
    #: A character from a symbol font (Word's Insert > Symbol), which has no keystroke.
    SYMBOL = "symbol"
    #: The words in a text box, quoted so the person can tell which part is missing.
    TEXT_BOX = "text_box"
    #: A document with tracked changes, typed as its final version.
    TRACKED_CHANGES = "tracked_changes"
    #: Custom fonts, sizes or colours (a Google Docs export only).
    STYLE = "style"


#: The kinds whose message needs more than a count to be worded.
_DETAIL_KINDS = frozenset({NoteKind.TEXT_BOX, NoteKind.TRACKED_CHANGES, NoteKind.STYLE})

#: The kinds an importer counts one at a time, which is all :func:`render` accepts in ``counts``.
COUNTED_KINDS: tuple[NoteKind, ...] = tuple(kind for kind in NoteKind if kind not in _DETAIL_KINDS)

#: Kinds worded by their count alone. ``{n}`` is the count and ``{s}`` is "" for one, "s"
#: for more. (A table is worded by :func:`_table_message`, which has more to say.)
_COUNTED_TEMPLATES: dict[NoteKind, str] = {
    NoteKind.LINK: "{n} link{s} typed as plain text",
    NoteKind.IMAGE: "{n} image{s} not reproduced (dropped)",
    NoteKind.RULE: "{n} horizontal rule{s} not reproduced (dropped)",
    NoteKind.BREAK: "{n} soft line break{s} typed as paragraph break{s}",
    NoteKind.FOOTNOTE: "{n} footnote/comment reference{s} not reproduced (dropped)",
    NoteKind.SYMBOL: "{n} special symbol{s} will be left out",
}

#: Said once however many changes there were: what matters is not the number but that a
#: choice between two versions was made on the person's behalf.
_TRACKED_CHANGES_MESSAGE = (
    "this document had tracked changes; it is typed as the final version, "
    "with the edits accepted and the deleted text left out"
)

_STYLE_MESSAGE = "this document uses custom fonts, sizes, or colours, which are not reproduced"


def _table_message(count: int, *, keeps_paragraph_formatting: bool) -> str:
    """Say a table's grid is gone and its cells are typed one after another.

    ``keeps_paragraph_formatting`` is accepted here and does not change the wording yet.
    """
    return f"{count} table{'' if count == 1 else 's'} flattened to one paragraph per cell"


def _text_box_message(excerpts: Sequence[str]) -> str:
    """Name the text boxes whose content is missing, quoting each one.

    The quoted words are the point of the message: "a text box will not be
    reproduced" on its own leaves the user hunting through their document for
    which part of it is gone.
    """
    noun = "1 text box's content" if len(excerpts) == 1 else f"{len(excerpts)} text boxes' content"
    quoted = ", ".join(f'"{excerpt}"' for excerpt in excerpts if excerpt)
    if not quoted:
        return f"{noun} will not be reproduced"
    return f"{noun} will not be reproduced: {quoted}"


def messages_by_kind(
    counts: Mapping[str, int],
    *,
    keeps_paragraph_formatting: bool,
    text_box_excerpts: Sequence[str] = (),
    tracked_changes: bool = False,
    custom_style: bool = False,
) -> dict[NoteKind, str]:
    """The message for each kind of loss that was found, in :class:`NoteKind` order.

    ``counts`` is what the importer tallied under :data:`COUNTED_KINDS`. The three kinds
    that need more than a count are passed as what they need: the (already shortened)
    words of each text box found, whether the document had tracked changes, and whether
    it used custom fonts, sizes or colours. ``keeps_paragraph_formatting`` is the
    importer saying whether a paragraph inside a table cell keeps its own heading style,
    alignment and list; it has no default so that no importer can leave the question
    unanswered.

    A count under anything that is not one of :data:`COUNTED_KINDS` raises: a loss
    counted under a name nothing words would otherwise be left out of the report without
    a word, which is the one thing an importer must not do.
    """
    unregistered = sorted({str(key) for key in counts if key not in COUNTED_KINDS})
    if unregistered:
        raise ValueError(
            f"an importer counted a loss under {unregistered} which is not a counted NoteKind "
            f"({', '.join(kind.value for kind in COUNTED_KINDS)}), so it would be left out of "
            "the report. Add the kind to NoteKind in auto_essay_typer/importers/notes.py, word "
            "it in messages_by_kind, and write its rule in auto_essay_typer/gui/document_warnings.py."
        )

    messages: dict[NoteKind, str] = {}
    for kind in NoteKind:
        message: str | None
        if kind is NoteKind.TEXT_BOX:
            message = _text_box_message(text_box_excerpts) if text_box_excerpts else None
        elif kind is NoteKind.TRACKED_CHANGES:
            message = _TRACKED_CHANGES_MESSAGE if tracked_changes else None
        elif kind is NoteKind.STYLE:
            message = _STYLE_MESSAGE if custom_style else None
        elif kind is NoteKind.TABLE:
            count = counts.get(kind, 0)
            message = (
                _table_message(count, keeps_paragraph_formatting=keeps_paragraph_formatting)
                if count
                else None
            )
        else:
            # A kind with no template fails here whether or not it was found, so a member
            # added to NoteKind without its wording cannot wait for a document that has one.
            template = _COUNTED_TEMPLATES[kind]
            count = counts.get(kind, 0)
            message = template.format(n=count, s="" if count == 1 else "s") if count else None
        if message is not None:
            messages[kind] = message
    return messages


def render(
    counts: Mapping[str, int],
    *,
    keeps_paragraph_formatting: bool,
    text_box_excerpts: Sequence[str] = (),
    tracked_changes: bool = False,
    custom_style: bool = False,
) -> tuple[str, ...]:
    """What an importer returns as its warnings: :func:`messages_by_kind`, without the kinds."""
    return tuple(
        messages_by_kind(
            counts,
            keeps_paragraph_formatting=keeps_paragraph_formatting,
            text_box_excerpts=text_box_excerpts,
            tracked_changes=tracked_changes,
            custom_style=custom_style,
        ).values()
    )
