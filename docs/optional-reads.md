# Optional reads

Besides the normal polling every 5 seconds, some register blocks are read less often. This page describes how those optional reads behave.

## Intervals

Each block has its own interval at the top of `coordinator.py` (0 = off):

| Block | Setting | Interval |
|-------|---------|----------|
| Device information (model, serial number, firmware) | `READ_IDENTITY`, `READ_DEVICE_INFO_EXT`, `READ_BMS_DEVICE_INFO` | 1 hour |
| Fault bits | `READ_FAULT_BITS` | 5 minutes |
| Grid import power limit (40937) | `READ_SITE_LIMIT` | 5 minutes |
| Extended monitor block (30600: EPS current, AC-coupled PV) | `READ_MONITOR_EXT` | 5 minutes |
| BMS summary | only with **Extended battery statistics** | 5 minutes |
| Cell voltages and cell temperatures | only with **Extended battery statistics** | 15 minutes |

The master switch `ENABLE_OPTIONAL_READS` turns all optional reads off.

The fault and warning codes themselves come every 5 s with the normal 30100 block; the fault bits only add which fault it is.

## When a block does not answer

- Each block gets a single attempt per interval.
- No answer pauses the block for an hour, after which one new attempt is made.
- Three refusals in a row (register not supported by the firmware) switch the block off until the next restart. The 30600 block is only answered by newer firmware; on older firmware it is switched off this way.
- While a block is paused or off, its sensors show unknown instead of a stale value.
- Optional failures never slow down the normal polling, and optional reads are skipped in any poll where a normal read failed.

## Logging

A failed optional read is always logged as a warning. Successful reads (address, duration) are logged at debug level: turn on **Enable debug logging** for the integration to see every read.

## Other notes

Firmware Update State is shown as text (Idle / Download finished / Update finished) with the raw value as attribute.

Fault and warning bit names come from the official [Pylontech Research integration](https://github.com/Pylontech-Research/HA-pylontech-force-H3X) (MIT).
