import random

import pytest

from auto_essay_typer.planner import plan
from auto_essay_typer.profiles import PRESETS, Profile
from auto_essay_typer.simulator import simulate

CORPUS = [
    "",
    "a",
    "ab",
    "the",
    "aaa aaa aaa",
    "The quick brown fox jumps over the lazy dog.",
    "Hello, world! How are you? I am fine; thanks.",
    "one\ntwo\nthree",
    "para one.\n\npara two.\n\npara three.",
    "\n\n\n",
    "   leading and trailing   ",
    "MiXeD CaSe WiTh CAPS",
    "symbols: !@#$%^&*()_+-={}[]|\\:;\"'<>,.?/~`",
    "digits 0123456789 and 42 and 3.14159",
    "café — naïve résumé; 日本語 mixed in",
    "hyphenated-words and some_snake_case and CamelCase",
    "a" * 200,
    "supercalifragilisticexpialidocious antidisestablishmentarianism",
    "Tabs\there\tand\tthere.",
    "The argument rests on a single premise. That premise is contested. " * 5,
]

# The most punishing settings we ever expose, plus a deliberately absurd one.
STRESS_PROFILES = [
    *PRESETS.values(),
    Profile(typo_rate_per_100=25.0, revision_rate_per_word=0.1, wpm=140),
    Profile(typo_rate_per_100=0.0, revision_rate_per_word=0.0, wpm=20),
]


@pytest.mark.parametrize("text", CORPUS)
@pytest.mark.parametrize("profile", STRESS_PROFILES, ids=lambda p: f"typo{p.typo_rate_per_100}")
def test_plan_reproduces_text_exactly(text, profile):
    for seed in range(40):
        assert simulate(plan(text, profile, seed)) == text, (
            f"invariant broken: seed={seed} profile={profile} text={text!r}"
        )


def test_invariant_holds_over_ten_thousand_random_cases():
    rng = random.Random(0)
    for iteration in range(10_000):
        text = rng.choice(CORPUS)
        profile = rng.choice(STRESS_PROFILES)
        seed = rng.randrange(1_000_000)
        assert simulate(plan(text, profile, seed)) == text, (
            f"invariant broken at iteration {iteration}: seed={seed} text={text!r}"
        )


def test_invariant_holds_on_randomly_generated_text():
    rng = random.Random(1)
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFG 123.,!?;:'\"-\n\t"
    for _ in range(2000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 120)))
        profile = rng.choice(STRESS_PROFILES)
        assert simulate(plan(text, profile, rng.randrange(10_000))) == text


def test_no_plan_ever_backspaces_past_the_start():
    # simulate() raises on underflow, so reaching the end without an exception
    # is the assertion. Text starting mid-word is the risky case.
    rng = random.Random(2)
    for _ in range(2000):
        text = rng.choice(["x", "xy", "the cat", "a. b. c.", "\nx"])
        simulate(plan(text, rng.choice(STRESS_PROFILES), rng.randrange(10_000)))


def test_adversarial_inputs():
    # Inputs chosen to stress span/index arithmetic in the planner: boundary
    # lengths, leading punctuation/whitespace, degenerate runs, and non-BMP
    # characters that can trip up naive character-counting.
    adversarial_texts = [
        "x",
        "x\n",
        ". hello",
        "  x",
        "\n\nx",
        "aaaaaaaa",
        ".....",
        "...",
        "?!?!",
        "a" * 300,
        "     ",
        "aAaAaAaA",
        "!@#$!@#$",
        "hi \U0001f44b there",
    ]

    for text in adversarial_texts:
        for profile in STRESS_PROFILES:
            for seed in range(50):
                assert simulate(plan(text, profile, seed)) == text, (
                    f"invariant broken: seed={seed} profile={profile} text={text!r}"
                )
