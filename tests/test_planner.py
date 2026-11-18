import pytest

from auto_essay_typer.actions import Backspace, KeyPress, Pause
from auto_essay_typer.planner import plan
from auto_essay_typer.profiles import Profile
from auto_essay_typer.simulator import simulate, total_duration_ms

CLEAN = Profile(typo_rate_per_100=0.0, revision_rate_per_word=0.0)


def test_clean_profile_types_text_verbatim():
    actions = plan("hello world", CLEAN, seed=1)
    assert simulate(actions) == "hello world"


def test_clean_profile_never_backspaces():
    actions = plan("hello world", CLEAN, seed=1)
    assert not any(isinstance(a, Backspace) for a in actions)


def test_every_keypress_is_preceded_by_a_pause():
    actions = plan("abc", CLEAN, seed=1)
    for index, action in enumerate(actions):
        if isinstance(action, KeyPress):
            assert index > 0 and isinstance(actions[index - 1], Pause)


def test_planning_is_deterministic_for_a_seed():
    assert plan("the quick brown fox", seed=42) == plan("the quick brown fox", seed=42)


def test_different_seeds_produce_different_plans():
    text = "the quick brown fox jumps over the lazy dog"
    assert plan(text, seed=1) != plan(text, seed=2)


def test_typos_are_always_corrected():
    text = "the quick brown fox jumps over the lazy dog. " * 10
    noisy = Profile(typo_rate_per_100=8.0, revision_rate_per_word=0.0)
    actions = plan(text, noisy, seed=5)
    assert any(isinstance(a, Backspace) for a in actions)
    assert simulate(actions) == text


def test_empty_text_produces_no_actions():
    assert plan("", CLEAN, seed=1) == []


def test_sentence_boundary_inserts_a_long_pause():
    actions = plan("Hi. Go", CLEAN, seed=3)
    assert any(isinstance(a, Pause) and a.ms >= 400 for a in actions)


def test_duration_is_broadly_consistent_with_wpm():
    text = "the quick brown fox jumps over the lazy dog " * 12
    profile = Profile(wpm=60, typo_rate_per_100=0.0, revision_rate_per_word=0.0)
    minutes = total_duration_ms(plan(text, profile, seed=9)) / 60_000.0
    observed_wpm = (len(text) / 5.0) / minutes
    assert 45 < observed_wpm < 75


def test_newlines_and_tabs_survive():
    text = "one\n\ttwo\nthree"
    assert simulate(plan(text, CLEAN, seed=4)) == text


def test_unmodelled_characters_survive():
    text = "café — naïve résumé"
    assert simulate(plan(text, CLEAN, seed=4)) == text
