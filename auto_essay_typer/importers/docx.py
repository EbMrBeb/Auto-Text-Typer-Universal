"""Parse a Microsoft Word ``.docx`` file into this project's ``Document`` model.

A ``.docx`` is a zip of XML parts. Only ``word/document.xml`` (the body),
``word/styles.xml`` (paragraph style id -> display name, e.g. ``"Heading1"``
-> ``"heading 1"``) and ``word/numbering.xml`` (list id -> bullet/numbered,
per level) are read for what they say; everything else in the package (theme,
settings, embedded media, ...) is ignored, with one exception. Word keeps each
header and footer in a part of its own, and nothing in one is typed, so those
parts (``word/header1.xml``, ``word/footer1.xml``, ...) are opened to learn
whether they hold anything, and each that does is reported
(see :func:`_record_headers_and_footers`). Uses only ``zipfile`` and
``xml.etree.ElementTree`` from the standard library, per this project's
no-new-dependencies rule.

Deliberately narrow in what it reads from a run's formatting: only *direct*
``w:rPr`` children of the ``w:r`` are consulted (never style-inherited or
paragraph-default formatting), because that is exactly the set of styles a
person could reproduce by pressing Google Docs shortcuts while typing --
there is no "current paragraph's default run style" concept to press a
shortcut for.

Deliberately *wide* in what it reads as text, because the opposite mistake is
worse: a word Word shows on screen but this parser skips leaves the user with
a silently incomplete essay. So the walk descends through everything that
wraps text without being text -- hyperlinks, content controls, smart tags,
fields -- and resolves tracked changes to the view Word shows, accepting
insertions and leaving deletions out. What it cannot turn into keystrokes
(a picture, a shape's text, a footnote marker, a symbol-font character, a
header or footer) is counted and named in the warnings this module returns,
never dropped in silence.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

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

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"


def _w(tag: str) -> str:
    """Qualify a bare tag name (``"p"``) with the WordprocessingML namespace."""
    return f"{{{_W_NS}}}{tag}"


def _mc(tag: str) -> str:
    """Qualify a bare tag name with the markup-compatibility namespace."""
    return f"{{{_MC_NS}}}{tag}"


_DOCUMENT_XML = "word/document.xml"
_STYLES_XML = "word/styles.xml"
_NUMBERING_XML = "word/numbering.xml"

_ALIGNMENT_BY_JC = {
    "left": Alignment.LEFT,
    "start": Alignment.LEFT,
    "center": Alignment.CENTER,
    "right": Alignment.RIGHT,
    "end": Alignment.RIGHT,
    "both": Alignment.JUSTIFIED,
    "distribute": Alignment.JUSTIFIED,
}

_STYLE_LABEL_TO_PARAGRAPH_STYLE = {
    "title": ParagraphStyle.TITLE,
    "subtitle": ParagraphStyle.SUBTITLE,
    "heading 1": ParagraphStyle.HEADING_1,
    "heading 2": ParagraphStyle.HEADING_2,
    "heading 3": ParagraphStyle.HEADING_3,
    "heading 4": ParagraphStyle.HEADING_4,
    "heading 5": ParagraphStyle.HEADING_5,
    "heading 6": ParagraphStyle.HEADING_6,
}

_OFF_VALUES = {"0", "false", "off"}

_HEADING_LABEL_RE = re.compile(r"^heading\s*([1-6])$")


def _normalize_style_label(label: str) -> str:
    """``"Heading1"`` / ``"Heading 1"`` / ``"heading1"`` all become ``"heading 1"``."""
    label = label.strip().lower()
    m = _HEADING_LABEL_RE.match(label)
    if m:
        return f"heading {m.group(1)}"
    return label


class _DocxError(ValueError):
    """A .docx package is malformed or missing a part this parser needs.

    Not part of the project's ``DocumentImportError`` hierarchy -- that
    hierarchy lives in ``google.py``/``__init__.py``, which wrap this into a
    user-facing error with the source (URL or file path) attached.
    """


@dataclass
class _NumberingSchemes:
    """``word/numbering.xml``, flattened to what a paragraph's ``numPr`` needs."""

    abstract_id_by_num_id: dict[str, str] = field(default_factory=dict)
    kind_by_abstract_and_level: dict[tuple[str, int], ListKind] = field(default_factory=dict)

    def list_kind_for(self, num_id: str, level: int) -> ListKind:
        abstract_id = self.abstract_id_by_num_id.get(num_id)
        if abstract_id is None:
            return ListKind.NUMBERED
        return self.kind_by_abstract_and_level.get((abstract_id, level), ListKind.NUMBERED)


