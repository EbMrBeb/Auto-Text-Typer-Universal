# The guarantee

The text that ends up in your document is character-for-character identical to
the text you supplied. Every mistake the engine injects is provably corrected.

The reason this can be stated rather than hoped for is that planning is a pure
function. `plan(text, profile, seed)` does no I/O, touches no clock and no
keyboard, and returns a list of key-press, backspace, pause and formatting
actions; a separate driver executes that list. So the whole realism engine can
be checked by pure computation:

> `simulate(plan(text, profile, seed)) == text`

`simulate` replays the generated keystroke stream through a virtual text
buffer — appending characters, deleting on backspace, raising if a backspace
would run past the start — and the test suite asserts the result equals the
input. Every typo is constructed as a `(wrong, correct)` pair covering an exact
span of source text, so its correction is pure arithmetic and no typo can
survive into the output. A session break is nothing but an extra pause action,
so it cannot break the invariant either.

`tests/test_invariant.py` exercises that property roughly 21,500 times per run
(104 collected test items): 20 corpus texts across 5 stress profiles at 40 seeds
each, 10,000 random `(text, profile, seed)` combinations, 2,000 randomly
generated strings, 2,000 cases checking nothing ever backspaces past the start,
and 14 adversarial texts at 50 seeds each. The corpus runs from empty and
one-character input through pure whitespace, symbols, digits, accented and
non-Latin characters, an emoji outside the basic plane and tabs.

```bash
.venv/bin/python -m pytest tests/test_invariant.py -q
```

Formatted documents get the same treatment with a second referee:
`simulate_document(plan_document(doc)).normalized() == doc.normalized()`, in
`tests/test_document_pipeline.py`, which runs the whole import → adapt → plan →
drive pipeline end to end.
