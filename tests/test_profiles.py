import pytest

from auto_essay_typer.profiles import PRESETS, Profile, preset


def test_default_profile_is_valid():
    assert Profile().wpm > 0


def test_presets_are_ordered_by_typo_rate():
    rates = [PRESETS[name].typo_rate_per_100 for name in ("clean", "natural", "sloppy")]
    assert rates == sorted(rates)


def test_preset_lookup_is_case_insensitive():
    assert preset("Natural") == PRESETS["natural"]


def test_unknown_preset_names_are_rejected():
    with pytest.raises(KeyError, match="nonexistent"):
        preset("nonexistent")


def test_wpm_must_be_positive():
    with pytest.raises(ValueError):
        Profile(wpm=0)


def test_typo_rate_cannot_be_negative():
    with pytest.raises(ValueError):
        Profile(typo_rate_per_100=-1.0)


def test_with_overrides_returns_a_modified_copy():
    base = preset("natural")
    fast = base.with_overrides(wpm=120)
    assert fast.wpm == 120
    assert fast.typo_rate_per_100 == base.typo_rate_per_100
    assert base.wpm != 120
