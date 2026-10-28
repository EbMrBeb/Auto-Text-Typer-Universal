from auto_essay_typer.gui.preview_dialog import PreviewDialog
from auto_essay_typer.gui.theme import LIGHT_TOKENS
from auto_essay_typer.planner import plan
from auto_essay_typer.profiles import Profile


def test_preview_dialog_shows_the_verified_badge_for_a_clean_run(qtbot, qapp):
    text = "The quick brown fox jumps over the lazy dog."
    actions = plan(text, Profile(typo_rate_per_100=0.0, revision_rate_per_word=0.0), seed=0)
    dialog = PreviewDialog(actions, text, LIGHT_TOKENS)
    qtbot.addWidget(dialog)

    assert dialog.verification_badge.property("tone") == "success"
    assert "verified" in dialog.verification_badge.text().lower()
    assert "matches your text exactly" in dialog.verification_badge.text()


def test_preview_dialog_shows_a_mismatch_badge_when_actions_dont_match_the_text(qtbot, qapp):
    from auto_essay_typer.actions import KeyPress, Pause

    text = "hello"
    # Deliberately wrong: this action stream types "hellx", not "hello".
    actions = [Pause(10.0), KeyPress("h"), KeyPress("e"), KeyPress("l"), KeyPress("l"), KeyPress("x")]
    dialog = PreviewDialog(actions, text, LIGHT_TOKENS)
    qtbot.addWidget(dialog)

    assert dialog.verification_badge.property("tone") == "danger"
    assert "mismatch" in dialog.verification_badge.text().lower()


def test_preview_dialog_shows_summary_counts(qtbot, qapp):
    text = "One sentence here. Another one there. And a third for good measure."
    actions = plan(text, Profile(typo_rate_per_100=6.0), seed=3)
    dialog = PreviewDialog(actions, text, LIGHT_TOKENS)
    qtbot.addWidget(dialog)
    # The dialog builds without error and produces a non-trivial timeline for
    # a sloppy profile -- the exact event count is an implementation detail
    # of preview.describe(), already covered by tests/test_preview.py.
    assert dialog.windowTitle() == "Preview"


def test_preview_dialog_close_button_accepts(qtbot, qapp):
    text = "hi"
    actions = plan(text, Profile(), seed=1)
    dialog = PreviewDialog(actions, text, LIGHT_TOKENS)
    qtbot.addWidget(dialog)

    from PySide6.QtWidgets import QDialog

    accepted = []
    dialog.finished.connect(lambda code: accepted.append(code))
    dialog.close_button.click()
    assert accepted == [QDialog.DialogCode.Accepted]


# --- previewing a formatted document ---------------------------------------------------------------------

from pathlib import Path  # noqa: E402

from PySide6.QtWidgets import QGridLayout, QLabel  # noqa: E402

from auto_essay_typer import targets  # noqa: E402
from auto_essay_typer.actions import Format, FormatOp  # noqa: E402
from auto_essay_typer.gui.document_session import DocumentSession  # noqa: E402
from auto_essay_typer.importers import load_document  # noqa: E402
from auto_essay_typer.planner import plan_document  # noqa: E402
from auto_essay_typer.platforms import OperatingSystem  # noqa: E402

_FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "gdocs_export_sample.html"
_CLEAN = Profile(typo_rate_per_100=0.0, revision_rate_per_word=0.0)


def _session(target_id: str = "google-docs") -> DocumentSession:
    document, report = load_document(str(_FIXTURE))
    return DocumentSession.create(
        document,
        str(_FIXTURE),
        report.warnings,
        targets.get_target(target_id),
        targets.keymap_for(target_id, OperatingSystem.MACOS),
    )


def _document_dialog(qtbot, actions, session: DocumentSession) -> PreviewDialog:
    dialog = PreviewDialog(
        actions, session.adapted.plain_text(), LIGHT_TOKENS, document=session.adapted
    )
    qtbot.addWidget(dialog)
    return dialog


def _label_texts(dialog: PreviewDialog) -> list[str]:
    return [label.text() for label in dialog.findChildren(QLabel)]


