"""Optional reads: device info, fault bits, AC-coupled PV, grid import limit.

Run with: python -m unittest discover -s tests -v
"""
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from test_coordinator import (
    coordinator_module,
    put_int,
    put_float,
    valid_blocks,
    validation_module,
)


# Intervals used in these tests: static info hourly, slow blocks every 30 s.
TEST_INTERVALS = {
    "READ_IDENTITY": 3600, "READ_DEVICE_INFO_EXT": 3600, "READ_BMS_DEVICE_INFO": 3600,
    "READ_FAULT_BITS": 30, "READ_SITE_LIMIT": 30, "READ_MONITOR_EXT": 30,
}
POLLS_PER_SLOW_READ = 6  # 6 polls x 5 s = 30 s
OPTIONAL_ADDRESSES = (30010, 30036, 0x1000, 30148, 30600, 40937)


def ascii_regs(text, count):
    raw = text.encode("ascii").ljust(count * 2, b"\x00")
    return list(struct.unpack(f">{count}H", raw))


def optional_blocks():
    blocks = valid_blocks()
    identity = [0] * 31
    identity[0:8] = ascii_regs("Pylontech", 8)
    identity[8:16] = ascii_regs("FH3X-5K", 8)
    identity[16:24] = ascii_regs("SN12345678", 8)
    blocks[30010] = identity
    ext = [0] * 43
    ext[0] = 5000
    ext[3:5] = [0x0100, 0x002B]     # V1.0.0.43
    ext[25:27] = [0x0100, 0x0007]   # V1.0.0.7
    blocks[30036] = ext
    bms = [0] * 31
    bms[10] = 0x0003                 # V0.3
    bms[30] = 0x0101                 # V1.1
    blocks[0x1000] = bms
    blocks[30148] = [0, 0b10, 0, 1 << 13, 0]  # fault bit 1, warning bit 13
    monitor = [0] * 60
    monitor[0] = 6                  # 30600 EPS current R 0.6 A
    put_int(monitor, 54, 0)         # 30654 AC-coupled PV R
    put_int(monitor, 56, -850)      # 30656 AC-coupled PV S
    put_int(monitor, 58, 0)         # 30658 AC-coupled PV T
    blocks[30600] = monitor
    blocks[40937] = [0, 7000]
    put_int(blocks[30100], 10, -840)
    put_float(blocks[30100], 12, 12.5)
    blocks[30100][17] = 0
    blocks[30100][18] = 0
    return blocks


class OptionalReadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.coordinator = coordinator_module.PylontechCoordinator(None, "test", 502)
        self.coordinator.use_single_block_read = False
        self.blocks = optional_blocks()
        self.refused = set()
        self.silent = set()
        self.calls = []
        self.now = 0
        self.coordinator.client.read_holding_registers.side_effect = self.response
        switches = [
            patch.object(coordinator_module, name, value)
            for name, value in TEST_INTERVALS.items()
        ]
        clock = SimpleNamespace(monotonic=lambda: self.now)
        for patcher in (
            *switches,
            patch.object(coordinator_module.asyncio, "sleep", new=AsyncMock()),
            patch.object(validation_module, "time", clock),
            patch.object(coordinator_module, "time", clock),
            patch.object(validation_module._LOGGER, "warning"),
            patch.object(coordinator_module._LOGGER, "warning"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def response(self, *, address, count, **kwargs):
        self.calls.append(address)
        if address in self.silent:
            raise TimeoutError("no response")
        if address in self.refused:
            # Modbus exception reply (illegal data address), as pymodbus returns it.
            return SimpleNamespace(isError=lambda: True, registers=None)
        return SimpleNamespace(isError=lambda: False, registers=self.blocks[address])

    async def poll(self):
        self.now += 5
        data = await self.coordinator._async_update_data()
        self.coordinator.data = data
        return data

    async def test_identity_is_reread_after_its_interval(self):
        for _ in range(3600 // 5):
            await self.poll()
        self.assertEqual(self.calls.count(30010), 1)
        self.assertEqual(self.calls.count(0x1000), 1)
        await self.poll()
        self.assertEqual(self.calls.count(30010), 2)
        self.assertEqual(self.calls.count(30036), 2)
        self.assertEqual(self.calls.count(0x1000), 2)

    async def test_identity_is_read_once_and_builds_device_info(self):
        await self.poll()
        await self.poll()
        self.assertEqual(self.calls.count(30010), 1)
        self.assertEqual(self.calls.count(0x1000), 1)
        entry = SimpleNamespace(entry_id="e1", title="Pylontech Force H3X")
        info = self.coordinator.build_device_info(entry)
        self.assertEqual(info["model"], "FH3X-5K")
        self.assertEqual(info["serial_number"], "SN12345678")
        self.assertEqual(info["sw_version"], "PCS V1.0.0.43")
        self.assertEqual(info["hw_version"], "BMS V0.3 / HMI V1.1")
        ident = self.coordinator.device_identity
        self.assertEqual(ident["pcs_afci_firmware_version"], "V1.0.0.7")
        self.assertEqual(ident["pcs_max_output_power"], 5000)

    async def test_free_values_from_existing_main_block(self):
        data = await self.poll()
        self.assertEqual(data["third_party_meter_power"], -840)
        self.assertEqual(data["fault_code"], 0)
        self.assertEqual(data["warning_code"], 0)

    async def test_slow_values_are_read_per_interval_and_cached(self):
        for _ in range(POLLS_PER_SLOW_READ):
            data = await self.poll()
            self.assertEqual(data["acc_power_total"], -850)
            self.assertEqual(data["acc_power_s"], -850)
            self.assertEqual(data["grid_import_power_limit"], 7000)
            self.assertEqual(data["fault_bits"], 0b10)
            self.assertEqual(data["warning_bits_extended"], 0)
        self.assertEqual(self.calls.count(30600), 1)
        await self.poll()
        self.assertEqual(self.calls.count(30600), 2)

    async def test_refused_block_is_disabled_and_never_triggers_backoff(self):
        self.refused.add(30600)
        interval = self.coordinator.update_interval
        for _ in range(POLLS_PER_SLOW_READ * 5):
            data = await self.poll()
            self.assertIn("battery_soc", data)
        # three attempts, then switched off until restart
        self.assertEqual(self.calls.count(30600), coordinator_module.OPTIONAL_MAX_FAILURES)
        self.assertIn(30600, self.coordinator._optional_disabled)
        self.assertEqual(self.coordinator._bad_polls, 0)
        self.assertEqual(self.coordinator.update_interval, interval)
        # a refusal is a normal Modbus reply: no reconnect
        self.coordinator.client.close.assert_not_called()
        # other optional blocks keep working
        self.assertEqual(data["grid_import_power_limit"], 7000)

    async def test_ok_reads_log_at_debug_and_failures_at_warning(self):
        self.refused.add(40937)
        with patch.object(coordinator_module._LOGGER, "debug") as debug, \
                patch.object(coordinator_module._LOGGER, "warning") as warning:
            await self.poll()
        ok = [c for c in debug.call_args_list if "OK in" in c.args[0]]
        failed = [c for c in warning.call_args_list if "FAILED" in c.args[0]]
        self.assertTrue(ok)
        self.assertFalse(any("OK in" in c.args[0] for c in warning.call_args_list))
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].args[1], 40937)

    async def test_optional_reads_skipped_when_a_normal_read_failed(self):
        self.refused.add(30183)  # normal read fails this poll
        await self.poll()
        self.assertNotIn(30010, self.calls)
        self.assertNotIn(30148, self.calls)
        self.refused.discard(30183)
        await self.poll()
        self.assertIn(30010, self.calls)

    async def test_master_switch_disables_all_optional_reads(self):
        with patch.object(coordinator_module, "ENABLE_OPTIONAL_READS", False):
            for _ in range(POLLS_PER_SLOW_READ):
                await self.poll()
        for address in OPTIONAL_ADDRESSES:
            self.assertNotIn(address, self.calls)

    async def test_unanswered_block_is_paused_then_retried(self):
        pause = coordinator_module.OPTIONAL_PAUSE_S
        self.silent.add(30600)
        for _ in range(POLLS_PER_SLOW_READ * 3):
            data = await self.poll()
        self.assertEqual(self.calls.count(30600), 1)
        self.assertNotIn(30600, self.coordinator._optional_disabled)
        self.assertEqual(self.coordinator._bad_polls, 0)
        self.assertIn("battery_soc", data)
        # still paused just before the pause ends
        while self.now + 5 < 5 + pause:
            await self.poll()
        self.assertEqual(self.calls.count(30600), 1)
        # one new attempt after the pause; it answers, normal interval resumes
        self.silent.discard(30600)
        data = await self.poll()
        self.assertEqual(self.calls.count(30600), 2)
        self.assertEqual(data["acc_power_total"], -850)
        for _ in range(POLLS_PER_SLOW_READ):
            await self.poll()
        self.assertEqual(self.calls.count(30600), 3)

    async def test_paused_block_drops_its_cached_values(self):
        data = await self.poll()
        self.assertEqual(data["fault_bits"], 0b10)
        self.assertEqual(data["firmware_update_state"], 0)
        self.silent.add(30148)
        for _ in range(POLLS_PER_SLOW_READ):
            data = await self.poll()
        self.assertEqual(self.calls.count(30148), 2)
        self.assertNotIn("fault_bits", data)
        self.assertNotIn("warning_bits", data)
        self.assertNotIn("firmware_update_state", data)
        # other blocks keep their values
        self.assertEqual(data["grid_import_power_limit"], 7000)

    async def test_disabled_block_drops_its_cached_values(self):
        data = await self.poll()
        self.assertEqual(data["grid_import_power_limit"], 7000)
        self.refused.add(40937)
        for _ in range(POLLS_PER_SLOW_READ * coordinator_module.OPTIONAL_MAX_FAILURES):
            data = await self.poll()
        self.assertIn(40937, self.coordinator._optional_disabled)
        self.assertNotIn("grid_import_power_limit", data)
        self.assertEqual(data["fault_bits"], 0b10)

    async def test_eps_current_is_decoded_from_its_own_block(self):
        self.blocks[30600][27] = 1           # 30627: extended fault bit 0
        self.blocks[30600][29] = 1 << 2      # 30629: extended warning bit 2
        data = await self.poll()
        self.assertEqual(data["fault_bits_extended"], 1)
        self.assertEqual(data["warning_bits_extended"], 4)
        self.assertEqual(data["acc_power_s"], -850)
        self.assertAlmostEqual(data["eps_current_r"], 0.6, places=3)
        self.assertEqual(data["eps_current_s"], 0)
        self.assertIn(30600, self.calls)

    async def test_eps_current_out_of_range_is_rejected(self):
        self.blocks[30600][0] = 5000
        data = await self.poll()
        self.assertNotIn("eps_current_r", data)
        self.assertEqual(data["grid_import_power_limit"], 7000)

    async def test_single_refusal_keeps_the_cached_value(self):
        await self.poll()
        self.refused.add(40937)
        for _ in range(POLLS_PER_SLOW_READ):
            data = await self.poll()
        self.assertEqual(self.calls.count(40937), 2)
        self.assertEqual(data["grid_import_power_limit"], 7000)

    async def test_blocks_have_their_own_interval(self):
        with patch.object(coordinator_module, "READ_SITE_LIMIT", 300):
            for _ in range(300 // 5):
                await self.poll()
        self.assertEqual(self.calls.count(30148), 10)
        self.assertEqual(self.calls.count(40937), 1)

    async def test_each_block_can_be_switched_off(self):
        with patch.object(coordinator_module, "READ_MONITOR_EXT", 0), \
                patch.object(coordinator_module, "READ_BMS_DEVICE_INFO", 0):
            for _ in range(POLLS_PER_SLOW_READ + 1):
                await self.poll()
        self.assertNotIn(30600, self.calls)
        self.assertNotIn(0x1000, self.calls)
        self.assertIn(30148, self.calls)
        self.assertIn(40937, self.calls)
        self.assertIn(30010, self.calls)


class DefaultsTests(unittest.IsolatedAsyncioTestCase):
    def test_default_intervals(self):
        self.assertEqual(coordinator_module.READ_IDENTITY, 3600)
        self.assertEqual(coordinator_module.READ_DEVICE_INFO_EXT, 3600)
        self.assertEqual(coordinator_module.READ_BMS_DEVICE_INFO, 3600)
        self.assertEqual(coordinator_module.READ_FAULT_BITS, 300)
        self.assertEqual(coordinator_module.READ_SITE_LIMIT, 300)
        self.assertEqual(coordinator_module.READ_MONITOR_EXT, 60)

    async def test_monitor_ext_reads_from_block_start(self):
        coordinator = coordinator_module.PylontechCoordinator(None, "test", 502)
        coordinator.use_single_block_read = False
        blocks = valid_blocks()
        calls = []

        blocks[30600] = [0] * 60

        async def response(*, address, count, **kwargs):
            calls.append(address)
            if address == 30600:
                calls.append(("count", count))
            return SimpleNamespace(isError=lambda: False, registers=blocks[address])

        coordinator.client.read_holding_registers.side_effect = response
        with patch.object(coordinator_module.asyncio, "sleep", new=AsyncMock()), \
                patch.object(coordinator_module._LOGGER, "warning"):
            for _ in range(POLLS_PER_SLOW_READ):
                await coordinator._async_update_data()
        self.assertIn(30600, calls)
        self.assertIn(("count", 60), calls)
        self.assertNotIn(30626, calls)
        self.assertIn(30148, calls)
        self.assertIn(40937, calls)


class AlarmDecodeTests(unittest.TestCase):
    def test_bits_to_names(self):
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "custom_components" / "pylon_fh3x" / "alarms.py"
        spec = importlib.util.spec_from_file_location("alarms_under_test", path)
        alarms = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(alarms)
        data = {"fault_bits": 0b10, "fault_bits_extended": 0b1}
        bits = alarms.combined_bits(data, "fault_bits")
        self.assertEqual(
            alarms.active_issues(bits, alarms.PCS_FAULTS),
            ["Current sensor fault", "Meter communication fault"],
        )
        self.assertEqual(alarms.issues_state([]), "None")
        self.assertEqual(alarms.active_issues(1 << 3, alarms.PCS_WARNINGS), ["Unknown bit 3"])
        self.assertIsNone(alarms.combined_bits({}, "fault_bits"))


if __name__ == "__main__":
    unittest.main()
