"""Importers: turn an external document into this project's ``Document`` model.

A source is either a Google Docs / Drive URL, or a local file (.docx, .html,
.htm, .zip, .txt, .md). ``load_document`` is the single dispatch point for
both; see it for the full contract. Every other name here is re-exported
from the submodule that owns it so callers only need to remember one import
path -- ``auto_essay_typer.importers`` -- rather than which of google.py /
gdocs_html.py / docx.py / plain.py happens to define a given error class.
"""
