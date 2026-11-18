"""Screen-reader support that Qt's widgets do not provide on their own.

Qt exposes every standard widget to the platform accessibility API (VoiceOver,
Narrator/NVDA, Orca) and reads a control's accessible name and value on
demand. What it cannot do is *speak* something unprompted: a progress bar's
value is read when the user navigates to it, but nobody is navigating while
the app types into another window. :func:`announce` sends the one thing that
does that -- an announcement event -- and :class:`RunAnnouncer` uses it to
narrate a typing run's milestones.

Everything here is a silent no-op when no assistive technology is running, so
it costs nothing for most users and can never raise.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject
from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent
from PySide6.QtWidgets import QWidget

from auto_essay_typer.gui.run_controller import RunController

#: Progress percentages announced during a run: often enough to reassure,
#: rarely enough not to talk over the document the user is watching.
PROGRESS_MILESTONES = (25, 50, 75)

Announcer = Callable[[QObject, str, bool], bool]


def spoken_duration(seconds: float) -> str:
    """``seconds`` the way a person says it: "1 minute 27 seconds", "12 seconds".

    The on-screen forms ("1:27", "~1 min 27 s") read badly through a screen
    reader, which tends to say "min" and "s" as letters.
    """
    total = max(0, int(round(seconds)))
    minutes, secs = divmod(total, 60)
    parts = []
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if secs or not minutes:
        parts.append(f"{secs} second{'s' if secs != 1 else ''}")
    return " ".join(parts)


def announce(anchor: QObject, message: str, assertive: bool = False) -> bool:
    """Ask the screen reader to speak ``message``; True if the request was sent.

    ``anchor`` is the widget the announcement is attributed to (the platform
    needs one). ``assertive`` interrupts whatever is being read -- reserve it
    for failures; the default waits its turn.
    """
    if not message or not QAccessible.isActive():
        return False
    event = QAccessibleAnnouncementEvent(anchor, message)
    event.setPoliteness(
        QAccessible.AnnouncementPoliteness.Assertive
        if assertive
        else QAccessible.AnnouncementPoliteness.Polite
    )
    QAccessible.updateAccessibility(event)
    return True


class RunAnnouncer(QObject):
    """Narrates a :class:`RunController`'s run to a screen reader.

    Listens only to the controller's signals (the same contract the Type page
    and the mini window use), so it works whichever of them is on screen and
    needs no cooperation from either. ``announcer`` is injectable so tests can
    capture what would have been spoken.
    """

    def __init__(
        self,
        run_controller: RunController,
        anchor: QWidget,
        announcer: Announcer = announce,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent if parent is not None else anchor)
        self._anchor = anchor
        self._announce = announcer
        self._spoken_milestones: set[int] = set()
        self._countdown_announced = False

        run_controller.stateChanged.connect(self._on_state_changed)
        run_controller.countdownTick.connect(self._on_countdown_tick)
        run_controller.progressChanged.connect(self._on_progress_changed)
        run_controller.finished.connect(self._on_finished)

    def _say(self, message: str, *, assertive: bool = False) -> None:
        self._announce(self._anchor, message, assertive)

    def _on_state_changed(self, state: str) -> None:
        if state == "planning":
            self._spoken_milestones.clear()
            self._countdown_announced = False
        elif state == "typing":
            self._say("Typing")
        elif state == "paused":
            self._say("Paused")
        elif state == "resuming":
            self._countdown_announced = False

    def _on_countdown_tick(self, seconds_left: int) -> None:
        # Only the first tick: a spoken 3-2-1 would talk over the user, who is
        # busy clicking into the document the text is about to go into.
        if not self._countdown_announced:
            self._countdown_announced = True
            self._say(f"Click where the text should go. Typing starts in {seconds_left} seconds.")

    def _on_progress_changed(self, done: int, total: int, _eta: float, _elapsed: float) -> None:
        if not total:
            return
        percent = int(done / total * 100)
        for milestone in PROGRESS_MILESTONES:
            if percent >= milestone and milestone not in self._spoken_milestones:
                self._spoken_milestones.add(milestone)
                self._say(f"{milestone} percent")

    def _on_finished(self, outcome: str, message: str) -> None:
        if outcome == "finished":
            self._say("Finished typing")
        elif outcome == "stopped":
            self._say("Stopped")
        else:
            self._say(f"Typing failed. {message}".strip(), assertive=True)
