"""Inter-keystroke interval model.

Three effects stack to produce the timing signature of a real typist:

1. A **lognormal** base distribution, which has the long right tail that real
   keystroke intervals show. Uniform jitter is the classic tell of automation
   and is used nowhere in this module.
2. **Digraph multipliers** from the physical layout -- same-finger pairs are
   slow, alternating-hand pairs are fast.
3. A **mean-reverting burst walk**, which produces stretches of fast and slow
   typing rather than per-character white noise. The walk's value multiplies
   the keystroke interval directly, so this is an *interval* scale, not a
   speed: values above 1.0 slow typing down, values below 1.0 speed it up.

On top of those sit thinking pauses at sentence, clause, and paragraph
boundaries.
"""

import math
import random

from auto_essay_typer import layout
from auto_essay_typer.profiles import Profile

_SENTENCE_ENDS = frozenset(".?!")
_CLAUSE_ENDS = frozenset(",;:")

# Fingers 0-4 and 6-9 exclude the thumbs; row 0 is the number/symbol row.
_SAME_FINGER = 1.6
_SAME_HAND = 1.05
_ALTERNATING = 0.85
_REPEATED_KEY = 0.9
_SPACE = 0.9
_NUMBER_ROW = 1.4


class TimingModel:
    """Stateful sampler for keystroke intervals and pauses.

    Stateful because burst and fatigue both depend on history. Construct one
    per plan and never share across plans -- doing so would leak state between
    runs and break reproducibility.
    """

    def __init__(self, profile: Profile, rng: random.Random) -> None:
        self.profile = profile
        self._rng = rng
        self.burst = 1.0
        self._elapsed_ms = 0.0

    # -- base -------------------------------------------------------------

    def base_interval_ms(self) -> float:
        """Mean milliseconds per character implied by the configured WPM."""
        return 60_000.0 / (self.profile.wpm * 5.0)

    def digraph_multiplier(self, previous: str | None, current: str) -> float:
        """Speed multiplier for typing ``current`` right after ``previous``."""
        info = layout.key_info(current)
        if info is None:
            return 1.0

        if previous is None:
            multiplier = 1.0
        elif layout.key_info(previous) is None:
            multiplier = 1.0
        elif previous.lower() == current.lower():
            multiplier = _REPEATED_KEY
        elif layout.same_finger(previous, current):
            multiplier = _SAME_FINGER
        elif not layout.same_hand(previous, current):
            multiplier = _ALTERNATING
        else:
            multiplier = _SAME_HAND

        if current == " ":
            multiplier *= _SPACE
        elif info.row == 0:
            multiplier *= _NUMBER_ROW
        return multiplier

    def shift_penalty_ms(self, previous: str | None, current: str) -> float:
        """Cost of reaching for shift, charged only when entering shift state.

        Typing ``AB`` holds shift down once, so only the ``A`` pays.
        """
        if layout.key_info(current) is None or not layout.needs_shift(current):
            return 0.0
        if previous is not None and layout.needs_shift(previous):
            return 0.0
        return self._rng.uniform(40.0, 90.0)

    # -- state ------------------------------------------------------------

    def advance_ms(self, milliseconds: float) -> None:
        """Advance the fatigue clock, e.g. across a long thinking pause."""
        self._elapsed_ms += milliseconds

    def rest(self, milliseconds: float) -> None:
        """Reduce the fatigue clock by ``milliseconds``, floored at 0.

        A break genuinely refreshes a typist: without this, a break would
        make the *next* stretch of typing slower rather than faster, which
        is backwards.
        """
        self._elapsed_ms = max(0.0, self._elapsed_ms - milliseconds)

    @property
    def elapsed_ms(self) -> float:
        """Read-only view of the fatigue clock, in simulated milliseconds."""
        return self._elapsed_ms

    @property
    def fatigue_multiplier(self) -> float:
        """Slowdown from sustained typing, saturating at ``fatigue_max``."""
        if self.profile.fatigue_max == 0.0:
            return 1.0
        saturation = 1.0 - math.exp(-self._elapsed_ms / self.profile.fatigue_tau_ms)
        return 1.0 + self.profile.fatigue_max * saturation

    def _step_burst(self) -> float:
        """Advance the mean-reverting random walk and return the multiplier."""
        low, high = self.profile.burst_range
        drift = self.profile.burst_reversion * (1.0 - self.burst)
        shock = self._rng.gauss(0.0, self.profile.burst_sigma)
        self.burst = min(high, max(low, self.burst + drift + shock))
        return self.burst

    def reset_burst(self, value: float) -> None:
        """Set the burst-walk state to ``value``, clamped into ``burst_range``.

        Used when resuming from a break: ``post_break_burst`` is a
        standalone field, so a hand-edited config could set it outside the
        profile's own ``burst_range`` and desync the walk -- clamping here
        keeps the invariant (``low <= burst <= high``) intact at the moment
        of assignment rather than relying on the next ``_step_burst`` call
        to pull it back in.
        """
        low, high = self.profile.burst_range
        self.burst = min(high, max(low, value))

    # -- sampling ---------------------------------------------------------

    def keystroke_interval_ms(self, previous: str | None, current: str) -> float:
        """Sample the delay before typing ``current``, advancing all state."""
        mean = (
            self.base_interval_ms()
            * self.digraph_multiplier(previous, current)
            * self._step_burst()
            * self.fatigue_multiplier
        )
        sigma = self.profile.lognormal_sigma
        if sigma > 0.0:
            # Shift mu so that the lognormal's mean is the value computed above.
            mu = math.log(mean) - sigma**2 / 2.0
            interval = self._rng.lognormvariate(mu, sigma)
        else:
            interval = mean
        interval += self.shift_penalty_ms(previous, current)
        self.advance_ms(interval)
        return interval

    def backspace_interval_ms(self) -> float:
        """Sample one step of a correction burst -- fast, and tighter than typing."""
        low, high = self.profile.backspace_ms
        interval = self._rng.uniform(low, high)
        self.advance_ms(interval)
        return interval

    def thinking_pause_ms(self, previous: str | None, upcoming_word: str) -> float:
        """Extra pause after ``previous``, before ``upcoming_word``.

        Returns 0.0 when no boundary or hesitation applies. Recovery from
        fatigue during long pauses is modelled by *not* advancing the fatigue
        clock here -- resting does not tire you further.
        """
        if previous is None:
            return 0.0
        if previous == "\n":
            return self._rng.uniform(*self.profile.paragraph_pause_ms)
        if previous in _SENTENCE_ENDS:
            return self._rng.uniform(*self.profile.sentence_pause_ms)
        if previous in _CLAUSE_ENDS:
            return self._rng.uniform(*self.profile.clause_pause_ms)
        if (
            len(upcoming_word) >= self.profile.long_word_threshold
            and self._rng.random() < self.profile.hesitation_probability
        ):
            return self._rng.uniform(*self.profile.hesitation_ms)
        return 0.0
