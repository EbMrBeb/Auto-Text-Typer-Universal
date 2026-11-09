"""Tests for the generated typing-profile form (``gui/profile_editor.py``).

The editor must be driven entirely by ``profiles.FIELD_INFO`` /
``group_fields()`` and the dataclass's type hints, hold *exact* values (spin
boxes round and clamp), and validate live. These tests lean on that: they
iterate ``field_names()`` rather than naming fields, and round-trip every
preset plus deliberately awkward values.
"""

from __future__ import annotations

import dataclasses
from typing import get_args, get_origin, get_type_hints

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QDoubleSpinBox, QSpinBox

from auto_essay_typer import profiles
from auto_essay_typer.gui import profile_editor
from auto_essay_typer.gui.advanced_common import (
    NoWheelSpinBox,
    humanize_ms,
    humanize_ms_range,
)
from auto_essay_typer.gui.controls import ToggleSwitch
from auto_essay_typer.gui.profile_editor import (
    ProfileEditor,
    RangeEdit,
    _spec_for,
    decimals_needed,
    field_specs,
    to_display,
    validate_values,
)
from auto_essay_typer.gui.theme import DARK_TOKENS, LIGHT_TOKENS, ThemeManager, ThemeMode
from auto_essay_typer.profiles import FIELD_INFO, PRESETS, Profile, field_names, group_fields

PERCENT_FIELDS = [name for name in field_names() if FIELD_INFO[name].unit == "%"]


@pytest.fixture(autouse=True)
def _delete_closed_widgets():
    """Run the deferred deletes of the widgets pytest-qt closed, so live widgets don't pile up
    (every ``ThemeManager`` re-polishes all of them)."""
    yield
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


def _make_editor(qtbot, theme_manager=None) -> ProfileEditor:
    editor = ProfileEditor(theme_manager)
    qtbot.addWidget(editor)
    return editor


def _spin(editor: ProfileEditor, name: str, index: int = 0):
    return editor.rows[name].spin_boxes()[index]


# --- generated structure ---------------------------------------------------------------------


def test_every_profile_field_gets_a_control(qtbot):
    editor = _make_editor(qtbot)
    assert set(editor.field_widgets) == set(field_names())
    assert set(editor.rows) == set(field_names())


def test_groups_follow_group_fields_in_order(qtbot):
    editor = _make_editor(qtbot)
    expected = group_fields()
    assert list(editor.group_sections) == [group for group, _names in expected]
    for group, names in expected:
        section = editor.group_sections[group]
        assert section.title() == group
        rows = [
            section.body_layout.itemAt(i).widget()
            for i in range(section.body_layout.count())
            if isinstance(section.body_layout.itemAt(i).widget(), profile_editor.FieldRow)
        ]
        assert [row.spec.name for row in rows] == names


def test_first_group_is_expanded_and_the_rest_are_folded(qtbot):
    editor = _make_editor(qtbot)
    states = [section.is_expanded() for section in editor.group_sections.values()]
    assert states[0] is True
    assert not any(states[1:])


def test_expand_all_and_collapse_all(qtbot):
    editor = _make_editor(qtbot)
    editor.expand_all()
    assert all(section.is_expanded() for section in editor.group_sections.values())
    editor.collapse_all()
    assert not any(section.is_expanded() for section in editor.group_sections.values())


def test_a_field_the_groups_forget_lands_in_an_other_group(monkeypatch):
    original = profiles.group_fields()
    monkeypatch.setattr(profiles, "group_fields", lambda: original[:-1])  # drop the last group
    groups = field_specs()
    assert groups[-1][0] == "Other"
    placed = {spec.name for _group, specs in groups for spec in specs}
    assert placed == set(field_names())


def test_an_unsupported_field_type_is_reported_loudly():
    with pytest.raises(TypeError, match="does not know how to edit"):
        _spec_for("wpm", str)


# --- one control of the right kind per field ------------------------------------------------------


