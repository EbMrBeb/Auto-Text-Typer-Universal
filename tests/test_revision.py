from auto_essay_typer.actions import Backspace, KeyPress
from auto_essay_typer.planner import plan
from auto_essay_typer.profiles import Profile
from auto_essay_typer.simulator import simulate

TEXT = "The argument rests on a single premise. That premise is contested. " * 8


def test_revision_produces_backspaces_without_any_typos():
    profile = Profile(typo_rate_per_100=0.0, revision_rate_per_word=1.0)
    actions = plan(TEXT, profile, seed=11)
    assert any(isinstance(a, Backspace) for a in actions)


def test_revision_preserves_the_text_exactly():
    profile = Profile(typo_rate_per_100=0.0, revision_rate_per_word=1.0)
    assert simulate(plan(TEXT, profile, seed=11)) == TEXT


def test_revision_can_be_disabled():
    profile = Profile(typo_rate_per_100=0.0, revision_rate_per_word=0.0)
    actions = plan(TEXT, profile, seed=11)
    assert not any(isinstance(a, Backspace) for a in actions)


def test_revision_span_respects_configured_word_count():
    profile = Profile(
        typo_rate_per_100=0.0, revision_rate_per_word=1.0, revision_span_words=(2, 2)
    )
    actions = plan(TEXT, profile, seed=11)
    runs = _backspace_run_lengths(actions)
    assert runs, "expected at least one revision"
    # Two words plus their separating space; generous upper bound for long words.
    assert all(3 <= run <= 40 for run in runs)


def _backspace_run_lengths(actions) -> list[int]:
    """Lengths of consecutive backspace bursts.

    A Pause between backspaces does not end a burst -- the planner
    deliberately interleaves them to produce a realistic deletion rhythm.
    Only a KeyPress ends a burst.
    """
    runs, current = [], 0
    for action in actions:
        if isinstance(action, Backspace):
            current += action.count
        elif isinstance(action, KeyPress) and current:
            runs.append(current)
            current = 0
    if current:
        runs.append(current)
    return runs
