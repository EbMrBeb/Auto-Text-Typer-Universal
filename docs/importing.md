# Importing a formatted document

Above the text box is an import row. Paste a **Google Docs link** into it and
press **Import**, or press **Browse…** to pick a file.

Accepted sources:

| Source | Notes |
|---|---|
| Google Docs link | Fetched via the document's own HTML export URL |
| Google Drive link | Only if the file is a Word `.docx` |
| `.docx` | A Word document |
| `.html` / `.htm` | A raw Google Docs HTML export |
| `.zip` | The Google Docs "Web page (.html, zipped)" download |
| `.txt` / `.md` | Plain text, with no formatting to recover |

Google Sheets, Slides and Forms links are rejected by name — there is no
keyboard-typing equivalent of a spreadsheet grid or a slide canvas.

**A Google Doc has to be shared before the app can read it.** There is no
sign-in and no OAuth: the app fetches the document the same way an anonymous
browser would, so its sharing must be set to **Anyone with the link can view**.
Otherwise the import fails with a message telling you to change the sharing, or
to use **File → Download → Microsoft Word (.docx)** and import that file
instead.

On a successful import the text box is replaced by a read-only view of the
document showing its real headings, bold, italic, underline, strikethrough,
alignment and lists. **Back to plain text** drops the formatting and lets you
edit the words again.

What the document model can reproduce: paragraph styles (Normal, Title,
Subtitle, Heading 1 to 6), alignment (left, centre, right, justified), bullet
and numbered lists including nesting, and bold, italic, underline and
strikethrough.

## "Typing into" — the editor picker

Formatting is reproduced by pressing the target editor's own keyboard
shortcuts, so the app has to know which editor is receiving the keystrokes.
That choice is the **Typing into** picker, in Quick settings on the Type page
(and as a default on the Settings page):

| Editor | Available on | Shortcuts checked against the vendor's own reference |
|---|---|---|
| Google Docs | macOS, Windows, Linux | Yes |
| Microsoft Word | macOS, Windows | No |
| LibreOffice Writer | macOS, Windows, Linux | No |
| TextEdit | macOS | No |
| Any app (plain text) | macOS, Windows, Linux | Yes (nothing to get wrong) |

Microsoft Word has no Linux release, and TextEdit is macOS-only, so neither is
offered where it does not exist. The three marked "No" are best-effort
transcriptions that have not been checked against a live install; the app says
so under the picker and points you at the Advanced page, where any individual
shortcut can be corrected.

Each editor also carries its own note, shown under the picker:

- **Google Docs** — on Windows and Linux, justified text is typed left-aligned,
  because the shortcut for it opens the browser's developer tools instead of
  reaching the document.
- **Microsoft Word** — turn off "Automatic bulleted lists" and "Automatic
  numbered lists" under AutoCorrect Options → AutoFormat As You Type. Lists are
  typed as plain text starting with "1." or "•", and Word would otherwise turn
  those into a list of its own.
- **LibreOffice Writer** — turn off Tools → AutoCorrect → While Typing, for the
  same reason.
- **TextEdit** — the document must be in rich text mode (Format → Make Rich
  Text) first. In plain text mode TextEdit ignores formatting shortcuts, so
  nothing would be formatted.
- **Any app (plain text)** — for a web form, a chat box, a terminal. Everything
  is typed as plain text, and each new paragraph presses Enter, which may send a
  message or run a command.

## "What will not be reproduced"

A document can contain things that cannot be produced by typing at all. The
app does not drop those silently. A panel titled **What will not be
reproduced** sits directly above the editor picker and lists every loss the
importer noticed, in plain language — for example that images will be left out
because images cannot be typed, that links will be typed as plain text rather
than as clickable links, that a table will be typed without its rows and columns
(each cell becomes one or more paragraphs, row by row), that a footnote's text
goes with its marker, that a Word document's header or footer will be left out,
or that custom fonts, sizes and colours will not survive.

Images, tables, text boxes, headers and footers, symbols from symbol fonts,
fonts, colours, links, footnotes, endnotes, comments and soft line breaks have
no representation in the document model at all, by design; this panel is how
their absence gets reported rather than hidden. A Word document with tracked
changes is the one case where the app has to choose for you: it is typed as its
final version (added text kept, deleted text left out), and the panel says so.
The list depends on the editor you picked and is recalculated the moment you
change it. Where a loss is caused by a missing shortcut rather than by the
document, the panel says which shortcut is missing for which editor — that is
the part you can fix on the Advanced page. When nothing is lost, the panel says
so instead.

## Turn off autocorrect in the target application

**This is the single biggest cause of imperfect output, and it is not the app's
fault.** The guarantee below is about the *keystroke stream*: the keys that get
pressed reproduce your text exactly. What the receiving application does with
those keystrokes afterwards is outside the app's control, and it cannot read
back what the target rendered.

Google Docs and Word in particular rewrite text *after* the keystrokes land:
straight quotes become smart quotes, the first letter of something gets
capitalised, `--` becomes an em dash. If that is switched on, the text on screen
can differ from your source even though every keystroke sent was correct. Turn
it off before a real run:

- **Google Docs:** Tools → Preferences → uncheck the substitution options.
- **Microsoft Word:** AutoCorrect settings → uncheck "Replace text as you
  type". Also uncheck "Automatic bulleted lists" and "Automatic numbered
  lists" under AutoFormat As You Type.
- **LibreOffice Writer:** Tools → AutoCorrect → uncheck While Typing.
