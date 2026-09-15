"""Tests for planner.plan_document.

test_document_pipeline.py already covers the full import -> adapt -> plan ->
drive pipeline against real-shaped documents; this file exercises
plan_document more narrowly, the way test_planner.py exercises plan(): the
core invariant across many synthetic documents and seeds, plus the specific
formatting-reconciliation rules from the shared contract that a document
built by hand can pin down precisely.
"""

import random

import pytest

from auto_essay_typer.actions import Format, FormatOp, KeyPress
from auto_essay_typer.document import (
    Alignment,
    Document,
    ListKind,
    Paragraph,
    ParagraphStyle,
    Run,
    TextStyle,
)
from auto_essay_typer.planner import plan_document
from auto_essay_typer.profiles import PRESETS, Profile
from auto_essay_typer.simulator import simulate, simulate_document

BOLD = frozenset({TextStyle.BOLD})
ITALIC = frozenset({TextStyle.ITALIC})
UNDERLINE = frozenset({TextStyle.UNDERLINE})
STRIKETHROUGH = frozenset({TextStyle.STRIKETHROUGH})
PLAIN = frozenset()

# --- a corpus of synthetic (already-adapted -- no TITLE/SUBTITLE) documents,
# covering styles, alignment, nested lists, and small/mixed-style runs.
DOCS = [
    Document((Paragraph(),)),
    Document((Paragraph(runs=(Run("hello world"),)),)),
    Document(
        (
            Paragraph(runs=(Run("Heading"),), style=ParagraphStyle.HEADING_1, alignment=Alignment.CENTER),
            Paragraph(runs=(Run("Sub"),), style=ParagraphStyle.HEADING_2),
            Paragraph(runs=(Run("Body text."),)),
        )
    ),
    Document(
        (
            Paragraph(
                runs=(Run("bold", BOLD), Run(" normal "), Run("italic", ITALIC), Run(" more normal")),
            ),
        )
    ),
    Document(
        (
            Paragraph(runs=(Run("first"),), list_kind=ListKind.BULLET, list_level=0),
            Paragraph(runs=(Run("second"),), list_kind=ListKind.BULLET, list_level=1),
            Paragraph(runs=(Run("third"),), list_kind=ListKind.BULLET, list_level=2),
            Paragraph(runs=(Run("fourth"),), list_kind=ListKind.NUMBERED, list_level=2),
            Paragraph(runs=(Run("fifth"),), list_kind=ListKind.NUMBERED, list_level=0),
            Paragraph(runs=(Run("not a list item anymore"),)),
        )
    ),
    Document(
        (
            Paragraph(
                runs=(
                    Run("ab", BOLD),
                    Run("cd", ITALIC),
                    Run("ef", UNDERLINE),
                    Run("gh", STRIKETHROUGH),
                    Run("ij", BOLD | ITALIC),
                    Run("kl mno pqr", PLAIN),
                ),
            ),
        )
    ),
    Document((Paragraph(), Paragraph(runs=(Run("after a blank paragraph"),)), Paragraph())),
    Document((Paragraph(runs=(Run("deeply nested"),), list_kind=ListKind.BULLET, list_level=8),)),
]

STRESS_PROFILES = [
    *PRESETS.values(),
    Profile(typo_rate_per_100=25.0, revision_rate_per_word=0.2, wpm=140),
    Profile(typo_rate_per_100=0.0, revision_rate_per_word=0.0),
]


@pytest.mark.parametrize("doc", DOCS, ids=lambda d: str(len(d.paragraphs)) + "p")
@pytest.mark.parametrize("profile", STRESS_PROFILES, ids=lambda p: f"typo{p.typo_rate_per_100}")
def test_plan_document_reproduces_the_document_exactly(doc, profile):
    adapted = doc.normalized()
    for seed in range(15):
        actions = plan_document(adapted, profile, seed)
        assert simulate_document(actions).normalized() == adapted, (
            f"invariant broken: seed={seed} profile={profile}"
        )
        assert simulate(actions) == adapted.plain_text()


