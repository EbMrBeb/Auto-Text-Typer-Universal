"""The formatted-document model shared by importers, the planner, and the
Google Docs editing simulator.

A :class:`Document` is deliberately *not* a rich-text format in the general
sense -- it only models what can actually be reproduced by typing into a
blank Google Doc and pressing its keyboard shortcuts (section 3 of the
project's Google Docs editing model). Anything a source document contains
that cannot be expressed this way (images, tables, fonts, colours, links,
footnotes, comments, soft line breaks, ...) has no representation here at
all; it is the importer's job to report that loss via ``ImportReport``
rather than silently drop it.

``normalized()`` exists because two Documents that differ only in
incidental structure -- an unmerged run split for no visible reason, or a
list attribute left dangling on a paragraph with no text -- must still
compare equal for the invariant this feature rests on:
``simulate_document(plan_document(doc)).normalized() == doc.normalized()``.
"""

from dataclasses import dataclass
from enum import StrEnum


class TextStyle(StrEnum):
    """A character-level style toggled by a Google Docs keyboard shortcut."""

    BOLD = "bold"
    ITALIC = "italic"
    UNDERLINE = "underline"
    STRIKETHROUGH = "strikethrough"


class ParagraphStyle(StrEnum):
    """A Google Docs paragraph style (the "Styles" menu / cmd+alt+n family)."""

    NORMAL = "normal"
    TITLE = "title"
    SUBTITLE = "subtitle"
    HEADING_1 = "heading_1"
    HEADING_2 = "heading_2"
    HEADING_3 = "heading_3"
    HEADING_4 = "heading_4"
    HEADING_5 = "heading_5"
    HEADING_6 = "heading_6"


class Alignment(StrEnum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"
    JUSTIFIED = "justified"


class ListKind(StrEnum):
    BULLET = "bullet"
    NUMBERED = "numbered"


@dataclass(frozen=True, slots=True)
class Run:
    """A span of text sharing one set of character styles.

    ``text`` may never be empty or contain a newline: an empty run carries no
    information a canonical Document needs, and a newline always means a new
    Paragraph rather than a character inside a run.
    """

    text: str
    styles: frozenset[TextStyle] = frozenset()

    def __post_init__(self) -> None:
        if not self.text:
            raise ValueError("Run.text must be non-empty")
        if "\n" in self.text:
            raise ValueError(f"Run.text must not contain a newline, got {self.text!r}")


@dataclass(frozen=True, slots=True)
class Paragraph:
    """One paragraph: a sequence of styled runs plus paragraph-level attributes.

    ``list_level`` is meaningless without a ``list_kind`` -- Google Docs has
    no notion of nesting depth for text that isn't a list item -- so a
    non-zero level with no list kind is rejected rather than silently
    ignored.
    """

    runs: tuple[Run, ...] = ()
    style: ParagraphStyle = ParagraphStyle.NORMAL
    alignment: Alignment = Alignment.LEFT
    list_kind: ListKind | None = None
    list_level: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.list_level <= 8:
            raise ValueError(f"Paragraph.list_level must be 0..8, got {self.list_level}")
        if self.list_kind is None and self.list_level != 0:
            raise ValueError("Paragraph.list_level must be 0 when list_kind is None")

    @property
    def text(self) -> str:
        """The paragraph's plain text: every run's text, concatenated in order."""
        return "".join(run.text for run in self.runs)


def _normalize_paragraph(paragraph: Paragraph) -> Paragraph:
    """Canonicalize one paragraph: see :meth:`Document.normalized`."""
    merged: list[Run] = []
    for run in paragraph.runs:
        if not run.text:
            continue  # Run() already forbids this; defensive, not reachable today.
        if merged and merged[-1].styles == run.styles:
            merged[-1] = Run(merged[-1].text + run.text, merged[-1].styles)
        else:
            merged.append(run)
    runs = tuple(merged)

    if runs:
        list_kind, list_level = paragraph.list_kind, paragraph.list_level
    else:
        # Google Docs ends a list when Enter is pressed on an empty item, so
        # an empty list item is not a state a real document can be in.
        list_kind, list_level = None, 0

    return Paragraph(
        runs=runs,
        style=paragraph.style,
        alignment=paragraph.alignment,
        list_kind=list_kind,
        list_level=list_level,
    )


@dataclass(frozen=True, slots=True)
class Document:
    """An entire document: an ordered sequence of paragraphs."""

    paragraphs: tuple[Paragraph, ...]

    def plain_text(self) -> str:
        """Every paragraph's text, joined by newlines -- what ``simulate()`` produces."""
        return "\n".join(paragraph.text for paragraph in self.paragraphs)

    def normalized(self) -> "Document":
        """Canonical form used for equality in the project's invariants.

        Merges adjacent runs that share identical styles, drops any run left
        with empty text by that merge, and clears the list attributes of any
        paragraph that ends up with no text (Google Docs cannot represent an
        empty list item, so keeping ``list_kind``/``list_level`` set on one
        would compare unequal to a document that never carried them at all).
        """
        return Document(tuple(_normalize_paragraph(p) for p in self.paragraphs))

    @classmethod
    def from_plain_text(cls, text: str) -> "Document":
        """Build a Document with one unstyled NORMAL paragraph per line.

        An empty line becomes a paragraph with no runs (``Run`` cannot hold
        empty text). Splitting and rejoining on ``"\\n"`` are exact inverses,
        so ``from_plain_text(text).plain_text() == text`` for every ``text``.
        """
        paragraphs = tuple(
            Paragraph(runs=(Run(line),) if line else ()) for line in text.split("\n")
        )
        return cls(paragraphs)


@dataclass(frozen=True, slots=True)
class ImportReport:
    """What an importer could and could not reproduce from a source document.

    ``warnings`` names every piece of layout that was dropped rather than
    typed -- images, tables, fonts, sizes, colours, links, footnotes,
    comments, soft line breaks -- so it is always reported, never silently lost.
    """

    source: str
    warnings: tuple[str, ...] = ()
