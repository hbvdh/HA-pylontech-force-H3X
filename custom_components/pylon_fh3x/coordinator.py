"""DataUpdateCoordinator for Pylontech Force H3X."""
import asyncio
import logging
import math
import struct
from datetime import timedelta

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, DEFAULT_SCAN_INTERVAL
from .validation import TelemetryValidator, VALUE_RANGES

_LOGGER = logging.getLogger(__name__)

# Retry-only thresholds, not hard limits for larger installations.
GRID_CURRENT_JUMP_RETRY_A = 50.0
GRID_CURRENT_INITIAL_RETRY_A = 100.0
GRID_CURRENT_RETRY_AGREEMENT_A = 10.0

BATTERY_STATUS_MAP = {
    0: "Sleep",
    1: "Charging",
    2: "Discharging",
    3: "Idle",
    4: "Standby",
    5: "Run",
    6: "Fault",
    7: "Offline",
}

# =========================================================
# Modbus register decoding helpers
# =========================================================
def get_16bit_uint(regs, idx):
    return regs[idx]

def get_16bit_int(regs, idx):
    return struct.unpack('>h', struct.pack('>H', regs[idx]))[0]

def get_32bit_int(regs, idx):
    return struct.unpack('>i', struct.pack('>HH', regs[idx], regs[idx+1]))[0]

def get_32bit_float(regs, idx):
    return struct.unpack('>f', struct.pack('>HH', regs[idx], regs[idx+1]))[0]


async def _modbus_read(client, address, count, target_id):
    
    try:
        return await client.read_holding_registers(address=address, count=count, slave=target_id)
    except TypeError:
        pass
    try:
        return await client.read_holding_registers(address=address, count=count, unit=target_id)
    except TypeError:
        pass
    return await client.read_holding_registers(address=address, count=count, device_id=target_id)


