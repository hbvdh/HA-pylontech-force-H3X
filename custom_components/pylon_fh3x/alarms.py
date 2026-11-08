"""Decode Force H3X PCS fault and warning bit fields.

The bit tables are taken from the official Pylontech Research integration
(https://github.com/Pylontech-Research/HA-pylontech-force-H3X,
custom_components/pylontech_fh3x/alarm_history.py), MIT License,
Copyright (c) 2026 FH3X Energy Card contributors.

Bits 0-31 come from registers 30148-30151 (fault/warning bits), bits 32 and
up from the extended words at 30626-30629.
"""
from __future__ import annotations

PCS_FAULTS = {
    0: "MCU fault",
    1: "Current sensor fault",
    2: "GFCI device fault",
    3: "Relay fault",
    4: "PV voltage high",
    5: "Isolation fault",
    6: "GFCI fault",
    7: "Overtemperature",
    8: "Grid missing",
    9: "Grid overvoltage",
    10: "Grid overfrequency",
    11: "DC injection current high",
    12: "Flash fault",
    13: "Main/voice MCU communication fault",
    14: "Bus voltage high",
    15: "Bus voltage low",
    16: "DRM S9 fault",
    17: "DRM S0 fault",
    18: "PE connection missing",
    19: "Bus voltage imbalance",
    20: "BMS communication failure",
    21: "CT not connected",
    22: "CT reverse connection",
    23: "Battery not connected",
    24: "Backup load overvoltage",
    25: "DC arc fault",
    26: "AFCI self-test fault",
    27: "Parallel address assignment fault",
    28: "Battery precharge circuit fault",
    29: "Battery protection GPIO fault",
    30: "Shutdown circuit fault",
    31: "Battery relay fault",
    32: "Meter communication fault",
    34: "Parallel slave CAN communication loss",
    35: "Parallel slave received master fault",
    36: "Parallel slave synchronization loss",
    39: "Parallel software version mismatch",
    40: "Battery voltage exceeds limit",
    42: "Parallel grid phase-order mismatch",
    43: "EPS voltage exceeds limit",
    44: "Grid phase fault",
}

PCS_WARNINGS = {
    0: "PV1 overcurrent",
    1: "PV2 overcurrent",
    2: "PV3 overcurrent",
    4: "PV1 undervoltage",
    5: "PV2 undervoltage",
    6: "PV3 undervoltage",
    8: "Bus overvoltage",
    9: "Bus undervoltage",
    10: "Inverter overcurrent",
    11: "Anti-islanding protection",
    12: "Grid peak overvoltage",
    13: "Firmware update active",
    14: "PE connection missing",
    15: "Grid voltage consistency warning",
    16: "Fan abnormal",
    17: "Backup load overload",
    18: "SPD failure",
    28: "Battery overcurrent",
    35: "First grid-connection parameters error",
    37: "Bus boost anomaly",
    38: "EPS instantaneous overcurrent",
}

# HA state strings are limited to 255 characters.
MAX_STATE_LENGTH = 250


def active_issues(bits: int, names: dict[int, str]) -> list[str]:
    """Return readable names for every set bit, including unknown bits."""
    issues = []
    bit = 0
    while bits >> bit:
        if bits & (1 << bit):
            issues.append(names.get(bit, f"Unknown bit {bit}"))
        bit += 1
    return issues


def combined_bits(data: dict, base_key: str) -> int | None:
    """Combine the base (bits 0-31) and extended (bits 32+) words.

    Returns None when the base word is not available. The extended word is
    optional: firmware that does not answer 30626 still yields bits 0-31.
    """
    base = data.get(base_key)
    if not isinstance(base, int):
        return None
    extended = data.get(f"{base_key}_extended")
    if isinstance(extended, int):
        return base | (extended << 32)
    return base


def issues_state(issues: list[str]) -> str:
    """Short state text: "None" or the joined issue names."""
    if not issues:
        return "None"
    text = ", ".join(issues)
    if len(text) > MAX_STATE_LENGTH:
        text = text[: MAX_STATE_LENGTH - 3] + "..."
    return text
