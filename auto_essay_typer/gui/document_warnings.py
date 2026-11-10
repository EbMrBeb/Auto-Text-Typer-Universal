"""Plain-language wording for the "What will not be reproduced" panel.

The libraries report what a document loses in two places, both worded for a
developer:

* an importer says what it could not read out of the source file
  (``ImportReport.warnings``: "1 image not reproduced (dropped)"), and
* ``keymaps.adapt_document`` says what it had to rewrite because there is no
  shortcut set up, for the editor being typed into, for something the document uses
  ("2 bullet-list paragraphs typed as plain text with a "•" marker (no
  bullet-list shortcut in this keymap)").

Neither is fit to show someone who has never heard of a keymap. This module
rewrites each one as a sentence about what *will happen to their document*,
naming the editor they chose. It is a pure mapping with no Qt import, so the
wording can be tested without constructing a widget.

Wording rule: an adaptation is never blamed on the editor itself. The keymaps leave
out shortcuts for several reasons (an editor genuinely has none; the shortcut would
open the browser's developer tools; the app's model of the editor does not cover
it), and an editor like Word does have list shortcuts. What is always true, and what
the person can act on from the Advanced page, is that *no shortcut is set up* for
the editor, so that is what the sentences say.

The importers' messages are matched with one rule per kind of loss. The kinds are
``importers.notes.NoteKind``, the importers' own list, and a rule is an anchored
pattern for the message they render for it plus the sentence it becomes; there must be
one for every kind, and ``tests/gui/test_import_note_rules.py`` fails, saying what to
add, when there is not. ``adapt_document``'s messages are matched with one anchored
pattern per template.

A message that matches none of the patterns is not lost: an importer's is shown as it
was worded, and an adaptation's with the jargon removed and the editor named, so the
panel always lists everything the libraries reported. :func:`is_known_import_warning`
and :func:`is_known_adaptation_warning` say whether a message matched, so tests can tell
a translation from that fallback.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from auto_essay_typer.importers.notes import NoteKind
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


@dataclass(frozen=True)
class _ImportRule:
    """How one kind of importer message is recognised, and what it is said to mean.

    ``pattern`` is anchored to the exact message ``importers/notes.py`` renders for the
    kind, and captures the leading count as group 1 where there is one. ``say`` turns a
    match into the sentence the person reads, without the capital letter and full stop
    that :func:`_sentence` adds.
    """

    pattern: re.Pattern[str]
    say: Callable[[re.Match[str]], str]


def _count(match: re.Match[str]) -> int:
    return int(match.group(1))


def _say_link(match: re.Match[str]) -> str:
    count = _count(match)
    return (
        f"{count} {_plural(count, 'link')} will be typed as plain text, "
        f"not as {_plural(count, 'a clickable link', 'clickable links')}"
    )


def _say_image(match: re.Match[str]) -> str:
    count = _count(match)
    return f"{count} {_plural(count, 'image')} will be left out, because images can't be typed"


def _say_table(match: re.Match[str]) -> str:
    """A table is typed without its grid, cell by cell.

    The Word reader keeps a cell's paragraphs as they are, so there is nothing more to say.
    The Google Docs reader types them as ordinary text, which its importer message says with
    an extra clause (captured as ``lost``), and that is a loss the person can act on.
    """
    count = _count(match)
    sentence = (
        f"{count} {_plural(count, 'table')} will be typed without {_plural(count, 'its', 'their')} "
        "rows and columns: each cell becomes one or more paragraphs, row by row"
    )
    if match.group("lost"):
        sentence += ". Headings, lists and alignment inside a cell are not kept"
    return sentence


def _say_rule(match: re.Match[str]) -> str:
    count = _count(match)
    return f"{count} horizontal {_plural(count, 'line')} will be left out"


def _say_break(match: re.Match[str]) -> str:
    count = _count(match)
    return (
        f"{count} line {_plural(count, 'break')} inside a paragraph will be typed as "
        f"{_plural(count, 'a new paragraph', 'new paragraphs')}"
    )


def _say_footnote(match: re.Match[str]) -> str:
    """A note is left out whole: the marker in the text *and* what the note says.

    Only the marker is easy to picture, so saying just that sends nobody to look for the
    citation or the comment that went with it.
    """
    count = _count(match)
    return (
        f"{count} {_plural(count, 'footnote, endnote or comment', 'footnotes, endnotes or comments')} "
        f"will be left out, both the {_plural(count, 'marker', 'markers')} and "
        f"{_plural(count, 'the text of the note', 'the text of each note')}. "
        f"If you need {_plural(count, 'it', 'them')}, add {_plural(count, 'it', 'them')} back by hand"
    )


def _say_symbol(match: re.Match[str]) -> str:
    """Word's Insert > Symbol, when it picks from a symbol font such as Wingdings or Symbol.

    A character from an ordinary font is plain text and is typed; one from a symbol font is a
    code only that font understands, which no keystroke reproduces.
    """
    count = _count(match)
    return (
        f"{count} {_plural(count, 'symbol from a symbol font', 'symbols from symbol fonts')}, "
        f"such as Wingdings, will be left out, because {_plural(count, 'it', 'they')} can't be typed"
    )


def _say_text_box(match: re.Match[str]) -> str:
    """A text box is left out with its words, which the importer quotes so they can be found."""
    count = _count(match)
    boxes = _plural(count, "text box", "text boxes")
    quoted = match.group("quoted")
    if not quoted:
        return f"{count} {boxes} will be left out"
    return (
        f"{count} {boxes} will be left out, with the text in {_plural(count, 'it', 'them')}: {quoted}. "
        "If you need that text, add it by hand"
    )


def _say_tracked_changes(match: re.Match[str]) -> str:
    """The app chose which version of a document with tracked changes to type; say so."""
    return (
        "This document has tracked changes. It will be typed as the final version: added text is "
        "kept and deleted text is left out. To type a different version, accept or reject the "
        "changes in Word first"
    )


def _say_style(match: re.Match[str]) -> str:
    return "Custom fonts, text sizes and colours will not be reproduced"


#: One rule for every :class:`~auto_essay_typer.importers.notes.NoteKind`, in that order.
#: A kind with no rule here is shown in the importer's own wording, so
#: ``tests/gui/test_import_note_rules.py`` fails until it has one.
_IMPORT_RULES: dict[NoteKind, _ImportRule] = {
    NoteKind.LINK: _ImportRule(re.compile(r"^(\d+) links? typed as plain text$"), _say_link),
    NoteKind.IMAGE: _ImportRule(re.compile(r"^(\d+) images? not reproduced \(dropped\)$"), _say_image),
    NoteKind.TABLE: _ImportRule(
        re.compile(
            r"^(\d+) tables? flattened: each cell typed as one or more paragraphs, row by row"
            r"(?P<lost>, losing any heading style, alignment or list inside a cell)?$"
        ),
        _say_table,
    ),
    NoteKind.RULE: _ImportRule(
        re.compile(r"^(\d+) horizontal rules? not reproduced \(dropped\)$"), _say_rule
    ),
    NoteKind.BREAK: _ImportRule(
        re.compile(r"^(\d+) soft line breaks? typed as paragraph breaks?$"), _say_break
    ),
    NoteKind.FOOTNOTE: _ImportRule(
        re.compile(
            r"^(\d+) footnote/endnote/comment references? dropped, along with the text of the note$"
        ),
        _say_footnote,
    ),
    NoteKind.SYMBOL: _ImportRule(re.compile(r"^(\d+) special symbols? will be left out$"), _say_symbol),
    NoteKind.TEXT_BOX: _ImportRule(
        # DOTALL: a quoted excerpt is a text box's own words and may hold a line break.
        re.compile(
            r"^(\d+) text (?:box's|boxes') content will not be reproduced(?:: (?P<quoted>.+))?$",
            re.DOTALL,
        ),
        _say_text_box,
    ),
    NoteKind.TRACKED_CHANGES: _ImportRule(
        re.compile(
            r"^this document had tracked changes; it is typed as the final version, "
            r"with the edits accepted and the deleted text left out$"
        ),
        _say_tracked_changes,
    ),
    NoteKind.STYLE: _ImportRule(
        re.compile(r"^this document uses custom fonts, sizes, or colours, which are not reproduced$"),
        _say_style,
    ),
}


def import_rule_kinds() -> frozenset[NoteKind]:
    """The kinds of importer message this module words itself: one rule each."""
    return frozenset(_IMPORT_RULES)


def _match_import(message: str) -> tuple[NoteKind, re.Match[str]] | None:
    text = message.strip()
    for kind, rule in _IMPORT_RULES.items():
        match = rule.pattern.match(text)
        if match:
            return kind, match
    return None


def import_warning_kind(message: str) -> NoteKind | None:
    """Which kind of loss ``message`` reports, or None if no rule recognises it."""
    matched = _match_import(message)
    return matched[0] if matched else None


def is_known_import_warning(message: str) -> bool:
    """Whether ``message`` is one of the importers' templates this module words itself."""
    return _match_import(message) is not None