def test_each_field_gets_the_right_kind_of_widget(qtbot):
    editor = _make_editor(qtbot)
    hints = get_type_hints(Profile)
    for name in field_names():
        widget = editor.field_widgets[name]
        annotation = hints[name]
        if get_origin(annotation) is tuple:
            element = get_args(annotation)[0]
            assert isinstance(widget, RangeEdit), name
            expected = QSpinBox if element is int else QDoubleSpinBox
            assert isinstance(widget.low, expected), name
            assert isinstance(widget.high, expected), name
        elif annotation is bool:
            assert isinstance(widget, ToggleSwitch), name  # the app's switch, not a look-alike checkbox
            assert widget.objectName() == "ToggleSwitch"
        elif annotation is int:
            assert isinstance(widget, QSpinBox), name
        else:
            assert annotation is float
            assert isinstance(widget, QDoubleSpinBox), name


def test_spin_boxes_honour_the_minimum_maximum_and_step(qtbot):
    editor = _make_editor(qtbot)
    editor.load_profile(Profile())
    for name in field_names():
        info = FIELD_INFO[name]
        scale = 100.0 if info.unit == "%" else 1.0
        for spin in editor.rows[name].spin_boxes():
            assert spin.minimum() == pytest.approx(info.minimum * scale), name
            assert spin.maximum() == pytest.approx(info.maximum * scale), name
            expected_step = max(1, round(info.step * scale)) if isinstance(spin, QSpinBox) else info.step * scale
            assert spin.singleStep() == pytest.approx(expected_step), name


def test_a_range_gets_two_spin_boxes_sharing_the_bounds(qtbot):
    editor = _make_editor(qtbot)
    widget = editor.field_widgets["burst_range"]
    info = FIELD_INFO["burst_range"]
    for spin in (widget.low, widget.high):
        assert spin.minimum() == pytest.approx(info.minimum)
        assert spin.maximum() == pytest.approx(info.maximum)
    assert widget.low.value() == pytest.approx(Profile().burst_range[0])
    assert widget.high.value() == pytest.approx(Profile().burst_range[1])


def test_the_unit_is_shown_as_the_spin_box_suffix(qtbot):
    editor = _make_editor(qtbot)
    for name in field_names():
        info = FIELD_INFO[name]
        if info.unit and editor.rows[name].spec.kind != "bool":
            for spin in editor.rows[name].spin_boxes():
                assert spin.suffix() == f" {info.unit}", name
        elif editor.rows[name].spec.kind != "bool":
            for spin in editor.rows[name].spin_boxes():
                assert spin.suffix() == "", name
    assert _spin(editor, "wpm").suffix() == " wpm"


def test_each_row_shows_its_label_help_and_a_tooltip(qtbot):
    editor = _make_editor(qtbot)
    for name in field_names():
        row, info = editor.rows[name], FIELD_INFO[name]
        assert row.title_label.text() == info.label
        assert row.help_label.text() == info.help
        assert row.title_label.toolTip() == info.help
        assert row.control.toolTip() == info.help


# --- percent fields ---------------------------------------------------------------------------------------


def test_there_are_percent_fields_to_exercise():
    assert PERCENT_FIELDS  # the scaling tests below would silently pass over nothing otherwise


@pytest.mark.parametrize("name", PERCENT_FIELDS)
def test_percent_fields_are_edited_as_0_to_100(qtbot, name):
    editor = _make_editor(qtbot)
    editor.load_profile(Profile())
    spin = _spin(editor, name)
    assert spin.suffix() == " %"
    assert spin.minimum() == 0
    assert spin.maximum() == 100


def test_a_percent_edit_is_stored_as_an_exact_fraction(qtbot):
    editor = _make_editor(qtbot)
    editor.load_profile(Profile())
    spin = _spin(editor, "hesitation_probability")
    spin.setValue(7)
    # 7 / 100 done in floating point is 0.07000000000000001 -- the editor must
    # hand Profile the same float a person would write as 0.07.
    assert editor.current_profile().hesitation_probability == 0.07
    spin.setValue(5)
    assert editor.current_profile().hesitation_probability == 0.05


def test_a_percent_value_displays_as_a_percentage(qtbot):
    editor = _make_editor(qtbot)
    editor.load_profile(Profile(hesitation_probability=0.035))
    spin = _spin(editor, "hesitation_probability")
    assert spin.value() == pytest.approx(3.5)
    assert spin.decimals() == 1  # one decimal is needed to show 3.5 honestly


def test_to_display_avoids_floating_point_noise_for_percentages():
    spec = _spec_for("hesitation_probability", float)
    assert to_display(spec, 0.07) == 7.0  # not 7.000000000000001


