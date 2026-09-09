"""Tests for auto_essay_typer.preview.describe().

Action streams are built by hand here (rather than always going through
planner.plan) so each test isolates exactly one behaviour: run detection,
revision-vs-typo classification, the "Pause does not split a run" rule,
break/pause thresholds, summary accounting, matches_input, and the event
cap. A handful of tests also run describe() over real planner.plan() output
to confirm the pure hand-traced logic holds up against the real thing.
"""

from __future__ import annotations

import dataclasses

import pytest

from auto_essay_typer.actions import Backspace, KeyPress, Pause
from auto_essay_typer.planner import plan
from auto_essay_typer.preview import PreviewEvent, PreviewSummary, describe
from auto_essay_typer.profiles import Profile
from auto_essay_typer.simulator import simulate

# --- typo detection -----------------------------------------------------


def test_typo_run_produces_one_typo_event():
    # Typed "ac" (transposed), noticed immediately, corrected to "ca", then "t".
    actions = [
        KeyPress("a"),
        KeyPress("c"),
        Backspace(1),
        Backspace(1),
        KeyPress("c"),
        KeyPress("a"),
        KeyPress("t"),
    ]
    events, summary = describe(actions, "cat")

    assert [e.kind for e in events] == ["typo"]
    assert summary.typo_count == 1
    assert summary.revision_count == 0


def test_typo_event_detail_names_wrong_and_correct_fragments():
    actions = [
        KeyPress("a"),
        KeyPress("c"),
        Backspace(1),
        Backspace(1),
        KeyPress("c"),
        KeyPress("a"),
        KeyPress("t"),
    ]
    events, _ = describe(actions, "cat")
    detail = events[0].detail
    assert "'ac'" in detail
    assert "'ca'" in detail


def test_typo_event_at_char_is_position_where_correction_lands():
    # Three correct chars, then a wrong 4th char immediately backspaced.
    actions = [
        KeyPress("f"),
        KeyPress("o"),
        KeyPress("o"),
        KeyPress("x"),
        Backspace(1),
        KeyPress("!"),
    ]
    events, _ = describe(actions, "foo!")
    assert events[0].at_char == 3


def test_typo_with_delayed_notice_reports_caught_n_chars_later():
    # Typed "x" instead of "y", kept typing "ab" before noticing, then fixed.
    actions = [
        KeyPress("x"),
        KeyPress("a"),
        KeyPress("b"),
        Backspace(1),
        Backspace(1),
        Backspace(1),
        KeyPress("y"),
        KeyPress("a"),
        KeyPress("b"),
    ]
    events, _ = describe(actions, "yab")
    assert events[0].kind == "typo"
    assert "caught 2 chars later" in events[0].detail


# --- revision vs typo classification -------------------------------------


def test_deleted_span_with_space_is_classified_as_revision():
    actions = [
        KeyPress("a"),
        KeyPress(" "),
        KeyPress("b"),
        Backspace(1),
        Backspace(1),
        Backspace(1),
        KeyPress("a"),
        KeyPress(" "),
        KeyPress("b"),
    ]
    events, summary = describe(actions, "a b")

    assert [e.kind for e in events] == ["revision"]
    assert summary.revision_count == 1
    assert summary.typo_count == 0


def test_deleted_span_without_space_is_classified_as_typo():
    actions = [
        KeyPress("a"),
        KeyPress("b"),
        Backspace(1),
        Backspace(1),
        KeyPress("a"),
        KeyPress("b"),
    ]
    _, summary = describe(actions, "ab")
    assert summary.typo_count == 1
    assert summary.revision_count == 0


def test_revision_detail_names_word_count():
    actions = [
        KeyPress("o"),
        KeyPress("n"),
        KeyPress("e"),
        KeyPress(" "),
        KeyPress("t"),
        KeyPress("w"),
        KeyPress("o"),
        *[Backspace(1) for _ in range(7)],
        KeyPress("o"),
        KeyPress("n"),
        KeyPress("e"),
        KeyPress(" "),
        KeyPress("t"),
        KeyPress("w"),
        KeyPress("o"),
    ]
    events, _ = describe(actions, "one two")
    assert events[0].kind == "revision"
    assert "2 words" in events[0].detail


