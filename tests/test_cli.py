import tomllib
from pathlib import Path

import pytest

from auto_essay_typer.cli import _EPILOG, build_profile, main, parse_args
from auto_essay_typer.profiles import Profile

#: The entry-point target whose installed command name ``--help`` advertises.
_CLI_ENTRY_POINT = "auto_essay_typer.cli:main"


def _installed_commands() -> dict[str, str]:
    """Every command ``pyproject.toml`` installs, mapped to its entry-point target."""
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
    return {**project.get("scripts", {}), **project.get("gui-scripts", {})}


def _installed_cli_name() -> str:
    """The command actually installed for ``cli:main``.

    Read out of the packaging metadata rather than repeated here: the point of
    the tests below is that the help text names a command the user's shell can
    really find, and a second hardcoded copy of that name could only ever
    drift away from the installed one alongside the first.
    """
    names = sorted(
        name for name, target in _installed_commands().items() if target == _CLI_ENTRY_POINT
    )
    assert len(names) == 1, f"expected one installed command for {_CLI_ENTRY_POINT}, got {names}"
    return names[0]


def _help_text(capsys) -> str:
    """Everything ``--help`` prints (argparse exits 0 afterwards)."""
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    return capsys.readouterr().out


def test_help_usage_line_names_the_installed_command(capsys):
    assert _help_text(capsys).startswith(f"usage: {_installed_cli_name()} ")


def test_help_never_names_a_command_that_is_not_installed(capsys):
    # Catches a stale name anywhere in the help -- a flag's own help string as
    # much as the usage line -- not only in the examples checked below.
    # Hyphenated, so a module path like auto_essay_typer.cli is not a command.
    mentioned = {word for word in _help_text(capsys).split() if "-typer" in word}
    assert mentioned, "the help text should name the command being run"
    assert mentioned <= set(_installed_commands())


def test_every_help_example_invokes_the_installed_command(capsys):
    examples = [
        line.strip() for line in _EPILOG.splitlines() if line.startswith("  ") and line.strip()
    ]
    assert examples, "the help epilog should carry copy-pasteable examples"
    assert {example.split()[0] for example in examples} == {_installed_cli_name()}
    # And every one of them must survive into what the user is actually shown.
    out = _help_text(capsys)
    assert all(example in out for example in examples)


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


# --- profile customization (--set, --profile, --list-profiles, --show-profile, ---
# --- --save-profile, --delete-profile, --config-path) ------------------------


@pytest.fixture
def profile_config(tmp_path, monkeypatch):
    """Point AUTO_ESSAY_TYPER_CONFIG at an isolated file so tests never touch ~/.config."""
    path = tmp_path / "profiles.toml"
    monkeypatch.setenv("AUTO_ESSAY_TYPER_CONFIG", str(path))
    return path


def test_set_overrides_the_preset(profile_config):
    profile = build_profile(parse_args(["f.txt", "--preset", "natural", "--set", "wpm=90"]))
    assert profile.wpm == 90.0


def test_set_beats_wpm_flag(profile_config):
    profile = build_profile(parse_args(["f.txt", "--wpm", "70", "--set", "wpm=90"]))
    assert profile.wpm == 90.0


def test_multiple_set_flags_all_apply(profile_config):
    profile = build_profile(
        parse_args(["f.txt", "--set", "wpm=90", "--set", "typo_rate_per_100=0.5"])
    )
    assert profile.wpm == 90.0
    assert profile.typo_rate_per_100 == 0.5


def test_set_unknown_key_exits_1_with_message_naming_the_key(profile_config, tmp_path, capsys):
    essay = tmp_path / "essay.txt"
    essay.write_text("Hello.")
    assert main([str(essay), "--set", "not_a_real_field=1", "--dry-run"]) == 1
    err = capsys.readouterr().err
    assert "not_a_real_field" in err
    assert "Traceback" not in err


def test_set_unparseable_value_exits_1_with_message(profile_config, tmp_path, capsys):
    essay = tmp_path / "essay.txt"
    essay.write_text("Hello.")
    assert main([str(essay), "--set", "wpm=not-a-number", "--dry-run"]) == 1
    err = capsys.readouterr().err
    assert "wpm" in err
    assert "Traceback" not in err


def test_set_bool_field(profile_config):
    profile = build_profile(parse_args(["f.txt", "--set", "session_breaks=false"]))
    assert profile.session_breaks is False


def test_list_profiles_lists_the_three_builtins_without_a_file_argument(profile_config, capsys):
    assert main(["--list-profiles"]) == 0
    out = capsys.readouterr().out
    assert "clean" in out
    assert "natural" in out
    assert "sloppy" in out


def test_save_list_resolve_delete_round_trip(profile_config, capsys):
    assert main(["--preset", "sloppy", "--wpm", "111", "--save-profile", "myfast"]) == 0
    capsys.readouterr()

    assert main(["--list-profiles"]) == 0
    out = capsys.readouterr().out
    assert "myfast" in out
    assert "user" in out

    profile = build_profile(parse_args(["f.txt", "--profile", "myfast"]))
    assert profile.wpm == 111.0

    assert main(["--delete-profile", "myfast"]) == 0
    capsys.readouterr()

    assert main(["--list-profiles"]) == 0
    out = capsys.readouterr().out
    assert "myfast" not in out


def test_user_profile_named_natural_shadows_the_builtin(profile_config):
    assert main(["--preset", "natural", "--wpm", "42", "--save-profile", "natural"]) == 0
    profile = build_profile(parse_args(["f.txt", "--profile", "natural"]))
    assert profile.wpm == 42.0


def test_show_profile_output_round_trips_via_tomllib(profile_config, capsys):
    assert main(["--preset", "sloppy", "--set", "wpm=80", "--show-profile"]) == 0
    out = capsys.readouterr().out
    data = tomllib.loads(out)
    expected = build_profile(parse_args(["f.txt", "--preset", "sloppy", "--set", "wpm=80"]))
    assert Profile.from_dict(data) == expected


def test_config_path_prints_a_path(profile_config, capsys):
    assert main(["--config-path"]) == 0
    out = capsys.readouterr().out.strip()
    assert out == str(profile_config)


def test_unknown_profile_name_exits_1_listing_valid_names(profile_config, tmp_path, capsys):
    essay = tmp_path / "essay.txt"
    essay.write_text("Hello.")
    assert main([str(essay), "--preset", "nonexistent", "--dry-run"]) == 1
    err = capsys.readouterr().err
    assert "nonexistent" in err
    assert "clean" in err and "natural" in err and "sloppy" in err
    assert "Traceback" not in err


def test_missing_file_with_no_management_flag_exits_2(profile_config, capsys):
    assert main([]) == 2
    err = capsys.readouterr().err
    assert err.strip()


def test_delete_profile_on_missing_name_exits_1(profile_config, capsys):
    assert main(["--delete-profile", "does-not-exist"]) == 1
    err = capsys.readouterr().err
    assert "does-not-exist" in err
