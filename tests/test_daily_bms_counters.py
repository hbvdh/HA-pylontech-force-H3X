"""Daily BMS energy counters (0x1100) and other group 1 value checks.

The daily counters come from a block read every few minutes; between reads
the cached value is repeated in every poll. The spike guard must measure the
allowed growth from when the value last changed, not from the previous poll.
"""

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "custom_components" / "pylon_fh3x" / "validation.py"
spec = importlib.util.spec_from_file_location("h3x_daily_validator_under_test", SOURCE)
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)

KEY = "bms_charge_energy_today"
POLL_S = 5
BLOCK_S = 300


class DailyCounterTests(unittest.TestCase):
    def setUp(self):
        self.validator = validation.TelemetryValidator()
        self.now = 0
        self.warnings = []
        clock = patch.object(validation, "time")
        self.addCleanup(clock.stop)
        clock.start().monotonic = lambda: self.now
        log = patch.object(validation._LOGGER, "warning", side_effect=self._warn)
        self.addCleanup(log.stop)
        log.start()

    def _warn(self, msg, *args):
        self.warnings.append(msg % args)

    def run_polls(self, value, seconds):
        """Poll every 5 s with the same cached value; return the last result."""
        out = None
        for _ in range(seconds // POLL_S):
            self.now += POLL_S
            out = self.validator.validate({KEY: value})
        return out

    def test_first_value_is_confirmed_by_the_next_poll(self):
        self.now += POLL_S
        self.assertNotIn(KEY, self.validator.validate({KEY: 2.0}))
        self.now += POLL_S
        self.assertEqual(self.validator.validate({KEY: 2.0})[KEY], 2.0)

    def test_real_growth_between_slow_reads_is_accepted_without_warning(self):
        # 5 kW charging: +0.42 kWh between two 0x1100 reads, 5 minutes apart.
        self.run_polls(2.00, BLOCK_S)
        out = self.run_polls(2.42, POLL_S)
        self.assertEqual(out[KEY], 2.42)
        self.assertEqual(self.warnings, [])

    def test_spike_is_rejected_even_after_a_long_flat_period(self):
        self.run_polls(2.00, BLOCK_S)
        self.run_polls(2.00, 3 * BLOCK_S)       # nothing charged for 15 min
        out = self.run_polls(150.0, POLL_S)     # garbage read
        self.assertNotIn(KEY, out)
        self.assertEqual(self.run_polls(2.05, POLL_S)[KEY], 2.05)

    def test_midnight_reset_is_accepted(self):
        self.run_polls(9.87, BLOCK_S)
        self.now += POLL_S
        self.assertNotIn(KEY, self.validator.validate({KEY: 0.0}))  # candidate
        out = self.run_polls(0.0, POLL_S)                              # confirmed
        self.assertEqual(out[KEY], 0.0)
        self.assertEqual(self.run_polls(0.31, BLOCK_S)[KEY], 0.31)

    def test_small_dip_is_still_rejected(self):
        self.run_polls(9.87, BLOCK_S)
        self.assertNotIn(KEY, self.run_polls(9.50, BLOCK_S))

    def test_after_the_block_paused_the_value_starts_fresh(self):
        # Paused block (no answer): values dropped for an hour, then 3 kWh more.
        self.run_polls(2.00, BLOCK_S)
        for _ in range(3600 // POLL_S):
            self.now += POLL_S
            self.validator.validate({})
        self.now += POLL_S
        self.validator.validate({KEY: 5.0})
        self.assertEqual(self.run_polls(5.0, POLL_S)[KEY], 5.0)
        self.assertEqual(self.warnings, [])


class RangeTests(unittest.TestCase):
    def test_hot_terminal_is_shown_not_filtered(self):
        out = validation.TelemetryValidator().validate({"bms_terminal_temperature_max": 95.0})
        self.assertEqual(out["bms_terminal_temperature_max"], 95.0)

    def test_unknown_grid_mode_is_rejected(self):
        with patch.object(validation._LOGGER, "warning"):
            out = validation.TelemetryValidator().validate({"grid_mode": 7})
        self.assertNotIn("grid_mode", out)

    def test_daily_counter_out_of_range_is_rejected(self):
        with patch.object(validation._LOGGER, "warning"):
            out = validation.TelemetryValidator().validate({KEY: 1_000.0})
        self.assertNotIn(KEY, out)


if __name__ == "__main__":
    unittest.main()
