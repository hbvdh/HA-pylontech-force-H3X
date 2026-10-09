"""Regression tests for implausible H3X lifetime grid energy-counter jumps."""

import importlib.util
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "custom_components" / "pylon_fh3x" / "validation.py"
spec = importlib.util.spec_from_file_location("h3x_grid_validator_under_test", SOURCE)
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)


class TestGridEnergyCounterGuard(unittest.TestCase):
    def test_known_081_kwh_jump_in_100_seconds_is_rejected(self):
        for key in ("total_grid_import", "total_grid_export"):
            with self.subTest(key=key):
                self.assertFalse(validation.TelemetryValidator._counter_step_valid(key, 206.06, 206.87, 100))

    def test_small_batched_updates_are_accepted(self):
        self.assertTrue(validation.TelemetryValidator._counter_step_valid("total_grid_import", 206.0, 206.1, 15))
        self.assertTrue(validation.TelemetryValidator._counter_step_valid("total_grid_import", 206.0, 206.0, 1))

    def test_short_interval_spike_exceeding_tolerance_is_rejected(self):
        self.assertFalse(validation.TelemetryValidator._counter_step_valid("total_grid_export", 112.30, 112.35, 1))

    def test_reject_spike_then_accept_real_values_without_reset(self):
        validator = validation.TelemetryValidator()
        key = "total_grid_import"
        self.assertFalse(validator._counter_valid(key, 206.05, -5))
        self.assertTrue(validator._counter_valid(key, 206.06, 0))
        self.assertFalse(validator._counter_valid(key, 206.87, 100))
        self.assertEqual(validator._counters[key][0], 206.06)
        self.assertTrue(validator._counter_valid(key, 206.07, 120))
        self.assertEqual(validator._counters[key][0], 206.07)

    def test_small_fall_still_rejected_to_protect_ha_statistics(self):
        validator = validation.TelemetryValidator()
        key = "total_grid_import"
        self.assertFalse(validator._counter_valid(key, 206.0, 0))
        self.assertTrue(validator._counter_valid(key, 206.01, 10))
        self.assertFalse(validator._counter_valid(key, 206.005, 20))
        self.assertFalse(validator._counter_valid(key, 206.006, 30))
        self.assertEqual(validator._counters[key][0], 206.01)

    def test_other_existing_limits_unchanged(self):
        self.assertEqual(validation.GRID_POWER_LIMIT, 1_000_000)
        self.assertEqual(validation.VALUE_RANGES["grid_total_power"], (-1_000_000, 1_000_000))
        self.assertEqual(validation.COUNTER_RATES["pv_total_energy"], validation.PV_POWER_LIMIT / 3_600_000)
        self.assertEqual(validation.COUNTER_RATES["total_battery_charge"], validation.INVERTER_POWER_LIMIT / 3_600_000)


if __name__ == "__main__":
    unittest.main()
