# Typing, hotkeys and the tray

## Typing plain text

1. Paste or type your text into the box on the **Type** page. You can also drop
   a `.txt` or `.md` file onto it. A counter under the box shows characters,
   words and an estimated typing time.
2. Set the **Quick settings** above the box: which editor you are **Typing
   into**, which **Profile** to use, **Speed**, **Mistakes**, and whether to
   take breaks on long documents.
3. Press **Preview** before committing to a run. It plans the whole run and
   types nothing, showing the duration, effective words per minute, how many
   mistakes, revisions and breaks are coming, a timeline of the notable moments,
   and a line confirming the planned keystrokes replay back to exactly your text.
4. Press **Start typing** (or Ctrl/Cmd+Return). A countdown runs — five seconds
   by default, adjustable from 0 to 30 on the Settings page — during which you
   click into the document or field where the text should go.
5. Keep your hands off the mouse and keyboard while it types.
6. Press **Esc**, or the **Stop** button, to end a run early. **Pause** stops
   it where it is; resuming runs a short three-second countdown first.

While a run is going, a small progress window appears at the bottom right with
the percentage, ETA and Pause/Stop buttons. It never takes keyboard focus, so
your focus stays in the document being typed into. Turn it off under **Settings
→ Show mini progress window while typing**.

## Global hotkeys and the tray

Global hotkeys drive a run from any application, without switching back to the
window. The defaults:

| Action | macOS | Windows and Linux |
|---|---|---|
| Start typing what is on the Type page | `<cmd>+<alt>+<shift>+t` | `<ctrl>+<alt>+<shift>+t` |
| Pause, or resume | `<cmd>+<alt>+<shift>+p` | `<ctrl>+<alt>+<shift>+p` |
| Stop a run that is typing | `<esc>` | `<esc>` |

They can be turned off and rewritten under **Settings → Global hotkeys**. A
hotkey that would fire during ordinary typing is refused with an explanation
rather than saved: a bare letter or digit, or Shift plus one, is rejected
because it would trigger every time you typed it. So is a chord that duplicates
or contains another hotkey, and so is one the app presses itself as a formatting
shortcut. Function keys and `Esc` are allowed on their own.

The tray icon — the menu bar on macOS — carries **Show window**, **Start typing
from clipboard**, **Pause**/**Resume**, **Stop** and **Quit**, so a run can be
controlled while the window is out of the way. Not every Linux desktop has a
tray; where there is none, the app carries on without one. Because starting from
the tray or a hotkey means the text is not in front of you, **Settings → Confirm
before typing from the tray or a hotkey** asks first, and is on by default.
