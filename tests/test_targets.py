"""Tests for the targets.py editor registry.

The unit-level tests below check the registry's own lookups (get_target,
targets_for, keymap_for) and a handful of spec-mandated shapes of the five
target keymaps. The property test at the bottom is the important one: it
proves the whole pipeline -- registry -> adapt_document -> plan_document ->
simulate_document -- actually reproduces a wide range of random documents for
every (target, OS) pair this module claims to support, not just Google Docs.
"""

import random

import pytest

from auto_essay_typer.actions import FormatOp
from auto_essay_typer.document import (
    Alignment,
    Document,
    ListKind,
    Paragraph,
    ParagraphStyle,
    Run,
    TextStyle,
)
from auto_essay_typer.keymaps import (
    GOOGLE_DOCS_MAC,
    KEYMAPS,
    NO_OP,
    adapt_document,
    format_chord,
    parse_chord,
    required_ops,
)
from auto_essay_typer.planner import plan_document
from auto_essay_typer.platforms import OperatingSystem
from auto_essay_typer.profiles import PRESETS
from auto_essay_typer.simulator import simulate_document
from auto_essay_typer.targets import TARGET_APPS, get_target, keymap_for, targets_for

_EXPECTED_IDS = ("google-docs", "microsoft-word", "libreoffice-writer", "textedit", "plain-text")


# --- registry lookups --------------------------------------------------------


def test_target_apps_insertion_order_matches_the_spec():
    assert list(TARGET_APPS) == list(_EXPECTED_IDS)


@pytest.mark.parametrize("target_id", _EXPECTED_IDS)
def test_get_target_returns_the_matching_target_app(target_id):
    target = get_target(target_id)
    assert target.id == target_id
    assert target is TARGET_APPS[target_id]


def test_get_target_raises_key_error_listing_valid_ids_for_an_unknown_id():
    with pytest.raises(KeyError) as exc_info:
        get_target("wordpad")
    message = str(exc_info.value)
    assert "wordpad" in message
    for target_id in _EXPECTED_IDS:
        assert target_id in message


# --- targets_for --------------------------------------------------------------


def test_targets_for_macos_includes_all_five_in_display_order():
    assert [t.id for t in targets_for(OperatingSystem.MACOS)] == list(_EXPECTED_IDS)


def test_targets_for_windows_excludes_textedit():
    ids = {t.id for t in targets_for(OperatingSystem.WINDOWS)}
    assert ids == {"google-docs", "microsoft-word", "libreoffice-writer", "plain-text"}


def test_targets_for_linux_excludes_microsoft_word_and_textedit():
    ids = {t.id for t in targets_for(OperatingSystem.LINUX)}
    assert ids == {"google-docs", "libreoffice-writer", "plain-text"}


# --- keymap_for ----------------------------------------------------------------


def test_keymap_for_every_valid_pair_returns_the_targets_own_keymap():
    for target in TARGET_APPS.values():
        for os_ in target.platforms:
            assert keymap_for(target.id, os_) == target.keymaps[os_]


def test_keymap_for_microsoft_word_on_linux_raises_a_clear_error():
    with pytest.raises(ValueError) as exc_info:
        keymap_for("microsoft-word", OperatingSystem.LINUX)
    message = str(exc_info.value).lower()
    assert "microsoft-word" in message or "microsoft word" in message
    assert "linux" in message
    assert "macos" in message
    assert "windows" in message


def test_keymap_for_textedit_on_windows_raises_a_clear_error():
    with pytest.raises(ValueError) as exc_info:
        keymap_for("textedit", OperatingSystem.WINDOWS)
    message = str(exc_info.value).lower()
    assert "textedit" in message
    assert "windows" in message
    assert "macos" in message


def test_keymap_for_textedit_on_linux_raises_a_clear_error():
    with pytest.raises(ValueError) as exc_info:
        keymap_for("textedit", OperatingSystem.LINUX)
    message = str(exc_info.value).lower()
    assert "textedit" in message
    assert "linux" in message
    assert "macos" in message


