"""Tests for the dongle back-off when the H3X stops answering."""
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from test_coordinator import coordinator_module, validation_module, valid_blocks


class BackoffTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.coordinator = coordinator_module.PylontechCoordinator(None, "test", 502)
        self.coordinator.use_single_block_read = False
        self.blocks = valid_blocks()
        self.down = False
        self.calls = 0
        self.now = 0
        self.coordinator.client.read_holding_registers.side_effect = self.response
        for patcher in (
            patch.object(coordinator_module.asyncio, "sleep", new=AsyncMock()),
            patch.object(validation_module, "time", SimpleNamespace(monotonic=lambda: self.now)),
            patch.object(validation_module._LOGGER, "warning"),
            patch.object(coordinator_module._LOGGER, "warning"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def response(self, *, address, count, **kwargs):
        self.calls += 1
        if self.down:
            return SimpleNamespace(isError=lambda: True, registers=[])
        return SimpleNamespace(isError=lambda: False, registers=self.blocks[address])

    async def poll(self):
        self.now += 5
        return await self.coordinator._async_update_data()

    async def test_dead_dongle_poll_stops_after_three_failed_reads(self):
        self.down = True
        with self.assertRaises(coordinator_module.UpdateFailed):
            await self.poll()
        # 3 reads, each tried twice, instead of all ~9 reads tried twice.
        self.assertEqual(self.calls, 6)

    async def test_interval_grows_after_repeated_bad_polls_and_recovers(self):
        normal = timedelta(seconds=coordinator_module.DEFAULT_SCAN_INTERVAL)
        self.down = True
        for _ in range(2):
            with self.assertRaises(coordinator_module.UpdateFailed):
                await self.poll()
        self.assertEqual(self.coordinator.update_interval, normal)
        with self.assertRaises(coordinator_module.UpdateFailed):
            await self.poll()
        self.assertEqual(self.coordinator.update_interval, timedelta(seconds=60))
        self.down = False
        data = await self.poll()
        self.assertIn("battery_power", data)
        self.assertEqual(self.coordinator.update_interval, normal)

    async def test_single_transient_failure_does_not_back_off(self):
        original = self.response
        state = {"failed": False}

        async def one_failure(*, address, count, **kwargs):
            if address == 40848 and not state["failed"]:
                state["failed"] = True
                return SimpleNamespace(isError=lambda: True, registers=[])
            return await original(address=address, count=count, **kwargs)

        self.coordinator.client.read_holding_registers.side_effect = one_failure
        data = await self.poll()
        self.assertIn("battery_power", data)
        self.assertEqual(self.coordinator._bad_polls, 0)


if __name__ == "__main__":
    unittest.main()