def explain_import_warning(message: str) -> str:
    """One importer warning as a plain sentence about the original document."""
    matched = _match_import(message)
    if matched is None:
        return _sentence(message)
    kind, match = matched
    return _sentence(_IMPORT_RULES[kind].say(match))


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

#: How an alignment is spoken about, as an adjective ("2 centered paragraphs").
_ALIGNMENT_ADJECTIVE = {
    "Left": "left-aligned",
    "Center": "centered",
    "Right": "right-aligned",
    "Justified": "justified",
}


@dataclass(frozen=True)
class _Adaptation:
    """One ``adapt_document`` message, taken apart.

    ``kind`` is ``substitution`` (a title or subtitle typed as a heading), ``heading``
    (a heading-like paragraph typed as body text), ``alignment``, ``style``, ``bullets``
    or ``numbered``. ``label`` is the style, alignment or character style it names, and
    ``bold`` says a heading's fallback text was made bold to stay distinct.
    """

    kind: str
    count: int
    label: str = ""
    replacement: str = ""
    bold: bool = False


def _parse_adaptation(message: str) -> _Adaptation | None:
    text = message.strip()
    match = _TITLE_SUBSTITUTION.match(text)
    if match:
        return _Adaptation("substitution", int(match.group(1)), match.group(2), replacement=match.group(3))
    match = _HEADING_FALLBACK.match(text)
    if match:
        return _Adaptation("heading", int(match.group(1)), match.group(2), bold=bool(match.group(3)))
    match = _ALIGNMENT_FALLBACK.match(text)
    if match:
        return _Adaptation("alignment", int(match.group(1)), match.group(2))
    match = _STYLE_LOST.match(text)
    if match:
        return _Adaptation("style", int(match.group(1)), match.group(2))
    match = _BULLET_FALLBACK.match(text)
    if match:
        return _Adaptation("bullets", int(match.group(1)))
    match = _NUMBERED_FALLBACK.match(text)
    if match:
        return _Adaptation("numbered", int(match.group(1)))
    return None


