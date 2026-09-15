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

Anything with no keyboard-typable equivalent -- images, tables, hard rules,
links (as clickable links), footnotes, comments, non-default fonts/sizes/
colours -- is dropped from the Document and reported through the warnings
this module returns, never silently lost.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
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
    class_props: dict[str, dict[str, str]]
    warnings: Counter[str] = field(default_factory=Counter)
    non_default_style: bool = False

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
# reference mark inline in the body text, and the section Google appends
# after the body listing their contents.


def _is_footnote_or_comment_anchor(node: _Node) -> bool:
    if node.tag != "a":
        return False
    ident = node.attrs.get("id", "").lower()
    href = node.attrs.get("href", "").lower()
    return (
        ident.startswith("ftnt")
        or ident.startswith("cmnt")
        or href.startswith("#ftnt")
        or href.startswith("#cmnt")
    )


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


# A piece of inline content: either one (text, styles) span, or None marking
# a <br> -- a split point between two Paragraphs the planner must reach with
# a real Enter keypress, since there is no "soft newline" action in this
# project's typing model.
_Piece = tuple[str, frozenset[TextStyle]] | None


def _extract_inline(node: _Node, inherited: frozenset[TextStyle], ctx: _ParseContext) -> list[_Piece]:
    pieces: list[_Piece] = []
    for child in node.children:
        if isinstance(child, str):
            text = _clean_text(child)
            if text:
                pieces.append((text, inherited))
            continue

        tag = child.tag
        if tag == "br":
            pieces.append(None)
        elif tag == "img":
            ctx.warnings["image"] += 1
        elif tag == "hr":
            ctx.warnings["hr"] += 1
        elif _is_footnote_or_comment_anchor(child):
            ctx.warnings["footnote"] += 1
        elif tag == "a":
            ctx.warnings["link"] += 1
            child_styles = inherited | _text_styles_from_props(ctx.resolve_props(child))
            pieces.extend(_extract_inline(child, child_styles, ctx))
        else:
            # span, b, i, u, s/strike, sup, sub, font, or anything else --
            # treated as a generic inline wrapper: fold in its own style,
            # recurse for its text. This is also how a <p> nested inside a
            # table cell gets flattened transparently (see _flatten_table).
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


def _build_paragraphs_from_block(
    node: _Node,
    style: ParagraphStyle,
    list_kind: ListKind | None,
    list_level: int,
    ctx: _ParseContext,
) -> list[Paragraph]:
    """One block element (``<p>``, ``<h1>``..``<h6>``, or a list's ``<li>``)
    -> one or more Paragraphs (more than one iff it contains a ``<br>``).

    A split half inherits the same style/alignment/list placement as the
    whole block it came from -- exactly what a real Enter keypress would
    produce standing in for that soft break (see ``simulate_document``'s
    rule that a new paragraph inherits alignment and list from the one
    before it).
    """
    alignment = _resolve_alignment(node, ctx)
    pieces = _extract_inline(node, frozenset(), ctx)

    groups: list[list[tuple[str, frozenset[TextStyle]]]] = [[]]
    for piece in pieces:
        if piece is None:
            ctx.warnings["break"] += 1
            groups.append([])
        else:
            groups[-1].append(piece)

    return [
        Paragraph(
            runs=_merge_runs(group),
            style=style,
            alignment=alignment,
            list_kind=list_kind,
            list_level=list_level,
        )
        for group in groups
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
    """One paragraph per cell, reading order -- table structure itself is
    dropped (there is no keyboard shortcut that recreates a grid)."""
    ctx.warnings["table"] += 1
    cells: list[Paragraph] = []

    def visit(container: _Node) -> None:
        for child in container.children:
            if isinstance(child, str):
                continue
            if child.tag in ("td", "th"):
                pieces = [p for p in _extract_inline(child, frozenset(), ctx) if p is not None]
                cells.append(Paragraph(runs=_merge_runs(pieces)))
            elif child.tag in ("tbody", "thead", "tfoot", "tr"):
                visit(child)
            # caption/colgroup/col etc. carry no cell text -- ignored.

    visit(table)
    return cells


def _handle_block(node: _Node, ctx: _ParseContext, out: list[Paragraph]) -> None:
    tag = node.tag

    if _is_footnote_or_comment_section(node):
        ctx.warnings["footnote"] += 1
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
            if not isinstance(child, str) and child.tag == "li":
                out.extend(_build_paragraphs_from_block(child, ParagraphStyle.NORMAL, list_kind, level, ctx))
        return

    if tag == "table":
        out.extend(_flatten_table(node, ctx))
        return

    if tag == "hr":
        ctx.warnings["hr"] += 1
        return

    if tag == "div":
        # Google wraps some ordinary content in a plain <div> (and the
        # footnote/comment section, already handled above, is one too) --
        # recurse so nothing nested inside an unremarkable <div> is dropped.
        for child in node.children:
            if not isinstance(child, str):
                _handle_block(child, ctx, out)
        return

    # Anything else at block level (a stray <meta>, a <style> already
    # consumed for its CSS, ...) carries no typable content -- ignore.


def _walk_body(body: _Node, ctx: _ParseContext) -> list[Paragraph]:
    paragraphs: list[Paragraph] = []
    for child in body.children:
        if not isinstance(child, str):
            _handle_block(child, ctx, paragraphs)
    return paragraphs


# --- warnings -------------------------------------------------------------------


def _render_warnings(ctx: _ParseContext) -> tuple[str, ...]:
    counts = ctx.warnings
    messages: list[str] = []

    def add(key: str, template: str) -> None:
        n = counts[key]
        if n:
            messages.append(template.format(n=n, s="" if n == 1 else "s"))

    add("link", "{n} link{s} typed as plain text")
    add("image", "{n} image{s} not reproduced (dropped)")
    add("table", "{n} table{s} flattened to one paragraph per cell")
    add("hr", "{n} horizontal rule{s} not reproduced (dropped)")
    add("break", "{n} soft line break{s} typed as paragraph break{s}")
    add("footnote", "{n} footnote/comment reference{s} not reproduced (dropped)")

    if ctx.non_default_style:
        messages.append(
            "this document uses custom fonts, sizes, or colours, which are not reproduced"
        )
    return tuple(messages)


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