def _parse_styles(root: ET.Element | None) -> dict[str, str]:
    """``word/styles.xml`` -> ``{styleId: normalized display name}``."""
    if root is None:
        return {}
    names: dict[str, str] = {}
    for style in root.findall(_w("style")):
        style_id = style.get(_w("styleId"))
        name_el = style.find(_w("name"))
        if style_id is None or name_el is None:
            continue
        name_val = name_el.get(_w("val"))
        if name_val:
            names[style_id] = _normalize_style_label(name_val)
    return names


def _parse_numbering(root: ET.Element | None) -> _NumberingSchemes:
    """``word/numbering.xml`` -> num id/level -> bullet-or-numbered lookup."""
    schemes = _NumberingSchemes()
    if root is None:
        return schemes

    for num in root.findall(_w("num")):
        num_id = num.get(_w("numId"))
        abstract_ref = num.find(_w("abstractNumId"))
        if num_id is None or abstract_ref is None:
            continue
        abstract_id = abstract_ref.get(_w("val"))
        if abstract_id is not None:
            schemes.abstract_id_by_num_id[num_id] = abstract_id

    for abstract_num in root.findall(_w("abstractNum")):
        abstract_id = abstract_num.get(_w("abstractNumId"))
        if abstract_id is None:
            continue
        for lvl in abstract_num.findall(_w("lvl")):
            ilvl = lvl.get(_w("ilvl"))
            fmt_el = lvl.find(_w("numFmt"))
            if ilvl is None or fmt_el is None:
                continue
            fmt_val = fmt_el.get(_w("val"), "")
            kind = ListKind.BULLET if fmt_val == "bullet" else ListKind.NUMBERED
            schemes.kind_by_abstract_and_level[(abstract_id, int(ilvl))] = kind

    return schemes


def _resolve_paragraph_style(style_id: str | None, names: dict[str, str]) -> ParagraphStyle:
    if style_id is None:
        return ParagraphStyle.NORMAL
    label = names.get(style_id) or _normalize_style_label(style_id)
    return _STYLE_LABEL_TO_PARAGRAPH_STYLE.get(label, ParagraphStyle.NORMAL)


def _is_on(el: ET.Element | None) -> bool:
    """``w:b`` / ``w:i`` / ``w:strike`` on/off rule: present and not explicitly off."""
    if el is None:
        return False
    val = el.get(_w("val"))
    if val is None:
        return True
    return val.strip().lower() not in _OFF_VALUES


def _run_styles(rpr: ET.Element | None) -> frozenset[TextStyle]:
    """Styles carried by a run's own *direct* ``w:rPr`` -- never inherited."""
    if rpr is None:
        return frozenset()
    styles: set[TextStyle] = set()
    if _is_on(rpr.find(_w("b"))):
        styles.add(TextStyle.BOLD)
    if _is_on(rpr.find(_w("i"))):
        styles.add(TextStyle.ITALIC)
    underline = rpr.find(_w("u"))
    if underline is not None and underline.get(_w("val"), "").strip().lower() != "none":
        styles.add(TextStyle.UNDERLINE)
    if _is_on(rpr.find(_w("strike"))):
        styles.add(TextStyle.STRIKETHROUGH)
    return frozenset(styles)


@dataclass
class _ParseState:
    """Everything the walk accumulates besides the paragraphs themselves.

    ``warnings`` counts the losses a number is enough for, each under a
    :class:`~auto_essay_typer.importers.notes.NoteKind`. The other two are
    the losses a number is not enough for: whether the document carried
    tracked changes at all (the user needs to know a choice was made for
    them), and enough of each text box's words that they can tell which part
    of their document will be missing.
    """

    warnings: Counter[NoteKind] = field(default_factory=Counter)
    tracked_changes: bool = False
    text_box_excerpts: list[str] = field(default_factory=list)


#: Wherever a run breaks the line, its text comes back carrying this instead,
#: for the paragraph builder to split on.
_BREAK = "\x00BR\x00"