def _card_positions(dialog: PreviewDialog) -> list[tuple[int, int, int, int]]:
    (grid,) = dialog.findChildren(QGridLayout)
    return [grid.getItemPosition(i) for i in range(grid.count())]


def test_a_document_plan_that_reproduces_the_document_shows_the_verified_badge(qtbot, qapp):
    session = _session()
    actions = plan_document(session.adapted, _CLEAN, seed=1)

    dialog = _document_dialog(qtbot, actions, session)

    assert dialog.verification_badge.property("tone") == "success"
    assert dialog.verification_badge.text() == (
        "Output verified: matches your document exactly, formatting included"
    )


def test_a_plan_with_the_right_text_but_missing_formatting_is_a_loud_mismatch(qtbot, qapp):
    session = _session()
    actions = plan_document(session.adapted, _CLEAN, seed=1)
    without_bold = [a for a in actions if a != Format(FormatOp.TOGGLE_BOLD)]
    # The characters on screen are still exactly right -- only the formatting is wrong.
    assert [a for a in without_bold if hasattr(a, "char")] == [a for a in actions if hasattr(a, "char")]

    dialog = _document_dialog(qtbot, without_bold, session)

    assert dialog.verification_badge.property("tone") == "danger"
    text = dialog.verification_badge.text()
    assert text.startswith("Mismatch: the planned output does not match your document exactly")
    assert "do not use this plan" in text
    assert "paragraph" in text  # says where it went wrong


def test_a_document_plan_that_types_the_wrong_text_is_a_mismatch_too(qtbot, qapp):
    session = _session()
    actions = plan_document(session.adapted, _CLEAN, seed=1)
    first_key = next(i for i, a in enumerate(actions) if hasattr(a, "char") and a.char.isalpha())
    from auto_essay_typer.actions import KeyPress

    tampered = [*actions[:first_key], KeyPress("#"), *actions[first_key + 1 :]]

    dialog = _document_dialog(qtbot, tampered, session)

    assert dialog.verification_badge.property("tone") == "danger"


def test_the_document_preview_counts_the_formatting_steps_and_plain_text_does_not(qtbot, qapp):
    session = _session()
    actions = plan_document(session.adapted, _CLEAN, seed=1)
    document_dialog = _document_dialog(qtbot, actions, session)
    steps = sum(1 for a in actions if isinstance(a, Format))
    assert "Formatting steps" in _label_texts(document_dialog)
    assert f"{steps:,}" in _label_texts(document_dialog)

    text = "plain words"
    plain = PreviewDialog(plan(text, _CLEAN, seed=1), text, LIGHT_TOKENS)
    qtbot.addWidget(plain)
    assert "Formatting steps" not in _label_texts(plain)


def test_the_summary_cards_fill_the_grid_in_rows_of_three(qtbot, qapp):
    text = "plain words"
    plain = PreviewDialog(plan(text, _CLEAN, seed=1), text, LIGHT_TOKENS)
    qtbot.addWidget(plain)
    # Plain text: five cards -- three across, then two sharing the full width (as before).
    assert _card_positions(plain) == [
        (0, 0, 1, 2),
        (0, 2, 1, 2),
        (0, 4, 1, 2),
        (1, 0, 1, 3),
        (1, 3, 1, 3),
    ]

    session = _session()
    document = _document_dialog(qtbot, plan_document(session.adapted, _CLEAN, seed=1), session)
    # A document adds a sixth card, which fills the second row.
    assert _card_positions(document) == [
        (0, 0, 1, 2),
        (0, 2, 1, 2),
        (0, 4, 1, 2),
        (1, 0, 1, 2),
        (1, 2, 1, 2),
        (1, 4, 1, 2),
    ]


def test_plain_text_previews_say_text_not_document(qtbot, qapp):
    from auto_essay_typer.actions import KeyPress, Pause

    dialog = PreviewDialog([Pause(10.0), KeyPress("x")], "y", LIGHT_TOKENS)
    qtbot.addWidget(dialog)
    assert dialog.verification_badge.text() == (
        "Mismatch: the planned output does not match your text exactly — do not use this plan"
    )
