"""Conservative, reusable temporal anomaly checks for H3X telemetry.

A jump is a reason to *verify*, not an absolute operating limit. Never
replace readings with interpolated or cached data. The coordinator must use
this guard alongside its existing range and counter validator.
"""

from dataclasses import dataclass
import math
import time


@dataclass(frozen=True)
class TransitionProfile:
    # Allowed absolute change plus additional allowance per elapsed second.
    jump: float
    rate: float
    window: float
    agreement: float
    # A sudden temperature increase could be a real overheating event:
    # if the retry is unavailable, do not hide it from HA.
    suppress_rise_without_confirmation: bool = True


# Deliberately conservative first rollout. Add profiles only after proving
# their thresholds with device data; power can legitimately jump instantly.
TRANSITION_PROFILES = {
    "heatsink_temperature": TransitionProfile(12.0, 0.4, 60.0, 1.5, False),
    "inverter_temperature": TransitionProfile(12.0, 0.4, 60.0, 1.5, False),
    "bms_temperature": TransitionProfile(12.0, 0.4, 60.0, 1.5, False),
    "battery_soc": TransitionProfile(15.0, 0.10, 60.0, 2.0),
    "bms_soc": TransitionProfile(15.0, 0.10, 60.0, 2.0),
}


class TelemetryTransitionGuard:
    """Track accepted readings and flag unlikely changes over short intervals."""

    def __init__(self, profiles=None):
        self.profiles = TRANSITION_PROFILES if profiles is None else profiles
        self._accepted = {}  # key -> (value, monotonic_timestamp)

    def is_suspicious(self, key, value, now=None):
        rule = self.profiles.get(key)
        previous = self._accepted.get(key)
        if rule is None or previous is None:
            return False
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            # Static validator handles non-finite or out-of-range data.
            return False
        previous_value, previous_time = previous
        now = time.monotonic() if now is None else now
        elapsed = now - previous_time
        return (
            0 <= elapsed <= rule.window
            and abs(value - previous_value) > rule.jump + rule.rate * elapsed
        )

    def suspicious(self, values, now=None):
        now = time.monotonic() if now is None else now
        return {
            key for key, value in values.items()
            if self.is_suspicious(key, value, now)
        }

    def confirmed(self, key, first, second):
        rule = self.profiles[key]
        return (
            isinstance(first, (int, float)) and math.isfinite(first)
            and isinstance(second, (int, float)) and math.isfinite(second)
            and abs(first - second) <= rule.agreement
        )

    def should_suppress_unconfirmed(self, key, value):
        """Do not hide possible true thermal runaway on a missing retry."""
        rule = self.profiles[key]
        if rule.suppress_rise_without_confirmation:
            return True
        previous = self._accepted.get(key)
        return previous is not None and value < previous[0]

    def remember(self, accepted, now=None):
        """Update baseline ONLY from values already accepted by validator."""
        now = time.monotonic() if now is None else now
        for key in self.profiles:
            value = accepted.get(key)
            if isinstance(value, (int, float)) and math.isfinite(value):
                self._accepted[key] = (value, now)
