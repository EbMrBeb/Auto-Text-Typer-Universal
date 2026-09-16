"""End-to-end tests for the Google Docs import pipeline.

The pipeline is: ``importers.load_document`` -> ``keymaps.adapt_document`` ->
``planner.plan_document`` -> ``driver.Driver``. Each of those already has its
own focused test file; what only an end-to-end test can catch is a mismatch
at the seams -- e.g. plan_document emitting a ``FormatOp`` the driver's
keymap can't reach, or a ``Document`` shape ``adapt_document`` produces that
``plan_document`` mis-tracks.
"""

import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from auto_essay_typer.actions import Format, FormatOp
from auto_essay_typer.document import Document, Paragraph, ParagraphStyle, Run
from auto_essay_typer.driver import Driver, RecordingKeyboard, RunResult, UnsupportedFormatError
from auto_essay_typer.importers import load_document
from auto_essay_typer.keymaps import GOOGLE_DOCS_MAC, adapt_document
from auto_essay_typer.planner import plan_document
from auto_essay_typer.profiles import PRESETS
from auto_essay_typer.simulator import simulate, simulate_document

FIXTURES = Path(__file__).parent / "fixtures"
SEEDS = (0, 1, 2, 7, 99)
_VALID_CHORDS = set(GOOGLE_DOCS_MAC.values())


def _assert_pipeline_reproduces(doc: Document) -> None:
    """Adapt ``doc``, then plan/simulate/drive it for several seeds.

    Shared by the HTML and .docx tests below: only the source differs.
    """
    adapted, _ = adapt_document(doc, GOOGLE_DOCS_MAC)

    for seed in SEEDS:
        actions = plan_document(adapted, PRESETS["sloppy"], seed)

        # The planner's own model must land exactly on the adapted document.
        assert simulate_document(actions).normalized() == adapted.normalized(), (
            f"seed={seed}: simulate_document(plan_document(doc)) diverged from doc"
        )
        assert simulate(actions) == adapted.plain_text(), f"seed={seed}: plain text diverged"

        # And a real Driver, typing into a RecordingKeyboard, must reproduce
        # both the text and only ever press chords the keymap actually has.
        # Pinned to the same GOOGLE_DOCS_MAC used by adapt_document above
        # (rather than the OS-resolved default) since _VALID_CHORDS below is
        # GOOGLE_DOCS_MAC's own value set.
        keyboard = RecordingKeyboard()
        driver = Driver(
            keyboard=keyboard, sleep=lambda _: None, countdown_seconds=0, keymap=GOOGLE_DOCS_MAC
        )
        result = driver.run(actions)

        assert result is RunResult.COMPLETED, f"seed={seed}: driver run did not complete"
        assert keyboard.text == adapted.plain_text(), f"seed={seed}: driver text diverged"
        assert all(chord in _VALID_CHORDS for chord in keyboard.chords), (
            f"seed={seed}: a recorded chord isn't in GOOGLE_DOCS_MAC: {keyboard.chords}"
        )


# --- a real Google Docs "Web page" export -----------------------------------


def test_html_export_pipeline_end_to_end():
    doc, report = load_document(str(FIXTURES / "gdocs_export_sample.html"))

    # The fixture deliberately includes an image, a link, a footnote, a
    # table, and a non-default font -- none of that is typeable, so the
    # report must say so rather than silently dropping it.
    assert report.warnings

    _assert_pipeline_reproduces(doc)


# --- a test-built .docx ------------------------------------------------------

_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _build_docx(body_xml: str) -> bytes:
    """A minimal in-memory .docx: just enough zip/XML structure to parse.

    Mirrors the helper in tests/test_importers_docx.py -- no binary fixture
    is committed to the repo.
    """
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
            'package/2006/content-types"></Types>',
        )
        zf.writestr(
            "word/document.xml",
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f"<w:document {_W}><w:body>{body_xml}</w:body></w:document>",
        )
    return buf.getvalue()


_DOCX_BODY = (
    '<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr>'
    "<w:r><w:t>A Test Essay</w:t></w:r></w:p>"
    '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
    "<w:r><w:t>Introduction</w:t></w:r></w:p>"
    "<w:p>"
    "<w:r><w:rPr><w:b/></w:rPr><w:t>Bold text, </w:t></w:r>"
    "<w:r><w:rPr><w:i/></w:rPr><w:t>italic text, </w:t></w:r>"
    "<w:r><w:t>and plain text.</w:t></w:r>"
    "</w:p>"
    '<w:p><w:pPr><w:jc w:val="center"/></w:pPr>'
    "<w:r><w:t>Centered paragraph.</w:t></w:r></w:p>"
    '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>'
    "<w:r><w:t>A list item.</w:t></w:r></w:p>"
)


def test_docx_pipeline_end_to_end(tmp_path):
    docx_path = tmp_path / "essay.docx"
    docx_path.write_bytes(_build_docx(_DOCX_BODY))

    doc, _ = load_document(str(docx_path))

    _assert_pipeline_reproduces(doc)


# --- Driver rejects an unadapted document up front --------------------------


def test_driver_rejects_a_plan_with_set_title_before_typing_anything():
    # Deliberately skip adapt_document: GOOGLE_DOCS_MAC has no shortcut for
    # SET_TITLE, so a plan built from this raw (unadapted) Document must be
    # rejected -- and rejected before a single keystroke is sent, not
    # discovered partway through typing.
    unadapted = Document((Paragraph(runs=(Run("My Essay"),), style=ParagraphStyle.TITLE),))
    actions = plan_document(unadapted, PRESETS["clean"], seed=0)
    assert any(isinstance(a, Format) and a.op is FormatOp.SET_TITLE for a in actions), (
        "test is only meaningful if the plan actually uses SET_TITLE"
    )

    keyboard = RecordingKeyboard()
    # keymap pinned to GOOGLE_DOCS_MAC for consistency with the other
    # Driver(...) calls in this file -- SET_TITLE is absent from every
    # google-docs keymap (mac and Windows/Linux alike), so this specific
    # test isn't actually OS-sensitive, but pinning it removes any doubt.
    driver = Driver(
        keyboard=keyboard, sleep=lambda _: None, countdown_seconds=0, keymap=GOOGLE_DOCS_MAC
    )

    with pytest.raises(UnsupportedFormatError, match="set_title"):
        driver.run(actions)

    assert keyboard.text == ""
    assert keyboard.chords == []