# --- exactness: loading never changes a value --------------------------------------------------------------


def _awkward_profiles() -> list[Profile]:
    return [
        Profile(revision_rate_per_word=1 / 800),
        Profile(revision_rate_per_word=1 / 300),
        Profile(fatigue_tau_ms=1_200_000.0),
        Profile(hesitation_probability=0.035, fatigue_max=0.123, long_break_probability=0.2),
        Profile(wpm=87.5, typo_rate_per_100=0.35, post_break_burst=1.15),
        # Hand-edited values outside the FIELD_INFO ranges.
        Profile(wpm=300.0, typo_rate_per_100=75.0, lognormal_sigma=2.5),
        Profile(burst_range=(0.05, 4.0), sentence_pause_ms=(0.0, 9000.0), revision_span_words=(1, 40)),
        Profile(long_word_threshold=99, hesitation_probability=0.5),
        Profile(wpm=65, session_breaks=False),  # an int where a float is declared
    ]


@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_every_preset_round_trips_exactly(qtbot, preset):
    editor = _make_editor(qtbot)
    editor.load_profile(PRESETS[preset])
    assert editor.current_profile() == PRESETS[preset]


def test_the_default_profile_round_trips_exactly(qtbot):
    editor = _make_editor(qtbot)
    editor.load_profile(Profile())
    assert editor.current_profile() == Profile()


@pytest.mark.parametrize("profile", _awkward_profiles(), ids=lambda p: "awkward")
def test_awkward_values_round_trip_exactly(qtbot, profile):
    editor = _make_editor(qtbot)
    editor.load_profile(profile)
    assert editor.current_profile() == profile


def test_a_three_decimal_spin_box_would_have_corrupted_the_clean_preset(qtbot):
    # Documents why exact values are kept: 1/800 needs five decimals.
    clean = PRESETS["clean"]
    assert clean.revision_rate_per_word == 1 / 800
    editor = _make_editor(qtbot)
    editor.load_profile(clean)
    spin = _spin(editor, "revision_rate_per_word")
    assert spin.decimals() >= 5
    assert spin.value() == pytest.approx(1 / 800)
    assert editor.current_profile().revision_rate_per_word == 1 / 800


def test_a_value_beyond_six_decimals_is_displayed_rounded_but_kept_exact(qtbot):
    editor = _make_editor(qtbot)
    editor.load_profile(PRESETS["sloppy"])  # revision_rate_per_word = 1/300
    assert _spin(editor, "revision_rate_per_word").decimals() == profile_editor.MAX_DECIMALS
    assert editor.current_profile().revision_rate_per_word == 1 / 300


def test_an_out_of_range_value_is_shown_honestly_not_clamped(qtbot):
    editor = _make_editor(qtbot)
    editor.load_profile(Profile(wpm=300.0))
    spin = _spin(editor, "wpm")
    assert spin.maximum() >= 300
    assert spin.value() == 300
    assert "Outside the usual range" in editor.rows["wpm"].hint_label.text()
    assert not editor.rows["wpm"].hint_label.isHidden()
    assert editor.current_profile().wpm == 300.0


def test_loading_a_profile_is_not_a_user_edit(qtbot):
    editor = _make_editor(qtbot)
    row = editor.rows["wpm"]
    with qtbot.assertNotEmitted(row.valueEdited):
        editor.load_profile(Profile(wpm=120.0))
    assert not editor.is_dirty()
    assert editor.changed_fields() == []


def test_editing_one_field_leaves_every_other_value_exactly_as_loaded(qtbot):
    clean = PRESETS["clean"]
    editor = _make_editor(qtbot)
    editor.load_profile(clean)
    _spin(editor, "wpm").setValue(90)
    assert editor.current_profile() == clean.with_overrides(wpm=90.0)
    assert editor.current_profile().revision_rate_per_word == 1 / 800


def test_editing_one_end_of_a_range_keeps_the_other_end_exact(qtbot):
    editor = _make_editor(qtbot)
    profile = Profile(sentence_pause_ms=(400.0, 1234.5))
    editor.load_profile(profile)
    _spin(editor, "sentence_pause_ms", 0).setValue(500)
    assert editor.current_profile().sentence_pause_ms == (500.0, 1234.5)
    _spin(editor, "sentence_pause_ms", 1).setValue(2000)
    assert editor.current_profile().sentence_pause_ms == (500.0, 2000.0)


