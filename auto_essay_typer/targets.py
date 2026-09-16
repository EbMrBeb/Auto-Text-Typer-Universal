"""Registry of target editors: per-(app, OS) keyboard shortcuts for typing.

``keymaps.py`` supplies the shared machinery -- ``Chord``/``parse_chord``/
``format_chord`` for reading and writing shortcuts, and ``adapt_document``,
which rewrites a ``Document`` so every op it needs exists in a given keymap,
falling back to the closest thing an editor *can* express otherwise. This
module is purely data: which apps this project knows how to type into, which
platforms each one runs on, and which chord presses which ``FormatOp`` on
each of those platforms.

Unless called out as a deliberate exception in a keymap's own comments below,
a chord uses "mod" to mean cmd on macOS and ctrl on Windows/Linux -- matching
``platforms.primary_modifier``. A handful of targets have real, deliberate
exceptions to that rule: a shortcut that is identical across platforms, or
that uses a different modifier on one platform than a mod-swap would
predict. Those are transcribed literally, not "corrected" into a mod
substitution.

Every ``TargetApp.verified`` flag is honest about its source: ``True`` means
the keymap is transcribed from the vendor's own published shortcut reference
(Google Docs' help center); ``False`` means it is a best-effort,
community-sourced transcription that has not been checked against a live
install of the application, and could be wrong in a way only hands-on
testing would catch.

Design rationale -- why only Google Docs gets list shortcuts:
``TOGGLE_BULLET_LIST``, ``TOGGLE_NUMBERED_LIST``, ``INDENT``, and ``OUTDENT``
are given only to ``google-docs``. ``simulator.simulate_document`` implements
one specific editing model for lists: each toggle op turns that list kind on
or off (switching kind while a list is already active keeps the current
nesting level), and INDENT/OUTDENT change the current paragraph's nesting
level directly. That is how Google Docs' own list shortcuts behave. Word,
LibreOffice Writer, and TextEdit do not behave this way (Word's and
LibreOffice's list toggling carries its own, different state; TextEdit has
no list shortcuts at all), so giving them these ops would make
``plan_document`` emit shortcuts that do not do what the simulator thinks
they do. Leaving the ops out entirely is instead exactly what triggers
``adapt_document``'s existing list fallback: every paragraph of a list kind
whose ops are not all present in the keymap gets flattened to a plain
paragraph with a typed marker ("• ", "1. ", ...) before planning ever sees
it, which is the only representation of a list these targets can reliably
reproduce.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from auto_essay_typer.actions import FormatOp
from auto_essay_typer.keymaps import Chord, GOOGLE_DOCS_MAC, NO_OP, parse_chord
from auto_essay_typer.platforms import OperatingSystem


@dataclass(frozen=True)
class TargetApp:
    """One application this project can type into, and how to reach it.

    ``keymaps`` holds one entry per OS in ``platforms`` -- never more, never
    fewer -- so ``keymap_for`` can distinguish "this target exists but not on
    that OS" from a programming error.
    """

    id: str
    name: str
    platforms: frozenset[OperatingSystem]
    keymaps: Mapping[OperatingSystem, Mapping[FormatOp, Chord]]
    notes: str
    verified: bool


# --- google-docs -------------------------------------------------------------
#
# macOS reuses keymaps.GOOGLE_DOCS_MAC unchanged. Windows and Linux are NOT a
# naive cmd->ctrl substitution of it: TOGGLE_STRIKETHROUGH is alt+shift+5
# (not ctrl+shift+x) on Windows/Linux, and ALIGN_JUSTIFIED has no shortcut at
# all there, because ctrl+shift+j opens Chrome DevTools instead of reaching
# the page.

_GOOGLE_DOCS_WINDOWS_LINUX: dict[FormatOp, Chord] = {
    FormatOp.CLEAR_FORMATTING: parse_chord("ctrl+\\"),
    FormatOp.TOGGLE_BOLD: parse_chord("ctrl+b"),
    FormatOp.TOGGLE_ITALIC: parse_chord("ctrl+i"),
    FormatOp.TOGGLE_UNDERLINE: parse_chord("ctrl+u"),
    # Deliberately alt, not ctrl: Google's real Windows/Linux shortcut,
    # unlike mac's cmd+shift+x.
    FormatOp.TOGGLE_STRIKETHROUGH: parse_chord("alt+shift+5"),
    FormatOp.SET_NORMAL: parse_chord("ctrl+alt+0"),
    FormatOp.SET_HEADING_1: parse_chord("ctrl+alt+1"),
    FormatOp.SET_HEADING_2: parse_chord("ctrl+alt+2"),
    FormatOp.SET_HEADING_3: parse_chord("ctrl+alt+3"),
    FormatOp.SET_HEADING_4: parse_chord("ctrl+alt+4"),
    FormatOp.SET_HEADING_5: parse_chord("ctrl+alt+5"),
    FormatOp.SET_HEADING_6: parse_chord("ctrl+alt+6"),
    FormatOp.ALIGN_LEFT: parse_chord("ctrl+shift+l"),
    FormatOp.ALIGN_CENTER: parse_chord("ctrl+shift+e"),
    FormatOp.ALIGN_RIGHT: parse_chord("ctrl+shift+r"),
    # ALIGN_JUSTIFIED is deliberately absent: ctrl+shift+j opens Chrome
    # DevTools instead of reaching the page, so there is no usable shortcut
    # for it here. adapt_document already falls justified paragraphs back to
    # left-aligned when an ALIGN_JUSTIFIED op is missing from the keymap.
    FormatOp.TOGGLE_NUMBERED_LIST: parse_chord("ctrl+shift+7"),
    FormatOp.TOGGLE_BULLET_LIST: parse_chord("ctrl+shift+8"),
    FormatOp.INDENT: parse_chord("tab"),
    FormatOp.OUTDENT: parse_chord("shift+tab"),
}

_GOOGLE_DOCS_NOTES = (
    "Transcribed from Google's own published Google Docs keyboard shortcuts "
    "reference. On Windows and Linux, ALIGN_JUSTIFIED is deliberately left "
    "out of this keymap: ctrl+shift+j opens the Chrome DevTools console "
    "instead of reaching the document, so pressing it would break the "
    "session instead of justifying a paragraph. adapt_document already "
    "handles the absence gracefully, typing justified paragraphs as "
    "left-aligned instead."
)

# --- microsoft-word ------------------------------------------------------------
#
# No Linux keymap: Word has no Linux release at all. CLEAR_FORMATTING is
# ctrl+space on BOTH mac and Windows -- a deliberate exception to the mod
# rule, not cmd+space on mac -- because that is Word's real, identical,
# cross-platform "Clear Formatting" shortcut.

_MICROSOFT_WORD_MACOS: dict[FormatOp, Chord] = {
    FormatOp.CLEAR_FORMATTING: parse_chord("ctrl+space"),
    FormatOp.TOGGLE_BOLD: parse_chord("cmd+b"),
    FormatOp.TOGGLE_ITALIC: parse_chord("cmd+i"),
    FormatOp.TOGGLE_UNDERLINE: parse_chord("cmd+u"),
    FormatOp.SET_NORMAL: parse_chord("cmd+shift+n"),
    FormatOp.SET_HEADING_1: parse_chord("cmd+alt+1"),
    FormatOp.SET_HEADING_2: parse_chord("cmd+alt+2"),
    FormatOp.SET_HEADING_3: parse_chord("cmd+alt+3"),
    FormatOp.ALIGN_LEFT: parse_chord("cmd+l"),
    FormatOp.ALIGN_CENTER: parse_chord("cmd+e"),
    FormatOp.ALIGN_RIGHT: parse_chord("cmd+r"),
    FormatOp.ALIGN_JUSTIFIED: parse_chord("cmd+j"),
}

_MICROSOFT_WORD_WINDOWS: dict[FormatOp, Chord] = {
    FormatOp.CLEAR_FORMATTING: parse_chord("ctrl+space"),
    FormatOp.TOGGLE_BOLD: parse_chord("ctrl+b"),
    FormatOp.TOGGLE_ITALIC: parse_chord("ctrl+i"),
    FormatOp.TOGGLE_UNDERLINE: parse_chord("ctrl+u"),
    FormatOp.SET_NORMAL: parse_chord("ctrl+shift+n"),
    FormatOp.SET_HEADING_1: parse_chord("ctrl+alt+1"),
    FormatOp.SET_HEADING_2: parse_chord("ctrl+alt+2"),
    FormatOp.SET_HEADING_3: parse_chord("ctrl+alt+3"),
    FormatOp.ALIGN_LEFT: parse_chord("ctrl+l"),
    FormatOp.ALIGN_CENTER: parse_chord("ctrl+e"),
    FormatOp.ALIGN_RIGHT: parse_chord("ctrl+r"),
    FormatOp.ALIGN_JUSTIFIED: parse_chord("ctrl+j"),
}

_MICROSOFT_WORD_NOTES = (
    "Not independently verified against a live Word install. Turn off "
    "Word's Tools > AutoCorrect Options > AutoFormat As You Type list "
    "options before typing: this keymap has no list FormatOps (see this "
    "module's docstring for why), so adapt_document types list items as "
    'plain text starting with a typed marker like "1. " or "• " -- '
    "AutoFormat As You Type would otherwise convert that marker into a real "
    "Word list out from under the plan."
)

# --- libreoffice-writer --------------------------------------------------------

_LIBREOFFICE_WRITER_MACOS: dict[FormatOp, Chord] = {
    # No usable macOS shortcut: cmd+m is the system-wide "Minimize"
    # shortcut, so there is genuinely nothing to bind here.
    FormatOp.CLEAR_FORMATTING: NO_OP,
    FormatOp.TOGGLE_BOLD: parse_chord("cmd+b"),
    FormatOp.TOGGLE_ITALIC: parse_chord("cmd+i"),
    FormatOp.TOGGLE_UNDERLINE: parse_chord("cmd+u"),
    FormatOp.SET_NORMAL: parse_chord("cmd+0"),
    FormatOp.SET_HEADING_1: parse_chord("cmd+1"),
    FormatOp.SET_HEADING_2: parse_chord("cmd+2"),
    FormatOp.SET_HEADING_3: parse_chord("cmd+3"),
    FormatOp.ALIGN_LEFT: parse_chord("cmd+l"),
    FormatOp.ALIGN_CENTER: parse_chord("cmd+e"),
    FormatOp.ALIGN_RIGHT: parse_chord("cmd+r"),
    FormatOp.ALIGN_JUSTIFIED: parse_chord("cmd+j"),
}

_LIBREOFFICE_WRITER_WINDOWS_LINUX: dict[FormatOp, Chord] = {
    FormatOp.CLEAR_FORMATTING: parse_chord("ctrl+m"),
    FormatOp.TOGGLE_BOLD: parse_chord("ctrl+b"),
    FormatOp.TOGGLE_ITALIC: parse_chord("ctrl+i"),
    FormatOp.TOGGLE_UNDERLINE: parse_chord("ctrl+u"),
    FormatOp.SET_NORMAL: parse_chord("ctrl+0"),
    FormatOp.SET_HEADING_1: parse_chord("ctrl+1"),
    FormatOp.SET_HEADING_2: parse_chord("ctrl+2"),
    FormatOp.SET_HEADING_3: parse_chord("ctrl+3"),
    FormatOp.ALIGN_LEFT: parse_chord("ctrl+l"),
    FormatOp.ALIGN_CENTER: parse_chord("ctrl+e"),
    FormatOp.ALIGN_RIGHT: parse_chord("ctrl+r"),
    FormatOp.ALIGN_JUSTIFIED: parse_chord("ctrl+j"),
}

_LIBREOFFICE_WRITER_NOTES = (
    "Not independently verified against a live LibreOffice install. "
    "CLEAR_FORMATTING has no macOS shortcut at all (cmd+m is the system "
    "Minimize shortcut there), so this keymap defines it as keymaps.NO_OP "
    "on macOS specifically -- Windows and Linux use ctrl+m. Turn off Tools "
    "> AutoCorrect Options' automatic list recognition and replacement "
    "options before typing, for the same reason as Word (see "
    "microsoft-word's notes)."
)

# --- textedit --------------------------------------------------------------------

_TEXTEDIT_MACOS: dict[FormatOp, Chord] = {
    FormatOp.CLEAR_FORMATTING: NO_OP,
    FormatOp.TOGGLE_BOLD: parse_chord("cmd+b"),
    FormatOp.TOGGLE_ITALIC: parse_chord("cmd+i"),
    FormatOp.TOGGLE_UNDERLINE: parse_chord("cmd+u"),
    FormatOp.ALIGN_LEFT: parse_chord("cmd+shift+["),
    FormatOp.ALIGN_CENTER: parse_chord("cmd+shift+\\"),
    FormatOp.ALIGN_RIGHT: parse_chord("cmd+shift+]"),
}

_TEXTEDIT_NOTES = (
    "Not independently verified against a live TextEdit install. The "
    "document must already be in rich text mode (Format > Make Rich Text) "
    "before typing -- TextEdit's plain text mode has no formatting "
    "shortcuts at all, so every Format action would silently do nothing."
)

# --- plain-text ------------------------------------------------------------------

_PLAIN_TEXT: dict[FormatOp, Chord] = {
    FormatOp.CLEAR_FORMATTING: NO_OP,
}

_PLAIN_TEXT_NOTES = (
    "The universal fallback for any application not otherwise listed here. "
    "CLEAR_FORMATTING is the only op defined (as keymaps.NO_OP), so "
    "adapt_document flattens every paragraph style, alignment, run style, "
    "and list to plain text with typed markers (see this module's "
    "docstring). Works in literally any text field -- a browser form, a "
    "terminal, a chat box -- at the cost of all visual formatting."
)


#: Insertion order is display order.
TARGET_APPS: dict[str, TargetApp] = {
    "google-docs": TargetApp(
        id="google-docs",
        name="Google Docs",
        platforms=frozenset(
            {OperatingSystem.MACOS, OperatingSystem.WINDOWS, OperatingSystem.LINUX}
        ),
        keymaps={
            OperatingSystem.MACOS: GOOGLE_DOCS_MAC,
            OperatingSystem.WINDOWS: _GOOGLE_DOCS_WINDOWS_LINUX,
            OperatingSystem.LINUX: _GOOGLE_DOCS_WINDOWS_LINUX,
        },
        notes=_GOOGLE_DOCS_NOTES,
        verified=True,
    ),
    "microsoft-word": TargetApp(
        id="microsoft-word",
        name="Microsoft Word",
        platforms=frozenset({OperatingSystem.MACOS, OperatingSystem.WINDOWS}),
        keymaps={
            OperatingSystem.MACOS: _MICROSOFT_WORD_MACOS,
            OperatingSystem.WINDOWS: _MICROSOFT_WORD_WINDOWS,
        },
        notes=_MICROSOFT_WORD_NOTES,
        verified=False,
    ),
    "libreoffice-writer": TargetApp(
        id="libreoffice-writer",
        name="LibreOffice Writer",
        platforms=frozenset(
            {OperatingSystem.MACOS, OperatingSystem.WINDOWS, OperatingSystem.LINUX}
        ),
        keymaps={
            OperatingSystem.MACOS: _LIBREOFFICE_WRITER_MACOS,
            OperatingSystem.WINDOWS: _LIBREOFFICE_WRITER_WINDOWS_LINUX,
            OperatingSystem.LINUX: _LIBREOFFICE_WRITER_WINDOWS_LINUX,
        },
        notes=_LIBREOFFICE_WRITER_NOTES,
        verified=False,
    ),
    "textedit": TargetApp(
        id="textedit",
        name="TextEdit",
        platforms=frozenset({OperatingSystem.MACOS}),
        keymaps={OperatingSystem.MACOS: _TEXTEDIT_MACOS},
        notes=_TEXTEDIT_NOTES,
        verified=False,
    ),
    "plain-text": TargetApp(
        id="plain-text",
        name="Any app (plain text)",
        platforms=frozenset(
            {OperatingSystem.MACOS, OperatingSystem.WINDOWS, OperatingSystem.LINUX}
        ),
        keymaps={
            OperatingSystem.MACOS: _PLAIN_TEXT,
            OperatingSystem.WINDOWS: _PLAIN_TEXT,
            OperatingSystem.LINUX: _PLAIN_TEXT,
        },
        notes=_PLAIN_TEXT_NOTES,
        verified=True,
    ),
}


def get_target(id: str) -> TargetApp:
    """Look up a target editor by id.

    Raises ``KeyError`` naming the bad id and every valid one, mirroring
    ``profiles.preset``'s error style.
    """
    if id not in TARGET_APPS:
        raise KeyError(f"unknown target {id!r}; choose one of {sorted(TARGET_APPS)}")
    return TARGET_APPS[id]


def targets_for(os_: OperatingSystem) -> list[TargetApp]:
    """Every ``TargetApp`` that supports ``os_``, in ``TARGET_APPS``'s display order."""
    return [target for target in TARGET_APPS.values() if os_ in target.platforms]


def keymap_for(target_id: str, os_: OperatingSystem) -> Mapping[FormatOp, Chord]:
    """The keymap ``target_id`` uses on ``os_``.

    Raises ``ValueError`` naming the target, the OS that was requested, and
    which OS(es) that target actually supports, when ``os_`` is not one of
    them -- e.g. Microsoft Word has no Linux release at all, so asking for
    ``keymap_for("microsoft-word", OperatingSystem.LINUX)`` is a programming
    error to surface loudly, not a case with a sensible fallback.
    """
    target = get_target(target_id)
    if os_ not in target.platforms:
        supported = ", ".join(sorted(p.value for p in target.platforms))
        raise ValueError(
            f"{target.name} ({target_id!r}) is not supported on "
            f"{os_.value!r}; it is only supported on: {supported}"
        )
    return target.keymaps[os_]
