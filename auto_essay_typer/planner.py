"""Turns text into an action stream.

Pure and seeded: no I/O, no clock, no keyboard. Given the same
``(text, profile, seed)`` this returns an identical list of actions, which is
what makes the core invariant checkable by pure computation and makes any
observed behaviour reproducible for debugging.

``plan_document`` extends the same idea to a formatted ``Document``: it reuses
the exact same per-segment typing machinery as ``plan`` (via ``_type_segment``)
for each run's text, and additionally emits ``Format`` actions to reproduce
paragraph styles, alignment, lists, and character styles by pressing Google
Docs keyboard shortcuts. ``simulator.simulate_document`` is the single source
of truth for how those shortcuts behave; ``plan_document`` tracks the exact
same state machine so a plan it builds is guaranteed to replay back to the
document it was built from (see ``tests/test_document_pipeline.py`` and the
project's Google Docs editing model).
"""

import random
from collections.abc import Callable

from auto_essay_typer.actions import Action, Backspace, Format, FormatOp, KeyPress, Pause
from auto_essay_typer.document import Alignment, Document, ListKind, ParagraphStyle, TextStyle
from auto_essay_typer.errors import make_typo, plan_correction, sample_notice_distance
from auto_essay_typer.profiles import Profile
from auto_essay_typer.timing import TimingModel


def _word_at(text: str, index: int) -> str:
    """The whole word starting at ``index``, or '' if mid-word.

    Used to decide whether an upcoming word is long enough to hesitate before.
    """
    if index > 0 and not text[index - 1].isspace():
        return ""
    end = index
    while end < len(text) and not text[end].isspace():
        end += 1
    return text[index:end]


def _at_sentence_or_paragraph_boundary(
    text: str, index: int, previous: str | None
) -> bool:
    """True at the start of a new sentence or paragraph -- the only place a
    session break may land, so a break never lands mid-word.

    Mirrors the boundary test the revision block uses to find a safe place
    to back up over already-typed text: ``previous`` is the space (or the
    ``None`` used right after a reset) that follows sentence-ending
    punctuation. A paragraph break is simpler -- any newline qualifies,
    the same boundary ``thinking_pause_ms`` already treats specially.
    """
    if previous == "\n":
        return True
    return (
        previous in (" ", None)
        and index > 0
        and text[index - 2 : index - 1] in (".", "?", "!")
    )


def _revision_span_start(text: str, end: int, words: int) -> int:
    """Index of the start of the last ``words`` words ending at ``end``.

    Walks backwards over word/space pairs. Never crosses a line break, so a
    revision stays within the paragraph being written -- and, since a caller
    that types one run at a time passes only that run's own text, this never
    crosses a run boundary either: there is nothing before index 0 to see.
    """
    index = end
    for _ in range(words):
        while index > 0 and text[index - 1].isspace():
            if text[index - 1] == "\n":
                return index
            index -= 1
        while index > 0 and not text[index - 1].isspace():
            index -= 1
    return index


