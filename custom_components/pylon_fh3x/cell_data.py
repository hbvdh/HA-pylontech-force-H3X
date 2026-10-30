"""Decode and validate BMS module/cell data (Modbus slave 1, region 0x1400).

Layout, taken from the Pylontech FH3X protocol as used by the Pylontech-Research
integration:

    0x1400 + 0x00   pile header (module count at +54, cell count at +55,
                    max/min cell voltage at +16/+17)
    0x1400 + 0x60   module voltages       (0.01 V,  one register per module)
    0x1400 + 0xB0   module temperatures   (0.1 C,   signed, one per module)
    0x1400 + 0x100  cell voltages         (1 mV,    one register per cell)
    0x1400 + 0x400  cell temperatures     (0.1 C,   signed, one per cell)

This module has no Home Assistant or pymodbus imports so it can be tested alone.
"""
from __future__ import annotations

from dataclasses import dataclass

BMS_PILE_ADDRESS = 0x1400
HEADER_COUNT = 64
MODULE_COUNT_INDEX = 54
CELL_COUNT_INDEX = 55
HEADER_CELL_MAX_INDEX = 16
HEADER_CELL_MIN_INDEX = 17
MODULE_VOLTAGE_ADDRESS = BMS_PILE_ADDRESS + 0x60
MODULE_TEMPERATURE_ADDRESS = BMS_PILE_ADDRESS + 0xB0
CELL_VOLTAGE_ADDRESS = BMS_PILE_ADDRESS + 0x100
CELL_TEMPERATURE_ADDRESS = BMS_PILE_ADDRESS + 0x400

# Broad sanity limits for one cell / one module. Not protection thresholds.
CELL_VOLTAGE_MIN_V = 2.0
CELL_VOLTAGE_MAX_V = 4.0
TEMPERATURE_MIN_C = -40.0
TEMPERATURE_MAX_C = 100.0
MAX_MODULES = 16
MAX_CELLS = 400
# The BMS header min/max is read a moment before the arrays; allow some drift.
HEADER_CROSSCHECK_TOLERANCE_V = 0.015
# Module voltage should roughly equal the sum of its cells.
MODULE_SUM_TOLERANCE = 0.05

# Rough open-circuit curve for one LFP cell. The BMS does not report a SOC per
# cell, so this is only an indication: LFP is very flat in the middle, and the
# voltage moves with load.
_LFP_SOC_CURVE = (
    (2.50, 0.0),
    (3.00, 2.0),
    (3.20, 10.0),
    (3.25, 20.0),
    (3.27, 30.0),
    (3.29, 50.0),
    (3.31, 70.0),
    (3.33, 80.0),
    (3.37, 90.0),
    (3.45, 95.0),
    (3.60, 100.0),
)


class CellDataError(ValueError):
    """The registers do not describe a plausible module/cell layout."""


@dataclass(frozen=True)
class CellLayout:
    modules: int
    cells: int

    @property
    def cells_per_module(self) -> int:
        return self.cells // self.modules


def _s16(value: int) -> int:
    return value - 0x10000 if value >= 0x8000 else value


def estimate_cell_soc(voltage: float) -> float:
    """Return an indicative state of charge (%) for one LFP cell voltage."""
    if voltage <= _LFP_SOC_CURVE[0][0]:
        return 0.0
    if voltage >= _LFP_SOC_CURVE[-1][0]:
        return 100.0
    for (v0, s0), (v1, s1) in zip(_LFP_SOC_CURVE, _LFP_SOC_CURVE[1:]):
        if v0 <= voltage <= v1:
            return round(s0 + (s1 - s0) * (voltage - v0) / (v1 - v0), 1)
    return 0.0  # pragma: no cover


def parse_layout(header: list[int]) -> CellLayout:
    """Read module/cell counts from the pile header and check they make sense."""
    if len(header) < HEADER_COUNT:
        raise CellDataError("pile header too short")
    modules = header[MODULE_COUNT_INDEX]
    cells = header[CELL_COUNT_INDEX]
    if not 1 <= modules <= MAX_MODULES:
        raise CellDataError(f"implausible module count {modules}")
    if not 1 <= cells <= MAX_CELLS:
        raise CellDataError(f"implausible cell count {cells}")
    if cells % modules:
        raise CellDataError(
            f"{cells} cells cannot be split evenly over {modules} modules"
        )
    return CellLayout(modules, cells)


