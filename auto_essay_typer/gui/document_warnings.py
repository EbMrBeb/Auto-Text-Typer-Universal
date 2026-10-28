"""Plain-language wording for the "What will not be reproduced" panel.

The libraries report what a document loses in two places, both worded for a
developer:

* an importer says what it could not read out of the source file
  (``ImportReport.warnings``: "1 image not reproduced (dropped)"), and
* ``keymaps.adapt_document`` says what it had to rewrite because the editor
  being typed into has no keyboard shortcut for something the document uses
  ("2 bullet-list paragraphs typed as plain text with a "•" marker (no
  bullet-list shortcut in this keymap)").

Neither is fit to show someone who has never heard of a keymap. This module
rewrites each one as a sentence about what *will happen to their document*,
naming the editor they chose. It is a pure mapping with no Qt import, so the
wording can be tested without constructing a widget.

The library messages are matched with one anchored pattern per template. A
message that matches none of them (a template added later) is not lost: it is
shown with the jargon removed and the editor named, so the panel always lists
everything the libraries reported.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from auto_essay_typer.targets import TargetApp

_PLAIN_TEXT_TARGET_ID = "plain-text"


def editor_phrase(target: TargetApp) -> str:
    """How to refer to ``target`` in the middle of a sentence.

    Most editors are simply called by name. The plain-text fallback's display
    name ("Any app (plain text)") does not read as a subject, so it becomes
    "a plain-text field".
    """
    return "a plain-text field" if target.id == _PLAIN_TEXT_TARGET_ID else target.name


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return singular if count == 1 else (plural if plural is not None else singular + "s")


def _sentence(text: str) -> str:
    """``text`` with a capital first letter and a final full stop."""
    text = text.strip()
    if not text:
        return text
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."


# --- what the importers report ------------------------------------------------------------------

#: (pattern, builder) pairs for the importers' messages, checked in order. Each
#: pattern is anchored and captures the leading count where there is one.
_IMPORT_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(\d+) links? typed as plain text$"), "link"),
    (re.compile(r"^(\d+) images? not reproduced \(dropped\)$"), "image"),
    (re.compile(r"^(\d+) tables? flattened to one paragraph per cell$"), "table"),
    (re.compile(r"^(\d+) horizontal rules? not reproduced \(dropped\)$"), "rule"),
    (re.compile(r"^(\d+) soft line breaks? typed as paragraph breaks?$"), "break"),
    (re.compile(r"^(\d+) footnote/comment references? not reproduced \(dropped\)$"), "footnote"),
    (
        re.compile(r"^this document uses custom fonts, sizes, or colours, which are not reproduced$"),
        "style",
    ),
)


def _import_sentence(kind: str, count: int) -> str:
    if kind == "link":
        return (
            f"{count} {_plural(count, 'link')} will be typed as plain text, "
            f"not as {_plural(count, 'a clickable link', 'clickable links')}"
        )
    if kind == "image":
        return f"{count} {_plural(count, 'image')} will be left out, because images can't be typed"
    if kind == "table":
        return f"{count} {_plural(count, 'table')} will be typed as plain paragraphs, one per cell"
    if kind == "rule":
        return f"{count} horizontal {_plural(count, 'line')} will be left out"
    if kind == "break":
        return (
            f"{count} line {_plural(count, 'break')} inside a paragraph will be typed as "
            f"{_plural(count, 'a new paragraph', 'new paragraphs')}"
        )
    if kind == "footnote":
        return f"{count} footnote or comment {_plural(count, 'marker')} will be left out"
    return "Custom fonts, text sizes and colours will not be reproduced"


def explain_import_warning(message: str) -> str:
    """One importer warning as a plain sentence about the original document."""
    text = message.strip()
    for pattern, kind in _IMPORT_RULES:
        match = pattern.match(text)
        if match:
            count = int(match.group(1)) if match.groups() else 0
            return _sentence(_import_sentence(kind, count))
    return _sentence(text)


# --- what adapt_document reports -------------------------------------------------------------------

_TITLE_SUBSTITUTION = re.compile(
    r"^(\d+) (Title|Subtitle) paragraphs? typed as (Heading \d) "
    r"\(no \2 shortcut in this keymap\)$"
)
_HEADING_FALLBACK = re.compile(
    r"^(\d+) (Title|Subtitle|Heading \d) paragraphs? typed as Normal(, bold)? "
    r"\(no \2 shortcut in this keymap\)$"
)
_ALIGNMENT_FALLBACK = re.compile(
    r"^(\d+) paragraphs? typed as left-aligned \(no (Left|Center|Right|Justified) "
    r"shortcut in this keymap\)$"
)
_STYLE_LOST = re.compile(
    r"^(\d+) runs? lost (bold|italic|underline|strikethrough) formatting "
    r"\(no \2 shortcut in this keymap\)$"
)
_BULLET_FALLBACK = re.compile(
    r'^(\d+) bullet-list paragraphs? typed as plain text with a "•" marker '
    r"\(no bullet-list shortcut in this keymap\)$"
)
_NUMBERED_FALLBACK = re.compile(
    r"^(\d+) numbered-list paragraphs? typed as plain text with typed numbers "
    r"\(no numbered-list shortcut in this keymap\)$"
)

#: How an alignment is spoken about: as an adjective ("2 centered paragraphs")
#: and as a kind of text ("a shortcut for centered text").
_ALIGNMENT_ADJECTIVE = {
    "Left": "left-aligned",
    "Center": "centered",
    "Right": "right-aligned",
    "Justified": "justified",
}


def explain_adaptation_warning(message: str, editor: str) -> str:
    """One ``adapt_document`` warning as a plain sentence about ``editor``.

    ``editor`` is already phrased for mid-sentence use (see :func:`editor_phrase`).
    """
    text = message.strip()

    match = _TITLE_SUBSTITUTION.match(text)
    if match:
        count, style, replacement = int(match.group(1)), match.group(2), match.group(3)
        noun = style.lower()
        return _sentence(
            f"{count} {_plural(count, noun)} will be typed as {replacement}, because {editor} "
            f"has no {noun} shortcut"
        )

    match = _HEADING_FALLBACK.match(text)
    if match:
        count, style, bold = int(match.group(1)), match.group(2), bool(match.group(3))
        subject = style if style.startswith("Heading") else style.lower()
        body = "bold text" if bold else "ordinary text"
        return _sentence(
            f"{count} {subject} {_plural(count, 'paragraph')} will be typed as {body}, because "
            f"{editor} has no {subject} shortcut"
        )

    match = _ALIGNMENT_FALLBACK.match(text)
    if match:
        count, alignment = int(match.group(1)), match.group(2)
        adjective = _ALIGNMENT_ADJECTIVE[alignment]
        return _sentence(
            f"{count} {adjective} {_plural(count, 'paragraph')} will be typed left-aligned, "
            f"because {editor} has no shortcut for {adjective} text"
        )

    match = _STYLE_LOST.match(text)
    if match:
        count, style = int(match.group(1)), match.group(2)
        return _sentence(
            f"{count} {_plural(count, 'piece')} of text will lose {style} formatting, because "
            f"{editor} has no {style} shortcut"
        )

    match = _BULLET_FALLBACK.match(text)
    if match:
        count = int(match.group(1))
        return _sentence(
            f"{count} bulleted list {_plural(count, 'item')} will be typed as plain text "
            f"starting with “•”, because {editor} has no shortcut for bulleted lists"
        )

    match = _NUMBERED_FALLBACK.match(text)
    if match:
        count = int(match.group(1))
        return _sentence(
            f"{count} numbered list {_plural(count, 'item')} will be typed as plain text with "
            f"typed numbers (1., 2., …), because {editor} has no shortcut for numbered lists"
        )

    # A template this module does not know: keep the facts, drop the jargon.
    return _sentence(text.replace("this keymap", editor).replace("keymap", "shortcut list"))


def explain_warnings(
    import_warnings: Iterable[str],
    adaptation_warnings: Iterable[str],
    editor: str,
) -> tuple[str, ...]:
    """Everything the panel lists, in plain language: the importer's, then the editor's.

    Each library warning becomes exactly one line, so nothing the libraries
    reported is merged away or lost. Identical lines are listed once.
    """
    lines: list[str] = []
    for message in import_warnings:
        lines.append(explain_import_warning(message))
    for message in adaptation_warnings:
        lines.append(explain_adaptation_warning(message, editor))
    return tuple(dict.fromkeys(line for line in lines if line))
