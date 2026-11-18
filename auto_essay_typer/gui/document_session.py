"""An imported document, fitted to one editor and checked before it is typed.

The Type page's *document mode* needs the same few facts in several places --
the document as imported, the version of it that can actually be typed into the
chosen editor, what that costs, and whether a typing plan will reproduce it --
so they live here, in one immutable value with no Qt in it:

* :class:`DocumentSession` is the imported :class:`~auto_essay_typer.document.Document`
  (the source of truth, never edited) plus its adaptation to one
  :class:`~auto_essay_typer.targets.TargetApp`'s shortcuts. Choosing another
  editor builds a new session from the same source document
  (:meth:`DocumentSession.for_target`), so adapting twice can never compound.
* :func:`verify_document_plan` is this project's governing invariant applied to
  a plan: replaying it must give back exactly the document it was planned from,
  ``simulate_document(plan).normalized() == document.normalized()``. The Preview
  dialog shows the result, and :class:`~auto_essay_typer.gui.run_controller.RunController`
  refuses to type a plan that fails it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from auto_essay_typer.actions import Action, FormatOp
from auto_essay_typer.document import Document, Paragraph
from auto_essay_typer.gui.document_warnings import editor_phrase, explain_warnings
from auto_essay_typer.keymaps import Chord, adapt_document
from auto_essay_typer.simulator import simulate_document
from auto_essay_typer.targets import TargetApp


@dataclass(frozen=True)
class DocumentSession:
    """``document`` adapted to ``target``'s ``keymap``.

    Build one with :meth:`create`; the fields are derived from its arguments.

    ``document`` is exactly what the importer produced and is what everything
    else is derived from. ``adapted`` is what will be typed: the same document
    rewritten so every formatting command it needs exists in ``keymap``.
    ``adaptation_warnings`` are ``adapt_document``'s own messages, and
    ``plain_warnings`` is everything the "What will not be reproduced" panel
    lists, in plain language (the importer's, then the editor's).
    """

    document: Document
    source: str
    import_warnings: tuple[str, ...]
    target: TargetApp
    keymap: Mapping[FormatOp, Chord]
    adapted: Document
    adaptation_warnings: tuple[str, ...]
    plain_warnings: tuple[str, ...]

    @classmethod
    def create(
        cls,
        document: Document,
        source: str,
        import_warnings: Sequence[str],
        target: TargetApp,
        keymap: Mapping[FormatOp, Chord],
    ) -> "DocumentSession":
        adapted, adaptation_warnings = adapt_document(document, keymap)
        return cls(
            document=document,
            source=source,
            import_warnings=tuple(import_warnings),
            target=target,
            keymap=keymap,
            adapted=adapted,
            adaptation_warnings=adaptation_warnings,
            plain_warnings=explain_warnings(
                import_warnings, adaptation_warnings, editor_phrase(target)
            ),
        )

    def for_target(self, target: TargetApp, keymap: Mapping[FormatOp, Chord]) -> "DocumentSession":
        """The same imported document, adapted to a different editor's shortcuts."""
        return DocumentSession.create(
            self.document, self.source, self.import_warnings, target, keymap
        )

    def typed_text(self) -> str:
        """The characters that will end up on screen: the adapted document's text."""
        return self.adapted.plain_text()

    def has_text(self) -> bool:
        return bool(self.typed_text().strip())


# --- verifying a plan ----------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanCheck:
    """The outcome of :func:`verify_document_plan`: ``ok``, or why it is not."""

    ok: bool
    detail: str = ""


class PlanVerificationError(Exception):
    """A document's typing plan did not reproduce the document.

    The message is written for the person using the app: it says nothing was
    typed, and where the plan went wrong.
    """


def _blank_if_empty(document: Document) -> Document:
    """A document with no paragraphs is a blank page, which is one empty paragraph.

    ``simulate_document`` always starts from (and so always returns at least)
    one paragraph, so the two ways of saying "empty" must compare equal.
    """
    return document if document.paragraphs else Document((Paragraph(),))


def _first_difference(produced: Document, expected: Document) -> str:
    if len(produced.paragraphs) != len(expected.paragraphs):
        return (
            f"the plan produces {len(produced.paragraphs)} paragraph(s) but the document "
            f"has {len(expected.paragraphs)}"
        )
    for number, (got, wanted) in enumerate(zip(produced.paragraphs, expected.paragraphs), start=1):
        if got != wanted:
            return f"paragraph {number} comes out different from the document"
    return "the plan does not reproduce the document"  # pragma: no cover - equal documents never get here


def verify_document_plan(actions: Sequence[Action], document: Document) -> PlanCheck:
    """Whether replaying ``actions`` gives back ``document``, formatting included.

    This is ``simulate_document(actions).normalized() == document.normalized()``,
    with the two ways a replay can go wrong reported rather than raised: a plan
    that cannot be replayed at all (it backspaces across a paragraph, indents a
    paragraph that is not a list item) and one that replays into a different
    document. Either way the result carries a short reason fit to show.
    """
    try:
        produced = simulate_document(actions).normalized()
    except Exception as exc:  # noqa: BLE001 - any replay failure means the plan is not usable
        return PlanCheck(False, f"the plan could not be replayed ({exc})")
    expected = _blank_if_empty(document).normalized()
    if produced == expected:
        return PlanCheck(True)
    return PlanCheck(False, _first_difference(produced, expected))
