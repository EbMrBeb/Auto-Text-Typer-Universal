# Permissions

Typing into another application means injecting keystrokes at the OS level, and
each OS has its own answer to whether that is allowed. The app checks this
before every run and explains what to do; it does not start typing into nothing.

## macOS — Accessibility permission is required

macOS blocks synthetic keystrokes unless the process has Accessibility access.
The app probes for it directly (`AXIsProcessTrusted`) rather than discovering
mid-run that nothing is landing, and reports the state in three places: the
first-run walkthrough, a dialog when you press Start without it, and a status
line at the bottom of the main window.

Grant it to **Auto Text Typer itself**:

1. Open **System Settings → Privacy & Security → Accessibility**.
2. Turn on **Auto Text Typer**.
3. **Quit and reopen Auto Text Typer.** macOS does not apply the change to a
   process that is already running.

The app has an **Open Settings** button that opens that pane directly, and a
**Check again** button, so you do not have to find it by hand.

If you are running from source rather than from a built app, the permission
belongs to whatever *launches* Python — Terminal, iTerm, or your IDE — not to
Python itself, and that launcher must also be quit and relaunched.

Global hotkeys need **Input Monitoring** as well as Accessibility on macOS. If
they cannot start, the main window shows a "Hotkeys unavailable" link to
Settings; typing itself still works.

## Windows — no permission needed

Nothing to grant. One caveat the app cannot check in advance, so it states it as
a note: to type into an application that is running **as administrator**, Auto
Text Typer has to be running as administrator too, or its keystrokes will not
reach it.

## Linux — X11 works, Wayland does not

- **X11 (Xorg):** works, with no permission to grant.
- **Wayland:** blocks one application from sending keystrokes to another
  window, and there is no permission dialog that can grant an exception. The
  app detects this and says so instead of typing nothing. The fix is to use an
  X11 session: log out, pick an X11/Xorg session at the login screen (for
  example "Ubuntu on Xorg", "GNOME on Xorg", "Plasma (X11)"), and log back in.
- **No graphical session at all** (a bare SSH login, a headless box): also
  cannot be typed into, and is reported as such.

Wayland sessions run an XWayland server that still sets `DISPLAY`, so the app
checks the Wayland signals first; otherwise a Wayland session would look like
X11 and fail silently. Under Wayland, global hotkeys may work only while an X11
window has focus.
