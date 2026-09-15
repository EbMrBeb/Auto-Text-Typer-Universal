"""Load a plain-text source (``.txt`` or ``.md``) as a ``Document``.

This project never interprets Markdown syntax -- a ``.md`` file is typed
exactly as its characters read, asterisks and all, because there is no
keyboard shortcut a person could press that would make Google Docs turn
literal ``**bold**`` text into actually-bold text after the fact. Treating
``.md`` as anything other than plain text would silently misrepresent what
gets typed, which this project's import report exists specifically to avoid
doing for everything else -- so plain text it is, one paragraph per line,
via ``Document.from_plain_text``.
"""

from __future__ import annotations

from auto_essay_typer.document import Document


def load_plain_text(data: bytes) -> Document:
    """Decode ``data`` as text and wrap it into an unstyled ``Document``.

    CRLF and lone-CR line endings (a file authored on Windows, or older
    macOS/classic-Mac tooling) are normalized to ``\\n`` first, so a file's
    origin never changes how many paragraphs it becomes. Undecodable bytes
    are replaced rather than raising -- an import report is meant to survive
    a slightly malformed input and say what happened, not crash on one.
    """
    text = data.decode("utf-8", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return Document.from_plain_text(text)
