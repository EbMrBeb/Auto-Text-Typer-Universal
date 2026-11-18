# Realism settings

Three built-in profiles differ in how often the typist makes mistakes and
second-guesses themselves:

| Preset | Mistakes per 100 characters | Revision chance per word |
|---|---|---|
| `clean` | 0.3 | 1 in 800 |
| `natural` | 1.2 | 1 in 500 |
| `sloppy` | 3.0 | 1 in 300 |

All three start from the same 65 words per minute. The Type page's Quick
settings adjust speed and mistake rate directly without going near a profile:
a speed slider from 20 to 160 wpm, and five mistake stops from None through
Natural to Sloppy.

Underneath, the realism comes from three stacked timing effects: a **lognormal**
base keystroke interval, because real keystroke timing is right-skewed rather
than uniform (uniform jitter is the classic tell of automation and is used
nowhere); **digraph multipliers** derived from QWERTY geometry, so same-finger
pairs are slow and alternating-hand pairs are fast; and a **mean-reverting
burst walk**, which produces stretches of fast and slow typing instead of
per-character noise. Thinking pauses sit on top of those at sentence, clause and
paragraph boundaries, and a fatigue term slows a long session down.

With session breaks on — the default — the planner also tracks simulated
elapsed time and inserts a real break every 7 to 15 minutes of typing, at a
sentence or paragraph boundary only. A break is 20 to 90 seconds, or with 20%
probability a 2 to 8 minute one. Coming back from a break starts with a
re-entry dip rather than an instant speed-up, and partially clears the fatigue
clock. An unbroken 40-minute typing session is itself a tell.

## The Advanced page

Everything above is a view onto one `Profile` object with 27 fields. The
**Advanced** page exposes all of them, in six collapsible groups — Speed,
Pauses, Mistakes, Revisions, Breaks, Formatting — with a label, a help line and
a control for each, carrying that field's usual range. The form is generated
from the model's own metadata, so it cannot drift out of step with the code.

The same page handles profile management: pick a profile, edit it, **Save** or
**Save as…** under a new name, **Delete** (which restores the built-in if you
had customised one), or **Reset to defaults**. A customised built-in shadows the
built-in everywhere.

Also on the Advanced page: a **Keyboard shortcuts** editor, one row per
formatting command per editor, for correcting a shortcut that is wrong in your
version of an application; and a **file locations** panel showing where the
profile and settings files live, with a button to open the folder. Both files
are plain text and can be edited by hand while the app is closed.