#: Elements that wrap runs without contributing text of their own: their runs
#: belong in the paragraph exactly as if the wrapper were not there. A smart
#: tag is Word's automatic tagging of a name, date or place; ``bdo``/``dir``
#: are right-to-left overrides around otherwise ordinary runs; ``fldSimple``
#: is one of them because a field's child runs hold the result Word displays
#: (the page number, a cross-reference's text), while the instruction that
#: produced it lives in an attribute this parser never reads.
_TRANSPARENT_RUN_WRAPPERS = frozenset(
    {_w("smartTag"), _w("fldSimple"), _w("bdo"), _w("dir"), _w("customXml")}
)

#: Tracked-change wrappers whose text Word *does* show in its final view -- an
#: insertion nobody has accepted yet, and the place a moved passage landed.
_ACCEPTED_REVISIONS = frozenset({_w("ins"), _w("moveTo")})

#: And the ones whose text it does not: a deletion, and the place a moved
#: passage came from.
_REJECTED_REVISIONS = frozenset({_w("del"), _w("moveFrom")})

#: Revision marks Word records on the paragraph mark itself rather than on any
#: run, which is how it remembers a paragraph someone split or merged.
_PARAGRAPH_MARK_REVISIONS = ("ins", "del", "moveTo", "moveFrom")

#: Pictures and shapes. Each is reported rather than typed -- but a shape can
#: have text trapped inside it, which is a different loss from a photograph
#: (see :func:`_record_graphic`).
_GRAPHIC_TAGS = frozenset({_w("drawing"), _w("pict"), _w("object"), _mc("AlternateContent")})

#: The markers Word leaves where a footnote, endnote or comment is anchored.
#: Each is a number the reader sees, with nothing in the body text to type in
#: its place.
_NOTE_REFERENCE_TAGS = frozenset(
    {_w("footnoteReference"), _w("endnoteReference"), _w("commentReference")}
)

#: How much of a text box's wording goes in the warning about it.
_TEXT_BOX_EXCERPT_CHARS = 60

#: Where Word keeps a header or a footer: a part of its own, apart from the body, named for
#: what it is and numbered (``word/header1.xml``, ``word/footer2.xml``). The header for a
#: document's first page, or its even pages, is a part of its own too. Nothing else matches:
#: ``word/footnotes.xml`` starts the way ``word/footer1.xml`` does and is not one.
_HEADER_FOOTER_PART_RE = re.compile(r"^word/(?:header|footer)\d*\.xml$")

#: Fields show something on the page without a ``w:t`` having to say what: Word works a
#: page number out when it lays the page out. It saves the number it got beside the field,
#: but not every program that writes a field does.
_FIELD_TAGS = frozenset({_w("fldSimple"), _w("instrText")})


@dataclass
class _OpenParagraph:
    """A ``Paragraph`` under construction: runs are still (text, styles) pairs."""

    style: ParagraphStyle
    alignment: Alignment
    list_kind: ListKind | None
    list_level: int
    pieces: list[tuple[str, frozenset[TextStyle]]] = field(default_factory=list)

    def append(self, text: str, styles: frozenset[TextStyle]) -> None:
        if not text:
            return
        if self.pieces and self.pieces[-1][1] == styles:
            prev_text, _ = self.pieces[-1]
            self.pieces[-1] = (prev_text + text, styles)
        else:
            self.pieces.append((text, styles))

    def clone_empty(self) -> "_OpenParagraph":
        """A fresh paragraph continuing this one's attributes (for a line break)."""
        return _OpenParagraph(
            style=self.style,
            alignment=self.alignment,
            list_kind=self.list_kind,
            list_level=self.list_level,
        )

    def to_paragraph(self) -> Paragraph:
        runs = tuple(Run(text, styles) for text, styles in self.pieces if text)
        return Paragraph(
            runs=runs,
            style=self.style,
            alignment=self.alignment,
            list_kind=self.list_kind,
            list_level=self.list_level,
        )


def _element_text(el: ET.Element) -> str:
    """Every ``w:t`` under ``el``, joined by single spaces.

    Used only to quote something in a warning, so paragraph and run
    boundaries inside it are deliberately flattened to spaces rather than
    preserved.
    """
    parts = (t.text or "" for t in el.iter(_w("t")))
    return " ".join(part for part in (raw.strip() for raw in parts) if part)


def _excerpt(text: str) -> str:
    """``text`` cut down to something short enough to sit in a warning line."""
    if len(text) <= _TEXT_BOX_EXCERPT_CHARS:
        return text
    return text[:_TEXT_BOX_EXCERPT_CHARS].rstrip() + "…"