def test_keymap_for_unknown_target_raises_key_error():
    with pytest.raises(KeyError):
        keymap_for("wordpad", OperatingSystem.MACOS)


# --- every chord parses --------------------------------------------------------


def test_every_keymap_chord_round_trips_through_format_and_parse():
    for target in TARGET_APPS.values():
        for keymap in target.keymaps.values():
            for op, chord in keymap.items():
                if chord == NO_OP:
                    continue
                assert parse_chord(format_chord(chord)) == chord, (target.id, op)


# --- spec-mandated shapes -------------------------------------------------------


def test_google_docs_windows_and_linux_have_no_align_justified():
    google_docs = get_target("google-docs")
    assert FormatOp.ALIGN_JUSTIFIED not in google_docs.keymaps[OperatingSystem.WINDOWS]
    assert FormatOp.ALIGN_JUSTIFIED not in google_docs.keymaps[OperatingSystem.LINUX]
    # macOS keeps it -- only the Windows/Linux Chrome DevTools collision is special.
    assert FormatOp.ALIGN_JUSTIFIED in google_docs.keymaps[OperatingSystem.MACOS]


def test_microsoft_word_has_no_linux_support():
    word = get_target("microsoft-word")
    assert OperatingSystem.LINUX not in word.platforms
    assert OperatingSystem.LINUX not in word.keymaps


def test_textedit_is_macos_only():
    textedit = get_target("textedit")
    assert textedit.platforms == frozenset({OperatingSystem.MACOS})
    assert set(textedit.keymaps) == {OperatingSystem.MACOS}


def test_google_docs_mac_keymap_still_matches_the_legacy_keymaps_module():
    assert KEYMAPS["google-docs-mac"] is GOOGLE_DOCS_MAC
    assert TARGET_APPS["google-docs"].keymaps[OperatingSystem.MACOS] is GOOGLE_DOCS_MAC


def test_no_target_other_than_google_docs_has_any_list_ops():
    # Design rationale (see targets.py's module docstring): only Google
    # Docs' list shortcuts match the toggle model simulate_document
    # implements, so every other target must have none of the four list ops
    # at all -- adapt_document's flatten-to-typed-markers fallback depends
    # on that absence.
    list_ops = {
        FormatOp.TOGGLE_BULLET_LIST,
        FormatOp.TOGGLE_NUMBERED_LIST,
        FormatOp.INDENT,
        FormatOp.OUTDENT,
    }
    for target in TARGET_APPS.values():
        if target.id == "google-docs":
            continue
        for keymap in target.keymaps.values():
            assert not (list_ops & set(keymap)), target.id


# --- the big property test: registry -> adapt -> plan -> simulate --------------

_WORD_BANK = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta")
_PARAGRAPH_STYLES = list(ParagraphStyle)
_ALIGNMENTS = list(Alignment)
_LIST_KINDS: list[ListKind | None] = [None, *ListKind]
_TEXT_STYLES = list(TextStyle)


def _random_run(rng: random.Random) -> Run:
    text = " ".join(rng.sample(_WORD_BANK, rng.randint(1, 3)))
    styles = frozenset(style for style in _TEXT_STYLES if rng.random() < 0.5)
    return Run(text, styles)


def _random_paragraph(rng: random.Random) -> Paragraph:
    style = rng.choice(_PARAGRAPH_STYLES)
    alignment = rng.choice(_ALIGNMENTS)
    list_kind = rng.choice(_LIST_KINDS)
    list_level = rng.randint(0, 8) if list_kind is not None else 0
    runs = tuple(_random_run(rng) for _ in range(rng.randint(0, 3)))
    return Paragraph(
        runs=runs, style=style, alignment=alignment, list_kind=list_kind, list_level=list_level
    )


def _random_document(rng: random.Random) -> Document:
    return Document(tuple(_random_paragraph(rng) for _ in range(rng.randint(1, 4))))


def _generate_corpus(size: int) -> list[Document]:
    rng = random.Random(0)
    return [_random_document(rng) for _ in range(size)]