def _type_segment(
    text: str,
    rng: random.Random,
    timing: TimingModel,
    profile: Profile,
    actions: list[Action],
    previous: str | None,
    next_break_at: float | None,
    on_backspace_to_start: Callable[[], None] | None = None,
) -> tuple[str | None, float | None]:
    """Append the action stream that types ``text`` in a human-like way.

    This is the machinery shared by ``plan`` (called once, over the whole
    input) and ``plan_document`` (called once per run, over just that run's
    own text): timing, typos with notice distance, revisions, thinking
    pauses, session breaks, and fatigue all live here so the two planners can
    never drift apart.

    A revision never looks, and a typo's correction never backspaces,
    further back than index 0 of ``text`` itself -- so a caller that passes
    only one run's own text gets run-boundary safety for free, with no
    special-casing needed: there is simply nothing before index 0 to reach
    into. ``on_backspace_to_start``, if given, is called immediately after
    any backspace run that empties ``text``'s typed-so-far content all the
    way back to index 0; ``plan_document`` uses it to restore the run's
    character styles before the retype that follows, since in the real
    Google Docs editing model that backspace exposes whatever formatting
    precedes this run (a person notices and re-applies it, e.g. re-pressing
    cmd+b).

    Returns the updated ``(previous, next_break_at)`` so the caller can carry
    them into the next segment.
    """
    typo_probability = profile.typo_rate_per_100 / 100.0

    def emit(char: str, care: float = 1.0) -> None:
        nonlocal previous
        interval = timing.keystroke_interval_ms(previous, char) * care
        actions.append(Pause(interval))
        actions.append(KeyPress(char))
        previous = char

    index = 0
    while index < len(text):
        thinking = timing.thinking_pause_ms(previous, _word_at(text, index))
        if thinking > 0.0:
            actions.append(Pause(thinking))

        # A session break fires only once enough simulated typing time has
        # elapsed, and only at a sentence/paragraph boundary -- never
        # mid-word. It appends nothing but a Pause, so it cannot affect the
        # simulate(plan(text)) == text invariant.
        if (
            next_break_at is not None
            and timing.elapsed_ms >= next_break_at
            and _at_sentence_or_paragraph_boundary(text, index, previous)
        ):
            if rng.random() < profile.long_break_probability:
                break_ms = rng.uniform(*profile.long_break_ms)
            else:
                break_ms = rng.uniform(*profile.short_break_ms)
            actions.append(Pause(break_ms))
            timing.rest(break_ms * profile.break_fatigue_recovery)
            timing.reset_burst(profile.post_break_burst)
            previous = None
            next_break_at = timing.elapsed_ms + rng.uniform(*profile.break_interval_ms)
            continue

        # A revision fires only at a sentence boundary, once enough text exists
        # to back over. The span is retyped identically -- wording never changes.
        if (
            previous in (" ", None)
            and index > 0
            and text[index - 2 : index - 1] in (".", "?", "!")
            and rng.random() < profile.revision_rate_per_word
        ):
            words = rng.randint(*profile.revision_span_words)
            start = _revision_span_start(text, index, words)
            span = text[start:index]
            if span.strip():
                actions.append(Pause(rng.uniform(*profile.sentence_pause_ms)))
                for _ in range(len(span)):
                    actions.append(Pause(timing.backspace_interval_ms()))
                    actions.append(Backspace(1))
                previous = None
                if start == 0 and on_backspace_to_start is not None:
                    on_backspace_to_start()
                for char in span:
                    emit(char, care=profile.retype_care_factor)

        typo = None
        if rng.random() < typo_probability:
            typo = make_typo(text, index, rng)

        if typo is None:
            emit(text[index])
            index += 1
            continue

        for char in typo.wrong:
            emit(char)

        # Keep typing correctly until the mistake is noticed. The tail stops
        # at a line break -- nobody starts a new paragraph without
        # re-reading -- and, for a single run's text, at the run's own end
        # for the same reason: ``text`` is already bounded to it.
        consumed = len(typo.correct)
        distance = sample_notice_distance(rng)
        tail = text[index + consumed : index + consumed + distance]
        if "\n" in tail:
            tail = tail[: tail.index("\n")]
        for char in tail:
            emit(char)

        actions.append(Pause(rng.uniform(*profile.oh_moment_ms)))

        backspaces, retype = plan_correction(typo, tail)
        for _ in range(backspaces):
            actions.append(Pause(timing.backspace_interval_ms()))
            actions.append(Backspace(1))

        # Backspacing resets the digraph context: the last key struck was not
        # a character key.
        previous = None
        # A zero-backspace correction (an omission typo noticed with zero
        # notice distance) never actually typed or removed anything, so it
        # never touched the model even when index == 0 -- only a *real*
        # backspace run can have exposed the previous run's styles.
        if index == 0 and backspaces > 0 and on_backspace_to_start is not None:
            on_backspace_to_start()
        for char in retype:
            emit(char, care=profile.retype_care_factor)

        index += consumed + len(tail)

    return previous, next_break_at


def plan(
    text: str,
    profile: Profile | None = None,
    seed: int | None = None,
) -> list[Action]:
    """Return the action stream that types ``text`` in a human-like way."""
    profile = profile or Profile()
    rng = random.Random(seed)
    timing = TimingModel(profile, rng)
    actions: list[Action] = []
    next_break_at = rng.uniform(*profile.break_interval_ms) if profile.session_breaks else None
    _type_segment(text, rng, timing, profile, actions, None, next_break_at)
    return actions