def _record_graphic(el: ET.Element, state: _ParseState) -> None:
    """Note one picture or shape as a loss, naming any text trapped inside it.

    Text in a shape sits beside the body rather than in it, and typing cannot
    put it back there, so it is reported with enough of its wording to
    recognize instead of being typed into the middle of the essay. A picture
    with no text in it is just an image.

    ``mc:AlternateContent`` holds the same shape twice -- a modern description
    and a fallback for older versions of Word -- so only its first non-empty
    branch is read, or one text box would be reported as two.
    """
    if el.tag == _mc("AlternateContent"):
        for branch in el:
            if len(branch):
                _record_graphic(branch, state)
                return
        state.warnings[NoteKind.IMAGE] += 1
        return

    text_boxes = el.findall(f".//{_w('txbxContent')}")
    if not text_boxes:
        state.warnings[NoteKind.IMAGE] += 1
        return
    for text_box in text_boxes:
        state.text_box_excerpts.append(_excerpt(_element_text(text_box)))


def _run_text(run_el: ET.Element, state: _ParseState) -> str:
    """The text one ``w:r`` contributes, with ``_BREAK`` where it breaks a line.

    A single run can hold several meaningful children (``w:t``, ``w:tab``,
    ``w:br``) in document order, so line breaks come back as a sentinel for
    the caller to split on -- keeping this function ignorant of
    paragraph-level state. Everything it cannot turn into characters is
    counted, so no run child is skipped without the user hearing about it.
    """
    parts: list[str] = []
    for child in run_el:
        tag = child.tag
        if tag == _w("t"):
            parts.append(child.text or "")
        elif tag == _w("delText"):
            # Deleted text, with or without a w:del around the run: Word's
            # final view does not show it.
            state.tracked_changes = True
        elif tag in (_w("tab"), _w("ptab")):
            parts.append("\t")
        elif tag in (_w("br"), _w("cr")):
            parts.append(_BREAK)
        elif tag == _w("noBreakHyphen"):
            parts.append("\u2011")  # non-breaking hyphen: a hyphen to read
        elif tag == _w("softHyphen"):
            # Optional hyphen. Invisible unless the word happens to land on a
            # line end, but still a character of the source, so it is typed
            # rather than quietly thrown away.
            parts.append("\u00ad")
        elif tag in _GRAPHIC_TAGS:
            _record_graphic(child, state)
        elif tag == _w("sym"):
            # A character from a specific symbol font, addressed by a code
            # point that means nothing outside it -- there is no keystroke
            # that reproduces it.
            state.warnings[NoteKind.SYMBOL] += 1
        elif tag in _NOTE_REFERENCE_TAGS:
            state.warnings[NoteKind.FOOTNOTE] += 1
    return "".join(parts)


def _iter_runs(container: ET.Element, state: _ParseState):
    """Yield every ``w:r`` whose text belongs in this paragraph, in order.

    Descends through the elements that wrap runs without text of their own --
    hyperlinks, content controls, smart tags, fields -- so their text is
    typed rather than dropped, and resolves tracked changes to the final view
    Word shows on screen: an insertion's runs are yielded, a deletion's are
    not.
    """
    for child in container:
        tag = child.tag
        if tag == _w("r"):
            yield child
        elif tag == _w("hyperlink"):
            state.warnings[NoteKind.LINK] += 1
            yield from _iter_runs(child, state)
        elif tag in _ACCEPTED_REVISIONS:
            state.tracked_changes = True
            yield from _iter_runs(child, state)
        elif tag in _REJECTED_REVISIONS:
            state.tracked_changes = True
        elif tag == _w("sdt"):
            content = child.find(_w("sdtContent"))
            if content is not None:
                yield from _iter_runs(content, state)
        elif tag in _TRANSPARENT_RUN_WRAPPERS:
            yield from _iter_runs(child, state)


def _paragraph_mark_revised(ppr: ET.Element | None) -> bool:
    """Whether the paragraph *mark* itself was inserted or deleted by an editor.

    Word records a split or merged paragraph here rather than on any run, so a
    document whose only tracked change is one of those would otherwise look
    as though it had never been edited.
    """
    if ppr is None:
        return False
    rpr = ppr.find(_w("rPr"))
    if rpr is None:
        return False
    return any(rpr.find(_w(tag)) is not None for tag in _PARAGRAPH_MARK_REVISIONS)


