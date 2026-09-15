"""Tests for the TOML configuration layer.

Covers Profile.to_dict/from_dict round-tripping, save/load/delete against
disk, merging built-in presets with user profiles, and the ``key=value``
override parser that powers a CLI ``--set`` flag. Every disk-touching test
redirects AUTO_ESSAY_TYPER_CONFIG into tmp_path so the real
``~/.config/auto-essay-typer`` is never read or written.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from auto_essay_typer import config
from auto_essay_typer.profiles import PRESETS, Profile, field_names

CUSTOM = Profile(
    wpm=91.5,
    lognormal_sigma=0.4,
    burst_sigma=0.11,
    burst_reversion=0.2,
    burst_range=(0.7, 1.4),
    fatigue_max=0.3,
    fatigue_tau_ms=900_000.0,
    sentence_pause_ms=(350.0, 1000.0),
    clause_pause_ms=(100.0, 300.0),
    paragraph_pause_ms=(1200.0, 4000.0),
    hesitation_probability=0.05,
    hesitation_ms=(250.0, 1200.0),
    long_word_threshold=7,
    typo_rate_per_100=2.5,
    oh_moment_ms=(100.0, 350.0),
    backspace_ms=(30.0, 70.0),
    retype_care_factor=1.3,
    revision_rate_per_word=1.0 / 250.0,
    revision_span_words=(1, 4),
)


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    """Point AUTO_ESSAY_TYPER_CONFIG at an isolated tmp_path file.

    Every config.* call in this module must go through this override so the
    tests can never touch the user's real ~/.config/auto-essay-typer.
    """
    path = tmp_path / "profiles.toml"
    monkeypatch.setenv("AUTO_ESSAY_TYPER_CONFIG", str(path))
    return path


# --- config_path ------------------------------------------------------------


def test_config_path_honours_override_env(tmp_path, monkeypatch):
    override = tmp_path / "custom.toml"
    monkeypatch.setenv("AUTO_ESSAY_TYPER_CONFIG", str(override))
    assert config.config_path() == override


def test_config_path_honours_xdg_config_home(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTO_ESSAY_TYPER_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config.config_path() == tmp_path / "auto-essay-typer" / "profiles.toml"


def test_config_path_defaults_under_home_config(monkeypatch):
    monkeypatch.delenv("AUTO_ESSAY_TYPER_CONFIG", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert (
        config.config_path()
        == Path.home() / ".config" / "auto-essay-typer" / "profiles.toml"
    )


# --- Profile.to_dict / from_dict round trip ---------------------------------


@pytest.mark.parametrize("name", sorted(PRESETS))
def test_preset_round_trips_through_dict(name):
    p = PRESETS[name]
    assert Profile.from_dict(p.to_dict()) == p


def test_custom_profile_round_trips_through_dict():
    assert Profile.from_dict(CUSTOM.to_dict()) == CUSTOM


def test_tuple_fields_become_lists_in_to_dict():
    d = CUSTOM.to_dict()
    assert isinstance(d["burst_range"], list)
    assert d["burst_range"] == [0.7, 1.4]


def test_tuple_fields_survive_round_trip_as_tuples():
    restored = Profile.from_dict(CUSTOM.to_dict())
    assert isinstance(restored.burst_range, tuple)
    assert isinstance(restored.revision_span_words, tuple)


def test_from_dict_rejects_unknown_key():
    with pytest.raises(KeyError, match="bogus_field"):
        Profile.from_dict({"bogus_field": 1})


def test_from_dict_unknown_key_error_lists_valid_field_names():
    with pytest.raises(KeyError) as exc_info:
        Profile.from_dict({"bogus_field": 1})
    message = str(exc_info.value)
    assert "wpm" in message
    assert "typo_rate_per_100" in message


def test_from_dict_rejects_wrong_tuple_length():
    with pytest.raises(ValueError, match="burst_range"):
        Profile.from_dict({"burst_range": [0.5]})


def test_field_names_matches_dataclass_fields_dynamically():
    # Derived from dataclasses.fields(Profile) directly rather than a
    # hardcoded count, so adding a knob to Profile never breaks this test.
    expected = tuple(sorted(f.name for f in dataclasses.fields(Profile)))
    assert field_names() == expected
    assert len(field_names()) == len(expected)


# --- config.load_profiles / save_profile / delete_profile ------------------


def test_missing_config_file_returns_empty_dict(config_file):
    assert config.load_profiles() == {}


def test_save_then_load_round_trips(config_file):
    config.save_profile("mine", CUSTOM)
    loaded = config.load_profiles()
    assert loaded["mine"] == CUSTOM


def test_saving_second_profile_preserves_first(config_file):
    config.save_profile("first", PRESETS["clean"])
    config.save_profile("second", PRESETS["sloppy"])
    loaded = config.load_profiles()
    assert loaded["first"] == PRESETS["clean"]
    assert loaded["second"] == PRESETS["sloppy"]


def test_delete_profile_removes_one_leaves_others(config_file):
    config.save_profile("first", PRESETS["clean"])
    config.save_profile("second", PRESETS["sloppy"])
    assert config.delete_profile("first") is True
    loaded = config.load_profiles()
    assert "first" not in loaded
    assert loaded["second"] == PRESETS["sloppy"]


def test_delete_profile_missing_name_returns_false(config_file):
    assert config.delete_profile("nope") is False


def test_malformed_toml_raises_naming_file(config_file):
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("this is not [valid toml", encoding="utf-8")
    with pytest.raises(Exception) as exc_info:
        config.load_profiles()
    assert str(config_file) in str(exc_info.value)


def test_unknown_field_in_file_raises_naming_field(config_file):
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("[oops]\nnonexistent_field = 1\n", encoding="utf-8")
    with pytest.raises(Exception) as exc_info:
        config.load_profiles()
    message = str(exc_info.value)
    assert "nonexistent_field" in message
    assert str(config_file) in message


# --- builtin_and_user_profiles / resolve_profile ----------------------------


def test_user_profile_shadows_same_named_builtin(config_file):
    custom_clean = PRESETS["clean"].with_overrides(wpm=200)
    config.save_profile("clean", custom_clean)
    merged = config.builtin_and_user_profiles()
    assert merged["clean"] == custom_clean
    assert merged["clean"] != PRESETS["clean"]


def test_builtin_and_user_profiles_includes_untouched_builtins(config_file):
    config.save_profile("mine", CUSTOM)
    merged = config.builtin_and_user_profiles()
    assert merged["natural"] == PRESETS["natural"]
    assert merged["mine"] == CUSTOM


def test_resolve_profile_returns_builtin_when_no_user_override(config_file):
    assert config.resolve_profile("natural") == PRESETS["natural"]


def test_resolve_profile_unknown_name_lists_available(config_file):
    with pytest.raises(KeyError, match="clean"):
        config.resolve_profile("nonexistent")


# --- parse_override / apply_overrides ---------------------------------------


def test_parse_override_float_field():
    assert config.parse_override("wpm=90") == ("wpm", 90.0)


def test_parse_override_int_field():
    assert config.parse_override("long_word_threshold=6") == (
        "long_word_threshold",
        6,
    )


def test_parse_override_tuple_field():
    key, value = config.parse_override("burst_range=0.7,1.3")
    assert key == "burst_range"
    assert value == (0.7, 1.3)


def test_parse_override_rejects_unknown_key():
    with pytest.raises(KeyError) as exc_info:
        config.parse_override("bogus=1")
    message = str(exc_info.value)
    assert "bogus" in message
    assert "wpm" in message


def test_parse_override_rejects_bad_tuple_arity():
    with pytest.raises(ValueError, match="burst_range"):
        config.parse_override("burst_range=0.7,1.3,2.0")


def test_parse_override_rejects_non_numeric_value():
    with pytest.raises(ValueError, match="wpm"):
        config.parse_override("wpm=fast")


def test_apply_overrides_applies_several_and_leaves_rest_untouched():
    base = PRESETS["natural"]
    updated = config.apply_overrides(base, ["wpm=100", "typo_rate_per_100=0.5"])
    assert updated.wpm == 100.0
    assert updated.typo_rate_per_100 == 0.5
    assert updated.burst_range == base.burst_range
    assert updated.revision_span_words == base.revision_span_words


# --- bool field handling (session_breaks) -----------------------------------
#
# bool is a subclass of int in Python, so a naive isinstance(x, int) or
# int(x) coercion path silently mangles it -- and bool("false") is True,
# since it's a non-empty string. These guard the fix in Profile's coercion
# helpers and the TOML writer.


def test_bool_field_to_dict_keeps_python_bool_type():
    d = Profile(session_breaks=False).to_dict()
    assert d["session_breaks"] is False
    assert isinstance(d["session_breaks"], bool)


def test_bool_field_round_trips_through_dict_both_values():
    for value in (True, False):
        restored = Profile.from_dict(Profile(session_breaks=value).to_dict())
        assert restored.session_breaks is value


def test_bool_field_round_trips_through_toml_file(config_file):
    config.save_profile("breaks-off", Profile(session_breaks=False))
    config.save_profile("breaks-on", Profile(session_breaks=True))
    loaded = config.load_profiles()
    assert loaded["breaks-off"].session_breaks is False
    assert loaded["breaks-on"].session_breaks is True


@pytest.mark.parametrize("text", ["true", "True", "TRUE", "yes", "YES", "1"])
def test_parse_override_accepts_true_bool_strings_case_insensitively(text):
    key, value = config.parse_override(f"session_breaks={text}")
    assert key == "session_breaks"
    assert value is True


@pytest.mark.parametrize("text", ["false", "False", "FALSE", "no", "NO", "0"])
def test_parse_override_accepts_false_bool_strings_case_insensitively(text):
    key, value = config.parse_override(f"session_breaks={text}")
    assert key == "session_breaks"
    assert value is False


def test_parse_override_bool_rejects_nonsense_value():
    with pytest.raises(ValueError, match="session_breaks"):
        config.parse_override("session_breaks=maybe")