# Generated once, at import time, and reused across all 12 (target, OS)
# pairs below -- regenerating per pair would multiply the runtime for no
# extra coverage.
_CORPUS_SIZE = 500
DOCS = _generate_corpus(_CORPUS_SIZE)

# Reuse profiles.PRESETS as the profiles, one per seed.
_SEEDS_AND_PROFILES = list(zip((0, 1, 2), PRESETS.values()))

_ALL_PAIRS = [(target, os_) for os_ in OperatingSystem for target in targets_for(os_)]


def test_all_pairs_covers_the_documented_twelve():
    counts: dict[str, int] = {}
    for target, _os in _ALL_PAIRS:
        counts[target.id] = counts.get(target.id, 0) + 1
    assert len(_ALL_PAIRS) == 12
    assert counts == {
        "google-docs": 3,
        "microsoft-word": 2,
        "libreoffice-writer": 3,
        "textedit": 1,
        "plain-text": 3,
    }


@pytest.mark.parametrize(
    "target, os_",
    _ALL_PAIRS,
    ids=[f"{target.id}-{os_.value}" for target, os_ in _ALL_PAIRS],
)
def test_registry_to_simulation_pipeline_holds_for_every_target_and_os(target, os_):
    """The full registry -> adapt -> plan -> simulate pipeline round-trips.

    This is the test that proves the whole pipeline actually works for
    every editor this project knows about, not just Google Docs: every
    keymap this module defines must let adapt_document fully discharge a
    document's required ops, stay idempotent under a second adaptation, and
    let plan_document/simulate_document reproduce the adapted document
    exactly, for several profiles and seeds.
    """
    keymap = keymap_for(target.id, os_)
    for index, doc in enumerate(DOCS):
        adapted, _warnings = adapt_document(doc, keymap)
        assert required_ops(adapted) <= set(keymap), (
            f"doc {index}: required_ops not covered by {target.id}/{os_.value}"
        )

        reapplied, warnings_again = adapt_document(adapted, keymap)
        assert reapplied == adapted, f"doc {index}: adapting twice was not idempotent"
        assert warnings_again == (), f"doc {index}: idempotent re-adaptation still warned"

        for seed, profile in _SEEDS_AND_PROFILES:
            actions = plan_document(adapted, profile, seed)
            assert simulate_document(actions).normalized() == adapted.normalized(), (
                f"doc {index}: seed={seed} pipeline diverged for {target.id}/{os_.value}"
            )


# --- the notes are shown to the person using the app --------------------------------------------------

#: Words that belong to the code, not to the person reading the Type page.
_DEVELOPER_WORDS = ("keymap", "FormatOp", "adapt_document", "NO_OP", "CLEAR_FORMATTING", "ALIGN_", "SET_", "TOGGLE_")


@pytest.mark.parametrize("target_id", _EXPECTED_IDS)
def test_every_target_note_is_plain_language_fit_to_show_a_user(target_id):
    note = get_target(target_id).notes
    assert note.strip() == note and note.endswith(".")
    assert 40 <= len(note) <= 320, "long enough to say something, short enough to sit under a picker"
    assert not any(word in note for word in _DEVELOPER_WORDS), note


def test_the_notes_that_ask_for_a_setting_change_name_the_menu_to_find_it_in():
    assert "AutoFormat As You Type" in get_target("microsoft-word").notes
    assert "Automatic bulleted lists" in get_target("microsoft-word").notes
    assert "AutoCorrect > While Typing" in get_target("libreoffice-writer").notes
    assert "Make Rich Text" in get_target("textedit").notes


def test_the_plain_text_note_warns_that_a_new_paragraph_presses_enter():
    # Typing into a chat box or a terminal, Enter sends the message or runs the command.
    note = get_target("plain-text").notes
    assert "presses Enter" in note and "send" in note


def test_no_note_sends_a_windows_user_to_a_menu_that_only_exists_on_a_mac():
    # Word for Windows has no Tools menu; the setting is reached through AutoCorrect Options.
    assert "Tools >" not in get_target("microsoft-word").notes