def test_integer_fields_and_integer_ranges_stay_integers(qtbot):
    editor = _make_editor(qtbot)
    editor.load_profile(Profile())
    _spin(editor, "long_word_threshold").setValue(12)
    _spin(editor, "revision_span_words", 1).setValue(7)
    profile = editor.current_profile()
    assert profile.long_word_threshold == 12 and isinstance(profile.long_word_threshold, int)
    assert profile.revision_span_words == (2, 7)
    assert all(isinstance(v, int) for v in profile.revision_span_words)


def test_a_boolean_field_toggles(qtbot):
    editor = _make_editor(qtbot)
    editor.load_profile(Profile(session_breaks=True))
    box = editor.field_widgets["session_breaks"]
    box.setChecked(False)
    assert editor.current_profile().session_breaks is False
    assert editor.is_dirty()
    box.setChecked(True)
    assert not editor.is_dirty()


def test_a_boolean_field_is_a_switch_as_wide_as_its_track_not_its_whole_column(qtbot):
    editor = _make_editor(qtbot)
    editor.expand_all()
    editor.resize(1000, 800)
    editor.show()
    qtbot.waitExposed(editor)

    switch = editor.field_widgets["session_breaks"]

    assert switch.width() <= switch.sizeHint().width()  # a stretched one would make empty space clickable
    assert switch.parentWidget().width() > 2 * switch.width()  # and the column really is much wider


# --- validation ------------------------------------------------------------------------------------------------


def test_a_range_with_low_above_high_is_an_error(qtbot):
    editor = _make_editor(qtbot)
    editor.load_profile(Profile())
    _spin(editor, "burst_range", 0).setValue(1.6)  # high is 1.25

    assert editor.is_valid() is False
    assert list(editor.errors()) == ["burst_range"]
    assert editor.errors()["burst_range"] == "Low must not be greater than high"
    assert editor.error_summary() == "Fix 1 setting before saving"
    row = editor.rows["burst_range"]
    assert not row.error_label.isHidden()
    assert row.error_label.text() == "Low must not be greater than high"
    assert all(spin.property("invalid") is True for spin in row.spin_boxes())
    with pytest.raises(ValueError, match="Low must not be greater than high"):
        editor.current_profile()


def test_fixing_the_error_clears_it(qtbot):
    editor = _make_editor(qtbot)
    editor.load_profile(Profile())
    _spin(editor, "burst_range", 0).setValue(1.6)
    _spin(editor, "burst_range", 1).setValue(2.0)

    assert editor.is_valid()
    assert editor.error_summary() == ""
    assert editor.rows["burst_range"].error_label.isHidden()
    assert not any(spin.property("invalid") for spin in editor.rows["burst_range"].spin_boxes())
    assert editor.current_profile().burst_range == (1.6, 2.0)


def test_several_errors_are_counted(qtbot):
    editor = _make_editor(qtbot)
    editor.load_profile(Profile())
    _spin(editor, "burst_range", 0).setValue(1.6)
    _spin(editor, "sentence_pause_ms", 0).setValue(4000)  # high is 1200
    assert editor.error_count() == 2
    assert editor.error_summary() == "Fix 2 settings before saving"


def test_a_profile_level_error_is_attached_to_its_field_with_the_label(qtbot, monkeypatch):
    # Allow 0 wpm in the form so Profile's own "wpm must be positive" check is what fires.
    loosened = dataclasses.replace(FIELD_INFO["wpm"], minimum=0.0)
    monkeypatch.setitem(profiles.FIELD_INFO, "wpm", loosened)
    editor = _make_editor(qtbot)
    editor.load_profile(Profile())
    _spin(editor, "wpm").setValue(0)

    assert not editor.is_valid()
    message = editor.errors()["wpm"]
    assert message.startswith("Typing speed must be positive")
    assert "wpm" not in message  # the field's label replaced its raw name
    assert not editor.rows["wpm"].error_label.isHidden()
    with pytest.raises(ValueError, match="Typing speed must be positive"):
        editor.current_profile()


