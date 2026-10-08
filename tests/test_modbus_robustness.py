"""Regression tests for H3X Modbus retries, pacing and read-back verification.

Run using: python -m unittest discover -s tests -v
No Home Assistant server or inverter is required.
"""

import asyncio
import importlib
from pathlib import Path
import struct
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class FakeModbusClient:
    """Fake transport that records requests without contacting real hardware."""

    def __init__(self, **kwargs):
        self.connected = True
        self.read_holding_registers = AsyncMock()
        self.write_register = AsyncMock(return_value=modbus_response([]))
        self.write_registers = AsyncMock(return_value=modbus_response([]))
        self.connect = AsyncMock(side_effect=self._connect)
        self.close = MagicMock(side_effect=self._close)
        self.connect_result = True

    async def _connect(self):
        self.connected = self.connect_result
        return self.connect_result

    def _close(self):
        self.connected = False


def modbus_response(registers, error=False):
    return SimpleNamespace(registers=registers, isError=lambda: error)


class CoordinatorStub:
    """Minimal Home Assistant coordinator interface used by the real class."""

    def __init__(self, *args, **kwargs):
        self.data = {}
        self.refresh_calls = 0
        self.refresh_error = None

    async def async_request_refresh(self):
        self.refresh_calls += 1
        if self.refresh_error:
            raise self.refresh_error


class UpdateFailed(Exception):
    pass


def load_modules():
    """Stub HA and PyModbus imports; execute the actual integration source."""
    names = (
        "pymodbus", "pymodbus.client", "pymodbus.exceptions",
        "homeassistant", "homeassistant.core", "homeassistant.helpers",
        "homeassistant.helpers.update_coordinator", "h3x_robustness_under_test",
    )
    modules = {name: ModuleType(name) for name in names}
    modules["pymodbus.client"].AsyncModbusTcpClient = FakeModbusClient
    modules["pymodbus.exceptions"].ModbusException = type(
        "ModbusException", (Exception,), {}
    )
    modules["homeassistant.core"].HomeAssistant = object
    modules["homeassistant.helpers.update_coordinator"].DataUpdateCoordinator = CoordinatorStub
    modules["homeassistant.helpers.update_coordinator"].UpdateFailed = UpdateFailed
    modules["h3x_robustness_under_test"].__path__ = [str(
        Path(__file__).resolve().parents[1] / "custom_components" / "pylon_fh3x"
    )]
    with patch.dict(sys.modules, modules):
        coordinator = importlib.import_module("h3x_robustness_under_test.coordinator")
        validation = importlib.import_module("h3x_robustness_under_test.validation")
    return coordinator, validation


coordinator_module, validation_module = load_modules()


def int32_into(words, offset, value):
    words[offset:offset + 2] = struct.unpack(">HH", struct.pack(">i", value))


def normal_blocks():
    blocks = {
        30100: [0] * 48,
        30183: [0] * 9,
        30156: [0] * 30,
        40400: [0] * 3,
        40848: [0],
        40901: [0] * 26,
        5123: [0] * 30,
    }
    blocks[30100][40] = 5000  # 50 Hz
    blocks[30156][8] = 4000  # 400 V
    blocks[30156][26] = 50   # 50% SOC
    blocks[5123][0] = 4000  # 400 V BMS
    blocks[5123][4] = 50    # 50% SOC BMS
    blocks[5123][13:15] = [3350, 3300]
    blocks[5123][29] = 100
    return blocks


class ModbusRobustnessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.coordinator = coordinator_module.PylontechCoordinator(None, "h3x.test", 502)
        self.client = self.coordinator.client
        self.blocks = normal_blocks()
        self.client.read_holding_registers.side_effect = self._response_for_address
        patcher = patch.object(coordinator_module.asyncio, "sleep", new=AsyncMock())
        self.sleep_mock = patcher.start()
        self.addCleanup(patcher.stop)

    async def _response_for_address(self, *, address, count, **kwargs):
        return modbus_response(list(self.blocks[address]))

    async def test_normal_read_uses_one_request(self):
        result = await self.coordinator.safe_read(30183, 9, 2)
        self.assertEqual(result, self.blocks[30183])
        self.assertEqual(self.client.read_holding_registers.await_count, 1)
        self.client.close.assert_not_called()

    async def test_modbus_exception_response_reconnects_and_retries_once(self):
        self.client.read_holding_registers.side_effect = [
            modbus_response([], error=True), modbus_response([1, 2])
        ]
        result = await self.coordinator.safe_read(123, 2, 2)
        self.assertEqual(result, [1, 2])
        self.assertEqual(self.client.read_holding_registers.await_count, 2)
        self.client.close.assert_called_once()
        self.client.connect.assert_awaited_once()

    async def test_retry_success_logs_confirmation(self):
        self.client.read_holding_registers.side_effect = [
            modbus_response([], error=True), modbus_response([1, 2])
        ]
        with self.assertLogs(coordinator_module._LOGGER, level="WARNING") as messages:
            result = await self.coordinator.safe_read(30100, 2, 2)
        self.assertEqual(result, [1, 2])
        self.assertEqual(self.client.read_holding_registers.await_count, 2)
        self.assertTrue(any(
            "Modbus retry successful at 30100 (Slave 2)" in line
            for line in messages.output
        ), messages.output)
        self.assertFalse(any("Modbus retry failed" in line for line in messages.output))

    async def test_retry_failure_logs_confirmation(self):
        self.client.read_holding_registers.side_effect = [None, None]
        with self.assertLogs(coordinator_module._LOGGER, level="WARNING") as messages:
            result = await self.coordinator.safe_read(30100, 2, 2)
        self.assertIsNone(result)
        self.assertEqual(self.client.read_holding_registers.await_count, 2)
        self.assertTrue(any(
            "Modbus retry failed at 30100 (Slave 2)" in line
            for line in messages.output
        ), messages.output)
        self.assertFalse(any("Modbus retry successful" in line for line in messages.output))

    async def test_malformed_register_response_is_retried(self):
        self.client.read_holding_registers.side_effect = [
            modbus_response([1]), modbus_response([1, 2])
        ]
        self.assertEqual(await self.coordinator.safe_read(123, 2, 2), [1, 2])
        self.assertEqual(self.client.read_holding_registers.await_count, 2)

    async def test_second_failed_read_returns_none_after_two_attempts(self):
        self.client.read_holding_registers.side_effect = [None, None]
        self.assertIsNone(await self.coordinator.safe_read(123, 2, 2))
        self.assertEqual(self.client.read_holding_registers.await_count, 2)

    async def test_initial_connection_failure_is_retried_once(self):
        self.client.connected = False
        self.client.connect_result = False
        self.assertIsNone(await self.coordinator.safe_read(30100, 48, 2))
        self.assertEqual(self.client.connect.await_count, 2)
        self.assertEqual(self.client.read_holding_registers.await_count, 0)

    async def test_initial_connection_recovers_after_retry(self):
        self.client.connected = False
        attempts = 0

        async def reconnect():
            nonlocal attempts
            attempts += 1
            self.client.connected = attempts >= 2
            return self.client.connected

        self.client.connect.side_effect = reconnect
        self.assertEqual(await self.coordinator.safe_read(30183, 9, 2), self.blocks[30183])
        self.assertEqual(self.client.connect.await_count, 2)
        self.assertEqual(self.client.read_holding_registers.await_count, 1)

    async def test_pacing_slow_when_switching_slaves(self):
        await self.coordinator.safe_read(30183, 9, 2)
        await self.coordinator.safe_read(5123, 30, 1)
        await self.coordinator.safe_read(5123, 30, 1)
        delays = [call.args[0] for call in self.sleep_mock.await_args_list]
        self.assertEqual(delays, [0.1, 0.2, 0.1])

    async def test_corrupt_battery_voltage_recovers_on_second_read(self):
        first = list(self.blocks[30156])
        first[8] = 63744  # 6374.4 V, invalid
        second = list(self.blocks[30156])
        self.client.read_holding_registers.side_effect = [
            modbus_response(first), modbus_response(second),
        ]
        result = await self.coordinator._read_with_sanity_retry(
            30156, 30, 2,
            lambda regs: {"battery_voltage": regs[8] * 0.1},
        )
        self.assertEqual(result, second)
        self.assertEqual(self.client.read_holding_registers.await_count, 2)

    async def test_double_corrupt_battery_value_rejected_by_validator(self):
        self.blocks[30156][8] = 63744
        data = await self.coordinator._async_update_data()
        self.assertNotIn("battery_voltage", data)
        self.assertEqual(data["battery_soc"], 50)
        battery_reads = [c for c in self.client.read_holding_registers.await_args_list
                         if c.kwargs["address"] == 30156]
        self.assertEqual(len(battery_reads), 2)

    async def test_bad_phase_power_recovers_after_second_read(self):
        good = list(self.blocks[30183])
        bad = list(good)
        int32_into(bad, 3, -16_711_833)
        calls = 0
        async def read(*, address, count, **kwargs):
            nonlocal calls
            if address == 30183:
                calls += 1
                return modbus_response(bad if calls == 1 else good)
            return modbus_response(list(self.blocks[address]))
        self.client.read_holding_registers.side_effect = read
        data = await self.coordinator._async_update_data()
        self.assertEqual(data["grid_power_r"], 0)
        self.assertEqual(calls, 2)

    async def test_period_3_invalid_value_gets_second_read(self):
        first = list(self.blocks[40901])
        first[19] = 1099
        second = list(self.blocks[40901])
        seen = 0
        async def read(*, address, count, **kwargs):
            nonlocal seen
            if address == 40901:
                seen += 1
                return modbus_response(first if seen == 1 else second)
            return modbus_response(list(self.blocks[address]))
        self.client.read_holding_registers.side_effect = read
        data = await self.coordinator._async_update_data()
        self.assertEqual(data["period_3"], 0)
        self.assertEqual(seen, 2)

    async def test_sanity_retry_does_not_advance_stateful_counter_twice(self):
        self.blocks[30156][8] = 63744
        await self.coordinator._async_update_data()
        # Even if a register is read twice, the stateful validator runs once per poll.
        with patch.object(self.coordinator._validator, "validate", wraps=self.coordinator._validator.validate) as validate:
            await self.coordinator._async_update_data()
            validate.assert_called_once()

    async def test_isolated_2048_amp_grid_spike_recovers_after_second_read(self):
        self.blocks[30183][1] = 8  # Normal L2/S current = 0.8 A
        await self.coordinator._async_update_data()  # Establish valid baseline.
        corrupt = list(self.blocks[30183])
        corrupt[1] = 2048  # 204.8 A -- syntactically valid but suspicious.
        clean = list(self.blocks[30183])
        attempts = 0

        async def read(*, address, count, **kwargs):
            nonlocal attempts
            if address == 30183:
                attempts += 1
                return modbus_response(corrupt if attempts == 1 else clean)
            return modbus_response(list(self.blocks[address]))

        self.client.read_holding_registers.side_effect = read
        data = await self.coordinator._async_update_data()
        self.assertAlmostEqual(data["grid_current_s"], 0.8)
        self.assertEqual(attempts, 2)

    async def test_confirmed_real_high_grid_current_is_allowed(self):
        self.blocks[30183][1] = 8
        await self.coordinator._async_update_data()
        self.blocks[30183][1] = 2000  # 200 A: valid on a sufficiently sized CT.
        self.client.read_holding_registers.reset_mock()
        data = await self.coordinator._async_update_data()
        self.assertEqual(data["grid_current_s"], 200.0)
        phase_reads = [call for call in self.client.read_holding_registers.await_args_list
                       if call.kwargs["address"] == 30183]
        self.assertEqual(len(phase_reads), 2)

    async def test_unverified_grid_current_spike_does_not_leak_to_ha(self):
        self.blocks[30183][1] = 8
        await self.coordinator._async_update_data()
        corrupt = list(self.blocks[30183])
        corrupt[1] = 2048
        attempts = 0

        async def read(*, address, count, **kwargs):
            nonlocal attempts
            if address == 30183:
                attempts += 1
                if attempts == 1:
                    return modbus_response(corrupt)
                return None  # Both transport attempts of verification fail.
            return modbus_response(list(self.blocks[address]))

        self.client.read_holding_registers.side_effect = read
        data = await self.coordinator._async_update_data()
        self.assertNotIn("grid_current_s", data)
        self.assertIn("battery_soc", data)
        self.assertEqual(self.coordinator._last_valid_grid_currents["grid_current_s"], 0.8)

    async def test_two_inconsistent_current_spikes_are_not_published(self):
        self.blocks[30183][1] = 8
        await self.coordinator._async_update_data()
        first = list(self.blocks[30183])
        second = list(self.blocks[30183])
        first[1] = 2048
        second[1] = 5000
        attempts = 0

        async def read(*, address, count, **kwargs):
            nonlocal attempts
            if address == 30183:
                attempts += 1
                return modbus_response(first if attempts == 1 else second)
            return modbus_response(list(self.blocks[address]))

        self.client.read_holding_registers.side_effect = read
        data = await self.coordinator._async_update_data()
        self.assertNotIn("grid_current_s", data)
        self.assertEqual(attempts, 2)

    async def test_reversed_bms_cell_voltage_recovers_on_second_read(self):
        # Real-world failure: minimum 3.27 V, maximum 0.20 V.
        bad = list(self.blocks[5123])
        bad[13:15] = [200, 3270]
        good = list(self.blocks[5123])
        attempts = 0

        async def read(*, address, count, **kwargs):
            nonlocal attempts
            if address == 5123:
                attempts += 1
                return modbus_response(bad if attempts == 1 else good)
            return modbus_response(list(self.blocks[address]))

        self.client.read_holding_registers.side_effect = read
        data = await self.coordinator._async_update_data()
        self.assertAlmostEqual(data["bms_cell_voltage_min"], 3.3)
        self.assertAlmostEqual(data["bms_cell_voltage_max"], 3.35)
        self.assertEqual(attempts, 2)

    async def test_reversed_bms_cell_voltage_rejected_after_two_bad_reads(self):
        self.blocks[5123][13:15] = [200, 3270]
        data = await self.coordinator._async_update_data()
        self.assertNotIn("bms_cell_voltage_min", data)
        self.assertNotIn("bms_cell_voltage_max", data)
        self.assertEqual(data["bms_soc"], 50)
        bms_reads = [call for call in self.client.read_holding_registers.await_args_list
                     if call.kwargs["address"] == 5123]
        self.assertEqual(len(bms_reads), 2)

    async def test_valid_bms_cell_voltage_does_not_trigger_retry(self):
        data = await self.coordinator._async_update_data()
        self.assertAlmostEqual(data["bms_cell_voltage_min"], 3.3)
        self.assertAlmostEqual(data["bms_cell_voltage_max"], 3.35)
        bms_reads = [call for call in self.client.read_holding_registers.await_args_list
                     if call.kwargs["address"] == 5123]
        self.assertEqual(len(bms_reads), 1)

    async def test_reversed_bms_cell_voltage_rejected_if_retry_fails(self):
        bad = list(self.blocks[5123])
        bad[13:15] = [200, 3270]
        attempts = 0

        async def read(*, address, count, **kwargs):
            nonlocal attempts
            if address == 5123:
                attempts += 1
                return modbus_response(bad) if attempts == 1 else None
            return modbus_response(list(self.blocks[address]))

        self.client.read_holding_registers.side_effect = read
        data = await self.coordinator._async_update_data()
        self.assertNotIn("bms_cell_voltage_min", data)
        self.assertNotIn("bms_cell_voltage_max", data)
        self.assertEqual(data["bms_soc"], 50)
        self.assertEqual(attempts, 3)  # Initial read + two transport attempts.

    async def test_signed_16bit_write_verified_from_unsigned_register(self):
        self.client.read_holding_registers.side_effect = None
        self.client.read_holding_registers.return_value = modbus_response([65535])
        ok = await self.coordinator.async_write_register(40901, -1, 2)
        self.assertTrue(ok)
        self.assertEqual(self.client.write_register.await_args.kwargs["value"], 65535)
        self.assertEqual(self.client.write_register.await_count, 1)
        self.assertEqual(self.client.read_holding_registers.await_count, 1)
        self.assertEqual(self.coordinator.refresh_calls, 1)

    async def test_32bit_write_verifies_both_words(self):
        value = -12345
        expected = list(struct.unpack(">HH", struct.pack(">i", value)))
        self.client.read_holding_registers.side_effect = None
        self.client.read_holding_registers.return_value = modbus_response(expected)
        ok = await self.coordinator.async_write_register_32bit(40401, value, 2)
        self.assertTrue(ok)
        self.assertEqual(self.client.write_registers.await_args.kwargs["values"], expected)
        self.assertEqual(self.client.write_registers.await_count, 1)

    async def test_verified_16bit_write_logs_success_after_readback(self):
        self.client.read_holding_registers.side_effect = None
        self.client.read_holding_registers.return_value = modbus_response([0])
        with self.assertLogs(coordinator_module._LOGGER, level="INFO") as messages:
            result = await self.coordinator.async_write_register(40901, 0, 2)
        self.assertTrue(result)
        self.client.write_register.assert_awaited_once()
        self.client.read_holding_registers.assert_awaited_once()
        self.assertTrue(any(
            "Modbus write verified at 40901 (Slave 2): value 0" in line
            for line in messages.output
        ), messages.output)

    async def test_verified_32bit_write_logs_both_register_values(self):
        expected = [0, 0]
        self.client.read_holding_registers.side_effect = None
        self.client.read_holding_registers.return_value = modbus_response(expected)
        with self.assertLogs(coordinator_module._LOGGER, level="INFO") as messages:
            result = await self.coordinator.async_write_register_32bit(40401, 0, 2)
        self.assertTrue(result)
        self.client.write_registers.assert_awaited_once()
        self.client.read_holding_registers.assert_awaited_once()
        self.assertTrue(any(
            "Modbus write verified at 40401 (Slave 2): values [0, 0]" in line
            for line in messages.output
        ), messages.output)

    async def test_failed_write_readback_does_not_log_success(self):
        self.client.read_holding_registers.side_effect = None
        self.client.read_holding_registers.return_value = modbus_response([456])
        with self.assertLogs(coordinator_module._LOGGER, level="ERROR") as messages:
            result = await self.coordinator.async_write_register(40901, 123, 2)
        self.assertFalse(result)
        self.assertFalse(any("Modbus write verified" in line for line in messages.output))
        self.assertTrue(any("Modbus write verification mismatch" in line for line in messages.output))

    async def test_write_readback_mismatch_returns_false_without_write_retry(self):
        self.client.read_holding_registers.side_effect = None
        self.client.read_holding_registers.return_value = modbus_response([456])
        ok = await self.coordinator.async_write_register(40901, 123, 2)
        self.assertFalse(ok)
        self.assertEqual(self.client.write_register.await_count, 1)

    async def test_readback_transport_error_returns_false(self):
        self.client.read_holding_registers.side_effect = OSError("link lost")
        ok = await self.coordinator.async_write_register(40901, 123, 2)
        self.assertFalse(ok)
        self.client.close.assert_called_once()
        self.assertEqual(self.client.write_register.await_count, 1)

    async def test_successful_write_still_true_if_refresh_fails(self):
        self.client.read_holding_registers.side_effect = None
        self.client.read_holding_registers.return_value = modbus_response([123])
        self.coordinator.refresh_error = RuntimeError("HA refresh failed")
        self.assertTrue(await self.coordinator.async_write_register(40901, 123, 2))
        self.assertEqual(self.coordinator.refresh_calls, 1)

    async def test_16bit_write_and_read_share_lock(self):
        read_started = asyncio.Event()
        release_read = asyncio.Event()
        order = []

        async def blocked_read(*, address, count, **kwargs):
            order.append("read-start")
            read_started.set()
            await release_read.wait()
            order.append("read-end")
            return modbus_response([123])

        async def write(*, address, value, **kwargs):
            order.append("write")
            return modbus_response([])

        self.client.read_holding_registers.side_effect = blocked_read
        self.client.write_register.side_effect = write
        read_task = asyncio.create_task(self.coordinator.safe_read(40901, 1, 2))
        try:
            await asyncio.wait_for(read_started.wait(), timeout=1)
            write_task = asyncio.create_task(self.coordinator.async_write_register(40901, 123, 2))
            await asyncio.sleep(0)
            self.assertEqual(order, ["read-start"])
            release_read.set()
            await asyncio.wait_for(asyncio.gather(read_task, write_task), timeout=1)
            self.assertEqual(order[:3], ["read-start", "read-end", "write"])
        finally:
            release_read.set()
            if not read_task.done():
                read_task.cancel()
            if "write_task" in locals() and not write_task.done():
                write_task.cancel()


if __name__ == "__main__":
    unittest.main()
