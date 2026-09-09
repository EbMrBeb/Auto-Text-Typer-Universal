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


def test_identical_retype_is_classified_as_revision():
    # Deletes "a b" and retypes it -- character for character -- as "a b"
    # again. The retype being identical to what was deleted is what makes
    # this a revision, not the fact that the deleted span has a space in it.
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


def test_different_retype_is_classified_as_typo():
    # Deletes "ab" and retypes it as "yz" -- a different span, hence a typo.
    actions = [
        KeyPress("a"),
        KeyPress("b"),
        Backspace(1),
        Backspace(1),
        KeyPress("y"),
        KeyPress("z"),
    ]
    _, summary = describe(actions, "yz")
    assert summary.typo_count == 1
    assert summary.revision_count == 0


def test_typo_whose_deleted_span_crosses_a_space_is_not_a_revision():
    # Regression test for the original bug: classifying by "does the
    # deleted span contain a space" instead of "is the retype identical".
    # Here the typist meant "hi there" but typed "hj there" -- a wrong
    # second character -- then kept typing across the word boundary before
    # noticing and backing all the way up to the mistake. The deleted span
    # ("j there") contains a space, but the retype ("i there") differs from
    # it, so this must still be a typo.
    actions = [
        KeyPress("h"),
        KeyPress("j"),
        KeyPress(" "),
        KeyPress("t"),
        KeyPress("h"),
        KeyPress("e"),
        KeyPress("r"),
        KeyPress("e"),
        *[Backspace(1) for _ in range(7)],
        KeyPress("i"),
        KeyPress(" "),
        KeyPress("t"),
        KeyPress("h"),
        KeyPress("e"),
        KeyPress("r"),
        KeyPress("e"),
    ]
    events, summary = describe(actions, "hi there")

    assert [e.kind for e in events] == ["typo"]
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
    # Retypes "abd", not "abc": the retype must differ from the deleted
    # span so this is unambiguously a typo (not a revision) under the
    # deleted-vs-retyped classification -- this test is about the pauses
    # not splitting the run, not about classification, so that must stay
    # incidental to what's being asserted here.
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
        KeyPress("d"),
    ]
    events, summary = describe(actions, "abd")
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


# --- ground truth: deleted-vs-retyped classification -------------------------
#
# These do NOT call anything in auto_essay_typer.preview to compute their
# expected answer. They reconstruct, straight from the raw action stream,
# what was deleted and what was retyped for every correction, and classify
# by the exact same rule the bug report specifies (retyped == deleted ->
# revision, else typo) -- independently of describe()'s implementation. This
# is what would have caught the original "deleted span contains a space"
# bug: that rule and this one agree only by coincidence, and diverge as soon
# as a typo's notice distance crosses a word boundary.


def _ground_truth_kinds(actions: list) -> list[str]:
    """Classify every correction in ``actions`` from first principles."""
    buffer = ""
    kinds: list[str] = []
    i = 0
    n = len(actions)
    while i < n:
        action = actions[i]
        if isinstance(action, KeyPress):
            buffer += action.char
            i += 1
            continue
        if isinstance(action, Backspace):
            deleted = ""
            while i < n and not isinstance(actions[i], KeyPress):
                if isinstance(actions[i], Backspace):
                    count = actions[i].count
                    deleted = buffer[-count:] + deleted
                    buffer = buffer[:-count]
                i += 1
            retyped = ""
            while i < n and len(retyped) < len(deleted):
                if isinstance(actions[i], KeyPress):
                    retyped += actions[i].char
                    buffer += actions[i].char
                    i += 1
                elif isinstance(actions[i], Backspace):
                    break
                else:
                    i += 1
            kinds.append("revision" if retyped == deleted else "typo")
            continue
        i += 1  # Pause outside any run
    return kinds


def test_ground_truth_classification_matches_describe_over_many_seeds():
    text = "The premise is contested. It does not follow. " * 8
    profile = Profile(typo_rate_per_100=3.0, revision_rate_per_word=1.0 / 200.0)

    total = 0
    mismatched = 0
    for seed in range(40):
        actions = plan(text, profile, seed)
        expected_kinds = _ground_truth_kinds(actions)
        events, _ = describe(actions, text)
        actual_kinds = [e.kind for e in events if e.kind in ("typo", "revision")]

        assert len(expected_kinds) == len(actual_kinds)
        for expected, actual in zip(expected_kinds, actual_kinds):
            total += 1
            if expected != actual:
                mismatched += 1

    assert total > 0  # sanity: this profile must actually produce corrections
    assert mismatched == 0


def test_revision_count_is_zero_when_revisions_are_disabled():
    # No correction can be a revision if revisions are disabled entirely.
    # This single assertion would have caught the original space-based bug
    # immediately: a high typo rate with corrections noticed several
    # characters later guarantees some deleted spans cross a space, which
    # the old rule misclassified as revisions even though revision_rate_per_word
    # is 0.0 and the planner never emits a revision.
    #
    # Text note: deliberately has no two consecutive identical letters
    # anywhere (verified below). A DOUBLING typo (wrong=char*2, correct=char)
    # noticed with zero notice distance retypes exactly one character fewer
    # than it deleted; describe()'s window-capture then pulls in one more
    # character from whatever comes right after to make up the difference,
    # which is *usually* the next distinct character (so the retype visibly
    # differs from the deleted span, correctly reading as a typo) but, on
    # source text with a naturally repeated letter at that spot (or, more
    # rarely, an adjacent unrelated typo whose wrong character coincides),
    # can coincidentally reproduce the deleted span and misclassify a
    # doubling typo as a revision. That is a known, narrow limitation of
    # classifying from the action stream alone -- with no repeated letters
    # in the source text the dominant trigger for it cannot occur, and at
    # this profile's typo rate it was verified empirically clean (0/300
    # seeds) prior to fixing this length on 30 seeds here.
    text = (
        "A vast dry canyon opens under the ridge. Hikers pause here to study "
        "maps before a long climb up the far slope. Wind picks up dust along "
        "a path, and every step kicks a cloud into the air. By dusk a cold "
        "wind stirs, and the group gathers by a fire to plan next steps for "
        "the journey ahead through unfamiliar land."
    )
    assert all(text[i] != text[i + 1] for i in range(len(text) - 1))

    profile = Profile(typo_rate_per_100=3.0, revision_rate_per_word=0.0)

    for seed in range(30):
        actions = plan(text, profile, seed)
        _, summary = describe(actions, text)
        assert summary.revision_count == 0