# -- plan_document: abstract style/alignment/list -> FormatOp lookups -------
#
# Small, local mirrors of the same style<->op mappings keymaps.py keeps
# (privately) for adapt_document. Duplicated rather than imported so this
# module never reaches into keymaps.py's underscore-prefixed internals:
# "which abstract FormatOp does a style need" (a Document/Action-level
# question, decided here) and "which keyboard shortcut reaches that op on a
# given keymap" (keymaps.py's job) are related but distinct concerns, and
# plan_document works purely in terms of the former -- it is deliberately
# keymap-agnostic, since its input is already adapted (see plan_document's
# docstring) and an unreachable op is Driver's concern to reject, not this
# module's to know about.
_SET_OP_BY_STYLE: dict[ParagraphStyle, FormatOp] = {
    ParagraphStyle.NORMAL: FormatOp.SET_NORMAL,
    ParagraphStyle.TITLE: FormatOp.SET_TITLE,
    ParagraphStyle.SUBTITLE: FormatOp.SET_SUBTITLE,
    ParagraphStyle.HEADING_1: FormatOp.SET_HEADING_1,
    ParagraphStyle.HEADING_2: FormatOp.SET_HEADING_2,
    ParagraphStyle.HEADING_3: FormatOp.SET_HEADING_3,
    ParagraphStyle.HEADING_4: FormatOp.SET_HEADING_4,
    ParagraphStyle.HEADING_5: FormatOp.SET_HEADING_5,
    ParagraphStyle.HEADING_6: FormatOp.SET_HEADING_6,
}
_ALIGN_OP_BY_ALIGNMENT: dict[Alignment, FormatOp] = {
    Alignment.LEFT: FormatOp.ALIGN_LEFT,
    Alignment.CENTER: FormatOp.ALIGN_CENTER,
    Alignment.RIGHT: FormatOp.ALIGN_RIGHT,
    Alignment.JUSTIFIED: FormatOp.ALIGN_JUSTIFIED,
}
_LIST_TOGGLE_OP_BY_KIND: dict[ListKind, FormatOp] = {
    ListKind.BULLET: FormatOp.TOGGLE_BULLET_LIST,
    ListKind.NUMBERED: FormatOp.TOGGLE_NUMBERED_LIST,
}
_TOGGLE_OP_BY_TEXT_STYLE: dict[TextStyle, FormatOp] = {
    TextStyle.BOLD: FormatOp.TOGGLE_BOLD,
    TextStyle.ITALIC: FormatOp.TOGGLE_ITALIC,
    TextStyle.UNDERLINE: FormatOp.TOGGLE_UNDERLINE,
    TextStyle.STRIKETHROUGH: FormatOp.TOGGLE_STRIKETHROUGH,
}
# Fixed order so toggling a set of styles is deterministic -- and therefore
# reproducible for a given seed -- regardless of frozenset iteration order.
_TEXT_STYLE_ORDER: tuple[TextStyle, ...] = (
    TextStyle.BOLD,
    TextStyle.ITALIC,
    TextStyle.UNDERLINE,
    TextStyle.STRIKETHROUGH,
)


