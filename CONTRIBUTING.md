# Contributing

Bug reports and pull requests are welcome. For anything larger than a small
fix, open an issue first so we can agree on the approach.

## Set up

Python 3.12 or newer.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,gui]"
```

Install `dev` and `gui` together: pytest-qt will not start without a Qt binding.
More in [Building from source](docs/building.md).

## Run the tests

```bash
.venv/bin/python -m pytest
```

On Linux, run under Xvfb: `xvfb-run -a .venv/bin/python -m pytest`. The suite
cannot type for real or listen to your keyboard. One test needs the network and
is skipped unless you set `ATT_NETWORK_TESTS=1`. CI runs the suite on macOS,
Windows and Linux.

## The invariant

For any text, profile and seed:

```
simulate(plan(text, profile, seed)) == text
```

`plan` is a pure function (no I/O, no clock, no keyboard) and the driver only
executes its result. Keep it that way. A change to the planner, timing, typo or
revision code must keep `tests/test_invariant.py` passing, and so must the
document equivalent in `tests/test_document_pipeline.py`. Do not add randomness
or I/O to planning that does not come from the seed.

## The import contract

An importer cannot turn every document into keystrokes, and it must say so
instead of dropping content silently.

- Every kind of loss is a member of `NoteKind` in
  `auto_essay_typer/importers/notes.py`. An importer counts losses under those
  members; a count under an unknown name raises.
- Every member needs a rule that words it for the user in
  `auto_essay_typer/gui/document_warnings.py`.
  `tests/gui/test_import_note_rules.py` fails when one is missing and says what
  to add.

To report a new kind of loss: add the member, word it in `messages_by_kind`,
count it in the importer, then add the GUI rule.

## Pull requests

- Add or update tests with the change.
- Keep the change focused, and update the matching page in `docs/` if you
  change behaviour.
- Do not commit secrets, build output or `.venv`.
