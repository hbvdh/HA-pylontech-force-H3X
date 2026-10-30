"""Tests for the optional BMS module/cell data."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from test_coordinator import coordinator_module, validation_module, valid_blocks
import importlib
import sys

cell_data = importlib.import_module("fh3x_under_test.cell_data") if "fh3x_under_test.cell_data" in sys.modules else None
if cell_data is None:
    # The package stub only lives during import; load the pure module directly.
    from importlib import util
    from pathlib import Path
    spec = util.spec_from_file_location(
        "cell_data_under_test",
        Path(__file__).resolve().parents[1] / "custom_components" / "pylon_fh3x" / "cell_data.py",
    )
    cell_data = util.module_from_spec(spec)
    sys.modules["cell_data_under_test"] = cell_data
    spec.loader.exec_module(cell_data)

MODULES, PER = 3, 16
CELLS = MODULES * PER


def good_registers():
    cell_v = [3300 + (i % 5) * 4 for i in range(CELLS)]  # 3.300 - 3.316 V
    header = [0] * 64
    header[16], header[17] = max(cell_v), min(cell_v)
    header[54], header[55] = MODULES, CELLS
    module_v = [int(sum(cell_v[m * PER:(m + 1) * PER]) / 10) for m in range(MODULES)]  # 0.01 V
    module_t = [250, 252, 255]
    cell_t = [250 + m for m in range(MODULES) for _ in range(PER)]
    return header, module_v, module_t, cell_v, cell_t


class DecodeTests(unittest.TestCase):
    def test_good_data_decodes(self):
        data, notes = cell_data.decode_cell_data(*good_registers())
        self.assertEqual(data["module_count"], 3)
        self.assertEqual(data["cells_per_module"], 16)
        module = data["modules"][0]
        self.assertEqual(len(module["cell_voltages"]), 16)
        self.assertEqual(module["cell_voltage_min"], 3.3)
        self.assertEqual(module["cell_voltage_delta_mv"], 16)
        self.assertEqual(module["temperature"], 25.0)
        self.assertEqual(notes, [])

    def test_cell_out_of_range_rejects_everything(self):
        header, mv, mt, cv, ct = good_registers()
        cv[20] = 0
        with self.assertRaises(cell_data.CellDataError):
            cell_data.decode_cell_data(header, mv, mt, cv, ct)

    def test_huge_cell_value_rejected(self):
        header, mv, mt, cv, ct = good_registers()
        cv[3] = 65535
        with self.assertRaises(cell_data.CellDataError):
            cell_data.decode_cell_data(header, mv, mt, cv, ct)

    def test_header_minmax_must_match_array(self):
        header, mv, mt, cv, ct = good_registers()
        header[16] = 3500
        with self.assertRaises(cell_data.CellDataError):
            cell_data.decode_cell_data(header, mv, mt, cv, ct)

    def test_negative_temperature_decodes(self):
        header, mv, mt, cv, ct = good_registers()
        ct[0] = 0x10000 - 50  # -5.0 C
        data, _ = cell_data.decode_cell_data(header, mv, mt, cv, ct)
        self.assertEqual(data["modules"][0]["cell_temperatures"][0], -5.0)

    def test_bad_layouts_rejected(self):
        header = good_registers()[0]
        for modules, cells in ((0, 48), (3, 0), (3, 50), (99, 99), (3, 5000)):
            bad = list(header)
            bad[54], bad[55] = modules, cells
            with self.subTest(modules=modules, cells=cells):
                with self.assertRaises(cell_data.CellDataError):
                    cell_data.parse_layout(bad)

    def test_module_sum_mismatch_is_only_a_note(self):
        header, mv, mt, cv, ct = good_registers()
        mv[0] = int(mv[0] * 1.2)
        data, notes = cell_data.decode_cell_data(header, mv, mt, cv, ct)
        self.assertEqual(len(notes), 1)
        self.assertEqual(len(data["modules"]), 3)

    def test_soc_estimate_is_monotonic_and_bounded(self):
        values = [cell_data.estimate_cell_soc(v / 100) for v in range(250, 380)]
        self.assertEqual(values, sorted(values))
        self.assertEqual(values[0], 0.0)
        self.assertEqual(values[-1], 100.0)


class CoordinatorCellTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.coordinator = coordinator_module.PylontechCoordinator(None, "test", 502)
        self.coordinator.cell_data_enabled = True
        self.blocks = valid_blocks()
        self.regs = good_registers()
        self.clock = 1000.0
        self.calls = []
        self.fail_cells = False
        self.coordinator.client.read_holding_registers.side_effect = self.response
        for patcher in (
            patch.object(coordinator_module.asyncio, "sleep", new=AsyncMock()),
            patch.object(coordinator_module, "time", SimpleNamespace(monotonic=lambda: self.clock)),
            patch.object(validation_module, "time", SimpleNamespace(monotonic=lambda: self.clock)),
            patch.object(validation_module._LOGGER, "warning"),
            patch.object(coordinator_module._LOGGER, "warning"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def response(self, *, address, count, **kwargs):
        slave = kwargs.get("slave", kwargs.get("unit", kwargs.get("device_id")))
        self.calls.append((address, count, slave))
        header, mv, mt, cv, ct = self.regs
        cell_map = {0x1400: header, 0x1460: mv, 0x14B0: mt, 0x1500: cv, 0x1800: ct}
        if address in cell_map:
            if self.fail_cells:
                return SimpleNamespace(isError=lambda: True, registers=[])
            return SimpleNamespace(isError=lambda: False, registers=cell_map[address][:count])
        if address == 30100 and count == 92:
            from test_single_block_read import memory_map
            return SimpleNamespace(isError=lambda: False, registers=memory_map(self.blocks))
        return SimpleNamespace(isError=lambda: False, registers=self.blocks[address])

    async def poll(self, advance=5):
        self.clock += advance
        return await self.coordinator._async_update_data()

    async def test_disabled_by_default_makes_no_cell_reads(self):
        self.coordinator.cell_data_enabled = False
        await self.poll()
        self.assertFalse([c for c in self.calls if 0x1400 <= c[0] < 0x2000 and c[0] != 5123])
        self.assertIsNone(self.coordinator.cell_data)

    async def test_first_poll_reads_cells_from_slave_1(self):
        await self.poll()
        data = self.coordinator.cell_data
        self.assertEqual(data["module_count"], 3)
        cell_calls = [c for c in self.calls if 0x1400 <= c[0] < 0x2000 and c[0] != 5123]
        self.assertEqual(
            [c[0] for c in cell_calls], [0x1400, 0x1460, 0x14B0, 0x1500, 0x1800]
        )
        self.assertTrue(all(c[2] == 1 for c in cell_calls))

    async def test_only_once_per_minute(self):
        await self.poll()
        before = len(self.calls)
        await self.poll()  # 5 s later
        later = [c for c in self.calls[before:] if c[0] in (0x1400, 0x1500)]
        self.assertEqual(later, [])
        await self.poll(advance=60)
        again = [c for c in self.calls[before:] if c[0] == 0x1400]
        self.assertEqual(len(again), 1)

    async def test_corrupt_cell_read_is_rejected_and_old_values_kept(self):
        await self.poll()
        good = self.coordinator.cell_data
        header, mv, mt, cv, ct = self.regs
        bad_cv = list(cv)
        bad_cv[5] = 60000
        self.regs = (header, mv, mt, bad_cv, ct)
        data = await self.poll(advance=60)
        self.assertIsNotNone(data)  # fast poll still works
        self.assertEqual(self.coordinator.cell_data, good)

    async def test_failing_cell_reads_never_break_the_fast_poll(self):
        self.fail_cells = True
        data = await self.poll()
        self.assertIn("battery_power", data)
        self.assertIsNone(self.coordinator.cell_data)

    async def test_stale_data_disappears(self):
        await self.poll()
        self.assertIsNotNone(self.coordinator.cell_data)
        self.fail_cells = True
        self.clock += 700
        self.assertIsNone(self.coordinator.cell_data)

    async def test_retry_on_fresh_connection_recovers(self):
        header, mv, mt, cv, ct = self.regs
        bad = list(cv)
        bad[0] = 0
        self.regs = (header, mv, mt, bad, ct)
        calls_seen = {"n": 0}
        original = self.response

        async def flaky(*, address, count, **kwargs):
            if address == 0x1400:
                calls_seen["n"] += 1
                if calls_seen["n"] == 2:  # fix the data before the retry
                    self.regs = (header, mv, mt, cv, ct)
            return await original(address=address, count=count, **kwargs)

        self.coordinator.client.read_holding_registers.side_effect = flaky
        await self.poll()
        self.assertIsNotNone(self.coordinator.cell_data)


if __name__ == "__main__":
    unittest.main()
