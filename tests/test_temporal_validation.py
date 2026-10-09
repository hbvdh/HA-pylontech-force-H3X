"""Temporal anomaly regression tests for the H3X coordinator.

These tests use the real decoder and coordinator with stubbed Modbus IO.
No physical inverter or Home Assistant installation is contacted.
"""

import unittest
from unittest.mock import AsyncMock

from test_coordinator import coordinator_module, valid_blocks
TelemetryTransitionGuard = coordinator_module.TelemetryTransitionGuard


class TemporalGuardUnitTests(unittest.TestCase):
    def setUp(self):
        self.guard = TelemetryTransitionGuard()
        self.guard.remember({"heatsink_temperature": 35.0, "battery_soc": 50}, now=100)

    def test_zero_heatsink_is_temporally_suspicious_but_within_static_range(self):
        self.assertTrue(self.guard.is_suspicious("heatsink_temperature", 0, now=105))
        self.assertFalse(self.guard.is_suspicious("heatsink_temperature", 35.2, now=105))

    def test_stale_history_does_not_block_a_new_reading(self):
        self.assertFalse(self.guard.is_suspicious("heatsink_temperature", 0, now=300))

    def test_power_step_is_not_temporally_restricted(self):
        self.assertFalse(self.guard.is_suspicious("battery_power", 5000, now=105))
        self.assertFalse(self.guard.is_suspicious("charge_discharge_power", -1000, now=105))

    def test_hot_temperature_rise_remains_visible_if_unconfirmed(self):
        self.assertTrue(self.guard.is_suspicious("heatsink_temperature", 80, now=105))
        self.assertFalse(self.guard.should_suppress_unconfirmed("heatsink_temperature", 80))
        self.assertTrue(self.guard.should_suppress_unconfirmed("heatsink_temperature", 0))

    def test_soc_jump_is_suspicious_and_requires_confirmation(self):
        self.assertTrue(self.guard.is_suspicious("battery_soc", 0, now=105))
        self.assertTrue(self.guard.should_suppress_unconfirmed("battery_soc", 0))

    def test_baseline_keeps_previous_value_when_field_is_missing(self):
        self.guard.remember({"battery_soc": 51}, now=105)
        self.assertEqual(self.guard._accepted["heatsink_temperature"], (35.0, 100))
        self.assertEqual(self.guard._accepted["battery_soc"], (51, 105))


class TemporalCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.coordinator = coordinator_module.PylontechCoordinator(None, "test", 502)
        self.coordinator._transition_guard.remember({
            "heatsink_temperature": 35.0,
            "inverter_temperature": 30.0,
            "battery_soc": 50,
        })

    def setup_blocks(self, first_heatsink, retry_heatsink):
        blocks = valid_blocks()
        first = blocks[30100].copy()
        first[47] = first_heatsink
        retry = None
        if retry_heatsink is not None:
            retry = blocks[30100].copy()
            retry[47] = retry_heatsink
        calls = {30100: 0}

        async def read(address, count, slave):
            if address == 30100:
                calls[30100] += 1
                return first if calls[30100] == 1 else retry
            return blocks[address]

        self.coordinator.safe_read = AsyncMock(side_effect=read)
        return calls

    async def test_isolated_zero_heatsink_recovers_by_second_read(self):
        calls = self.setup_blocks(0, 352)
        data = await self.coordinator._async_update_data()
        self.assertEqual(calls[30100], 2)
        self.assertAlmostEqual(data["heatsink_temperature"], 35.2)
        self.assertEqual(data["ac_total_power"], 3000)

    async def test_failed_second_read_suppresses_only_heatsink(self):
        calls = self.setup_blocks(0, None)
        data = await self.coordinator._async_update_data()
        self.assertEqual(calls[30100], 2)
        self.assertNotIn("heatsink_temperature", data)
        self.assertEqual(data["inverter_temperature"], 30.0)
        self.assertEqual(data["ac_total_power"], 3000)
        self.assertEqual(self.coordinator._transition_guard._accepted["heatsink_temperature"][0], 35.0)

    async def test_two_consistent_zero_reads_confirm_real_change(self):
        calls = self.setup_blocks(0, 0)
        data = await self.coordinator._async_update_data()
        self.assertEqual(calls[30100], 2)
        self.assertEqual(data["heatsink_temperature"], 0)
        self.assertEqual(self.coordinator._transition_guard._accepted["heatsink_temperature"][0], 0)

    async def test_two_different_unlikely_temperatures_do_not_poison_history(self):
        calls = self.setup_blocks(0, 140)
        data = await self.coordinator._async_update_data()
        self.assertEqual(calls[30100], 2)
        self.assertNotIn("heatsink_temperature", data)
        self.assertEqual(data["ac_total_power"], 3000)
        self.assertEqual(self.coordinator._transition_guard._accepted["heatsink_temperature"][0], 35.0)

    async def test_unverified_high_temperature_remains_visible_for_safety(self):
        calls = self.setup_blocks(800, None)
        data = await self.coordinator._async_update_data()
        self.assertEqual(calls[30100], 2)
        self.assertEqual(data["heatsink_temperature"], 80)

    async def test_unconfirmed_soc_drop_suppressed_without_losing_battery_power(self):
        blocks = valid_blocks()
        first = blocks[30156].copy()
        first[26] = 0
        attempts = {30156: 0}

        async def read(address, count, slave):
            if address == 30156:
                attempts[30156] += 1
                return first if attempts[30156] == 1 else None
            return blocks[address]

        self.coordinator.safe_read = AsyncMock(side_effect=read)
        data = await self.coordinator._async_update_data()
        self.assertEqual(attempts[30156], 2)
        self.assertNotIn("battery_soc", data)
        self.assertEqual(data["battery_power"], 1000)

    async def test_normal_read_has_no_extra_temporal_retry(self):
        calls = self.setup_blocks(351, 352)
        data = await self.coordinator._async_update_data()
        self.assertEqual(calls[30100], 1)
        self.assertAlmostEqual(data["heatsink_temperature"], 35.1)


if __name__ == "__main__":
    unittest.main()
