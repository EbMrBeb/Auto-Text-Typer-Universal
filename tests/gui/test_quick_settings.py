import pytest

from auto_essay_typer.gui.quick_settings import (
    MISTAKE_STOPS,
    nearest_mistake_stop_index,
    speed_label,
)


@pytest.mark.parametrize(
    ("wpm", "expected"),
    [
        (20, "Slow"),
        (34, "Slow"),
        (35, "Relaxed"),
        (49, "Relaxed"),
        (50, "Natural"),
        (65, "Natural"),
        (74, "Natural"),
        (75, "Fast"),
        (99, "Fast"),
        (100, "Very fast"),
        (160, "Very fast"),
    ],
)
def test_speed_label_boundaries(wpm, expected):
    assert speed_label(wpm) == expected


def test_mistake_stops_values_and_labels():
    assert MISTAKE_STOPS == (
        (0.0, "None"),
        (0.3, "Rare"),
        (1.2, "Natural"),
        (3.0, "Frequent"),
        (6.0, "Sloppy"),
    )


@pytest.mark.parametrize(
    ("rate", "expected_index"),
    [
        (0.0, 0),
        (0.3, 1),
        (1.2, 2),
        (3.0, 3),
        (6.0, 4),
        (-1.0, 0),  # clamps to the nearest stop, not out of range
        (100.0, 4),
        (0.15, 0),  # exactly between None and Rare -- ties go to the lower index
        (0.7, 1),  # closer to Rare (0.3) than Natural (1.2)
    ],
)
def test_nearest_mistake_stop_index(rate, expected_index):
    assert nearest_mistake_stop_index(rate) == expected_index


# -- the "Typing into" editor picker ----------------------------------------------------------------

from auto_essay_typer import targets  # noqa: E402
from auto_essay_typer.gui.quick_settings import initial_target_id, target_caution  # noqa: E402
from auto_essay_typer.platforms import OperatingSystem  # noqa: E402


@pytest.mark.parametrize("os_", list(OperatingSystem))
def test_the_saved_editor_is_selected_when_this_system_offers_it(os_):
    choices = targets.targets_for(os_)
    for target in choices:
        assert initial_target_id(target.id, choices) == target.id


def test_an_editor_this_system_does_not_offer_falls_back_to_the_first_one():
    linux = targets.targets_for(OperatingSystem.LINUX)
    # Word has no Linux release and TextEdit exists only on macOS.
    assert initial_target_id("microsoft-word", linux) == linux[0].id
    assert initial_target_id("textedit", linux) == linux[0].id
    assert initial_target_id("no-such-editor", linux) == linux[0].id


def test_only_editors_whose_shortcuts_are_unchecked_get_a_caution():
    for target in targets.TARGET_APPS.values():
        caution = target_caution(target)
        assert bool(caution) is (not target.verified), target.id


def test_the_caution_says_the_shortcuts_are_best_effort_and_where_to_change_them():
    caution = target_caution(targets.get_target("microsoft-word"))
    assert "Microsoft Word" in caution
    assert "best effort" in caution
    assert "Advanced page" in caution


def test_with_nothing_to_choose_from_the_saved_choice_is_returned_unchanged():
    assert initial_target_id("google-docs", []) == "google-docs"
