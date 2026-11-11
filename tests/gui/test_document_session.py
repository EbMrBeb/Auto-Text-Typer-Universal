"""``DocumentSession`` (an imported document fitted to an editor) and plan verification."""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_essay_typer import targets
from auto_essay_typer.actions import Backspace, Format, FormatOp, KeyPress, Pause
from auto_essay_typer.document import Document, Paragraph, ParagraphStyle, Run
from auto_essay_typer.gui.document_session import (
    DocumentSession,
    PlanCheck,
    verify_document_plan,
)
from auto_essay_typer.importers import load_document
from auto_essay_typer.keymaps import adapt_document
from auto_essay_typer.planner import plan_document
from auto_essay_typer.platforms import OperatingSystem
from auto_essay_typer.profiles import PRESETS, Profile

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "gdocs_export_sample.html"
OS = OperatingSystem.MACOS


def _session(target_id: str = "google-docs") -> DocumentSession:
    document, report = load_document(str(FIXTURE))
    target = targets.get_target(target_id)
    return DocumentSession.create(
        document, str(FIXTURE), report.warnings, target, targets.keymap_for(target_id, OS)
    )


# --- the session ------------------------------------------------------------------------------------


def test_a_session_keeps_the_imported_document_and_adapts_a_copy():
    session = _session("google-docs")
    adapted, adaptation_warnings = adapt_document(session.document, session.keymap)
    assert session.adapted == adapted
    assert session.adaptation_warnings == adaptation_warnings
    # Google Docs has no Title or Subtitle shortcut, so those two were rewritten ...
    assert session.adapted != session.document
    # ... but the document that was imported is untouched.
    assert session.document.paragraphs[0].style is ParagraphStyle.TITLE


def test_a_sessions_plain_warnings_are_the_importers_then_the_editors_in_plain_language():
    session = _session("microsoft-word")
    assert len(session.plain_warnings) == len(session.import_warnings) + len(session.adaptation_warnings)
    assert not any("keymap" in line for line in session.plain_warnings)
    assert any("Microsoft Word" in line for line in session.plain_warnings)
    assert "image" in session.plain_warnings[1]  # the fixture's importer warnings come first


def test_switching_editor_adapts_the_source_again_rather_than_the_already_adapted_copy():
    google = _session("google-docs")
    plain = google.for_target(targets.get_target("plain-text"), targets.keymap_for("plain-text", OS))
    back = plain.for_target(targets.get_target("google-docs"), targets.keymap_for("google-docs", OS))
    assert plain.document == google.document
    assert plain.adapted != google.adapted
    # Round trip: nothing was lost on the way through the flattening editor.
    assert back == google


def test_an_editor_that_cannot_format_types_the_same_words_as_one_that_can():
    google = _session("google-docs")
    plain = _session("plain-text")
    assert [p.text for p in plain.adapted.paragraphs][-3:] == [p.text for p in google.adapted.paragraphs][-3:]
    # The list markers are typed characters in the plain-text editor, so its text is longer.
    assert len(plain.typed_text()) > len(google.typed_text())


def test_typed_text_is_the_adapted_documents_text_and_has_text_ignores_whitespace():
    session = _session("google-docs")
    assert session.typed_text() == session.adapted.plain_text()
    assert session.has_text()
    blank = DocumentSession.create(
        Document((Paragraph((Run("   "),)), Paragraph())),
        "x",
        (),
        targets.get_target("google-docs"),
        targets.keymap_for("google-docs", OS),
    )
    assert not blank.has_text()


# --- verify_document_plan ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [0, 1, 2, 7, 99])
@pytest.mark.parametrize("target_id", ["google-docs", "microsoft-word", "textedit", "plain-text"])
def test_a_real_plan_of_an_adapted_document_always_verifies(target_id, seed):
    session = _session(target_id)
    actions = plan_document(session.adapted, PRESETS["sloppy"], seed)
    assert verify_document_plan(actions, session.adapted) == PlanCheck(True)


def test_a_plan_that_types_the_wrong_text_fails_and_says_where():
    session = _session("google-docs")
    actions = plan_document(session.adapted, Profile(typo_rate_per_100=0.0), seed=1)
    index = next(i for i, a in enumerate(actions) if isinstance(a, KeyPress) and a.char.isalpha())
    tampered = [*actions[:index], KeyPress("#"), *actions[index + 1 :]]

    check = verify_document_plan(tampered, session.adapted)

    assert check.ok is False
    assert "paragraph 1" in check.detail


def test_a_plan_that_forgets_a_formatting_step_fails():
    session = _session("google-docs")
    actions = plan_document(session.adapted, Profile(typo_rate_per_100=0.0), seed=1)
    without_bold = [a for a in actions if a != Format(FormatOp.TOGGLE_BOLD)]
    assert len(without_bold) < len(actions)

    assert verify_document_plan(without_bold, session.adapted).ok is False


def test_a_plan_with_a_different_number_of_paragraphs_fails_and_says_so():
    document = Document((Paragraph((Run("one"),)), Paragraph((Run("two"),))))
    actions = plan_document(document, Profile(typo_rate_per_100=0.0), seed=1)
    one_paragraph = actions[: next(i for i, a in enumerate(actions) if a == KeyPress("\n"))]

    check = verify_document_plan(one_paragraph, document)

    assert check.ok is False
    assert "1 paragraph" in check.detail and "2" in check.detail


def test_a_plan_that_cannot_be_replayed_fails_instead_of_raising():
    document = Document((Paragraph((Run("hi"),)),))
    unreplayable = [Pause(1.0), KeyPress("h"), Backspace(2)]  # backs up past the start

    check = verify_document_plan(unreplayable, document)

    assert check.ok is False
    assert "could not be replayed" in check.detail


def test_an_empty_document_verifies_against_its_own_plan():
    empty = Document(())
    actions = plan_document(empty, Profile(), seed=0)
    assert verify_document_plan(actions, empty).ok is True