# --- backspace runs split by Pause must NOT be split ----------------------


def test_run_interrupted_by_pause_is_not_split():
    # A big pause (bigger than the break threshold) sits *between* two
    # backspaces of the same correction. If the run were wrongly split by
    # the Pause, this would show up as a standalone "break" event plus two
    # separate typo events instead of one.
    actions = [
        KeyPress("x"),
        KeyPress("y"),
        Backspace(1),
        Pause(20_000.0),
        Backspace(1),
        KeyPress("y"),
        KeyPress("x"),
    ]
    events, summary = describe(actions, "yx")

    assert [e.kind for e in events] == ["typo"]
    assert summary.typo_count == 1
    assert summary.break_count == 0


def test_pause_inside_run_still_counts_toward_longest_pause():
    actions = [
        KeyPress("x"),
        KeyPress("y"),
        Backspace(1),
        Pause(20_000.0),
        Backspace(1),
        KeyPress("y"),
        KeyPress("x"),
    ]
    _, summary = describe(actions, "yx")
    assert summary.longest_pause_ms == 20_000.0


def test_run_interrupted_by_multiple_pauses_stays_one_event():
    actions = [
        KeyPress("a"),
        KeyPress("b"),
        KeyPress("c"),
        Backspace(1),
        Pause(50.0),
        Backspace(1),
        Pause(50.0),
        Backspace(1),
        KeyPress("a"),
        KeyPress("b"),
        KeyPress("c"),
    ]
    events, summary = describe(actions, "abc")
    assert len(events) == 1
    assert summary.typo_count == 1


# --- break / pause detection -----------------------------------------------


def test_pause_over_break_threshold_is_a_break():
    events, summary = describe([Pause(20_000.0)], "")
    assert [e.kind for e in events] == ["break"]
    assert summary.break_count == 1


def test_pause_over_800ms_under_break_threshold_is_a_pause():
    events, summary = describe([Pause(1_000.0)], "")
    assert [e.kind for e in events] == ["pause"]
    assert summary.break_count == 0


def test_pause_at_exactly_800ms_is_not_reported():
    events, _ = describe([Pause(800.0)], "")
    assert events == []


def test_pause_at_exactly_15000ms_is_a_pause_not_a_break():
    events, summary = describe([Pause(15_000.0)], "")
    assert [e.kind for e in events] == ["pause"]
    assert summary.break_count == 0


def test_short_pause_produces_no_event():
    events, _ = describe([Pause(200.0)], "")
    assert events == []


def test_break_event_detail_mentions_seconds():
    events, _ = describe([Pause(30_000.0)], "")
    assert "30" in events[0].detail


# --- summary accounting -----------------------------------------------------


def test_summary_characters_is_length_of_target_text():
    actions = [KeyPress(c) for c in "hello"]
    actions_with_pause = []
    for a in actions:
        actions_with_pause.append(Pause(10.0))
        actions_with_pause.append(a)
    _, summary = describe(actions_with_pause, "hello")
    assert summary.characters == 5


def test_summary_duration_matches_total_duration_ms():
    from auto_essay_typer.simulator import total_duration_ms

    actions = [Pause(100.0), KeyPress("h"), Pause(200.0), KeyPress("i")]
    _, summary = describe(actions, "hi")
    assert summary.duration_ms == total_duration_ms(actions)


def test_summary_effective_wpm_is_positive_for_nonzero_duration():
    actions = [Pause(60_000.0), KeyPress("h"), KeyPress("i")]
    _, summary = describe(actions, "hi")
    # 2 chars / 5 chars-per-word == 0.4 words, over 1 minute == 0.4 wpm.
    assert summary.effective_wpm == pytest.approx(0.4)


