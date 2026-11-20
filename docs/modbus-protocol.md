# Pylontech Force H3X: register map of this integration

This page lists the Modbus registers this integration reads and writes, how it
interprets them, and what we learned about the device along the way. It is a
description of the integration, not a copy of a manufacturer document: registers
the integration does not use are not listed.

**Sources.** The addresses and formats were worked out from the Pylontech Modbus
protocol (not included in this repository; obtain it from Pylontech), the register
map in the MIT-licensed [Pylontech Research integration](https://github.com/Pylontech-Research/HA-pylontech-force-H3X),
and checks on a running system (FH3X-5K-HY-1P-EU, PCS V1.0.0.43, BMS V0.34).
Points that rest only on live observation are marked *(live)*.

See [optional-reads.md](optional-reads.md) for the read intervals and
[telemetry-validation.md](telemetry-validation.md) for the plausibility checks.

## Connection

- Modbus TCP, port 502, through the H3X's Wi-Fi/LAN dongle.
- Two unit (slave) IDs:
  - **Unit 2**: the inverter (PCS). Addresses are decimal (30xxx, 40xxx).
  - **Unit 1**: the battery management system (BMS). Addresses are written in hex.
- Function code 03 (read holding registers) for reads, 06/16 for writes.
- Multi-register values are big-endian (high word first).

Notation in the tables: **U16/S16** unsigned/signed 16-bit, **U32/S32** two
registers, **F32** IEEE-754 float over two registers, **ASCII** two characters per
register. *Scale* is what the raw value is multiplied by.

---

## Unit 2: inverter (PCS)

### Identity and versions

Read once per hour.

| Register | Format | Scale / unit | Meaning |
| --- | --- | --- | --- |
| 30010 | ASCII × 8 | | Manufacturer |
| 30018 | ASCII × 8 | | Model |
| 30026 | ASCII × 8 | | Serial number |
| 30036 | U16 | W | Maximum output power |
| 30039 | 4 × U8 | | PCS firmware version (Vx.x.x.x) |
| 30061 | 4 × U8 | | AFCI firmware version |
| 30071 | U16 | | Number of inverters in parallel |

The serial number identifies the H3X. When a different serial number answers, it is
read again after a minute before anything is concluded; a confirmed different
serial number leads to the replaced-H3X confirmation in Home Assistant.

### Live values (30100-30191)

Read every 5 seconds in one request of 92 registers (see
[Practical notes](#practical-notes)).

| Register | Format | Scale / unit | Meaning |
| --- | --- | --- | --- |
| 30100 | S32 | W | AC power of the inverter (+ output, − input) |
| 30108 | S32 | W | Grid power total (+ import, − export) |
| 30110 | S32 | W | Third-party meter power (meter CT2, e.g. a separate PV inverter) |
| 30112 | F32 | kWh | Third-party meter energy |
| 30115 | U16 | | Inverter state (0 wait, 1 normal, 2 fault) |
| 30117 | U16 | | Fault code (0 = none) |
| 30118 | U16 | | Warning code (0 = none) |
| 30119-30124 | U16 | 0.1 V / 0.1 A | PV1, PV2, PV3: voltage and current, alternating |
| 30127 | S32 | W | PV power total |
| 30129 | F32 | kWh | PV energy total |
| 30131-30136 | U16 | 0.1 V / 0.1 A | Phase R, S, T: voltage and current, alternating |
| 30140 | U16 | 0.01 Hz | Grid frequency |
| 30142 | U16 | kΩ | Insulation resistance PV/DC to earth |
| 30146 | S16 | 0.1 °C | Internal temperature |
| 30147 | S16 | 0.1 °C | Heatsink temperature |
| 30156 | F32 | kWh | Grid import energy (meter) |
| 30158 | F32 | kWh | Grid export energy (meter) |
| 30160 | U16 | | Grid mode: 0 none, 1 grid-tied, 2 backup |
| 30161 | U16 | | Battery state: 0 sleep, 1 charging, 2 discharging, 3 idle, 4 standby, 5 run, 6 fault, 7 offline |
| 30162 | S32 | W | Battery power (+ discharge, − charge) |
| 30164 | U16 | 0.1 V | Battery voltage |
| 30165 | S16 | 0.1 A | Battery current (+ discharge, − charge) |
| 30170 | U16 | 0.1 V | EPS (backup) voltage |
| 30171 | U16 | 0.01 Hz | EPS frequency |
| 30172 | S32 | W | EPS power |
| 30174 | F32 | kWh | Battery charge energy total |
| 30176 | F32 | kWh | Battery discharge energy total |
| 30180 | F32 | kWh | EPS output energy |
| 30182 | U16 | % | Battery SOC |
| 30183-30185 | S16 | 0.1 A | Grid current per phase R, S, T (meter/CT) |
| 30186-30191 | S32 | W | Grid power per phase R, S, T (+ import, − export) |

Notes:

- 30110 is read signed. Some descriptions call it unsigned; signed matches what the
  device sends.

### Fault and warning bits

Read every 5 minutes.

| Register | Format | Meaning |
| --- | --- | --- |
| 30148 | U32 | Fault bits 0-31 |
| 30150 | U32 | Warning bits 0-31 |
| 30152 | U16 | Firmware update state: 0 idle, 1 download finished, 2 update finished |
| 30626 | U32 | Fault bits 32 and up (bit 0 here = bit 32) |
| 30628 | U32 | Warning bits 32 and up |

The bit names are in [`alarms.py`](../custom_components/pylon_fh3x/alarms.py)
(taken from the Pylontech Research integration). A set bit without a known name is
shown as "Unknown bit N".

### Extended monitor block (30600-30659)

Read every 5 minutes in one request of 60 registers. Only newer firmware answers
this block; on older firmware it is switched off automatically.

| Register | Format | Scale / unit | Meaning |
| --- | --- | --- | --- |
| 30600-30602 | U16 | 0.1 A | EPS current per phase R, S, T |
| 30626-30629 | | | Extended fault/warning bits (see above) |
| 30654-30659 | S32 | W | AC-coupled PV power per phase R, S, T |

### Control and settings

Read every poll unless stated otherwise.

| Register | Format | Scale / unit | Access | Meaning |
| --- | --- | --- | --- | --- |
| 40400 | U16 | | read | Active power control mode |
| 40401 | S32 | W | read/write | Meter export limit; always negative |
| 40848 | U16 | | read/write | Heat pump switch: 0 off, 1 on |
| 40901 | S16 | 0.1 % of rated power | read/write | Charge/discharge power setpoint (+ discharge, − charge, ±1000 = ±100 %); used in User mode |
| 40902 | U16 | % | read/write | Charge limit SOC (50-100) |
| 40903 | U16 | % | read/write | Discharge limit SOC (5-100) |
| 40904 | U16 | | read | EPS (backup) output enabled |
| 40907 | U16 | | read/write | EMS mode, see below |
| 40908, 40914, 40920, 40926 | U16 | | read/write | Enable of time slot 1, 2, 3, 4 |
| 40937 | U32 | W | read (every 5 min) | Grid import power limit; shown only, never written |

EMS mode values:

| Value | Mode |
| --- | --- |
| 0 | Self-consumption |
| 1 | Backup |
| 2 | Off-grid |
| 3 | Feed-in priority |
| 4 | User mode (setpoint from 40901) |
| 5 | PN-customer mode |
| 262 (0x0106) | Dynamic mode, set in the Pylontech app *(live)*. Shown, never written. |

Every write is read back. If the H3X does not confirm the new value, Home Assistant
reports an error instead of silently keeping the old value.

---

## Unit 1: battery (BMS)

The BMS has three areas:

| Base | Content |
| --- | --- |
| 0x1000 | Equipment information |
| 0x1100 | System summary (all battery stacks together) |
| 0x1400 | Battery stack 1 (cell voltages from 0x1500, cell temperatures from 0x1800) |

### Equipment information (0x1000)

Read once per hour (0x1000-0x101E, 31 registers).

| Register | Format | Meaning |
| --- | --- | --- |
| 0x100A | U16 | BMS main version: high byte major, low byte minor (0x0003 = V0.3) |
| 0x100E-0x101D | ASCII × 32 | Product model |
| 0x101E | U16 | HMI version, same coding |

### Battery stack 1 (0x1400)

Read every 5 seconds (0x1403, 30 registers).

| Register | Format | Scale / unit | Meaning |
| --- | --- | --- | --- |
| 0x1403 | U16 | 0.1 V | Battery voltage |
| 0x1406 | S16 | 0.1 °C | Battery temperature |
| 0x1407 | U16 | % | SOC |
| 0x1408 | U16 | | Cycle count |
| 0x1410 | U16 | mV | Highest cell voltage |
| 0x1411 | U16 | mV | Lowest cell voltage |
| 0x1420 | U16 | % | SOH |

### System summary (0x1100)

Read every 5 minutes (0x1100-0x114E, 79 registers), only when *Extended battery
statistics* is on in the integration options.

| Offset | Format | Scale / unit | Meaning |
| --- | --- | --- | --- |
| 0x00 | U16 | | Basic status (bit field) |
| 0x01 | U16 | | Protection status (bit field) |
| 0x02 | U16 | | Alarm status 1 (bit field) |
| 0x09 | U16 | 0.1 V | Maximum charge voltage |
| 0x0A | U32 | 0.01 A | Maximum charge current |
| 0x0C | U16 | 0.1 V | Minimum discharge voltage |
| 0x0D | S32 | 0.01 A | Maximum discharge current |
| 0x10 / 0x11 | U16 | mV | Highest / lowest cell voltage |
| 0x12 / 0x13 | U16 | | Cell number of highest / lowest cell voltage |
| 0x14 / 0x15 | S16 | 0.1 °C | Highest / lowest cell temperature |
| 0x16 / 0x17 | U16 | | Cell number of highest / lowest cell temperature |
| 0x18 / 0x19 | U16 | 0.01 V | Highest / lowest module voltage |
| 0x1A / 0x1B | U16 | | Module number of highest / lowest module voltage |
| 0x1C / 0x1D | S16 | 0.1 °C | Highest / lowest module temperature |
| 0x21 | U32 | Wh | Remaining energy |
| 0x27 | U32 | Wh | Charged today (resets at the BMS's own midnight) |
| 0x29 | U32 | Wh | Discharged today |
| 0x2F | U16 | | Force charge request (1 = requested) |
| 0x30 | U16 | | Full/balance charge request (1 = requested) |
| 0x32 | U32 | | BMS error code 1 |
| 0x34 | U32 | | BMS error code 2 |
| 0x36 | U16 | | Number of modules |
| 0x37 | U16 | | Number of cells |
| 0x38 / 0x39 | U16 | | Charging / discharging blocked by the BMS (1 = blocked) |
| 0x48 | U16 | kΩ | Insulation resistance |
| 0x49 | U16 | | Insulation error level |
| 0x4A / 0x4B | S16 | 0.1 °C | Highest / lowest terminal temperature |
| 0x4C / 0x4D | U16 | | Channel of highest / lowest terminal temperature |
| 0x4E | U16 | | Alarm status 2 (bit field) |

Notes:

- From 0x48 this block is laid out differently from the stack block at 0x1400:
  alarm status 2 is at 0x114E here, while in the stack block it is at 0x1449.
- BMS firmware that does not fill 0x48-0x4D returns zeros there. Those sensors are
  then shown as unknown instead of 0 kΩ / 0.0 °C.
- Protection status, alarm status 1 and 2 and the two error codes together form one
  *BMS Alarm* problem sensor (on when any of them is non-zero), with the raw values
  as attributes; individual bits are not decoded.

### Cell voltages and temperatures

Only when *Extended battery statistics* is on. Read every 15 minutes, each block in
one request whose length is the cell count from 0x1137 (96 on a three-module FH3X).

| Register | Format | Scale / unit | Meaning |
| --- | --- | --- | --- |
| 0x1500 + n | U16 | mV | Voltage of cell n |
| 0x1800 + n | S16 | 0.1 °C | Temperature of cell n |

Cell numbering *(live)*: module 0 is the bottom module of the stack; within a
module, cell 0 is the leftmost seen from the front. Each module has 32 cells.
Larger batteries would need the reads split into parts; that is not supported yet.

---

## Practical notes

Learned on live systems; this is why `coordinator.py` reads the way it does.

- The weak link is the dongle, not the H3X itself. Since dongle firmware V1.3.1
  (September 2026) it now and then returns late, mixed-up or implausible replies.
  Every value is therefore checked, and suspicious samples are read again instead of
  being trusted.
- Fewer, larger reads work better than many small ones. 30100-30191 in one request
  gives fewer chances for a mixed-up reply and keeps all values from the same moment.
  If that read fails three times in a row, the integration falls back to three
  smaller reads (30100/48, 30156/30, 30183/9).
- Too many extra requests at once can make the dongle hang until the H3X is
  restarted. This happened on a first attempt at reading the cell blocks. Since then:
  at most one optional block per poll, cell blocks only in a poll without any other
  optional block, an unanswered optional block is paused for an hour, and three
  refusals in a row switch it off until the next restart.
- The H3X can get a new IP address after a firmware update. Use *Reconfigure* to
  enter the new address; entities keep their IDs.
