"""DataUpdateCoordinator for Pylontech Force H3X."""
import asyncio
import logging
import math
import struct
import time
from datetime import timedelta

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, DEFAULT_SCAN_INTERVAL, MANUFACTURER, MODEL
from .validation import TelemetryValidator, VALUE_RANGES
from .telemetry_guard import TelemetryTransitionGuard

_LOGGER = logging.getLogger(__name__)

# Retry-only thresholds, not hard limits for larger installations.
GRID_CURRENT_JUMP_RETRY_A = 50.0
GRID_CURRENT_INITIAL_RETRY_A = 100.0
GRID_CURRENT_RETRY_AGREEMENT_A = 10.0

# One request for registers 30100-30191 instead of three separate ones
# (30100/48, 30156/30, 30183/9). Fewer requests per poll means fewer chances
# for the dongle to return a late or mixed-up reply, and all values in the
# block come from the same moment. Falls back to the three small reads if the
# device refuses the larger one.
MAIN_BLOCK_ADDRESS = 30100
MAIN_BLOCK_COUNT = 92
MAIN_SLICE = slice(0, 48)        # 30100-30147
BATTERY_SLICE = slice(56, 86)    # 30156-30185
GRID_PHASE_SLICE = slice(83, 92) # 30183-30191
SINGLE_BLOCK_MAX_FAILURES = 3

# When the dongle stops answering, hammering it with reconnects does not help.
# A poll in which this many reads failed (each already retried once) counts as
# a bad poll; the rest of that poll is skipped, and after several bad polls in a
# row the poll interval is lengthened until the dongle answers again.
POLL_MAX_FAILED_READS = 3
BACKOFF_AFTER_BAD_POLLS = 3
BACKOFF_INTERVAL_S = 60

# ---------------------------------------------------------------------------
# Optional extra reads (device info, faults, AC-coupled PV, grid import limit)
# ---------------------------------------------------------------------------
# Every optional block has its own read interval below (0 = off).
#
# Safety rules for each optional block:
# - one attempt, no retry;
# - a Modbus exception reply (register not supported) counts as a failure,
#   without reconnecting; after OPTIONAL_MAX_FAILURES the block is switched off;
# - NO answer (timeout / connection error) switches the block off at once,
#   because an unanswered request is what confuses the dongle;
# - a switched-off block stays off until Home Assistant restarts;
# - optional failures never count towards the bad-poll backoff;
# - optional reads are skipped in any poll where a normal read failed.
ENABLE_OPTIONAL_READS = True   # master switch: False = all optional reads off

# Per block: interval in seconds between reads, 0 = off. The first read happens
# in the first healthy poll, after that at most once per interval. Longer
# intervals keep the bus free for the normal 5 s polls.

# Static device information: re-read hourly to pick up a firmware update.
READ_IDENTITY = 3600           # 30010/31  manufacturer, model, serial
READ_DEVICE_INFO_EXT = 3600    # 30036/43  PCS / AFCI firmware, rated power
READ_BMS_DEVICE_INFO = 3600    # 0x1000/31 (BMS, slave 1) BMS main / HMI version

# Settings and status. Fault/warning codes already come every 5 s with the
# 30100 block; these bits only tell which fault, so 5 minutes is enough.
READ_FAULT_BITS = 300          # 30148/5   fault bits, warning bits, fw update state
READ_SITE_LIMIT = 300          # 40937/2   grid import power limit

# Off: AC-coupled PV is covered by the third-party meter in the 30100 block.
# Fallback for the Growatt test; this block confused the dongle in a first test.
READ_MONITOR_EXT = 0           # 30626/34  extended bits, AC-coupled PV (newer firmware)

# Log every optional read (address, result, duration) at WARNING level, so the
# last request before any dongle problem is visible in the normal HA log.
LOG_OPTIONAL_READS = True

OPTIONAL_MAX_FAILURES = 3

IDENTITY_BLOCK = (30010, 31, 2)
DEVICE_INFO_EXT_BLOCK = (30036, 43, 2)
BMS_DEVICE_INFO_BLOCK = (0x1000, 31, 1)
FAULT_BITS_BLOCK = (30148, 5, 2)
SITE_LIMIT_BLOCK = (40937, 2, 2)
MONITOR_EXT_BLOCK = (30626, 34, 2)