def is_known_adaptation_warning(message: str) -> bool:
    """Whether ``message`` is one of ``adapt_document``'s templates this module words itself."""
    return _parse_adaptation(message) is not None


def _unknown_adaptation(message: str, editor: str) -> str:
    """A template this module does not know: keep the facts, drop the jargon."""
    text = message.strip().replace(" in this keymap", f" set up for {editor}")
    return _sentence(text.replace("keymap", "shortcut list"))


def _shortcut_noun(label: str) -> str:
    """What to call a paragraph style's shortcut: "Heading 3", "title", "subtitle"."""
    return label if label.startswith("Heading") else label.lower()


def _body_text(bold: bool) -> str:
    return "bold text" if bold else "ordinary text"


def _adaptation_sentence(item: _Adaptation, editor: str) -> str:
    count = item.count
    if item.kind == "substitution":
        noun = item.label.lower()
        return _sentence(
            f"{count} {_plural(count, noun)} will be typed as {item.replacement}, because no "
            f"{noun} shortcut is set up for {editor}"
        )
    if item.kind == "heading":
        noun = _shortcut_noun(item.label)
        return _sentence(
            f"{count} {noun} {_plural(count, 'paragraph')} will be typed as {_body_text(item.bold)}, "
            f"because no {noun} shortcut is set up for {editor}"
        )
    if item.kind == "alignment":
        adjective = _ALIGNMENT_ADJECTIVE[item.label]
        return _sentence(
            f"{count} {adjective} {_plural(count, 'paragraph')} will be typed left-aligned, "
            f"because no shortcut for {adjective} text is set up for {editor}"
        )
    if item.kind == "style":
        # The importers count runs, which mean nothing to a reader, so no number is shown.
        return _sentence(
            f"Some text will lose {item.label} formatting, because no {item.label} shortcut "
            f"is set up for {editor}"
        )
    if item.kind == "bullets":
        return _sentence(
            f"{count} bulleted list {_plural(count, 'item')} will be typed as plain text "
            f"starting with “•”, because no shortcut for bulleted lists is set up for {editor}"
        )
    return _sentence(
        f"{count} numbered list {_plural(count, 'item')} will be typed as plain text with "
        f"typed numbers (1., 2., …), because no shortcut for numbered lists is set up for {editor}"
    )