def test_an_error_that_names_no_field_becomes_the_general_error(qtbot, monkeypatch):
    editor = _make_editor(qtbot)

    def explode(**_values):
        raise ValueError("something odd about the whole profile")

    monkeypatch.setattr(profile_editor, "Profile", explode)
    editor.load_profile(PRESETS["natural"])

    assert editor.general_error() == "something odd about the whole profile"
    assert editor.errors() == {}
    assert not editor.is_valid()
    assert editor.error_count() == 1
    assert editor.error_summary() == "Fix 1 setting before saving"
    with pytest.raises(ValueError, match="something odd"):
        editor.current_profile()


def test_validate_values_reports_the_range_message_once(qtbot):
    specs = [spec for _group, group_specs in field_specs() for spec in group_specs]
    values = {spec.name: getattr(Profile(), spec.name) for spec in specs}
    values["format_shortcut_ms"] = (900.0, 100.0)  # also trips Profile's own low<=high check
    errors, general = validate_values(values, specs)
    assert errors == {"format_shortcut_ms": "Low must not be greater than high"}
    assert general == ""


def test_an_error_inside_a_folded_group_opens_it(qtbot):
    editor = _make_editor(qtbot)
    editor.load_profile(Profile())
    editor.collapse_all()
    assert not editor.group_sections["Speed"].is_expanded()
    _spin(editor, "burst_range", 0).setValue(1.6)
    assert editor.group_sections["Speed"].is_expanded()


# --- dirty tracking and group badges -------------------------------------------------------------------------------


def test_editing_marks_the_editor_dirty_and_reverting_cleans_it(qtbot):
    editor = _make_editor(qtbot)
    editor.load_profile(Profile())
    assert not editor.is_dirty()
    _spin(editor, "wpm").setValue(90)
    assert editor.is_dirty()
    assert editor.changed_fields() == ["wpm"]
    _spin(editor, "wpm").setValue(65)
    assert not editor.is_dirty()


def test_loading_without_a_baseline_keeps_the_saved_state_to_compare_against(qtbot):
    editor = _make_editor(qtbot)
    editor.load_profile(PRESETS["natural"])
    editor.load_profile(PRESETS["clean"], baseline=False)
    assert editor.is_dirty()
    assert "typo_rate_per_100" in editor.changed_fields()
    editor.load_profile(PRESETS["natural"], baseline=False)
    assert not editor.is_dirty()


def test_mark_saved_makes_the_current_values_the_baseline(qtbot):
    editor = _make_editor(qtbot)
    editor.load_profile(Profile())
    _spin(editor, "wpm").setValue(90)
    editor.mark_saved()
    assert not editor.is_dirty()
    assert editor.current_profile().wpm == 90.0


def test_state_changed_fires_for_edits_and_loads(qtbot):
    editor = _make_editor(qtbot)
    with qtbot.waitSignal(editor.stateChanged, timeout=1000):
        editor.load_profile(Profile())
    with qtbot.waitSignal(editor.stateChanged, timeout=1000):
        _spin(editor, "wpm").setValue(90)


def test_a_group_header_counts_its_changed_fields(qtbot):
    editor = _make_editor(qtbot)
    editor.load_profile(Profile())
    badge = editor._badges["Speed"]
    assert badge.isHidden()
    _spin(editor, "wpm").setValue(90)
    _spin(editor, "lognormal_sigma").setValue(0.5)
    assert not badge.isHidden()
    assert badge.text() == "2 changed"
    assert badge.property("tone") == "accent"


def test_a_group_header_flags_issues_over_changes(qtbot):
    editor = _make_editor(qtbot)
    editor.load_profile(Profile())
    _spin(editor, "wpm").setValue(90)
    _spin(editor, "burst_range", 0).setValue(1.6)
    badge = editor._badges["Speed"]
    assert badge.text() == "1 issue"
    assert badge.property("tone") == "danger"


# --- hints -----------------------------------------------------------------------------------------------------------


def test_a_long_millisecond_field_shows_a_human_duration(qtbot):
    editor = _make_editor(qtbot)
    editor.load_profile(Profile(fatigue_tau_ms=1_200_000.0))
    hint = editor.rows["fatigue_tau_ms"].hint_label
    assert not hint.isHidden()
    assert "20 minutes" in hint.text()


