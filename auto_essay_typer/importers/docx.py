"""Parse a Microsoft Word ``.docx`` file into this project's ``Document`` model.

A ``.docx`` is a zip of XML parts. Only ``word/document.xml`` (the body),
``word/styles.xml`` (paragraph style id -> display name, e.g. ``"Heading1"``
-> ``"heading 1"``) and ``word/numbering.xml`` (list id -> bullet/numbered,
per level) are read; everything else in the package (theme, settings,
embedded media, ...) is ignored. Uses only ``zipfile`` and
``xml.etree.ElementTree`` from the standard library, per this project's
no-new-dependencies rule.

Deliberately narrow in what it reads from a run's formatting: only *direct*
``w:rPr`` children of the ``w:r`` are consulted (never style-inherited or
paragraph-default formatting), because that is exactly the set of styles a
person could reproduce by pressing Google Docs shortcuts while typing --
there is no "current paragraph's default run style" concept to press a
shortcut for.
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

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _w(tag: str) -> str:
    """Qualify a bare tag name (``"p"``) with the WordprocessingML namespace."""
    return f"{{{_W_NS}}}{tag}"


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
        """A fresh paragraph continuing this one's attributes (for a ``w:br`` split)."""
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


def _run_text_pieces(run_el: ET.Element, warnings: Counter[str]) -> tuple[str, list[str]]:
    """Text contributed by one ``w:r``, plus any ``"br"`` split markers it contains.

    A single run can hold more than one meaningful child (``w:t``, ``w:tab``,
    ``w:br``) in document order, so this returns the run's text with a
    sentinel ``"\\x00BR\\x00"`` wherever a ``w:br`` occurred, for the caller to
    split on -- keeping this function ignorant of paragraph-level state.
    """
    parts: list[str] = []
    for child in run_el:
        tag = child.tag
        if tag == _w("t"):
            parts.append(child.text or "")
        elif tag == _w("tab"):
            parts.append("\t")
        elif tag == _w("br"):
            parts.append("\x00BR\x00")
        elif tag in (_w("drawing"), _w("pict")):
            warnings["image"] += 1
    return "".join(parts), []


def _iter_runs(container: ET.Element, warnings: Counter[str]):
    """Yield ``(run_element, inside_hyperlink)`` for every ``w:r`` in a paragraph,
    unwrapping exactly one level of ``w:hyperlink`` and counting each such link."""
    for child in container:
        if child.tag == _w("r"):
            yield child, False
        elif child.tag == _w("hyperlink"):
            warnings["link"] += 1
            for grandchild in child:
                if grandchild.tag == _w("r"):
                    yield grandchild, True


def _parse_paragraph(p_el: ET.Element, names: dict[str, str], numbering: _NumberingSchemes,
                      warnings: Counter[str]) -> list[Paragraph]:
    """One ``w:p`` -> one or more ``Paragraph`` (more than one iff it holds a ``w:br``)."""
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

    style = _resolve_paragraph_style(style_id, names)
    current = _OpenParagraph(style=style, alignment=alignment, list_kind=list_kind,
                              list_level=list_level)
    finished: list[Paragraph] = []
    had_break = False

    for run_el, _in_link in _iter_runs(p_el, warnings):
        rpr = run_el.find(_w("rPr"))
        styles = _run_styles(rpr)
        text, _unused = _run_text_pieces(run_el, warnings)
        for segment in text.split("\x00BR\x00"):
            if had_break:
                finished.append(current.to_paragraph())
                current = current.clone_empty()
            current.append(segment, styles)
            had_break = False
        # A run's split markers are consumed one at a time above; count the
        # actual number of breaks that occurred so the *next* segment (if
        # any, even in a later run) starts a fresh paragraph.
        break_count = text.count("\x00BR\x00")
        if break_count:
            warnings["break"] += break_count
            had_break = True

    finished.append(current.to_paragraph())
    return finished


def _flatten_table(tbl_el: ET.Element, names: dict[str, str], numbering: _NumberingSchemes,
                    warnings: Counter[str]) -> list[Paragraph]:
    """One paragraph per cell, reading order -- table structure itself is dropped."""
    warnings["table"] += 1
    cell_paragraphs: list[Paragraph] = []
    for row in tbl_el.findall(_w("tr")):
        for cell in row.findall(_w("tc")):
            pieces: list[tuple[str, frozenset[TextStyle]]] = []
            for p_el in cell.findall(_w("p")):
                for paragraph in _parse_paragraph(p_el, names, numbering, warnings):
                    for run in paragraph.runs:
                        pieces.append((run.text, run.styles))
            merged: list[Run] = []
            for text, styles in pieces:
                if merged and merged[-1].styles == styles:
                    merged[-1] = Run(merged[-1].text + text, styles)
                elif text:
                    merged.append(Run(text, styles))
            cell_paragraphs.append(Paragraph(runs=tuple(merged)))
    return cell_paragraphs


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

    names = _parse_styles(styles_root)
    numbering = _parse_numbering(numbering_root)
    warnings: Counter[str] = Counter()

    body = _find(document_root, "body")
    paragraphs: list[Paragraph] = []
    for child in body:
        if child.tag == _w("p"):
            paragraphs.extend(_parse_paragraph(child, names, numbering, warnings))
        elif child.tag == _w("tbl"):
            paragraphs.extend(_flatten_table(child, names, numbering, warnings))

    return Document(tuple(paragraphs)), _render_warnings(warnings)


def _render_warnings(counts: Counter[str]) -> tuple[str, ...]:
    messages: list[str] = []
    if counts["link"]:
        n = counts["link"]
        messages.append(f"{n} link{'s' if n != 1 else ''} typed as plain text")
    if counts["image"]:
        n = counts["image"]
        messages.append(f"{n} image{'s' if n != 1 else ''} not reproduced (dropped)")
    if counts["table"]:
        n = counts["table"]
        messages.append(
            f"{n} table{'s' if n != 1 else ''} flattened to one paragraph per cell"
        )
    if counts["break"]:
        n = counts["break"]
        messages.append(
            f"{n} soft line break{'s' if n != 1 else ''} typed as paragraph break{'s' if n != 1 else ''}"
        )
    return tuple(messages)
