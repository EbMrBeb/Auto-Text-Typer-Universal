"""Parse a Google Docs HTML export (or the "Web page, zipped" download) into
this project's ``Document`` model, using only ``html.parser`` from the
standard library.

Google's export embeds every paragraph's and run's formatting as CSS: a
``<style>`` block defining classes like ``.c3{font-weight:700}``, referenced
from ``class="c3"`` attributes on the elements themselves (occasionally
alongside an inline ``style="..."`` attribute carrying the same kind of
declarations directly). This module resolves both down to the same four
character styles and the same paragraph-alignment values the rest of the
project understands, because that -- not the CSS itself -- is what can
actually be reproduced by pressing a Google Docs keyboard shortcut while
typing.

Lists are the one structural surprise: Google does not nest ``<ul>``/``<ol>``
elements to represent indent level the way a hand-written document would.
It emits a flat run of sibling list elements, one per *visible* nesting
change, and encodes the actual level in the class name itself
(``lst-kix_<listid>-<level>``). This module reads that suffix rather than
tree depth.

Footnotes and comments are the other surprise, and the dangerous one. Google
marks the note's own text in no way at all: it is an ordinary ``<p>`` inside
an ordinary ``<div>``, recognisable only by the small back-link at its start.
Read as ordinary content it becomes the closing paragraphs of the essay. This
module recognises that back-link, drops the note, and counts each footnote
once rather than once per half of it (see ``_NOTE_ID_RE``).

Anything with no keyboard-typable equivalent -- images, tables, hard rules,
links (as clickable links), footnotes, comments, non-default fonts/sizes/
colours -- is dropped from the Document and reported through the warnings
this module returns, never silently lost. And nothing that *is* typable is
ever run onto its neighbour: a line break or a nested block element inside
inline content ends the paragraph it is in, so two unrelated pieces of text
never fuse into a word that is in no document (see ``_group_pieces``).
"""

from __future__ import annotations

import io
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from html.parser import HTMLParser

from auto_essay_typer.document import (
    Alignment,
    Document,
    ListKind,
    Paragraph,
    ParagraphStyle,
    Run,
    TextStyle,
)
from auto_essay_typer.importers.notes import NoteKind, render

# --- a minimal DOM ------------------------------------------------------------
#
# html.parser is a SAX-style (event) parser; everything below it builds just
# enough of a tree from those events to let the rest of this module walk the
# document the way a person reads it -- block elements in order, each
# holding a run of inline content -- rather than reacting to tags as they
# stream by.


class _Node:
    """One element: its tag, attributes, and children (``_Node`` or ``str``)."""

    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list["_Node | str"] = []


# Elements that never have a closing tag (or content) -- never pushed onto
# the open-element stack, so a missing "</img>" etc. can't unbalance it.
_VOID_TAGS = frozenset({"img", "br", "hr", "meta", "link", "input", "col", "area", "base"})