class PylontechCoordinator(DataUpdateCoordinator):
    """Coordinate Modbus reads and writes for the inverter."""

    def __init__(self, hass: HomeAssistant, host: str, port: int) -> None:
        self.client = AsyncModbusTcpClient(
            host=host,
            port=port,
            timeout=5,
            retries=0,
        )
        self.host = host
        self._validator = TelemetryValidator()
        self._modbus_lock = asyncio.Lock()
        self._last_modbus_slave = None
        self._last_valid_grid_currents = {}
        
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )

    async def _pace_modbus(self, slave: int) -> None:
        """Apply request pacing, with extra delay when switching slaves."""
        if (
            self._last_modbus_slave is not None
            and self._last_modbus_slave != slave
        ):
            delay = 0.2
        else:
            delay = 0.1

        await asyncio.sleep(delay)
        self._last_modbus_slave = slave

    def _find_suspicious_values(self, data: dict) -> list[str]:
        """Find implausible data or abrupt grid-current changes, without mutating state."""
        suspicious = []

        for key, value in data.items():
            limits = VALUE_RANGES.get(key)
            if limits is None:
                continue

            minimum, maximum = limits

            if (
                not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not minimum <= value <= maximum
            ):
                suspicious.append(key)

        # Detect isolated current spikes without imposing a universal 40/100 A
        # ceiling: the CT may monitor a much larger installation. A sudden
        # change only *triggers verification*; confirmed high loads are valid.
        for phase in "rst":
            key = f"grid_current_{phase}"
            current = data.get(key)
            if not isinstance(current, (int, float)) or not math.isfinite(current):
                continue
            previous = self._last_valid_grid_currents.get(key)
            if previous is None:
                needs_retry = abs(current) > GRID_CURRENT_INITIAL_RETRY_A
            else:
                needs_retry = abs(current - previous) > GRID_CURRENT_JUMP_RETRY_A
            if needs_retry and key not in suspicious:
                suspicious.append(key)

        return suspicious

    async def _read_with_sanity_retry(
        self, address, count, slave, decode
    ):
        """Retry a register block once if decoded values look implausible."""
        registers = await self.safe_read(address, count, slave)

        if registers is None:
            return None

        first_values = decode(registers)
        suspicious = self._find_suspicious_values(first_values)

        if not suspicious:
            return registers

        _LOGGER.warning(
            "Suspicious Modbus values at address %s (Slave %s): %s. "
            "Reading register block again.",
            address, slave, ", ".join(suspicious),
        )

        retry_registers = await self.safe_read(
            address, count, slave
        )

        if retry_registers is None:
            if any(key.startswith("grid_current_") for key in suspicious):
                # Without a confirming sample a huge current jump cannot be
                # distinguished from a corrupt Modbus response. Skip this
                # phase block rather than publish an unverified current.
                _LOGGER.warning(
                    "Skipping grid phase block %s: current jump could not be verified",
                    address,
                )
                return None
            # The normal validator will reject any static out-of-range values.
            return registers

        retry_values = decode(retry_registers)
        still_suspicious = self._find_suspicious_values(retry_values)
        for key in suspicious:
            if key.startswith("grid_current_") and key in still_suspicious:
                if abs(retry_values[key] - first_values[key]) > GRID_CURRENT_RETRY_AGREEMENT_A:
                    _LOGGER.warning(
                        "Skipping grid phase block %s: unconfirmed current jump "
                        "%s (first=%s A, retry=%s A)",
                        address, key, first_values[key], retry_values[key],
                    )
                    return None

        if still_suspicious:
            _LOGGER.warning(
                "Modbus retry at address %s (Slave %s) "
                "still contains suspicious values: %s",
                address, slave, ", ".join(still_suspicious),
            )
        else:
            _LOGGER.info(
                "Modbus retry at address %s (Slave %s) "
                "recovered valid telemetry.",
                address, slave,
            )

        return retry_registers
    
    async def safe_read(self, address, count, slave):
        """Read Modbus registers, retrying once after a failed response."""
        async with self._modbus_lock:
            for attempt in range(2):
                try:
                    if not self.client.connected:
                        if not await self.client.connect():
                            raise ConnectionError("Unable to connect to H3X")
                        self._last_modbus_slave = None

                    # Respect Modbus request pacing and slave switching.
                    await self._pace_modbus(slave)

                    res = await _modbus_read(
                        self.client, address, count, slave
                    )

                    if res is not None and not res.isError():
                        registers = getattr(res, "registers", None)

                        if (
                            isinstance(registers, (list, tuple))
                            and len(registers) == count
                            and all(
                                type(word) is int and 0 <= word <= 0xFFFF
                                for word in registers
                            )
                        ):
                            return registers

                        error = "Malformed register response"
                    else:
                        error = f"Modbus error response: {res}"

                except (OSError, TimeoutError, ModbusException) as err:
                    error = f"{type(err).__name__}: {err}"

                if attempt == 0:
                    _LOGGER.warning(
                        "Modbus read failed at %s (Slave %s): %s. "
                        "Reconnecting and retrying once.",
                        address, slave, error,
                    )
                    self.client.close()
                    self._last_modbus_slave = None
                    await asyncio.sleep(0.2)
                else:
                    _LOGGER.warning(
                        "Modbus read failed after retry at %s "
                        "(Slave %s): %s",
                        address, slave, error,
                    )

            return None


    async def _async_update_data(self):
        """Fetch data from the inverter via Modbus."""
        try:
            data = {}

            # Read frequently changing inverter data in two contiguous blocks.
            r_inverter_main = await self._read_with_sanity_retry(
                30100,
                48,
                2,
                lambda regs: {
                    "ac_total_power": get_32bit_int(regs, 0),
                    "grid_total_power": get_32bit_int(regs, 8),
                    "pv1_voltage": get_16bit_uint(regs, 19) * 0.1,
                    "pv1_current": get_16bit_uint(regs, 20) * 0.1,
                    "pv2_voltage": get_16bit_uint(regs, 21) * 0.1,
                    "pv2_current": get_16bit_uint(regs, 22) * 0.1,
                    "pv3_voltage": get_16bit_uint(regs, 23) * 0.1,
                    "pv3_current": get_16bit_uint(regs, 24) * 0.1,
                    "pv_total_power": get_32bit_int(regs, 27),
                    "ac_frequency": get_16bit_uint(regs, 40) * 0.01,
                    "inverter_temperature": get_16bit_int(regs, 46) * 0.1,
                    "heatsink_temperature": get_16bit_int(regs, 47) * 0.1,
                },
            )
            if r_inverter_main:
                data["ac_total_power"] = get_32bit_int(r_inverter_main, 0)
                data["grid_total_power"] = get_32bit_int(r_inverter_main, 8)
                data["inverter_status"] = get_16bit_uint(r_inverter_main, 15)

                data["pv1_voltage"] = get_16bit_uint(r_inverter_main, 19) * 0.1
                data["pv1_current"] = get_16bit_uint(r_inverter_main, 20) * 0.1
                data["pv2_voltage"] = get_16bit_uint(r_inverter_main, 21) * 0.1
                data["pv2_current"] = get_16bit_uint(r_inverter_main, 22) * 0.1
                data["pv3_voltage"] = get_16bit_uint(r_inverter_main, 23) * 0.1
                data["pv3_current"] = get_16bit_uint(r_inverter_main, 24) * 0.1

                data["pv_total_power"] = get_32bit_int(r_inverter_main, 27)
                data["pv_total_energy"] = get_32bit_float(r_inverter_main, 29)
                data["grid_voltage_r"] = get_16bit_uint(r_inverter_main, 31) * 0.1
                data["ac_current_r"] = get_16bit_uint(r_inverter_main, 32) * 0.1
                data["grid_voltage_s"] = get_16bit_uint(r_inverter_main, 33) * 0.1
                data["ac_current_s"] = get_16bit_uint(r_inverter_main, 34) * 0.1
                data["grid_voltage_t"] = get_16bit_uint(r_inverter_main, 35) * 0.1
                data["ac_current_t"] = get_16bit_uint(r_inverter_main, 36) * 0.1
                data["ac_frequency"] = get_16bit_uint(r_inverter_main, 40) * 0.01
                data["inverter_temperature"] = get_16bit_int(r_inverter_main, 46) * 0.1
                data["heatsink_temperature"] = get_16bit_int(r_inverter_main, 47) * 0.1

            # Per-phase grid current/power (CT clamp measurements at the grid connection).
            r_grid_phases = await self._read_with_sanity_retry(
                30183,
                9,
                2,
                lambda regs: {
                    "grid_current_r": get_16bit_int(regs, 0) * 0.1,
                    "grid_current_s": get_16bit_int(regs, 1) * 0.1,
                    "grid_current_t": get_16bit_int(regs, 2) * 0.1,
                    "grid_power_r": get_32bit_int(regs, 3),
                    "grid_power_s": get_32bit_int(regs, 5),
                    "grid_power_t": get_32bit_int(regs, 7),
                },
            )
            if r_grid_phases:
                data["grid_current_r"] = get_16bit_int(r_grid_phases, 0) * 0.1
                data["grid_current_s"] = get_16bit_int(r_grid_phases, 1) * 0.1
                data["grid_current_t"] = get_16bit_int(r_grid_phases, 2) * 0.1
                data["grid_power_r"] = get_32bit_int(r_grid_phases, 3)
                data["grid_power_s"] = get_32bit_int(r_grid_phases, 5)
                data["grid_power_t"] = get_32bit_int(r_grid_phases, 7)

            r_inverter_battery = await self._read_with_sanity_retry(
                30156,
                30,
                2,
                lambda regs: {
                    "battery_power": get_32bit_int(regs, 6),
                    "battery_voltage": get_16bit_uint(regs, 8) * 0.1,
                    "battery_current": get_16bit_int(regs, 9) * 0.1,
                    "battery_soc": get_16bit_uint(regs, 26),
                },
            )
            if r_inverter_battery:
                data["total_grid_import"] = get_32bit_float(r_inverter_battery, 0)
                data["total_grid_export"] = get_32bit_float(r_inverter_battery, 2)

                raw_status = get_16bit_uint(r_inverter_battery, 5)
                data["battery_status"] = BATTERY_STATUS_MAP.get(raw_status, f"Unknown ({raw_status})")
                data["battery_power"] = get_32bit_int(r_inverter_battery, 6)
                data["battery_voltage"] = get_16bit_uint(r_inverter_battery, 8) * 0.1
                data["battery_current"] = get_16bit_int(r_inverter_battery, 9) * 0.1
                data["eps_power"] = get_32bit_int(r_inverter_battery, 16)
                data["total_battery_charge"] = get_32bit_float(r_inverter_battery, 18)
                data["total_battery_discharge"] = get_32bit_float(r_inverter_battery, 20)
                data["battery_soc"] = get_16bit_uint(r_inverter_battery, 26)

            # Keep independent control ranges separate from telemetry blocks.
            r_active = await self.safe_read(40400, 3, 2)
            if r_active:
                data["active_power_control_mode"] = get_16bit_uint(r_active, 0)
                data["meter_export_power_max"] = get_32bit_int(r_active, 1)

            r_hp = await self.safe_read(40848, 1, 2)
            if r_hp:
                data["heat_pump"] = get_16bit_uint(r_hp, 0)

            r_ems = await self._read_with_sanity_retry(
                40901,
                26,
                2,
                lambda regs: {
                    "charge_discharge_power": get_16bit_int(regs, 0),
                    "charge_limit_soc": get_16bit_uint(regs, 1),
                    "discharge_limit_soc": get_16bit_uint(regs, 2),
                    "period_1": get_16bit_uint(regs, 7),
                    "period_2": get_16bit_uint(regs, 13),
                    "period_3": get_16bit_uint(regs, 19),
                    "period_4": get_16bit_uint(regs, 25),
                },
            )
            if r_ems:
                data["charge_discharge_power"] = get_16bit_int(r_ems, 0)
                data["charge_limit_soc"] = get_16bit_uint(r_ems, 1)
                data["discharge_limit_soc"] = get_16bit_uint(r_ems, 2)
                data["ems_mode"] = str(get_16bit_uint(r_ems, 6))
                data["period_1"] = get_16bit_uint(r_ems, 7)
                data["period_2"] = get_16bit_uint(r_ems, 13)
                data["period_3"] = get_16bit_uint(r_ems, 19)
                data["period_4"] = get_16bit_uint(r_ems, 25)

            # Read all BMS values from one contiguous slave-1 block.
            r_bms = await self._read_with_sanity_retry(
                5123,
                30,
                1,
                lambda regs: {
                    "bms_voltage": get_16bit_uint(regs, 0) * 0.1,
                    "bms_temperature": get_16bit_int(regs, 3) * 0.1,
                    "bms_soc": get_16bit_uint(regs, 4),
                    "bms_cell_voltage_max": get_16bit_uint(regs, 13) * 0.001,
                    "bms_cell_voltage_min": get_16bit_uint(regs, 14) * 0.001,
                    "bms_soh": get_16bit_uint(regs, 29),
                },
            )
            if r_bms:
                data["bms_voltage"] = get_16bit_uint(r_bms, 0) * 0.1
                data["bms_temperature"] = get_16bit_int(r_bms, 3) * 0.1
                data["bms_soc"] = get_16bit_uint(r_bms, 4)
                data["bms_cycles"] = get_16bit_uint(r_bms, 5)
                data["bms_cell_voltage_max"] = get_16bit_uint(r_bms, 13) * 0.001
                data["bms_cell_voltage_min"] = get_16bit_uint(r_bms, 14) * 0.001
                data["bms_soh"] = get_16bit_uint(r_bms, 29)

            # Validate before exposing values or deriving any power sensors.
            data = self._validator.validate(data)
            if not data:
                raise UpdateFailed("No data received out of inverter.")
            # Update the comparison baseline only from values accepted by the
            # existing stateful validator, never from rejected raw samples.
            for phase in "rst":
                key = f"grid_current_{phase}"
                if key in data:
                    self._last_valid_grid_currents[key] = data[key]

            return data

        except UpdateFailed:
            raise
        except ModbusException as err:
            raise UpdateFailed(f"error with modbus communication: {err}")
        except Exception as err:
            raise UpdateFailed(f"unexpected error: {err}")

    async def _verify_write_locked(
        self, address: int, expected: list[int], slave: int
    ) -> bool:
        """Verify written registers while holding the Modbus lock."""
        try:
            await self._pace_modbus(slave)

            response = await _modbus_read(
                self.client, address, len(expected), slave
            )

            if response is None or response.isError():
                _LOGGER.error(
                    "Modbus write verification read failed at %s "
                    "(Slave %s): %s",
                    address, slave, response,
                )
                return False

            actual = getattr(response, "registers", None)

            if (
                not isinstance(actual, (list, tuple))
                or list(actual) != expected
            ):
                _LOGGER.error(
                    "Modbus write verification mismatch at %s "
                    "(Slave %s): expected %s, received %s",
                    address, slave, expected, actual,
                )
                return False

            return True

        except (OSError, TimeoutError, ModbusException) as err:
            _LOGGER.error(
                "Modbus write verification error at %s "
                "(Slave %s): %s",
                address, slave, err,
            )
            self.client.close()
            self._last_modbus_slave = None
            return False

    async def async_write_register(self, address: int, value: int, slave: int = 2) -> bool:
        """Write a signed or unsigned 16-bit value to a Modbus register."""
        try:
            if value < 0:
                value &= 0xFFFF

            async with self._modbus_lock:
                if not self.client.connected:
                    if not await self.client.connect():
                        raise ConnectionError("Unable to connect to H3X")
                    self._last_modbus_slave = None

                await self._pace_modbus(slave)
                
                # Support different pymodbus versions.
                try:
                    res = await self.client.write_register(
                        address=address, value=value, slave=slave
                    )
                except TypeError:
                    try:
                        res = await self.client.write_register(
                            address=address, value=value, unit=slave
                        )
                    except TypeError:
                        res = await self.client.write_register(
                            address=address, value=value, device_id=slave
                        )

                if res is None or res.isError():
                    _LOGGER.error(
                        "Modbus write failed at %s (Slave %s): %s",
                        address, slave, res,
                    )
                    return False
                    
                verified = await self._verify_write_locked(
                    address, [value], slave
                )

            # A refresh failure must not change a verified write result.
            try:
                await self.async_request_refresh()
            except Exception as err:
                _LOGGER.warning(
                    "Modbus write verification result %s at %s (Slave %s), "
                    "but coordinator refresh failed: %s",
                    verified, address, slave, err,
                )
            return verified

        except Exception as err:
            _LOGGER.error(
                "Error writing register %s (Slave %s): %s",
                address, slave, err,
            )
            return False

        

    async def async_write_register_32bit(self, address: int, value: int, slave: int = 2) -> bool:
        """Write a 32-bit signed value as two consecutive registers."""
        try:
            # Split into two big-endian 16-bit registers.
            packed = struct.pack(">i", value)
            high, low = struct.unpack(">HH", packed)

            async with self._modbus_lock:
                if not self.client.connected:
                    if not await self.client.connect():
                        raise ConnectionError("Unable to connect to H3X")
                    self._last_modbus_slave = None

                await self._pace_modbus(slave) 

                # Support different pymodbus versions.
                try:
                    res = await self.client.write_registers(
                        address=address,
                        values=[high, low],
                        slave=slave,
                    )
                except TypeError:
                    try:
                        res = await self.client.write_registers(
                            address=address,
                            values=[high, low],
                            unit=slave,
                        )
                    except TypeError:
                        res = await self.client.write_registers(
                            address=address,
                            values=[high, low],
                            device_id=slave,
                        )

                if res is None or res.isError():
                    _LOGGER.error(
                        "Modbus 32-bit write failed at %s (Slave %s): %s",
                        address, slave, res,
                    )
                    return False
                    
                verified = await self._verify_write_locked(
                    address, [high, low], slave
                )

            # A refresh failure must not change a verified write result.
            try:
                await self.async_request_refresh()
            except Exception as err:
                _LOGGER.warning(
                    "Modbus write verification result %s at %s (Slave %s), "
                    "but coordinator refresh failed: %s",
                    verified, address, slave, err,
                )
            return verified

        except Exception as err:
            _LOGGER.error(
                "Error writing 32-bit register %s (Slave %s): %s",
                address, slave, err,
            )
            return False