def _parse_paragraph(p_el: ET.Element, names: dict[str, str], numbering: _NumberingSchemes,
                      state: _ParseState) -> list[Paragraph]:
    """One ``w:p`` -> one or more ``Paragraph`` (more than one iff it breaks a line)."""
    ppr = p_el.find(_w("pPr"))
    style_id = None
    alignment = Alignment.LEFT
    list_kind: ListKind | None = None
    list_level = 0

    if ppr is not None:
        pstyle_el = ppr.find(_w("pStyle"))
        if pstyle_el is not None:
            style_id = pstyle_el.get(_w("val"))

        jc_el = ppr.find(_w("jc"))
        if jc_el is not None:
            alignment = _ALIGNMENT_BY_JC.get(jc_el.get(_w("val"), ""), Alignment.LEFT)

        num_pr = ppr.find(_w("numPr"))
        if num_pr is not None:
            ilvl_el = num_pr.find(_w("ilvl"))
            num_id_el = num_pr.find(_w("numId"))
            if num_id_el is not None:
                num_id = num_id_el.get(_w("val"), "")
                level = int(ilvl_el.get(_w("val"), "0")) if ilvl_el is not None else 0
                level = max(0, min(level, 8))
                list_kind = numbering.list_kind_for(num_id, level)
                list_level = level

    if _paragraph_mark_revised(ppr):
        state.tracked_changes = True

    style = _resolve_paragraph_style(style_id, names)
    current = _OpenParagraph(style=style, alignment=alignment, list_kind=list_kind,
                              list_level=list_level)
    finished: list[Paragraph] = []

    for run_el in _iter_runs(p_el, state):
        styles = _run_styles(run_el.find(_w("rPr")))
        # One segment per piece of the run between its line breaks: every
        # boundary between two segments is a break, whether it fell inside
        # this run or between this run and the one before it.
        for index, segment in enumerate(_run_text(run_el, state).split(_BREAK)):
            if index:
                state.warnings[NoteKind.BREAK] += 1
                finished.append(current.to_paragraph())
                current = current.clone_empty()
            current.append(segment, styles)

    finished.append(current.to_paragraph())
    return finished


def _iter_cells(container: ET.Element):
    """Yield every ``w:tc`` of one table in reading order.

    Descends through rows, and through any content control wrapped around a
    run of them, so no cell is missed because an editable region was put
    around its row.
    """
    for child in container:
        tag = child.tag
        if tag == _w("tc"):
            yield child
        elif tag == _w("tr"):
            yield from _iter_cells(child)
        elif tag == _w("sdt"):
            content = child.find(_w("sdtContent"))
            if content is not None:
                yield from _iter_cells(content)
        elif tag == _w("customXml"):
            yield from _iter_cells(child)


def _flatten_table(tbl_el: ET.Element, names: dict[str, str], numbering: _NumberingSchemes,
                    state: _ParseState) -> list[Paragraph]:
    """Every cell's paragraphs in reading order -- the grid itself is dropped.

    A cell's own paragraphs stay separate paragraphs. Running them together
    would fuse the last word of one onto the first word of the next and
    invent a word that is in no document, which is the one thing a flattened
    table must not do.
    """
    state.warnings[NoteKind.TABLE] += 1
    paragraphs: list[Paragraph] = []
    for cell in _iter_cells(tbl_el):
        cell_paragraphs = _parse_block_content(cell, names, numbering, state)
        # Word always puts a paragraph in a cell, but an empty cell still
        # stands for a line the reader sees.
        paragraphs.extend(cell_paragraphs or [Paragraph()])
    return paragraphs


def _parse_block_content(container: ET.Element, names: dict[str, str],
                          numbering: _NumberingSchemes,
                          state: _ParseState) -> list[Paragraph]:
    """Every paragraph of one block container, in reading order.

    The container is the body, a table cell, or the contents of a content
    control. Recursive because a content control can hold whole paragraphs
    and tables, and a cell can hold another table: without descending into
    them their text would be absent from the Document with nothing said
    about it.
    """
    paragraphs: list[Paragraph] = []
    for child in container:
        tag = child.tag
        if tag == _w("p"):
            paragraphs.extend(_parse_paragraph(child, names, numbering, state))
        elif tag == _w("tbl"):
            paragraphs.extend(_flatten_table(child, names, numbering, state))
        elif tag == _w("sdt"):
            content = child.find(_w("sdtContent"))
            if content is not None:
                paragraphs.extend(_parse_block_content(content, names, numbering, state))
        elif tag == _w("customXml"):
            paragraphs.extend(_parse_block_content(child, names, numbering, state))
    return paragraphs