def decode_cell_data(
    header: list[int],
    module_voltages: list[int],
    module_temperatures: list[int],
    cell_voltages: list[int],
    cell_temperatures: list[int],
) -> tuple[dict, list[str]]:
    """Decode and validate one complete read of the module/cell registers.

    Raises CellDataError when anything is implausible; a corrupt reply must
    never be partly published. Returns (data, notes). Notes list soft findings
    (not rejected) such as a module voltage that differs from its cell sum.
    """
    layout = parse_layout(header)
    if (
        len(module_voltages) != layout.modules
        or len(module_temperatures) != layout.modules
        or len(cell_voltages) != layout.cells
        or len(cell_temperatures) != layout.cells
    ):
        raise CellDataError("register block lengths do not match the layout")

    cells_v = [raw * 0.001 for raw in cell_voltages]
    for index, volts in enumerate(cells_v, start=1):
        if not CELL_VOLTAGE_MIN_V <= volts <= CELL_VOLTAGE_MAX_V:
            raise CellDataError(f"cell {index} voltage {volts:.3f} V out of range")

    cells_t = [_s16(raw) * 0.1 for raw in cell_temperatures]
    for index, temp in enumerate(cells_t, start=1):
        if not TEMPERATURE_MIN_C <= temp <= TEMPERATURE_MAX_C:
            raise CellDataError(f"cell {index} temperature {temp:.1f} C out of range")

    modules_v = [raw * 0.01 for raw in module_voltages]
    modules_t = [_s16(raw) * 0.1 for raw in module_temperatures]
    for index, temp in enumerate(modules_t, start=1):
        if not TEMPERATURE_MIN_C <= temp <= TEMPERATURE_MAX_C:
            raise CellDataError(f"module {index} temperature {temp:.1f} C out of range")

    per = layout.cells_per_module
    for index, volts in enumerate(modules_v, start=1):
        if not CELL_VOLTAGE_MIN_V * per <= volts <= CELL_VOLTAGE_MAX_V * per:
            raise CellDataError(f"module {index} voltage {volts:.2f} V out of range")

    # Hard cross-check: the minimum/maximum in the header must match the arrays.
    header_max = header[HEADER_CELL_MAX_INDEX] * 0.001
    header_min = header[HEADER_CELL_MIN_INDEX] * 0.001
    if (
        abs(max(cells_v) - header_max) > HEADER_CROSSCHECK_TOLERANCE_V
        or abs(min(cells_v) - header_min) > HEADER_CROSSCHECK_TOLERANCE_V
    ):
        raise CellDataError(
            "cell min/max in header (%.3f/%.3f V) do not match the cell array "
            "(%.3f/%.3f V)" % (header_min, header_max, min(cells_v), max(cells_v))
        )

    notes: list[str] = []
    modules = []
    for number in range(1, layout.modules + 1):
        start = (number - 1) * per
        volts = cells_v[start:start + per]
        temps = cells_t[start:start + per]
        module_voltage = modules_v[number - 1]
        # Soft check only: scale assumptions are not verified on every device.
        total = sum(volts)
        if total and abs(module_voltage - total) / total > MODULE_SUM_TOLERANCE:
            notes.append(
                f"module {number} voltage {module_voltage:.2f} V differs from "
                f"cell sum {total:.2f} V"
            )
        modules.append(
            {
                "number": number,
                "voltage": round(module_voltage, 2),
                "temperature": round(modules_t[number - 1], 1),
                "cell_voltages": [round(v, 3) for v in volts],
                "cell_temperatures": [round(t, 1) for t in temps],
                "cell_soc_estimate": [estimate_cell_soc(v) for v in volts],
                "cell_voltage_min": round(min(volts), 3),
                "cell_voltage_max": round(max(volts), 3),
                "cell_voltage_avg": round(sum(volts) / len(volts), 3),
                "cell_voltage_delta_mv": round((max(volts) - min(volts)) * 1000),
                "cell_temperature_min": round(min(temps), 1),
                "cell_temperature_max": round(max(temps), 1),
            }
        )

    return {
        "modules": modules,
        "module_count": layout.modules,
        "cells_per_module": per,
        "cell_voltage_min": round(min(cells_v), 3),
        "cell_voltage_max": round(max(cells_v), 3),
    }, notes