def test_a_millisecond_range_shows_a_human_duration(qtbot):
    editor = _make_editor(qtbot)
    editor.load_profile(Profile())
    assert "7 to 15 minutes" in editor.rows["break_interval_ms"].hint_label.text()


def test_short_millisecond_values_need_no_hint(qtbot):
    editor = _make_editor(qtbot)
    editor.load_profile(Profile())
    assert editor.rows["clause_pause_ms"].hint_label.isHidden()  # 120-350 ms


@pytest.mark.parametrize(
    ("ms", "expected"),
    [
        (0, ""),
        (999, ""),
        (1000, "about 1 second"),
        (1500, "about 1.5 seconds"),
        (45_000, "about 45 seconds"),
        (90_000, "about 90 seconds"),
        (600_000, "about 10 minutes"),
        (1_200_000, "about 20 minutes"),
        (5_400_000, "about 1.5 hours"),
        (7_200_000, "about 2 hours"),
    ],
)
def test_humanize_ms(ms, expected):
    assert humanize_ms(ms) == expected


@pytest.mark.parametrize(
    ("low", "high", "expected"),
    [
        (100, 900, ""),
        (400, 1200, "about 0.4 to 1.2 seconds"),
        (420_000, 900_000, "about 7 to 15 minutes"),
        (20_000, 90_000, "about 20 to 90 seconds"),
        (45_000, 600_000, "about 45 seconds to 10 minutes"),
    ],
)
def test_humanize_ms_range(low, high, expected):
    assert humanize_ms_range(low, high) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.00125, 5), (1 / 300, 6), (65.0, 0), (7.000000000000001, 0), (0.15, 2), (-0.25, 2), (float("nan"), 0)],
)
def test_decimals_needed(value, expected):
    assert decimals_needed(value) == expected


# --- wheel safety and theming ---------------------------------------------------------------------------------------------


def _wheel_event() -> QWheelEvent:
    return QWheelEvent(
        QPointF(5, 5),
        QPointF(5, 5),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def test_scrolling_over_an_unfocused_spin_box_does_not_change_it(qtbot):
    editor = _make_editor(qtbot)
    editor.load_profile(Profile())
    spin = _spin(editor, "wpm")
    before = spin.value()
    event = _wheel_event()
    spin.wheelEvent(event)
    assert spin.value() == before
    assert event.isAccepted() is False  # handed on, so the scroll area scrolls instead


def test_scrolling_over_a_focused_spin_box_changes_it(qtbot, monkeypatch):
    editor = _make_editor(qtbot)
    editor.load_profile(Profile())
    spin = _spin(editor, "wpm")
    monkeypatch.setattr(NoWheelSpinBox, "hasFocus", lambda self: True)
    from auto_essay_typer.gui.advanced_common import NoWheelDoubleSpinBox

    monkeypatch.setattr(NoWheelDoubleSpinBox, "hasFocus", lambda self: True)
    before = spin.value()
    spin.wheelEvent(_wheel_event())
    assert spin.value() == before + spin.singleStep()


def test_the_editor_restyles_when_the_theme_changes(qtbot, qapp):
    theme_manager = ThemeManager(qapp, mode=ThemeMode.LIGHT)
    editor = _make_editor(qtbot, theme_manager)
    assert LIGHT_TOKENS.text in editor.styleSheet()
    assert LIGHT_TOKENS.border in editor.styleSheet()
    assert DARK_TOKENS.text not in editor.styleSheet()

    theme_manager.set_mode(ThemeMode.DARK)
    assert DARK_TOKENS.text in editor.styleSheet()
    assert DARK_TOKENS.border in editor.styleSheet()
    assert LIGHT_TOKENS.text not in editor.styleSheet()
    theme_manager.set_mode(ThemeMode.SYSTEM)


def test_the_spin_box_stylesheet_references_arrow_images_that_exist(qtbot):
    from auto_essay_typer.gui.advanced_common import spin_arrow_urls

    editor = _make_editor(qtbot)
    urls = spin_arrow_urls(LIGHT_TOKENS.muted_text)
    assert urls is not None
    up, down = urls
    from pathlib import Path

    assert Path(up).exists() and Path(down).exists()
    assert up in editor.styleSheet() and down in editor.styleSheet()
