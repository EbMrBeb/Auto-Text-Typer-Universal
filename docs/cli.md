# The command line

A headless entry point for plain text. It types a text file and nothing else —
document import, formatting and the editor picker are GUI-only.

```bash
auto-text-typer essay.txt --preset natural --wpm 70
auto-text-typer essay.txt --dry-run          # preview, no typing
```

From a source checkout, without installing the console script:

```bash
.venv/bin/python -m auto_essay_typer.cli essay.txt --preset natural --wpm 70
```

It takes one positional argument, the path to the text file to type, plus:

| Flag | What it does |
|---|---|
| `--preset PRESET` | Named typing profile: a builtin (`clean`, `natural`, `sloppy`) or a saved user profile. Defaults to `natural` |
| `--profile PROFILE` | Alias for `--preset`; whichever of the two is given wins |
| `--wpm WPM` | Target words per minute |
| `--typo-rate TYPO_RATE` | Mistakes per 100 characters |
| `--set KEY=VALUE` | Override a single profile field, e.g. `--set wpm=90`. Repeatable |
| `--seed SEED` | Reproduce an exact run |
| `--countdown COUNTDOWN` | Seconds to focus the target window. Defaults to 5 |
| `--dry-run` | Print what would be typed and how long it would take, then exit |
| `--list-profiles` | List every available profile, builtin and user-saved, then exit |
| `--show-profile` | Print the fully resolved profile as TOML, then exit |
| `--save-profile NAME` | Save the fully resolved profile under NAME, then exit |
| `--delete-profile NAME` | Delete a previously saved user profile, then exit |
| `--config-path` | Print the path to the user profiles config file, then exit |

Precedence, lowest to highest: `--preset`/`--profile` < `--wpm`/`--typo-rate` <
`--set`. The last five need no `file` argument.

`--set KEY=VALUE` reaches any of the 27 profile fields by name; range fields
take a comma-separated pair, as in `--set burst_range=0.7,1.3`. Saved profiles
are hand-editable TOML, and a malformed file, an unknown field or a wrong-sized
range raises an error naming the file and the bad field, so a typo in the config
is never silently ignored.

```bash
.venv/bin/python -m auto_essay_typer.cli essay.txt --profile natural --set wpm=90 --set session_breaks=false
.venv/bin/python -m auto_essay_typer.cli --preset natural --wpm 80 --save-profile my-fast
.venv/bin/python -m auto_essay_typer.cli --list-profiles
```

Ctrl-C stops a run.
