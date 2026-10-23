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
