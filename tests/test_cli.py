import pytest

from auto_essay_typer.cli import build_profile, main, parse_args


def test_preset_sets_the_typo_rate():
    profile = build_profile(parse_args(["f.txt", "--preset", "sloppy"]))
    assert profile.typo_rate_per_100 == 3.0


def test_explicit_flags_override_the_preset():
    profile = build_profile(parse_args(["f.txt", "--preset", "sloppy", "--typo-rate", "0.1"]))
    assert profile.typo_rate_per_100 == 0.1


def test_wpm_flag_is_applied():
    assert build_profile(parse_args(["f.txt", "--wpm", "90"])).wpm == 90.0


def test_dry_run_prints_the_text_without_typing(tmp_path, capsys):
    essay = tmp_path / "essay.txt"
    essay.write_text("The quick brown fox.")
    assert main([str(essay), "--dry-run", "--seed", "1"]) == 0
    out = capsys.readouterr().out
    assert "The quick brown fox." in out
    assert "estimated" in out.lower()


def test_dry_run_output_matches_the_input_exactly(tmp_path, capsys):
    text = "Hello, world! Second sentence here."
    essay = tmp_path / "essay.txt"
    essay.write_text(text)
    main([str(essay), "--dry-run", "--seed", "2", "--preset", "sloppy"])
    printed = capsys.readouterr().out
    assert text in printed


def test_missing_file_reports_an_error(capsys):
    assert main(["definitely-not-here.txt"]) == 1
    assert "not found" in capsys.readouterr().err.lower()


def test_empty_file_is_rejected(tmp_path, capsys):
    essay = tmp_path / "empty.txt"
    essay.write_text("   \n  ")
    assert main([str(essay)]) == 1
    assert "empty" in capsys.readouterr().err.lower()


def test_run_uses_the_injected_keyboard(tmp_path):
    from auto_essay_typer.driver import RecordingKeyboard

    text = "Typed via injection."
    essay = tmp_path / "essay.txt"
    essay.write_text(text)
    keyboard = RecordingKeyboard()
    assert main([str(essay), "--seed", "3", "--countdown", "0"], keyboard=keyboard, sleep=lambda _: None) == 0
    assert keyboard.text == text
