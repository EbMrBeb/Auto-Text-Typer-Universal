import pytest

from auto_essay_typer.app import accessibility_hint, profile_from_settings


def test_profile_from_settings_applies_preset_and_overrides():
    profile = profile_from_settings(preset_name="sloppy", wpm=95.0, typo_rate=0.5)
    assert profile.wpm == 95.0
    assert profile.typo_rate_per_100 == 0.5


def test_profile_from_settings_keeps_preset_defaults_when_not_overridden():
    profile = profile_from_settings(preset_name="clean", wpm=60.0, typo_rate=0.3)
    assert profile.revision_rate_per_word == pytest.approx(1.0 / 800.0)


def test_accessibility_hint_names_the_settings_pane():
    hint = accessibility_hint()
    assert "Accessibility" in hint
    assert "Privacy" in hint