class _TreeBuilder(HTMLParser):
    """Turn an HTML document into a tree of :class:`_Node`, leniently.

    Stray or mismatched closing tags (not uncommon in generated HTML) are
    ignored rather than raising: this parser's job is to extract what it can
    from real Google Docs exports, not to validate them.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {})
        self._stack: list[_Node] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, {k: (v if v is not None else "") for k, v in attrs})
        self._stack[-1].children.append(node)
        if tag not in _VOID_TAGS:
            self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        for depth in range(len(self._stack) - 1, 0, -1):
            if self._stack[depth].tag == tag:
                del self._stack[depth:]
                return
        # No matching open tag on the stack -- ignore the stray close.

    def handle_data(self, data: str) -> None:
        if data:
            self._stack[-1].children.append(data)


def _build_tree(html: str) -> _Node:
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


def _find_first(node: _Node, tag: str) -> _Node | None:
    if node.tag == tag:
        return node
    for child in node.children:
        if isinstance(child, str):
            continue
        found = _find_first(child, tag)
        if found is not None:
            return found
    return None


# --- CSS: <style> blocks and inline style="" attributes -----------------------


def _parse_declarations(text: str) -> dict[str, str]:
    """``"font-weight:700;font-style:italic"`` -> ``{"font-weight": "700", ...}``.

    Shared between ``<style>`` block rule bodies and inline ``style=""``
    attributes -- both use the same ``prop:value;prop:value`` grammar.
    """
    props: dict[str, str] = {}
    for decl in text.split(";"):
        prop, sep, value = decl.partition(":")
        if not sep:
            continue
        prop = prop.strip().lower()
        value = value.strip()
        if prop:
            props[prop] = value
    return props


_CSS_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_CSS_CLASS_RE = re.compile(r"\.([\w-]+)")


def _collect_style_text(node: _Node, out: list[str]) -> None:
    if node.tag == "style":
        out.extend(child for child in node.children if isinstance(child, str))
        return
    for child in node.children:
        if not isinstance(child, str):
            _collect_style_text(child, out)


def _parse_stylesheets(tree: _Node) -> dict[str, dict[str, str]]:
    """Every ``<style>`` block anywhere in the document -> ``{class: props}``.

    A class touched by more than one rule (or a rule with more than one
    comma-separated class selector) has its declarations merged, later rule
    winning per property -- close enough to real CSS cascade for a
    machine-generated stylesheet that never relies on specificity tricks.
    Selectors this project has no use for (id selectors, ``:before``
    pseudo-elements carrying list bullet glyphs, bare element selectors) are
    harmless to also match here: they just populate properties this module
    never looks up.
    """
    chunks: list[str] = []
    _collect_style_text(tree, chunks)
    css_text = "".join(chunks)

    class_props: dict[str, dict[str, str]] = {}
    for selectors, body in _CSS_RULE_RE.findall(css_text):
        props = _parse_declarations(body)
        if not props:
            continue
        for selector in selectors.split(","):
            for class_name in _CSS_CLASS_RE.findall(selector):
                class_props.setdefault(class_name, {}).update(props)
    return class_props


# --- resolving one element's effective style -----------------------------------


def _default_font_size(value: str) -> bool:
    return value.strip().lower() == "11pt"


def _default_color(value: str) -> bool:
    return value.strip().lower() in ("#000000", "black", "rgb(0,0,0)", "rgb(0, 0, 0)")


def _default_font_family(value: str) -> bool:
    return "arial" in value.strip().strip("'\"").lower()


@dataclass
class _ParseContext:
    """The stylesheet, plus everything the walk accumulates about what is lost.

    ``note_keys`` is not a count of its own: it is which footnotes and
    comments have already been counted, so that the two halves Google writes
    for one of them (see :func:`_note_key`) add up to one.
    """

    class_props: dict[str, dict[str, str]]
    warnings: Counter[NoteKind] = field(default_factory=Counter)
    non_default_style: bool = False
    note_keys: set[tuple[str, str]] = field(default_factory=set)

    def add_note(self, key: tuple[str, str] | None) -> None:
        """Count one footnote or comment, once however many halves of it turn up.

        A note with no key of its own -- an unnumbered comments block -- is
        always counted, since there is nothing to match it against.
        """
        if key is not None:
            if key in self.note_keys:
                return
            self.note_keys.add(key)
        self.warnings[NoteKind.FOOTNOTE] += 1

    def resolve_props(self, node: _Node) -> dict[str, str]:
        """This element's effective CSS: its classes' declarations, then its
        own ``style=""`` attribute (which wins on conflicts, as in real CSS).

        Also the single place that notices non-default fonts/sizes/colours,
        since every element whose style matters for anything else passes
        through here exactly once.
        """
        merged: dict[str, str] = {}
        for class_name in node.attrs.get("class", "").split():
            merged.update(self.class_props.get(class_name, {}))
        merged.update(_parse_declarations(node.attrs.get("style", "")))

        if "font-family" in merged and not _default_font_family(merged["font-family"]):
            self.non_default_style = True
        if "font-size" in merged and not _default_font_size(merged["font-size"]):
            self.non_default_style = True
        if "color" in merged and not _default_color(merged["color"]):
            self.non_default_style = True

        return merged


def _is_bold_weight(value: str) -> bool:
    value = value.strip().lower()
    if value == "bold":
        return True
    try:
        return int(value) >= 600
    except ValueError:
        return False


def _text_styles_from_props(props: dict[str, str]) -> frozenset[TextStyle]:
    styles: set[TextStyle] = set()
    if _is_bold_weight(props.get("font-weight", "")):
        styles.add(TextStyle.BOLD)
    if props.get("font-style", "").strip().lower() == "italic":
        styles.add(TextStyle.ITALIC)
    decoration = props.get("text-decoration", "").strip().lower()
    if "underline" in decoration:
        styles.add(TextStyle.UNDERLINE)
    if "line-through" in decoration:
        styles.add(TextStyle.STRIKETHROUGH)
    return frozenset(styles)


_ALIGNMENT_BY_CSS_VALUE = {
    "center": Alignment.CENTER,
    "right": Alignment.RIGHT,
    "justify": Alignment.JUSTIFIED,
}


def _resolve_alignment(node: _Node, ctx: _ParseContext) -> Alignment:
    value = ctx.resolve_props(node).get("text-align", "").strip().lower()
    return _ALIGNMENT_BY_CSS_VALUE.get(value, Alignment.LEFT)


# --- footnotes / comments -------------------------------------------------------
#
# Google Docs has no keyboard-typable equivalent of a footnote or a comment,
# so both are dropped everywhere they can appear: the small superscript
# reference mark inline in the body text, and the note's own text, which
# Google writes after the body.
#
# Nothing marks the note's text as a note. Google gives it an ordinary <p>
# inside an ordinary <div>, and the only sign is the small back-link at its
# start -- so a parser that goes by the elements alone types the whole
# bibliography as the closing paragraphs of the essay.

#: Google's ids for the two halves of one footnote or comment: the marker in
#: the body text ("ftnt_ref3") and the note's own text ("ftnt3"). Both reduce
#: to the same key, so a footnote is reported once and not once per half.
_NOTE_ID_RE = re.compile(r"^#?(ftnt|cmnt)(_ref)?(\d*)$")

#: Inline elements Google may wrap a marker or a back-link in.
_INLINE_WRAPPER_TAGS = frozenset(
    {"span", "sup", "sub", "b", "i", "u", "s", "strike", "em", "strong", "font", "small"}
)


def _note_key(value: str) -> tuple[str, str] | None:
    """``"ftnt_ref3"`` / ``"ftnt3"`` / ``"#ftnt3"`` all become ``("ftnt", "3")``."""
    match = _NOTE_ID_RE.match(value.strip().lower())
    return (match.group(1), match.group(3)) if match else None


def _note_anchor_key(node: _Node) -> tuple[str, str] | None:
    """Which footnote or comment this ``<a>`` belongs to, or None for an
    ordinary link (the one kind of anchor whose text *is* typed)."""
    if node.tag != "a":
        return None
    for attr in ("id", "href"):
        key = _note_key(node.attrs.get(attr, ""))
        if key is not None:
            return key
    return None


def _is_note_text_anchor(node: _Node) -> bool:
    """Whether this ``<a>`` is the back-link at the start of a note's own text,
    rather than the marker left in the body text.

    The two differ only by a ``_ref`` in the id Google gives them.
    """
    if node.tag != "a":
        return False
    match = _NOTE_ID_RE.match(node.attrs.get("id", "").strip().lower())
    return match is not None and match.group(2) is None


def _leading_note_key(node: _Node) -> tuple[str, str] | None:
    """The note whose text this block holds, if it opens with a back-link.

    A block whose *first* content is a back-link is a note; a body paragraph
    that happens to start with a footnote marker is not.
    """
    for child in node.children:
        if isinstance(child, str):
            if child.strip():
                return None
            continue
        if _is_note_text_anchor(child):
            return _note_anchor_key(child)
        if child.tag in _INLINE_WRAPPER_TAGS:
            return _leading_note_key(child)
        return None
    return None


def _is_footnote_or_comment_section(node: _Node) -> bool:
    ident = node.attrs.get("id", "").lower()
    classes = node.attrs.get("class", "").lower().split()
    return (
        ident in ("footnotes", "comments")
        or ident.startswith("ftnt")
        or ident.startswith("cmnt")
        or "footnote" in classes
        or "comment" in classes
    )


def _count_notes_under(node: _Node, ctx: _ParseContext) -> int:
    """Count every footnote and comment anchored anywhere under ``node``."""
    found = 0
    key = _note_anchor_key(node)
    if key is not None:
        found += 1
        ctx.add_note(key)
    for child in node.children:
        if not isinstance(child, str):
            found += _count_notes_under(child, ctx)
    return found


def _record_note_section(node: _Node, ctx: _ParseContext) -> None:
    """Count the notes in a section Google appends after the body, and drop it.

    Counting the back-links inside rather than the section itself is what
    keeps a note that also has a marker in the body text from being reported
    twice. A section with no back-links at all (a bare comments block) still
    stands for one thing the user will not get, so it counts as one.
    """
    if not _count_notes_under(node, ctx):
        ctx.add_note(None)


# --- inline content: text, spans, links, images, line breaks -------------------


def _clean_text(text: str) -> str:
    """Non-breaking spaces become regular spaces; embedded newlines become
    spaces (only whitespace *within* a text node -- never collapsed further,
    and never stripped, since Google splits one visual sentence across
    several adjacent spans and a boundary space is often meaningful)."""
    return (
        text.replace("\xa0", " ")
        .replace("\r\n", " ")
        .replace("\n", " ")
        .replace("\r", " ")
    )


class _Split(Enum):
    """A boundary between two Paragraphs, inside one block element's content.

    The two kinds behave differently when the groups are built. A
    ``SOFT_BREAK`` always ends its paragraph, even where that leaves an empty
    one, because the reader sees that blank line; a ``BLOCK`` edge only ends
    one where there is text on both sides of it, or every wrapper ``<div>``
    would add a blank paragraph of its own.
    """

    SOFT_BREAK = "soft_break"  # a <br>
    BLOCK = "block"  # a nested block element's own start or end


# A piece of inline content: either one (text, styles) span, or a split point
# between two Paragraphs the planner must reach with a real Enter keypress,
# since there is no "soft newline" action in this project's typing model.
_Piece = tuple[str, frozenset[TextStyle]] | _Split

#: Elements that are paragraphs (or hold them) rather than inline content. One
#: nested inside inline content still has its own edges: without them its text
#: would be fused onto whatever sits either side of it -- a <p> inside a table
#: cell onto the next <p>, a nested list's item onto the item it hangs under.
_BLOCK_TAGS = frozenset(
    {
        "p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
        "ul", "ol", "li", "dl", "dt", "dd",
        "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption",
        "blockquote", "pre", "figure", "figcaption", "address",
        "section", "article", "header", "footer", "main", "nav", "aside",
    }
)

#: Elements whose content is machinery rather than text anyone reads: a
#: stylesheet (already consumed for its declarations), the page's own title,
#: and the head that holds them. Typing any of it would put CSS in the essay.
_NON_CONTENT_TAGS = frozenset(
    {
        "style", "script", "noscript", "template",
        "head", "title", "meta", "link", "base",
        "colgroup", "col",
    }
)


def _extract_inline(node: _Node, inherited: frozenset[TextStyle], ctx: _ParseContext) -> list[_Piece]:
    pieces: list[_Piece] = []
    for child in node.children:
        if isinstance(child, str):
            text = _clean_text(child)
            if text:
                pieces.append((text, inherited))
            continue

        tag = child.tag
        note_key = _note_anchor_key(child)
        if tag == "br":
            pieces.append(_Split.SOFT_BREAK)
        elif tag == "img":
            ctx.warnings[NoteKind.IMAGE] += 1
        elif tag == "hr":
            ctx.warnings[NoteKind.RULE] += 1
        elif tag in _NON_CONTENT_TAGS:
            # A <style> or <script> that ended up inside body content: its
            # text is machinery, and typing it would put CSS in the essay.
            continue
        elif note_key is not None:
            # A footnote's or comment's marker: a small superscript number
            # with no keystroke behind it, so it is reported, not typed.
            ctx.add_note(note_key)
        elif tag == "a":
            ctx.warnings[NoteKind.LINK] += 1
            child_styles = inherited | _text_styles_from_props(ctx.resolve_props(child))
            pieces.extend(_extract_inline(child, child_styles, ctx))
        elif tag in _BLOCK_TAGS:
            if tag == "table":
                ctx.warnings[NoteKind.TABLE] += 1
            child_styles = inherited | _text_styles_from_props(ctx.resolve_props(child))
            pieces.append(_Split.BLOCK)
            pieces.extend(_extract_inline(child, child_styles, ctx))
            pieces.append(_Split.BLOCK)
        else:
            # span, b, i, u, s/strike, sup, sub, font, or anything else --
            # treated as a generic inline wrapper: fold in its own style,
            # recurse for its text.
            child_styles = inherited | _text_styles_from_props(ctx.resolve_props(child))
            pieces.extend(_extract_inline(child, child_styles, ctx))
    return pieces


def _merge_runs(pieces: list[tuple[str, frozenset[TextStyle]]]) -> tuple[Run, ...]:
    merged: list[Run] = []
    for text, styles in pieces:
        if not text:
            continue
        if merged and merged[-1].styles == styles:
            merged[-1] = Run(merged[-1].text + text, styles)
        else:
            merged.append(Run(text, styles))
    return tuple(merged)


def _group_pieces(
    pieces: list[_Piece], ctx: _ParseContext
) -> list[list[tuple[str, frozenset[TextStyle]]]]:
    """One block's inline pieces, split into one group of runs per Paragraph.

    Only the ``<br>`` splits are counted as soft line breaks: a nested block
    element's edges are structure the reader never sees as a break, so
    reporting them would tell the user about line breaks their document does
    not have. See :class:`_Split` for why the two kinds split differently.
    """
    groups: list[list[tuple[str, frozenset[TextStyle]]]] = [[]]
    pending_block_edge = False
    for piece in pieces:
        if piece is _Split.SOFT_BREAK:
            ctx.warnings[NoteKind.BREAK] += 1
            groups.append([])
            pending_block_edge = False
        elif piece is _Split.BLOCK:
            pending_block_edge = True
        else:
            if pending_block_edge and groups[-1]:
                groups.append([])
            pending_block_edge = False
            groups[-1].append(piece)
    return groups


def _build_paragraphs_from_block(
    node: _Node,
    style: ParagraphStyle,
    list_kind: ListKind | None,
    list_level: int,
    ctx: _ParseContext,
) -> list[Paragraph]:
    """One block element (``<p>``, ``<h1>``..``<h6>``, or a list's ``<li>``)
    -> one or more Paragraphs (more than one iff its content splits).

    A split half inherits the same style/alignment/list placement as the
    whole block it came from -- exactly what a real Enter keypress would
    produce standing in for that soft break (see ``simulate_document``'s
    rule that a new paragraph inherits alignment and list from the one
    before it).
    """
    alignment = _resolve_alignment(node, ctx)
    pieces = _extract_inline(node, frozenset(), ctx)

    return [
        Paragraph(
            runs=_merge_runs(group),
            style=style,
            alignment=alignment,
            list_kind=list_kind,
            list_level=list_level,
        )
        for group in _group_pieces(pieces, ctx)
    ]


# --- block-level structure: paragraphs, headings, lists, tables ---------------

_HEADING_STYLE_BY_TAG: dict[str, ParagraphStyle] = {
    "h1": ParagraphStyle.HEADING_1,
    "h2": ParagraphStyle.HEADING_2,
    "h3": ParagraphStyle.HEADING_3,
    "h4": ParagraphStyle.HEADING_4,
    "h5": ParagraphStyle.HEADING_5,
    "h6": ParagraphStyle.HEADING_6,
}

_LIST_KIND_BY_TAG: dict[str, ListKind] = {"ul": ListKind.BULLET, "ol": ListKind.NUMBERED}

# Google encodes nesting depth in the class name itself, e.g.
# "lst-kix_kix.abc123-2" is level 2 -- see the module docstring.
_LIST_LEVEL_RE = re.compile(r"\blst-kix_\S*-(\d+)\b")


def _list_level_from_class(class_attr: str) -> int:
    m = _LIST_LEVEL_RE.search(class_attr)
    if not m:
        return 0
    return max(0, min(int(m.group(1)), 8))


def _flatten_table(table: _Node, ctx: _ParseContext) -> list[Paragraph]:
    """Every cell's paragraphs in reading order -- the grid itself is dropped
    (there is no keyboard shortcut that recreates one).

    A cell holding more than one paragraph, or a line break, keeps those
    boundaries. Run together they would fuse the end of one line onto the
    start of the next and invent a word that is in no document.
    """
    ctx.warnings[NoteKind.TABLE] += 1
    cells: list[Paragraph] = []

    def visit(container: _Node) -> None:
        for child in container.children:
            if isinstance(child, str):
                continue
            if child.tag in ("caption", "td", "th"):
                # A caption is not a cell, but it is text the reader sees, and
                # it sits ahead of the rows in reading order as it does here.
                pieces = _extract_inline(child, frozenset(), ctx)
                cells.extend(
                    Paragraph(runs=_merge_runs(group))
                    for group in _group_pieces(pieces, ctx)
                )
            elif child.tag in ("tbody", "thead", "tfoot", "tr"):
                visit(child)
            # colgroup/col carry no text at all -- ignored.

    visit(table)
    return cells


def _handle_block(node: _Node, ctx: _ParseContext, out: list[Paragraph]) -> None:
    tag = node.tag

    if tag in _NON_CONTENT_TAGS:
        return

    if _is_footnote_or_comment_section(node):
        _record_note_section(node, ctx)
        return

    leading_note = _leading_note_key(node)
    if leading_note is not None:
        # A note's own text, which Google marks only with the back-link at
        # its start. Typed as it stands it would read as the last paragraph
        # of the essay.
        ctx.add_note(leading_note)
        return

    heading_style = _HEADING_STYLE_BY_TAG.get(tag)
    if heading_style is not None:
        out.extend(_build_paragraphs_from_block(node, heading_style, None, 0, ctx))
        return

    if tag == "p":
        classes = node.attrs.get("class", "").split()
        if "title" in classes:
            style = ParagraphStyle.TITLE
        elif "subtitle" in classes:
            style = ParagraphStyle.SUBTITLE
        else:
            style = ParagraphStyle.NORMAL
        out.extend(_build_paragraphs_from_block(node, style, None, 0, ctx))
        return

    list_kind = _LIST_KIND_BY_TAG.get(tag)
    if list_kind is not None:
        level = _list_level_from_class(node.attrs.get("class", ""))
        for child in node.children:
            if isinstance(child, str):
                continue
            if child.tag == "li":
                out.extend(
                    _build_paragraphs_from_block(
                        child, ParagraphStyle.NORMAL, list_kind, level, ctx
                    )
                )
            else:
                # A list is not meant to hold anything but its items, but
                # text put there anyway still has to be typed.
                _handle_block(child, ctx, out)
        return

    if tag == "table":
        out.extend(_flatten_table(node, ctx))
        return

    if tag == "hr":
        ctx.warnings[NoteKind.RULE] += 1
        return

    # Anything else that holds block content: the plain <div> Google wraps
    # some ordinary content in (the footnote/comment section, handled above,
    # is one too), a <blockquote>, a <section>, a stray <li>. None of them
    # carries a paragraph style of its own, but all of them hold text that
    # still has to be typed, so each is walked rather than dropped.
    _handle_container(node, ctx, out)


def _handle_container(node: _Node, ctx: _ParseContext, out: list[Paragraph]) -> None:
    """Walk a container: the ``<body>``, or any block element with no
    paragraph style of its own -- a plain wrapper ``<div>``, a
    ``<blockquote>``, a ``<section>``.

    Its block children are handled one by one, so a heading or a list inside
    it keeps the style it carries rather than being flattened into the
    container's own content. Anything else is inline content with no
    paragraph of its own: text sitting straight in the container, a bare
    ``<span>``, an ``<img>``. That is gathered up and becomes a paragraph
    where it stands, because a file that is simply plain text would
    otherwise import as an empty document with nothing reported.
    """
    inline: list[_Node | str] = []

    def flush() -> None:
        if not inline:
            return
        # The gathered content needs an element to sit in to be read as one
        # block's worth. It is not in the tree, so it carries no classes and
        # no alignment -- which is right: the alignment belongs to the
        # container, not to this run of text.
        holder = _Node("p", {})
        holder.children = list(inline)
        inline.clear()
        pieces = _extract_inline(holder, frozenset(), ctx)
        if not pieces:
            # Nothing typable in it: a horizontal rule, a bare image. Both
            # have been counted already, and neither is a blank line the
            # reader sees, so neither gets a paragraph.
            return
        out.extend(
            Paragraph(runs=_merge_runs(group)) for group in _group_pieces(pieces, ctx)
        )

    for child in node.children:
        if isinstance(child, str):
            if child.strip():
                inline.append(child)
        elif child.tag in _NON_CONTENT_TAGS:
            continue
        elif child.tag in _BLOCK_TAGS:
            flush()
            _handle_block(child, ctx, out)
        else:
            inline.append(child)
    flush()


def _walk_body(body: _Node, ctx: _ParseContext) -> list[Paragraph]:
    paragraphs: list[Paragraph] = []
    _handle_container(body, ctx, paragraphs)
    return paragraphs


# --- warnings -------------------------------------------------------------------


def _render_warnings(ctx: _ParseContext) -> tuple[str, ...]:
    """Everything the walk could not turn into keystrokes, as the messages the report carries.

    The wording is in :mod:`~auto_essay_typer.importers.notes`, shared with the Word
    reader. A table cell's content is read as inline text alone (``_flatten_table``), so a
    heading, an alignment or a list inside a cell comes out as ordinary text: this reader
    does not keep a paragraph's formatting there (``keeps_paragraph_formatting``).
    """
    return render(
        ctx.warnings,
        keeps_paragraph_formatting=False,
        custom_style=ctx.non_default_style,
    )


# --- public entry points ---------------------------------------------------------


def parse_gdocs_html(html: str) -> tuple[Document, tuple[str, ...]]:
    """Parse a Google Docs HTML export (or any similarly-shaped HTML) into a
    ``Document`` plus aggregated warnings for everything that could not be
    reproduced. Works on a bare fragment too (no ``<html>``/``<body>``
    wrapper required), which is all a hand-written test fixture needs."""
    tree = _build_tree(html)
    ctx = _ParseContext(class_props=_parse_stylesheets(tree))
    body = _find_first(tree, "body")
    paragraphs = _walk_body(body if body is not None else tree, ctx)
    return Document(tuple(paragraphs)), _render_warnings(ctx)


def parse_gdocs_html_zip(data: bytes) -> tuple[Document, tuple[str, ...]]:
    """Parse the Docs "Web page (.html, zipped)" download: a .zip holding one
    top-level .html file (plus an images/ folder this tool has no use for,
    since images can never be reproduced by typing)."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("not a valid zip archive") from exc

    with zf:
        html_names = [name for name in zf.namelist() if name.lower().endswith((".html", ".htm"))]
        if not html_names:
            raise ValueError("zip archive does not contain an .html file")
        html_bytes = zf.read(sorted(html_names, key=len)[0])

    return parse_gdocs_html(html_bytes.decode("utf-8", errors="replace"))
