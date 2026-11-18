import pytest

from auto_essay_typer.profiles import (
    FIELD_INFO,
    PRESETS,
    Profile,
    field_names,
    group_fields,
    preset,
)


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


# --- FIELD_INFO / group_fields ----------------------------------------------

_ALLOWED_GROUPS = {"Speed", "Pauses", "Mistakes", "Revisions", "Breaks", "Formatting"}


def test_field_info_covers_exactly_the_profile_fields():
    assert set(FIELD_INFO) == set(field_names())


def test_field_info_ranges_bound_the_actual_defaults():
    defaults = Profile()
    for name in field_names():
        info = FIELD_INFO[name]
        value = getattr(defaults, name)
        if isinstance(value, bool):
            ends = (int(value),)
        elif isinstance(value, tuple):
            ends = value
        else:
            ends = (value,)
        for end in ends:
            assert info.minimum <= end <= info.maximum, (
                f"{name}: default end {end} not in [{info.minimum}, {info.maximum}]"
            )


def test_field_info_group_is_one_of_the_six_allowed_groups():
    for name in field_names():
        assert FIELD_INFO[name].group in _ALLOWED_GROUPS


def test_group_fields_covers_every_field_in_the_right_groups():
    groups = group_fields()
    assert [group for group, _ in groups] == [
        "Speed",
        "Pauses",
        "Mistakes",
        "Revisions",
        "Breaks",
        "Formatting",
    ]
    all_names: list[str] = []
    for group, names in groups:
        for name in names:
            assert FIELD_INFO[name].group == group
        all_names.extend(names)
    assert len(all_names) == len(set(all_names))
    assert set(all_names) == set(field_names())


def test_only_the_three_beginner_fields_are_non_advanced():
    non_advanced = {name for name in field_names() if not FIELD_INFO[name].advanced}
    assert non_advanced == {"wpm", "typo_rate_per_100", "session_breaks"}