def _find(root: ET.Element, path: str) -> ET.Element:
    found = root if root.tag == _w(path) else root.find(f".//{_w(path)}")
    if found is None:
        raise _DocxError(f"not a valid Word document: missing <w:{path}>")
    return found


def _safe_fromstring(data: bytes) -> ET.Element:
    """Parse one XML part, refusing a DOCTYPE or ENTITY declaration outright.

    A legitimate Word XML part never has either. Rejecting them up front
    closes off billion-laughs / entity-expansion attacks from a downloaded
    or user-supplied file, without pulling in a third-party XML library --
    this project's parsing code is standard-library only by design (see
    ``xml.sax.saxutils`` docs on "XML vulnerabilities" for why the stdlib
    parser alone is not a safe default for untrusted input).
    """
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise _DocxError(
            "not a valid Word document: XML part contains a DOCTYPE/ENTITY declaration"
        )
    return ET.fromstring(data)


def _read_part(zf: zipfile.ZipFile, name: str) -> ET.Element | None:
    try:
        data = zf.read(name)
    except KeyError:
        return None
    return _safe_fromstring(data)


def _has_something_to_show(part: ET.Element) -> bool:
    """Whether a header or footer part has anything in it that Word would put on the page.

    Words, or a field such as the page number (see ``_FIELD_TAGS``). A part with neither is
    an empty paragraph, which Word leaves behind whenever someone clicks into a header and out
    again, and nothing is lost with it. Deleted text is not ``w:t``, so it does not count: it
    is not on the page in the final version this reader types.
    """
    for element in part.iter():
        if element.tag in _FIELD_TAGS:
            return True
        if element.tag == _w("t") and (element.text or "").strip():
            return True
    return False


def _record_headers_and_footers(zf: zipfile.ZipFile, state: _ParseState) -> None:
    """Count each header and footer part that has something in it as a loss.

    Word keeps them apart from the body, so none of what is in them reaches the typed
    text, and the writer's own words there (a name and a page number at the top of every
    page, say) would go missing without a word about it. A part with nothing in it is not
    a loss and is not counted.

    The parts are found by their names, as this module finds all of its parts, and not by
    following the relationships that point to them: a part nothing points to is counted too,
    which is the safe way to be wrong.
    """
    for name in zf.namelist():
        if not _HEADER_FOOTER_PART_RE.match(name):
            continue
        part = _read_part(zf, name)
        if part is not None and _has_something_to_show(part):
            state.warnings[NoteKind.HEADER_FOOTER] += 1


def parse_docx(data: bytes) -> tuple[Document, tuple[str, ...]]:
    """Parse raw ``.docx`` bytes into a ``Document`` plus aggregated warnings.

    Raises ``ValueError`` (specifically ``_DocxError``, a private subclass --
    callers only need to know it is a ``ValueError``) if ``data`` is not a
    zip or is missing ``word/document.xml``, so a corrupt or non-Word file
    fails with a message instead of a cryptic zipfile/XML traceback.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise _DocxError("not a valid Word document: not a zip archive") from exc

    state = _ParseState()
    with zf:
        try:
            document_xml = zf.read(_DOCUMENT_XML)
        except KeyError as exc:
            raise _DocxError(
                f"not a valid Word document: missing {_DOCUMENT_XML}"
            ) from exc
        document_root = _safe_fromstring(document_xml)
        styles_root = _read_part(zf, _STYLES_XML)
        numbering_root = _read_part(zf, _NUMBERING_XML)
        _record_headers_and_footers(zf, state)

    names = _parse_styles(styles_root)
    numbering = _parse_numbering(numbering_root)

    body = _find(document_root, "body")
    paragraphs = _parse_block_content(body, names, numbering, state)

    return Document(tuple(paragraphs)), _render_warnings(state)


def _render_warnings(state: _ParseState) -> tuple[str, ...]:
    """Everything the walk could not turn into keystrokes, as the messages the report carries.

    The wording is in :mod:`~auto_essay_typer.importers.notes`, shared with the Google
    Docs reader. A paragraph inside a table cell is read by the same code as any other,
    so it keeps its own style, alignment and list (``keeps_paragraph_formatting``).
    """
    return render(
        state.warnings,
        keeps_paragraph_formatting=True,
        text_box_excerpts=state.text_box_excerpts,
        tracked_changes=state.tracked_changes,
    )
