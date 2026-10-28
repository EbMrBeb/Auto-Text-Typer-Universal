"""The plain-language wording of the "What will not be reproduced" panel.

These tests pin two things. The wording of each known library message, and --
because the wording is matched against the libraries' own strings -- that every
message the real importers and ``adapt_document`` can produce is translated, so
a template added or reworded in a library fails here instead of leaking jargon
onto the screen.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_essay_typer import targets
from auto_essay_typer.document import (
    Alignment,
    Document,
    ListKind,
    Paragraph,
    ParagraphStyle,
    Run,
    TextStyle,
)
from auto_essay_typer.gui.document_warnings import (
    editor_phrase,
    explain_adaptation_warning,
    explain_import_warning,
    explain_warnings,
)
from auto_essay_typer.importers import load_document
from auto_essay_typer.keymaps import adapt_document
from auto_essay_typer.platforms import OperatingSystem

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "gdocs_export_sample.html"

#: Words that only make sense to a developer. None may reach the screen.
_JARGON = ("keymap", "FormatOp", "adapt_document", "runs ", " run ", "(dropped)", "flattened", "typed as plain text with a")


# --- the importers' messages ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "1 link typed as plain text",
            "1 link will be typed as plain text, not as a clickable link.",
        ),
        (
            "3 links typed as plain text",
            "3 links will be typed as plain text, not as clickable links.",
        ),
        (
            "1 image not reproduced (dropped)",
            "1 image will be left out, because images can't be typed.",
        ),
        (
            "2 images not reproduced (dropped)",
            "2 images will be left out, because images can't be typed.",
        ),
        (
            "1 table flattened to one paragraph per cell",
            "1 table will be typed as plain paragraphs, one per cell.",
        ),
        (
            "2 tables flattened to one paragraph per cell",
            "2 tables will be typed as plain paragraphs, one per cell.",
        ),
        ("1 horizontal rule not reproduced (dropped)", "1 horizontal line will be left out."),
        ("4 horizontal rules not reproduced (dropped)", "4 horizontal lines will be left out."),
        (
            "1 soft line break typed as paragraph break",
            "1 line break inside a paragraph will be typed as a new paragraph.",
        ),
        (
            "5 soft line breaks typed as paragraph breaks",
            "5 line breaks inside a paragraph will be typed as new paragraphs.",
        ),
        (
            "1 footnote/comment reference not reproduced (dropped)",
            "1 footnote or comment marker will be left out.",
        ),
        (
            "2 footnote/comment references not reproduced (dropped)",
            "2 footnote or comment markers will be left out.",
        ),
        (
            "this document uses custom fonts, sizes, or colours, which are not reproduced",
            "Custom fonts, text sizes and colours will not be reproduced.",
        ),
    ],
)
def test_each_importer_message_becomes_a_plain_sentence(message, expected):
    assert explain_import_warning(message) == expected


def test_an_importer_message_this_module_does_not_know_is_kept_as_a_sentence():
    assert explain_import_warning("some brand new loss") == "Some brand new loss."


def test_every_warning_the_real_importer_produces_is_translated():
    _document, report = load_document(str(FIXTURE))
    assert report.warnings, "the fixture is meant to lose things"
    for message in report.warnings:
        plain = explain_import_warning(message)
        assert plain != message
        assert plain[0].isupper() or plain[0].isdigit()
        assert plain.endswith(".")
        assert not any(word in plain for word in _JARGON), plain


# --- adapt_document's messages ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "1 Title paragraph typed as Heading 1 (no Title shortcut in this keymap)",
            "1 title will be typed as Heading 1, because Google Docs has no title shortcut.",
        ),
        (
            "2 Title paragraphs typed as Heading 1 (no Title shortcut in this keymap)",
            "2 titles will be typed as Heading 1, because Google Docs has no title shortcut.",
        ),
        (
            "1 Subtitle paragraph typed as Heading 2 (no Subtitle shortcut in this keymap)",
            "1 subtitle will be typed as Heading 2, because Google Docs has no subtitle shortcut.",
        ),
        (
            "1 Heading 3 paragraph typed as Normal, bold (no Heading 3 shortcut in this keymap)",
            "1 Heading 3 paragraph will be typed as bold text, because Google Docs has no "
            "Heading 3 shortcut.",
        ),
        (
            "2 Heading 1 paragraphs typed as Normal (no Heading 1 shortcut in this keymap)",
            "2 Heading 1 paragraphs will be typed as ordinary text, because Google Docs has no "
            "Heading 1 shortcut.",
        ),
        (
            "1 Title paragraph typed as Normal, bold (no Title shortcut in this keymap)",
            "1 title paragraph will be typed as bold text, because Google Docs has no title "
            "shortcut.",
        ),
        (
            "1 paragraph typed as left-aligned (no Center shortcut in this keymap)",
            "1 centered paragraph will be typed left-aligned, because Google Docs has no "
            "shortcut for centered text.",
        ),
        (
            "3 paragraphs typed as left-aligned (no Justified shortcut in this keymap)",
            "3 justified paragraphs will be typed left-aligned, because Google Docs has no "
            "shortcut for justified text.",
        ),
        (
            "2 paragraphs typed as left-aligned (no Right shortcut in this keymap)",
            "2 right-aligned paragraphs will be typed left-aligned, because Google Docs has no "
            "shortcut for right-aligned text.",
        ),
        (
            "1 run lost strikethrough formatting (no strikethrough shortcut in this keymap)",
            "1 piece of text will lose strikethrough formatting, because Google Docs has no "
            "strikethrough shortcut.",
        ),
        (
            "3 runs lost bold formatting (no bold shortcut in this keymap)",
            "3 pieces of text will lose bold formatting, because Google Docs has no bold shortcut.",
        ),
        (
            '2 bullet-list paragraphs typed as plain text with a "•" marker (no bullet-list shortcut in this keymap)',
            "2 bulleted list items will be typed as plain text starting with “•”, because Google Docs "
            "has no shortcut for bulleted lists.",
        ),
        (
            '1 bullet-list paragraph typed as plain text with a "•" marker (no bullet-list shortcut in this keymap)',
            "1 bulleted list item will be typed as plain text starting with “•”, because Google Docs "
            "has no shortcut for bulleted lists.",
        ),
        (
            "1 numbered-list paragraph typed as plain text with typed numbers (no numbered-list shortcut in this keymap)",
            "1 numbered list item will be typed as plain text with typed numbers (1., 2., …), "
            "because Google Docs has no shortcut for numbered lists.",
        ),
    ],
)
def test_each_adaptation_message_becomes_a_plain_sentence(message, expected):
    assert explain_adaptation_warning(message, "Google Docs") == expected


def test_the_editor_is_named_in_the_sentence():
    message = "1 run lost bold formatting (no bold shortcut in this keymap)"
    assert "TextEdit" in explain_adaptation_warning(message, "TextEdit")
    assert "a plain-text field" in explain_adaptation_warning(message, "a plain-text field")


def test_an_adaptation_message_this_module_does_not_know_loses_its_jargon_but_not_its_facts():
    plain = explain_adaptation_warning(
        "4 widgets typed as gadgets (no widget shortcut in this keymap)", "Microsoft Word"
    )
    assert plain == "4 widgets typed as gadgets (no widget shortcut in Microsoft Word)."
    assert "keymap" not in plain


def _every_kind_of_loss() -> Document:
    """A document that triggers every fallback ``adapt_document`` has."""
    styled = lambda text, *styles: Run(text, frozenset(styles))  # noqa: E731
    return Document(
        (
            Paragraph((Run("Title"),), style=ParagraphStyle.TITLE),
            Paragraph((Run("Subtitle"),), style=ParagraphStyle.SUBTITLE),
            Paragraph((Run("Heading one"),), style=ParagraphStyle.HEADING_1),
            Paragraph((Run("Heading three"),), style=ParagraphStyle.HEADING_3),
            Paragraph((Run("Centered"),), alignment=Alignment.CENTER),
            Paragraph((Run("Right"),), alignment=Alignment.RIGHT),
            Paragraph((Run("Justified"),), alignment=Alignment.JUSTIFIED),
            Paragraph(
                (
                    styled("bold", TextStyle.BOLD),
                    styled("italic", TextStyle.ITALIC),
                    styled("underline", TextStyle.UNDERLINE),
                    styled("strike", TextStyle.STRIKETHROUGH),
                )
            ),
            Paragraph((Run("bullet"),), list_kind=ListKind.BULLET),
            Paragraph((Run("number"),), list_kind=ListKind.NUMBERED),
        )
    )


def _every_editor():
    for os_ in OperatingSystem:
        for target in targets.targets_for(os_):
            yield pytest.param(os_, target, id=f"{os_.value}-{target.id}")


@pytest.mark.parametrize(("os_", "target"), list(_every_editor()))
def test_every_warning_adapt_document_can_produce_is_translated(os_, target):
    keymap = targets.keymap_for(target.id, os_)
    fixture_document, _report = load_document(str(FIXTURE))
    for document in (fixture_document, _every_kind_of_loss()):
        _adapted, messages = adapt_document(document, keymap)
        for message in messages:
            plain = explain_adaptation_warning(message, editor_phrase(target))
            assert plain != message
            assert editor_phrase(target) in plain
            assert plain.endswith(".")
            assert not any(word in plain for word in _JARGON), plain


def test_the_plain_text_fallback_is_the_noisiest_editor_and_still_fully_translated():
    plain_text = targets.get_target("plain-text")
    _adapted, messages = adapt_document(
        _every_kind_of_loss(), targets.keymap_for("plain-text", OperatingSystem.MACOS)
    )
    lines = [explain_adaptation_warning(m, editor_phrase(plain_text)) for m in messages]
    assert len(lines) == len(messages) >= 8
    assert not any("keymap" in line for line in lines)


# --- putting the panel's list together ----------------------------------------------------------------


def test_editor_phrase_calls_the_plain_text_fallback_a_field_and_others_by_name():
    assert editor_phrase(targets.get_target("plain-text")) == "a plain-text field"
    assert editor_phrase(targets.get_target("google-docs")) == "Google Docs"
    assert editor_phrase(targets.get_target("microsoft-word")) == "Microsoft Word"


def test_the_importers_warnings_come_first_then_the_editors():
    lines = explain_warnings(
        ["1 image not reproduced (dropped)"],
        ["1 run lost bold formatting (no bold shortcut in this keymap)"],
        "TextEdit",
    )
    assert len(lines) == 2
    assert "image" in lines[0]
    assert "bold" in lines[1] and "TextEdit" in lines[1]


def test_each_library_warning_is_one_line_and_nothing_is_merged_away():
    importer = ["1 link typed as plain text", "2 images not reproduced (dropped)"]
    adaptation = [
        "1 Title paragraph typed as Heading 1 (no Title shortcut in this keymap)",
        "1 run lost italic formatting (no italic shortcut in this keymap)",
    ]
    assert len(explain_warnings(importer, adaptation, "Microsoft Word")) == 4


def test_identical_lines_are_listed_once():
    lines = explain_warnings(["1 link typed as plain text", "1 link typed as plain text"], [], "Google Docs")
    assert len(lines) == 1


def test_no_warnings_gives_an_empty_list():
    assert explain_warnings([], [], "Google Docs") == ()
