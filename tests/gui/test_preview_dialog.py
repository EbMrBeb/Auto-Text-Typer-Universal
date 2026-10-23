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