def block_interval(block) -> int:
    """Read interval in seconds for a block, 0 = off.

    Looked up at call time so tests can patch the settings above.
    """
    if not ENABLE_OPTIONAL_READS:
        return 0
    setting = {
        IDENTITY_BLOCK[0]: "READ_IDENTITY",
        DEVICE_INFO_EXT_BLOCK[0]: "READ_DEVICE_INFO_EXT",
        BMS_DEVICE_INFO_BLOCK[0]: "READ_BMS_DEVICE_INFO",
        FAULT_BITS_BLOCK[0]: "READ_FAULT_BITS",
        SITE_LIMIT_BLOCK[0]: "READ_SITE_LIMIT",
        MONITOR_EXT_BLOCK[0]: "READ_MONITOR_EXT",
    }.get(block[0])
    return max(0, int(globals()[setting])) if setting else 0


_GRID_PHASE_KEYS = (
    "grid_current_r", "grid_current_s", "grid_current_t",
    "grid_power_r", "grid_power_s", "grid_power_t",
)

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


def _decode_main_sample(regs):
    return {
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
    }


def _decode_grid_phase_sample(regs):
    return {
        "grid_current_r": get_16bit_int(regs, 0) * 0.1,
        "grid_current_s": get_16bit_int(regs, 1) * 0.1,
        "grid_current_t": get_16bit_int(regs, 2) * 0.1,
        "grid_power_r": get_32bit_int(regs, 3),
        "grid_power_s": get_32bit_int(regs, 5),
        "grid_power_t": get_32bit_int(regs, 7),
    }


def _decode_battery_sample(regs):
    return {
        "battery_power": get_32bit_int(regs, 6),
        "battery_voltage": get_16bit_uint(regs, 8) * 0.1,
        "battery_current": get_16bit_int(regs, 9) * 0.1,
        "battery_soc": get_16bit_uint(regs, 26),
    }


def _ascii(regs, idx, count):
    raw = b"".join(struct.pack(">H", regs[i]) for i in range(idx, idx + count))
    return raw.replace(b"\x00", b"").decode("ascii", errors="ignore").strip()


def _version_4(regs, idx):
    """Two registers as four bytes: V1.0.0.43 (PCS / AFCI firmware)."""
    raw = struct.pack(">HH", regs[idx], regs[idx + 1])
    return "V" + ".".join(str(part) for part in raw)


def _version_2(reg):
    """One register as two bytes: V0.3 (BMS main / HMI version)."""
    return f"V{reg >> 8}.{reg & 0xFF}"


def decode_identity(regs):
    """PCS 30010-30040: identity strings."""
    return {
        "manufacturer": _ascii(regs, 0, 8),
        "model": _ascii(regs, 8, 8),
        "serial": _ascii(regs, 16, 8),
    }


def decode_device_info_ext(regs):
    """PCS 30036-30078: firmware versions and ratings."""
    return {
        "pcs_max_output_power": get_16bit_uint(regs, 0),
        "pcs_software_version": _version_4(regs, 3),
        "pcs_afci_firmware_version": _version_4(regs, 25),
        "pcs_parallel_machine_count": get_16bit_uint(regs, 35),
    }


def decode_bms_device_info(regs):
    """BMS 0x1000-0x101E (unit 1): BMS firmware versions and model."""
    return {
        "bms_main_version": _version_2(regs[10]),
        "bms_hmi_version": _version_2(regs[30]),
        "bms_product_model": _ascii(regs, 14, 16),
    }


def decode_fault_bits(regs):
    """PCS 30148-30152."""
    return {
        "fault_bits": (regs[0] << 16) | regs[1],
        "warning_bits": (regs[2] << 16) | regs[3],
        "firmware_update_state": regs[4],
    }