def plan_document(
    doc: Document,
    profile: Profile | None = None,
    seed: int | None = None,
) -> list[Action]:
    """Return the action stream that types ``doc`` into a blank Google Doc.

    ``doc`` must already be keymap-adapted (``keymaps.adapt_document``): this
    function has no fallback for a Title or Subtitle paragraph style, it
    simply emits the ``FormatOp`` a style calls for. A keymap-aware
    ``Driver.run`` (or ``adapt_document`` beforehand) is what rejects or
    rewrites a style the target keymap cannot reach -- plan_document itself
    is deliberately keymap-agnostic.

    Reuses ``_type_segment`` -- the same machinery ``plan`` uses -- for every
    run's text, so typos, revisions, thinking pauses, session breaks, and
    fatigue all behave exactly as they do for plain text. What's new here is
    tracking the same state ``simulator.simulate_document`` tracks (each
    paragraph's style/alignment/list, and the current insertion styles) so
    the right ``Format`` actions get emitted at the right time: paragraph
    attributes are reconciled against that tracked model before any of the
    paragraph's text, and character styles are reconciled before each run.
    """
    profile = profile or Profile()
    rng = random.Random(seed)
    timing = TimingModel(profile, rng)
    actions: list[Action] = []
    next_break_at = rng.uniform(*profile.break_interval_ms) if profile.session_breaks else None
    previous: str | None = None
    model_insertion: frozenset[TextStyle] = frozenset()

    def emit_format(op: FormatOp) -> None:
        nonlocal previous
        pause_ms = rng.uniform(*profile.format_shortcut_ms)
        timing.advance_ms(pause_ms)
        actions.append(Pause(pause_ms))
        actions.append(Format(op))
        previous = None

    # The user's insertion state when the doc was opened is unknown.
    emit_format(FormatOp.CLEAR_FORMATTING)

    prev_style = ParagraphStyle.NORMAL
    prev_alignment = Alignment.LEFT
    prev_list_kind: ListKind | None = None
    prev_list_level = 0

    for p_index, paragraph in enumerate(doc.paragraphs):
        if p_index > 0:
            interval = timing.keystroke_interval_ms(previous, "\n")
            actions.append(Pause(interval))
            actions.append(KeyPress("\n"))
            previous = "\n"
            # The paragraph-length thinking pause belongs right here, before
            # attribute reconciliation below can reset the digraph context
            # and suppress it.
            pause_ms = timing.thinking_pause_ms("\n", "")
            if pause_ms > 0.0:
                actions.append(Pause(pause_ms))

        # -- reconcile paragraph style --
        # A new paragraph is always NORMAL in the tracked model (Enter never
        # carries a heading forward), so "differs from the model" reduces to
        # "isn't NORMAL" -- except SET_NORMAL is also re-asserted explicitly
        # whenever the previous paragraph was not NORMAL, so the plan stays
        # correct whether or not real Docs resets headings on Enter.
        if paragraph.style != ParagraphStyle.NORMAL or (
            p_index > 0 and prev_style != ParagraphStyle.NORMAL
        ):
            emit_format(_SET_OP_BY_STYLE[paragraph.style])

        # -- reconcile alignment --
        if paragraph.alignment != prev_alignment:
            emit_format(_ALIGN_OP_BY_ALIGNMENT[paragraph.alignment])

        # -- reconcile list kind and nesting level --
        if paragraph.list_kind != prev_list_kind:
            if paragraph.list_kind is None:
                emit_format(_LIST_TOGGLE_OP_BY_KIND[prev_list_kind])
                current_level = 0
            else:
                # Toggling the kind that's already active would end the list
                # instead of switching it (see simulator._toggle_list), so
                # this branch only ever toggles the *target* kind: starting
                # a fresh list at level 0 if none was active, or switching
                # kind while keeping the current level otherwise.
                emit_format(_LIST_TOGGLE_OP_BY_KIND[paragraph.list_kind])
                current_level = 0 if prev_list_kind is None else prev_list_level
        else:
            current_level = prev_list_level

        level_diff = paragraph.list_level - current_level
        if level_diff > 0:
            for _ in range(level_diff):
                emit_format(FormatOp.INDENT)
        elif level_diff < 0:
            for _ in range(-level_diff):
                emit_format(FormatOp.OUTDENT)

        prev_style = paragraph.style
        prev_alignment = paragraph.alignment
        prev_list_kind = paragraph.list_kind
        prev_list_level = paragraph.list_level

        # -- runs: character styles, then the run's own text --
        for r_index, run in enumerate(paragraph.runs):
            to_toggle = model_insertion ^ run.styles
            for style in _TEXT_STYLE_ORDER:
                if style in to_toggle:
                    emit_format(_TOGGLE_OP_BY_TEXT_STYLE[style])
            model_insertion = run.styles

            def restore_insertion() -> None:
                """A correction that empties this run's typed-so-far text
                back to its very first character exposes the previous run's
                styles underneath it (simulator.simulate_document's
                Backspace rule) -- unless this is the paragraph's first run,
                in which case the model's insertion is left unchanged
                because the paragraph has no characters left at all. Restore
                the model to what Backspace just did for free, then
                re-toggle to this run's own styles before the retype that
                follows: the same way a person notices formatting reverted
                and re-applies it.
                """
                nonlocal model_insertion
                if r_index == 0:
                    return
                exposed = paragraph.runs[r_index - 1].styles
                to_fix = exposed ^ run.styles
                for style in _TEXT_STYLE_ORDER:
                    if style in to_fix:
                        emit_format(_TOGGLE_OP_BY_TEXT_STYLE[style])
                model_insertion = run.styles

            previous, next_break_at = _type_segment(
                run.text,
                rng,
                timing,
                profile,
                actions,
                previous,
                next_break_at,
                on_backspace_to_start=restore_insertion,
            )

    return actions