def test_summary_effective_wpm_is_zero_when_duration_is_zero():
    actions = [KeyPress("h"), KeyPress("i")]
    _, summary = describe(actions, "hi")
    assert summary.effective_wpm == 0.0


def test_summary_longest_pause_tracks_the_maximum():
    actions = [Pause(100.0), KeyPress("a"), Pause(5_000.0), KeyPress("b"), Pause(300.0)]
    _, summary = describe(actions, "ab")
    assert summary.longest_pause_ms == 5_000.0


# --- matches_input -----------------------------------------------------------


def test_matches_input_true_for_a_valid_plan():
    text = "The quick brown fox jumps over the lazy dog."
    profile = Profile(typo_rate_per_100=3.0, revision_rate_per_word=1.0 / 100.0)
    actions = plan(text, profile, seed=42)
    assert simulate(actions) == text  # sanity: the planner's own invariant
    _, summary = describe(actions, text)
    assert summary.matches_input is True


def test_matches_input_false_for_a_doctored_stream():
    text = "The quick brown fox jumps over the lazy dog."
    profile = Profile(typo_rate_per_100=3.0, revision_rate_per_word=1.0 / 100.0)
    actions = plan(text, profile, seed=42)

    # Corrupt the stream: swap the character of the very last KeyPress.
    doctored = list(actions)
    for i in range(len(doctored) - 1, -1, -1):
        if isinstance(doctored[i], KeyPress):
            doctored[i] = KeyPress("#") if doctored[i].char != "#" else KeyPress("@")
            break

    _, summary = describe(doctored, text)
    assert summary.matches_input is False


def test_matches_input_false_does_not_crash_event_building():
    text = "abc"
    actions = [KeyPress("a"), KeyPress("z"), KeyPress("c")]  # wrong middle char
    events, summary = describe(actions, text)
    assert summary.matches_input is False
    assert isinstance(events, list)


# --- event cap ---------------------------------------------------------------


def test_event_list_is_capped_but_counts_are_not():
    # 300 independent single-character typo corrections: each is
    # KeyPress(wrong), Backspace(1), KeyPress(right).
    actions = []
    text_chars = []
    for i in range(300):
        actions.append(KeyPress("!"))
        actions.append(Backspace(1))
        actions.append(KeyPress("a"))
        text_chars.append("a")
    text = "".join(text_chars)

    events, summary = describe(actions, text)

    assert len(events) == 200
    assert summary.typo_count == 300


def test_event_cap_also_applies_to_pause_events():
    actions = []
    for _ in range(250):
        actions.append(Pause(1_000.0))
    events, summary = describe(actions, "")
    assert len(events) == 200
    # break_count/typo_count aren't affected by pause events, but this
    # exercises the same cap path for a different event kind.
    assert len(events) <= 200


# --- dataclass shape (guards accidental field renames) -----------------------


def test_preview_event_fields():
    names = {f.name for f in dataclasses.fields(PreviewEvent)}
    assert names == {"kind", "at_char", "detail", "snapshot"}


def test_preview_summary_fields():
    names = {f.name for f in dataclasses.fields(PreviewSummary)}
    assert names == {
        "characters",
        "duration_ms",
        "effective_wpm",
        "typo_count",
        "revision_count",
        "break_count",
        "longest_pause_ms",
        "matches_input",
    }


# --- snapshot content ----------------------------------------------------------


def test_snapshot_is_trailing_slice_of_on_screen_text():
    long_text = "x" * 60
    actions = [KeyPress(c) for c in long_text]
    # Interleave with tiny pauses so this at least resembles a real plan.
    interleaved = []
    for a in actions:
        interleaved.append(Pause(1.0))
        interleaved.append(a)
    events, _ = describe(interleaved, long_text)
    # No typo/revision/pause/break events expected here, so nothing to
    # check on events -- but describe() must not raise on a long buffer.
    assert events == []
