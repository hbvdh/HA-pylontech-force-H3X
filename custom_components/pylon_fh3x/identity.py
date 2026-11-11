"""Check that the device answering is the H3X this entry was set up for.

The serial number is not part of any entity ID. It is only remembered next to
the connection settings, so that:

- a different device that got the old IP address is never written to;
- a replaced H3X (new serial number) can be accepted with one confirmation,
  keeping every entity and its history.

The H3X dongle occasionally returns corrupt data. One odd reading must
therefore never change anything: a serial number is only learned, and a
different one only reported, after two identical readings in a row.
"""
import re

_SERIAL_RE = re.compile(r"[A-Za-z0-9._\-]{6,32}")

IGNORED = "ignored"        # not a usable serial number; nothing changed
OK = "ok"                  # the expected serial number
PENDING = "pending"        # seen once; needs a second identical reading
LEARNED = "learned"        # first serial number confirmed and remembered
MISMATCH_NEW = "mismatch_new"  # another serial number, now confirmed
MISMATCH = "mismatch"      # still the same other serial number
CLEARED = "cleared"        # the expected serial number answers again


def usable_serial(serial) -> bool:
    """True for a plausible serial number (not empty, not corrupt)."""
    return isinstance(serial, str) and _SERIAL_RE.fullmatch(serial) is not None


class IdentityGuard:
    """Remember the expected serial number and judge each new reading."""

    def __init__(self, expected: str | None = None) -> None:
        self.expected = expected
        # Confirmed serial number of another device, or None.
        self.mismatch: str | None = None
        # A serial number seen once that still needs a second reading.
        self._candidate: str | None = None

    @property
    def writes_blocked(self) -> bool:
        """True while the device is not known to be the configured H3X.

        Also true after a single deviating reading: refusing a write for a
        minute costs nothing, writing to the wrong device might.
        """
        return self.mismatch is not None or (
            self.expected is not None and self._candidate is not None
        )

    def observe(self, serial) -> str:
        """Process one serial number reading and return what it meant."""
        if not usable_serial(serial):
            return IGNORED
        if self.expected is None:
            if self._candidate == serial:
                self.expected = serial
                self._candidate = None
                return LEARNED
            self._candidate = serial
            return PENDING
        if serial == self.expected:
            was_mismatch = self.mismatch is not None
            self.mismatch = None
            self._candidate = None
            return CLEARED if was_mismatch else OK
        if serial == self.mismatch:
            self._candidate = None
            return MISMATCH
        if serial == self._candidate:
            self.mismatch = serial
            self._candidate = None
            return MISMATCH_NEW
        self._candidate = serial
        return PENDING

    def accept(self, serial: str) -> None:
        """The user confirmed that this serial number is the replacement."""
        self.expected = serial
        self.mismatch = None
        self._candidate = None