def decode_monitor_ext(regs):
    """PCS 30626-30659 (part of the 30600 extended monitor block)."""
    acc = [get_32bit_int(regs, i) for i in (28, 30, 32)]   # 30654/56/58
    return {
        "fault_bits_extended": (regs[0] << 16) | regs[1],   # 30626
        "warning_bits_extended": (regs[2] << 16) | regs[3], # 30628
        "acc_power_r": acc[0],
        "acc_power_s": acc[1],
        "acc_power_t": acc[2],
        "acc_power_total": sum(acc),
    }


def decode_site_limit(regs):
    """PCS 40937-40938: grid import power limit (U32, W)."""
    return {"grid_import_power_limit": (regs[0] << 16) | regs[1]}


def _decode_single_block_sample(regs):
    """Decode the sanity-check values of the combined 30100-30191 block."""
    return {
        **_decode_main_sample(regs[MAIN_SLICE]),
        **_decode_battery_sample(regs[BATTERY_SLICE]),
        **_decode_grid_phase_sample(regs[GRID_PHASE_SLICE]),
    }


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
        self._transition_guard = TelemetryTransitionGuard()
        self._modbus_lock = asyncio.Lock()
        self._last_modbus_slave = None
        self._last_valid_grid_currents = {}
        # Off by default: the combined read caused errors on a live system.
        # The code path stays available for testing (set to True to enable).
        self.use_single_block_read = False
        self._single_block_failures = 0
        self._bad_polls = 0
        self._failed_reads_this_poll = 0
        self._in_poll = False

        # Optional reads: static device info, slow blocks and their cache.
        self.device_identity: dict = {}
        self._optional_failures: dict[int, int] = {}
        self._optional_disabled: set[int] = set()
        self._optional_next_read: dict[int, float] = {}
        self._slow_values: dict = {}

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

    @property
    def ac_coupled_pv_enabled(self) -> bool:
        """True when the 30626 block (AC-coupled PV) is read at all."""
        return block_interval(MONITOR_EXT_BLOCK) > 0

    def _optional_due(self, block) -> bool:
        """True when a block is on, not switched off, and its interval passed."""
        address = block[0]
        if block_interval(block) <= 0 or address in self._optional_disabled:
            return False
        next_read = self._optional_next_read.get(address)
        return next_read is None or time.monotonic() >= next_read

    async def _read_optional(self, block, decode) -> dict | None:
        """Read an optional block once; never retry or reconnect on a refusal.

        A Modbus exception reply (e.g. illegal address on older firmware) only
        counts a failure. A timeout or connection error closes the connection,
        exactly like the normal read path, because a late reply could otherwise
        be taken for the answer to the next request. After OPTIONAL_MAX_FAILURES
        failures in a row the block is disabled until Home Assistant restarts.
        Failures here never count towards the bad-poll backoff.
        """
        address, count, slave = block
        if not self._optional_due(block):
            return None

        error = None
        registers = None
        no_answer = False
        started = time.monotonic()
        # Next attempt one interval from now, also after a failure, so a
        # refusing block is not hammered every poll.
        self._optional_next_read[address] = started + block_interval(block)
        async with self._modbus_lock:
            try:
                if not self.client.connected:
                    if not await self.client.connect():
                        raise ConnectionError("Unable to connect to H3X")
                    self._last_modbus_slave = None
                await self._pace_modbus(slave)
                res = await _modbus_read(self.client, address, count, slave)
                if res is not None and not res.isError():
                    regs = getattr(res, "registers", None)
                    if (
                        isinstance(regs, (list, tuple))
                        and len(regs) == count
                        and all(type(w) is int and 0 <= w <= 0xFFFF for w in regs)
                    ):
                        registers = list(regs)
                    else:
                        error = "malformed register response"
                else:
                    error = f"Modbus error response: {res}"
            except Exception as err:  # no (valid) answer from the dongle
                error = f"{type(err).__name__}: {err}"
                no_answer = True
                self.client.close()
                self._last_modbus_slave = None
        duration = time.monotonic() - started

        if registers is not None:
            try:
                values = decode(registers)
            except (struct.error, IndexError, ValueError) as err:
                error = f"decode error: {err}"
            else:
                self._optional_failures[address] = 0
                if LOG_OPTIONAL_READS:
                    _LOGGER.warning(
                        "Optional read %s/%s (Slave %s): OK in %.2f s",
                        address, count, slave, duration,
                    )
                return values

        failures = self._optional_failures.get(address, 0) + 1
        self._optional_failures[address] = failures
        disable = no_answer or failures >= OPTIONAL_MAX_FAILURES
        if disable:
            self._optional_disabled.add(address)
        if LOG_OPTIONAL_READS or disable:
            _LOGGER.warning(
                "Optional read %s/%s (Slave %s): FAILED in %.2f s (%s)%s",
                address, count, slave, duration, error,
                "; block switched off until Home Assistant restarts"
                if disable else f"; attempt {failures}/{OPTIONAL_MAX_FAILURES}",
            )
        return None

    def build_device_info(self, entry) -> dict:
        """Device registry info, enriched with firmware data when available."""
        ident = self.device_identity
        info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": ident.get("manufacturer") or MANUFACTURER,
            "model": ident.get("model") or MODEL,
        }
        if ident.get("serial"):
            info["serial_number"] = ident["serial"]
        if ident.get("pcs_software_version"):
            info["sw_version"] = f"PCS {ident['pcs_software_version']}"
        bms = [
            f"{label} {ident[key]}"
            for label, key in (("BMS", "bms_main_version"), ("HMI", "bms_hmi_version"))
            if ident.get(key)
        ]
        if bms:
            info["hw_version"] = " / ".join(bms)
        return info

    def _push_device_info(self) -> None:
        """Write firmware/model info to the device registry once it is known.

        At startup this runs before the entities exist (the entities then pick
        it up via build_device_info); if the identity only arrives on a later
        poll, the existing device entry is updated here.
        """
        entry = getattr(self, "config_entry_ref", None)
        if entry is None or not self.device_identity:
            return
        try:
            from homeassistant.helpers import device_registry as dr

            info = self.build_device_info(entry)
            info.pop("name", None)
            dr.async_get(self.hass).async_get_or_create(
                config_entry_id=entry.entry_id, **info
            )
        except Exception as err:  # never let device info break polling
            _LOGGER.debug("Could not update device info: %s", err)

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

        # Detect impossible relationships between individually plausible BMS
        # readings before the stateful validator runs. For example, a cell
        # minimum of 3.27 V cannot exceed a cell maximum of 0.20 V.
        cell_min = data.get("bms_cell_voltage_min")
        cell_max = data.get("bms_cell_voltage_max")
        if (
            isinstance(cell_min, (int, float))
            and isinstance(cell_max, (int, float))
            and math.isfinite(cell_min)
            and math.isfinite(cell_max)
            and cell_min > cell_max
        ):
            for key in ("bms_cell_voltage_min", "bms_cell_voltage_max"):
                if key not in suspicious:
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

        # Temporal checks complement broad static ranges (e.g. 35 -> 0 C).
        for key in self._transition_guard.suspicious(data):
            if key not in suspicious:
                suspicious.append(key)
        return suspicious

    async def _read_with_sanity_retry(
        self, address, count, slave, decode, unconfirmed=None, partial_ok=False
    ):
        """Retry a register block once if decoded values look implausible."""
        registers = await self.safe_read(address, count, slave)

        if registers is None:
            return None

        first_values = decode(registers)
        suspicious = self._find_suspicious_values(first_values)
        first_transitions = self._transition_guard.suspicious(first_values)

        if not suspicious:
            return registers

        def log_first_read(level):
            _LOGGER.log(
                level,
                "Suspicious Modbus values at address %s (Slave %s): %s. "
                "Reading register block again on a fresh connection. "
                "Raw registers (first read): %s",
                address, slave, ", ".join(suspicious), list(registers),
            )

        # Evidence/robustness: a suspicious reply may be a late or mixed-up
        # response left on the TCP connection by the dongle. Re-read on a new
        # connection so the second sample cannot share that contamination.
        async with self._modbus_lock:
            self.client.close()
            self._last_modbus_slave = None
        await asyncio.sleep(0.2)

        retry_registers = await self.safe_read(
            address, count, slave
        )

        if retry_registers is None:
            log_first_read(logging.WARNING)
            if unconfirmed is not None:
                for key in first_transitions:
                    if self._transition_guard.should_suppress_unconfirmed(
                        key, first_values[key]
                    ):
                        unconfirmed.add(key)
            if any(key.startswith("grid_current_") for key in suspicious):
                # Without a confirming sample a huge current jump cannot be
                # distinguished from a corrupt Modbus response. Skip this
                # phase block rather than publish an unverified current.
                _LOGGER.warning(
                    "Skipping grid phase block %s: current jump could not be verified",
                    address,
                )
                if partial_ok and unconfirmed is not None:
                    # Combined block: drop only the grid phase values.
                    unconfirmed.update(_GRID_PHASE_KEYS)
                    return registers
                return None
            # The normal validator will reject any static out-of-range values.
            return registers

        changed = [
            (i, a, b)
            for i, (a, b) in enumerate(zip(registers, retry_registers))
            if a != b
        ]
        retry_values = decode(retry_registers)
        still_suspicious = self._find_suspicious_values(retry_values)
        # A confirmed large change may be genuine. If repeat readings differ,
        # suppress ONLY the unconfirmed field, never the whole register block.
        # Temperatures rising rapidly are fail-open to preserve safety signals.
        second_transitions = self._transition_guard.suspicious(retry_values)
        confirmed_changes = set()
        for key in first_transitions | second_transitions:
            if key not in retry_values:
                continue
            if key not in second_transitions:
                continue  # Second reading returned to a plausible value.
            if key in first_transitions and self._transition_guard.confirmed(
                key, first_values[key], retry_values[key]
            ):
                confirmed_changes.add(key)
                continue  # Two consistent samples confirm a real change.
            if unconfirmed is not None and self._transition_guard.should_suppress_unconfirmed(
                key, retry_values[key]
            ):
                unconfirmed.add(key)
                _LOGGER.warning(
                    "Ignoring unconfirmed temporal anomaly %s at %s (Slave %s)",
                    key, address, slave,
                )
        # A confirmed change (e.g. PV MPPT re-sweeping at dusk) is real
        # telemetry, not a fault: report it once at INFO and keep the raw
        # register evidence at DEBUG. Anything else keeps the full WARNINGs.
        if (
            confirmed_changes
            and set(suspicious) <= confirmed_changes
            and second_transitions <= confirmed_changes
        ):
            _LOGGER.info(
                "Confirmed genuine change at address %s (Slave %s): %s",
                address, slave, ", ".join(
                    f"{key} {first_values[key]:g} -> {retry_values[key]:g}"
                    for key in sorted(confirmed_changes)
                ),
            )
            log_first_read(logging.DEBUG)
            _LOGGER.debug(
                "Modbus re-read at address %s (Slave %s) differs in %s of %s "
                "registers (index, first, second): %s",
                address, slave, len(changed), count, changed[:20],
            )
            return retry_registers

        log_first_read(logging.WARNING)
        _LOGGER.warning(
            "Modbus re-read at address %s (Slave %s) differs in %s of %s "
            "registers (index, first, second): %s",
            address, slave, len(changed), count, changed[:20],
        )
        still_suspicious = [
            key for key in still_suspicious if key not in confirmed_changes
        ]

        for key in suspicious:
            if key.startswith("grid_current_") and key in still_suspicious:
                if abs(retry_values[key] - first_values[key]) > GRID_CURRENT_RETRY_AGREEMENT_A:
                    _LOGGER.warning(
                        "Skipping grid phase block %s: unconfirmed current jump "
                        "%s (first=%s A, retry=%s A)",
                        address, key, first_values[key], retry_values[key],
                    )
                    if partial_ok and unconfirmed is not None:
                        unconfirmed.update(_GRID_PHASE_KEYS)
                        continue
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
        if self._in_poll and self._failed_reads_this_poll >= POLL_MAX_FAILED_READS:
            # The dongle is not answering; do not keep reconnecting this poll.
            return None
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
                            if attempt == 1:
                                _LOGGER.warning(
                                    "Modbus retry successful at %s (Slave %s)",
                                    address, slave,
                                )
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
                        "Modbus retry failed at %s (Slave %s): %s",
                        address, slave, error,
                    )

            if self._in_poll:
                self._failed_reads_this_poll += 1
            return None


    async def _async_update_data(self):
        """Poll the inverter; back off when the dongle stops answering."""
        self._failed_reads_this_poll = 0
        self._in_poll = True
        try:
            data = await self._async_poll_once()
        except UpdateFailed:
            self._register_poll_result(bad=True)
            raise
        finally:
            self._in_poll = False
        self._register_poll_result(
            bad=self._failed_reads_this_poll >= POLL_MAX_FAILED_READS,
            healthy=self._failed_reads_this_poll == 0,
        )
        return data

    def _register_poll_result(self, bad: bool, healthy: bool = False) -> None:
        """Lengthen the poll interval after repeated bad polls; restore on recovery."""
        if bad:
            self._bad_polls += 1
            if (
                self._bad_polls >= BACKOFF_AFTER_BAD_POLLS
                and self.update_interval != timedelta(seconds=BACKOFF_INTERVAL_S)
            ):
                self.update_interval = timedelta(seconds=BACKOFF_INTERVAL_S)
                _LOGGER.warning(
                    "The H3X has not answered for %s polls in a row; polling "
                    "once every %s s until it responds again.",
                    self._bad_polls, BACKOFF_INTERVAL_S,
                )
        elif healthy and (
            self._bad_polls
            or self.update_interval != timedelta(seconds=DEFAULT_SCAN_INTERVAL)
        ):
            self._bad_polls = 0
            if self.update_interval != timedelta(seconds=DEFAULT_SCAN_INTERVAL):
                self.update_interval = timedelta(seconds=DEFAULT_SCAN_INTERVAL)
                _LOGGER.warning(
                    "The H3X answers again; polling every %s s.",
                    DEFAULT_SCAN_INTERVAL,
                )

    async def _async_poll_once(self):
        """Fetch data from the inverter via Modbus."""
        try:
            data = {}
            unconfirmed = set()

            r_inverter_main = r_grid_phases = r_inverter_battery = None
            if self.use_single_block_read:
                r_all = await self._read_with_sanity_retry(
                    MAIN_BLOCK_ADDRESS,
                    MAIN_BLOCK_COUNT,
                    2,
                    _decode_single_block_sample,
                    unconfirmed=unconfirmed,
                    partial_ok=True,
                )
                if r_all:
                    self._single_block_failures = 0
                    r_inverter_main = r_all[MAIN_SLICE]
                    r_inverter_battery = r_all[BATTERY_SLICE]
                    r_grid_phases = r_all[GRID_PHASE_SLICE]
                else:
                    self._single_block_failures += 1
                    if self._single_block_failures >= SINGLE_BLOCK_MAX_FAILURES:
                        self.use_single_block_read = False
                        _LOGGER.warning(
                            "Combined Modbus read of %s-%s failed %s times in a "
                            "row; using the three smaller reads instead.",
                            MAIN_BLOCK_ADDRESS,
                            MAIN_BLOCK_ADDRESS + MAIN_BLOCK_COUNT - 1,
                            self._single_block_failures,
                        )
                    else:
                        _LOGGER.warning(
                            "Combined Modbus read failed (%s/%s); using the "
                            "smaller reads for this poll.",
                            self._single_block_failures,
                            SINGLE_BLOCK_MAX_FAILURES,
                        )

            if not self.use_single_block_read or r_inverter_main is None:
                r_inverter_main = await self._read_with_sanity_retry(
                    30100, 48, 2, _decode_main_sample, unconfirmed=unconfirmed,
                )
                r_grid_phases = await self._read_with_sanity_retry(
                    30183, 9, 2, _decode_grid_phase_sample, unconfirmed=unconfirmed,
                )
                r_inverter_battery = await self._read_with_sanity_retry(
                    30156, 30, 2, _decode_battery_sample, unconfirmed=unconfirmed,
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

                # Already inside the existing 30100/48 read: no extra Modbus traffic.
                # 30110/30112: third-party meter (CT2 / AC-coupled PV). The V1.2
                # protocol says U32; the official integration reads it signed.
                data["third_party_meter_power"] = get_32bit_int(r_inverter_main, 10)
                data["third_party_meter_energy"] = get_32bit_float(r_inverter_main, 12)
                data["fault_code"] = get_16bit_uint(r_inverter_main, 17)
                data["warning_code"] = get_16bit_uint(r_inverter_main, 18)

            # Per-phase grid current/power (CT clamp measurements at the grid connection).
            if r_grid_phases:
                data["grid_current_r"] = get_16bit_int(r_grid_phases, 0) * 0.1
                data["grid_current_s"] = get_16bit_int(r_grid_phases, 1) * 0.1
                data["grid_current_t"] = get_16bit_int(r_grid_phases, 2) * 0.1
                data["grid_power_r"] = get_32bit_int(r_grid_phases, 3)
                data["grid_power_s"] = get_32bit_int(r_grid_phases, 5)
                data["grid_power_t"] = get_32bit_int(r_grid_phases, 7)

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
                unconfirmed=unconfirmed,
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

            # Optional PCS (slave 2) reads, still before switching to slave 1.
            # Skipped entirely when a normal read already failed this poll, so a
            # struggling dongle never gets extra requests.
            # Each block is read only when its own interval has passed.
            if self._failed_reads_this_poll == 0:
                identity_changed = False
                for block, decode in (
                    (IDENTITY_BLOCK, decode_identity),
                    (DEVICE_INFO_EXT_BLOCK, decode_device_info_ext),
                ):
                    values = await self._read_optional(block, decode)
                    if values:
                        self.device_identity.update(values)
                        identity_changed = True
                if identity_changed:
                    self._push_device_info()
                for block, decode in (
                    (FAULT_BITS_BLOCK, decode_fault_bits),
                    (MONITOR_EXT_BLOCK, decode_monitor_ext),
                    (SITE_LIMIT_BLOCK, decode_site_limit),
                ):
                    values = await self._read_optional(block, decode)
                    if values:
                        self._slow_values.update(values)

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
                unconfirmed=unconfirmed,
            )
            if r_bms:
                data["bms_voltage"] = get_16bit_uint(r_bms, 0) * 0.1
                data["bms_temperature"] = get_16bit_int(r_bms, 3) * 0.1
                data["bms_soc"] = get_16bit_uint(r_bms, 4)
                data["bms_cycles"] = get_16bit_uint(r_bms, 5)
                data["bms_cell_voltage_max"] = get_16bit_uint(r_bms, 13) * 0.001
                data["bms_cell_voltage_min"] = get_16bit_uint(r_bms, 14) * 0.001
                data["bms_soh"] = get_16bit_uint(r_bms, 29)

            # Optional BMS (slave 1) device info, after the normal BMS read so
            # the slave only switches once per poll.
            if self._failed_reads_this_poll == 0:
                values = await self._read_optional(
                    BMS_DEVICE_INFO_BLOCK, decode_bms_device_info
                )
                if values:
                    self.device_identity.update(values)
                    self._push_device_info()

            # Slow optional values are cached between their reads, so their
            # sensors do not flip to unknown in between. Only added when this
            # poll produced real data, so a dead dongle still raises UpdateFailed.
            if data:
                data = {**self._slow_values, **data}

            # Drop just the unconfirmed fields; preserve all other readings.
            for key in unconfirmed:
                data.pop(key, None)
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

            # Baselines must never be updated with rejected/unconfirmed values.
            self._transition_guard.remember(data)
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

            # Only report success after the read-back matches exactly. This
            # does not imply the device has already physically acted on it.
            if len(expected) == 1:
                _LOGGER.info(
                    "Modbus write verified at %s (Slave %s): value %s",
                    address, slave, actual[0],
                )
            else:
                _LOGGER.info(
                    "Modbus write verified at %s (Slave %s): values %s",
                    address, slave, list(actual),
                )
            return True

        except (OSError, TimeoutError, ModbusException) as err:
            _LOGGER.error(
                "Modbus write verification error at %s "
                "(Slave %s): %s",
                address, slave, err,
            )
            # The caller will reconnect and check the register again. Avoid
            # closing the connection twice before that recovery step.
            return False

    async def _reconcile_write_locked(
        self, address: int, expected: list[int], slave: int
    ) -> bool:
        """After an unconfirmed write, reconnect and READ; never repeat the write.

        The write might have reached the inverter even if its acknowledgement was
        lost. A matching read-back confirms the *requested register state*, not
        that a write took place (it may already have held that value).
        """
        # The caller already owns _modbus_lock. Never call safe_read here;
        # re-acquiring the same lock would deadlock.
        self.client.close()
        self._last_modbus_slave = None
        await asyncio.sleep(0.2)
        try:
            if not await self.client.connect():
                raise ConnectionError("Unable to reconnect for write verification")
            await self._pace_modbus(slave)
            response = await _modbus_read(
                self.client, address, len(expected), slave
            )
            if response is None or response.isError():
                _LOGGER.error(
                    "Modbus write outcome unknown at %s (Slave %s): "
                    "reconnect read returned %s; write NOT repeated",
                    address, slave, response,
                )
                return False
            actual = getattr(response, "registers", None)
            if not isinstance(actual, (list, tuple)) or (
                len(actual) != len(expected)
                or not all(type(word) is int and 0 <= word <= 0xFFFF for word in actual)
            ):
                _LOGGER.error(
                    "Modbus write outcome unknown at %s (Slave %s): "
                    "invalid reconnect read %s; write NOT repeated",
                    address, slave, actual,
                )
                return False
            if list(actual) != expected:
                _LOGGER.error(
                    "Modbus write requested state NOT confirmed at %s (Slave %s): "
                    "expected %s, read %s; write NOT repeated",
                    address, slave, expected, list(actual),
                )
                return False
            _LOGGER.warning(
                "Modbus write acknowledgement/verification was uncertain at %s "
                "(Slave %s), but requested register state confirmed by reconnect "
                "read-back: %s; write NOT repeated",
                address, slave, list(actual),
            )
            return True
        except (OSError, TimeoutError, ModbusException) as err:
            _LOGGER.error(
                "Modbus write outcome unknown at %s (Slave %s): "
                "reconnect read failed: %s; write NOT repeated",
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
                
                # A timeout during a write does NOT prove it was rejected.
                # Inspect the register after reconnect, never blindly re-write.
                try:
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
                    acknowledged = res is not None and not res.isError()
                except (OSError, TimeoutError, ModbusException) as err:
                    acknowledged = False
                    _LOGGER.warning(
                        "Modbus write response uncertain at %s (Slave %s): %s; "
                        "checking requested register state after reconnect",
                        address, slave, err,
                    )
                else:
                    if not acknowledged:
                        _LOGGER.warning(
                            "Modbus write not acknowledged at %s (Slave %s): %s; "
                            "checking requested register state after reconnect",
                            address, slave, res,
                        )

                verified = (
                    await self._verify_write_locked(address, [value], slave)
                    if acknowledged else False
                )
                if not verified:
                    verified = await self._reconcile_write_locked(
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

                try:
                    try:
                        res = await self.client.write_registers(
                            address=address, values=[high, low], slave=slave,
                        )
                    except TypeError:
                        try:
                            res = await self.client.write_registers(
                                address=address, values=[high, low], unit=slave,
                            )
                        except TypeError:
                            res = await self.client.write_registers(
                                address=address, values=[high, low], device_id=slave,
                            )
                    acknowledged = res is not None and not res.isError()
                except (OSError, TimeoutError, ModbusException) as err:
                    acknowledged = False
                    _LOGGER.warning(
                        "Modbus 32-bit write response uncertain at %s (Slave %s): "
                        "%s; checking requested register state after reconnect",
                        address, slave, err,
                    )
                else:
                    if not acknowledged:
                        _LOGGER.warning(
                            "Modbus 32-bit write not acknowledged at %s (Slave %s): "
                            "%s; checking requested register state after reconnect",
                            address, slave, res,
                        )

                verified = (
                    await self._verify_write_locked(address, [high, low], slave)
                    if acknowledged else False
                )
                if not verified:
                    verified = await self._reconcile_write_locked(
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

