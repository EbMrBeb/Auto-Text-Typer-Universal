import random

import pytest

from auto_essay_typer.errors import (
    TypoKind,
    make_typo,
    plan_correction,
    sample_notice_distance,
)


def rng() -> random.Random:
    return random.Random(7)


def build(kind: TypoKind, text: str, index: int):
    return make_typo(text, index, rng(), forced_kind=kind)


def test_transposition_swaps_two_adjacent_characters():
    typo = build(TypoKind.TRANSPOSITION, "the cat", 0)
    assert typo.correct == "th"
    assert typo.wrong == "ht"


def test_substitution_uses_a_physically_adjacent_key():
    typo = build(TypoKind.SUBSTITUTION, "something", 5)
    assert typo.correct == "h"
    assert typo.wrong != "h"
    assert len(typo.wrong) == 1


def test_doubling_repeats_one_character():
    typo = build(TypoKind.DOUBLING, "the", 0)
    assert typo.correct == "t"
    assert typo.wrong == "tt"


def test_omission_types_nothing():
    typo = build(TypoKind.OMISSION, "the", 0)
    assert typo.correct == "t"
    assert typo.wrong == ""


def test_typos_are_never_placed_on_whitespace():
    for index, char in enumerate("a b\nc"):
        typo = make_typo("a b\nc", index, rng())
        if char.isspace():
            assert typo is None


def test_transposition_is_skipped_for_identical_neighbours():
    assert build(TypoKind.TRANSPOSITION, "aa", 0) is None


def test_unmodelled_characters_are_never_typo_sites():
    assert make_typo("éa", 0, rng()) is None


def test_typo_at_end_of_text_is_rejected():
    # Nothing follows, so there is no realistic way to notice it late.
    assert make_typo("a", 0, rng()) is None


def test_correction_backspaces_wrong_text_plus_tail():
    typo = build(TypoKind.DOUBLING, "the", 0)
    backspaces, retype = plan_correction(typo, tail="he")
    assert backspaces == len("tt") + len("he")
    assert retype == "the"


def test_correction_of_an_omission_backspaces_only_the_tail():
    typo = build(TypoKind.OMISSION, "the", 0)
    backspaces, retype = plan_correction(typo, tail="he")
    assert backspaces == 2
    assert retype == "the"


def test_correction_with_no_tail_is_immediate():
    typo = build(TypoKind.SUBSTITUTION, "the", 0)
    backspaces, retype = plan_correction(typo, tail="")
    assert backspaces == 1
    assert retype == "t"


def test_notice_distance_is_non_negative_and_usually_short():
    r = random.Random(99)
    samples = [sample_notice_distance(r) for _ in range(2000)]
    assert all(s >= 0 for s in samples)
    assert sum(1 for s in samples if s <= 4) / len(samples) > 0.7


def test_every_kind_is_reachable_without_forcing():
    r = random.Random(3)
    kinds = {make_typo("something clever", i % 8, r).kind for i in range(400)}
    assert kinds == set(TypoKind)