def test_invariant_holds_over_a_thousand_random_cases():
    rng = random.Random(0)
    for iteration in range(1000):
        doc = rng.choice(DOCS).normalized()
        profile = rng.choice(STRESS_PROFILES)
        seed = rng.randrange(1_000_000)
        assert simulate_document(plan_document(doc, profile, seed)).normalized() == doc, (
            f"invariant broken at iteration {iteration}: seed={seed}"
        )


def test_plan_document_is_deterministic_for_a_seed():
    doc = DOCS[3].normalized()
    assert plan_document(doc, seed=42) == plan_document(doc, seed=42)


def test_empty_document_only_clears_formatting():
    actions = plan_document(Document((Paragraph(),)), PRESETS["clean"], seed=0)
    formats = [a for a in actions if isinstance(a, Format)]
    assert formats == [Format(FormatOp.CLEAR_FORMATTING)]
    assert not any(isinstance(a, KeyPress) for a in actions)


def test_clear_formatting_is_always_the_first_format_action():
    for doc in DOCS:
        actions = plan_document(doc.normalized(), PRESETS["sloppy"], seed=3)
        first_format = next(a for a in actions if isinstance(a, Format))
        assert first_format.op is FormatOp.CLEAR_FORMATTING


def test_style_is_re_asserted_after_a_non_normal_paragraph_even_back_to_normal():
    # Heading paragraph followed by a NORMAL one: SET_NORMAL must be emitted
    # explicitly (the contract requires this whether or not real Docs resets
    # headings on Enter), not just skipped because NORMAL is the model default.
    doc = Document(
        (
            Paragraph(runs=(Run("Heading"),), style=ParagraphStyle.HEADING_1),
            Paragraph(runs=(Run("Back to normal"),)),
        )
    )
    actions = plan_document(doc, PRESETS["clean"], seed=0)
    ops = [a.op for a in actions if isinstance(a, Format)]
    assert FormatOp.SET_HEADING_1 in ops
    assert ops.count(FormatOp.SET_NORMAL) == 1


def test_list_kind_switch_keeps_the_nesting_level():
    # Switching BULLET -> NUMBERED at level 2 must toggle once and keep the
    # level (see simulator._toggle_list) -- no OUTDENT/INDENT pair needed.
    doc = Document(
        (
            Paragraph(runs=(Run("a"),), list_kind=ListKind.BULLET, list_level=2),
            Paragraph(runs=(Run("b"),), list_kind=ListKind.NUMBERED, list_level=2),
        )
    )
    actions = plan_document(doc, PRESETS["clean"], seed=0)
    assert simulate_document(actions).normalized() == doc
    ops = [a.op for a in actions if isinstance(a, Format)]
    # The only INDENTs at all are the two that establish paragraph 1's own
    # level 2 from scratch; the kind switch for paragraph 2 adds none.
    assert ops.count(FormatOp.INDENT) == 2
    assert FormatOp.OUTDENT not in ops
    assert ops.count(FormatOp.TOGGLE_BULLET_LIST) == 1
    assert ops.count(FormatOp.TOGGLE_NUMBERED_LIST) == 1


def test_revision_reaching_back_through_a_run_boundary_restores_the_style():
    # Engineered so the revision boundary check (a sentence-ending "." right
    # before the cursor) fires at run2's own second character, and a
    # single-word span walks it back to index 0 of run2 -- exactly the case
    # the contract calls out: the correction re-exposes run1's style, so the
    # retype must re-toggle back to run2's own style before typing again.
    profile = Profile(
        typo_rate_per_100=0.0, revision_rate_per_word=1.0, revision_span_words=(1, 1)
    )
    doc = Document(
        (
            Paragraph(runs=(Run("Ready", BOLD), Run(". Go now", PLAIN))),
        )
    )
    actions = plan_document(doc, profile, seed=0)

    assert simulate_document(actions).normalized() == doc
    assert simulate(actions) == doc.plain_text()
    # The mechanism actually fired: a Backspace happened before the retype.
    from auto_essay_typer.actions import Backspace

    assert any(isinstance(a, Backspace) for a in actions)