def explain_adaptation_warning(message: str, editor: str) -> str:
    """One ``adapt_document`` warning as a plain sentence about ``editor``.

    ``editor`` is already phrased for mid-sentence use (see :func:`editor_phrase`).
    A message is explained on its own here; :func:`explain_warnings` is what also
    joins the two messages ``adapt_document`` writes about one title.
    """
    item = _parse_adaptation(message)
    if item is None:
        return _unknown_adaptation(message, editor)
    return _adaptation_sentence(item, editor)


#: The order adaptation lines are listed in: the more visible losses first. Within the
#: paragraph-style group, titles come before subtitles before Heading 1 to 6.
_GROUP = {"substitution": 0, "heading": 0, "alignment": 1, "style": 2, "bullets": 3, "numbered": 4}
_STYLE_RANK = {"Title": 0, "Subtitle": 1, **{f"Heading {n}": 1 + n for n in range(1, 7)}}


def _sort_key(item: _Adaptation) -> tuple[int, int, str]:
    rank = _STYLE_RANK.get(item.label, 0) if item.kind in ("substitution", "heading") else 0
    return (_GROUP[item.kind], rank, item.label)


def _explain_adaptations(messages: Iterable[str], editor: str) -> list[str]:
    """The editor's losses as plain sentences, most visible first.

    ``adapt_document`` says two things about a title that has to become body text. With no
    Title shortcut a title is rewritten to Heading 1; and if the editor cannot set Heading 1
    (or leave it again) every Heading 1 paragraph -- the substituted titles included -- is
    typed as body text, in a second message whose count includes them. Read as they stand,
    the two say "1 title will be typed as Heading 1" and "2 Heading 1 paragraphs will be
    typed as ordinary text" when the truth is one title and one Heading 1, both typed as
    ordinary text. Subtitles and Heading 2 work the same way. Such a pair is worded
    together here: the titles are said to be typed as body text, and only the real
    Heading 1 paragraphs are left in the second line.
    """
    parsed: list[_Adaptation] = []
    unknown: list[str] = []
    for message in messages:
        item = _parse_adaptation(message)
        if item is None:
            unknown.append(_unknown_adaptation(message, editor))
        else:
            parsed.append(item)

    fallbacks = {item.label: item for item in parsed if item.kind == "heading"}
    #: Heading fallbacks that already include a title or subtitle, by the heading they name.
    absorbed = {
        item.replacement: item
        for item in parsed
        if item.kind == "substitution"
        and item.replacement in fallbacks
        and fallbacks[item.replacement].count >= item.count
    }

    lines: list[tuple[tuple[int, int, str], str]] = []
    for item in parsed:
        if item.kind == "substitution" and item.replacement in absorbed:
            heading = fallbacks[item.replacement]
            noun = item.label.lower()
            lines.append(
                (
                    _sort_key(item),
                    _sentence(
                        f"{item.count} {_plural(item.count, noun)} will be typed as "
                        f"{_body_text(heading.bold)}, because no {noun} or "
                        f"{_shortcut_noun(item.replacement)} shortcut is set up for {editor}"
                    ),
                )
            )
        elif item.kind == "heading" and item.label in absorbed:
            real = item.count - absorbed[item.label].count
            if real > 0:
                lines.append(
                    (_sort_key(item), _adaptation_sentence(dataclasses.replace(item, count=real), editor))
                )
        else:
            lines.append((_sort_key(item), _adaptation_sentence(item, editor)))
    lines.sort(key=lambda pair: pair[0])
    return [sentence for _key, sentence in lines] + unknown


def explain_warnings(
    import_warnings: Iterable[str],
    adaptation_warnings: Iterable[str],
    editor: str,
) -> tuple[str, ...]:
    """Everything the panel lists, in plain language: the importer's, then the editor's.

    Each library warning becomes one line, with one exception: the two messages
    ``adapt_document`` writes about a title that has to become body text are joined
    (see :func:`_merge_titles`). Nothing the libraries reported is dropped. Identical
    lines are listed once.
    """
    lines = [explain_import_warning(message) for message in import_warnings]
    lines.extend(_explain_adaptations(adaptation_warnings, editor))
    return tuple(dict.fromkeys(line for line in lines if line))
