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


def test_substitution_on_a_letter_never_produces_a_non_letter_slip():
    text = "e r d s wordy clever raspy design"
    for seed in range(50):
        r = random.Random(seed)
        for index, char in enumerate(text):
            if not char.isalpha():
                continue
            typo = make_typo(text, index, r, forced_kind=TypoKind.SUBSTITUTION)
            if typo is not None:
                assert typo.wrong.isalpha()


def test_substitution_on_an_uppercase_letter_yields_an_uppercase_slip():
    text = "Really Super Discs Extra"
    found_one = False
    for seed in range(50):
        r = random.Random(seed)
        for index, char in enumerate(text):
            if not (char.isalpha() and char.isupper()):
                continue
            typo = make_typo(text, index, r, forced_kind=TypoKind.SUBSTITUTION)
            if typo is not None:
                found_one = True
                assert typo.wrong.isalpha()
                assert typo.wrong.isupper()
    assert found_one


def test_substitution_on_a_digit_never_produces_a_letter_slip():
    text = "call 4567890 now"
    found_one = False
    for seed in range(50):
        r = random.Random(seed)
        for index, char in enumerate(text):
            if not char.isdigit():
                continue
            typo = make_typo(text, index, r, forced_kind=TypoKind.SUBSTITUTION)
            if typo is not None:
                found_one = True
                assert typo.wrong.isdigit()
    assert found_one


def test_substitution_on_punctuation_keeps_unfiltered_neighbours():
    # ',' neighbours ['k', 'l', 'm', '.'] -- a mix of letters and punctuation.
    # Unlike letters/digits, punctuation slips are left unrestricted, so this
    # should still produce substitutions rather than always falling through
    # to None.
    text = "wait, really?"
    index = text.index(",")
    hits = 0
    for seed in range(50):
        r = random.Random(seed)
        typo = make_typo(text, index, r, forced_kind=TypoKind.SUBSTITUTION)
        if typo is not None:
            hits += 1
    assert hits > 0


def test_e_never_slips_to_4():
    text = "something clever and rather wordy"
    index = text.index("e")
    for seed in range(500):
        r = random.Random(seed)
        typo = make_typo(text, index, r, forced_kind=TypoKind.SUBSTITUTION)
        if typo is not None:
            assert typo.wrong != "4"
