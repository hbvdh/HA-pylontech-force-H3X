# Telemetry validation

The coordinator validates Modbus replies and decoded measurements before returning
data to Home Assistant. A Modbus success response alone does not establish that its
register values are plausible.

## Behaviour on a bad read

- Reject a response with missing, extra or malformed register words before decoding
  it. A word must be an integer between 0 and 65535.
- Remove individual measurements outside their sanity range. Other valid values
  from the same response remain available.
- Calculate PV and load power only after checking their source measurements. A bad
  source also removes the dependent measurement for that poll.
- Return no numeric value for a rejected measurement. The existing sensor returns
  `None`, so Home Assistant shows it as unknown. Do not clamp it to a limit, replace
  it with zero, or repeat the previous measurement as if it were current.
- Accept the next valid measurement immediately. Normal sudden power changes,
  charging, grid export, zero readings and negative temperatures remain supported.
- Log rejected source values with the sensor key, value and reason. Repeated
  rejections of the same key are logged once until a poll clears the rejection.

If no usable data remains, the coordinator reports an update failure through Home
Assistant's existing availability mechanism.

## Sanity ranges

These intentionally broad limits detect corrupt telemetry; they are not device
ratings or protection settings. Limits live in
`custom_components/pylon_fh3x/validation.py`.

| Measurement | Accepted range |
| --- | --- |
| Inverter AC, battery and EPS power | -30,000 to 30,000 W |
| PV Total Power (`pv_total_power`) | 0 to 10,000 W (fork-specific threshold) |
| Calculated PV string power and PV energy-counter rate | Existing 40,000 W sanity bound (unchanged) |
| Grid total power | -1,000,000 to 1,000,000 W |
| Grid power per phase | -350,000 to 350,000 W |
| Battery and BMS stack voltage | 0 to 1,000 V |
| Battery current | -100 to 100 A |
| PV voltage / current | 0 to 1,100 V / 0 to 50 A |
| AC phase voltage / current | 0 to 350 V / 0 to 100 A |
| Grid CT current per phase | -1,000 to 1,000 A |
| AC frequency | 0 to 70 Hz |
| Inverter and heatsink temperature | -50 to 150 degrees C |
| BMS temperature | -50 to 100 degrees C |
| Cell voltage | 0 to 5 V, with minimum no greater than maximum |
| SOC, SOH and SOC settings | 0 to 100 percent |
| Lifetime energy counters | 0 to 10,000,000 kWh, plus growth checks |
| BMS cycle counter | 0 to 65,534, plus growth checks |

The [included Modbus protocol](ModBus-Protocol-Pylon-FH3X-V1.2_20250811.md)
defines register types, units, scaling, signs and percentage fields. The
[Pylontech Force H3X datasheet](https://shop.kpenergy.se/globalassets/product-sheets/inverters/pylontech/pylontech-force-h3x-hybrid-ess-datasheet-260121-en.pdf)
lists models up to 15 kW, 24 kW PV input, 1,000 V maximum PV voltage, 50 A
continuous battery current and 22.5 kVA peak backup output. The sanity limits allow
margin above these ratings. The separate PV Total Power threshold rejects the >15 kW spikes seen on a 2.5 kW installation. It is a conservative fork-specific filter, not a universal Force H3X rating. Sites with more than 10 kW of PV output must adjust this threshold before using the fork.
The separate CT limits are deliberately much wider:
the grid connection can supply loads beyond the inverter's own rating.

## PV MPPT transition checks

The temporal guard now also monitors PV1/PV2/PV3 voltage and current (registers
30119–30124). A fast change greater than 45 V plus 2 V per elapsed second,
or 4 A plus 0.1 A per elapsed second, within 60 seconds of the previous
accepted sample triggers a second read of block 30100. These are retry
thresholds, **not** permanent PV production or current limits.

When the repeated measurement is plausible, it is used. Two matching unusual
measurements are accepted as a genuine transition. If the retry fails or
does not confirm the transition, the suspect PV source field is omitted,
which also omits the derived PV string power for that poll. Unrelated readings
are still published. This is especially intended to detect the observed
PV1 2.3 → 9.1 A / PV2 188 → 0 V simultaneous glitch. It is possible for a
repeated corrupt reading to pass confirmation; these filters are not a
replacement for a firmware fix.

## Cumulative counters

Energy and cycle counters need two consistent samples at startup. Until then they
are unknown. After startup, compare each counter with its last accepted value and
the actual elapsed time, including time spent missing readings.

PV and battery energy growth follows the corresponding power sanity limit and
allows 0.1 kWh or two float32 precision steps, whichever is larger, for rounding
and batched updates. The **grid import/export lifetime counters** use a separate
**25 kW** equivalent growth limit and **0.02 kWh** tolerance (or two float32
precision steps, whichever is larger). This rejects the observed 206.06 to
206.87 kWh jump in 100 seconds, which would require 29.16 kW average import.
The cycle counter allows 100 cycles per day plus a one-cycle margin.
These are corruption checks, not estimates of expected consumption or cycle life.
The 25 kW counter bound allows margin for a 3x25 A connection but may need
adjustment for sites capable of higher grid power.

An impossible increase is rejected even if it repeats, and does not replace the
last accepted baseline. The tighter grid counter bound prevents the observed
single erroneous high reading from blocking subsequent ordinary readings.
It does not repair bad values already stored by Home Assistant. A decrease of 10% or less is rejected, even if it repeats,
until the counter reaches its last accepted value again. This includes small
backward steps such as 2025.52001953125 to 2025.50854492188 kWh.
A decrease of more than 10% needs a second consistent sample before it is accepted
as a counter reset. This matches the reset boundary in
[Home Assistant Recorder](https://github.com/home-assistant/core/blob/2026.9.0/homeassistant/components/sensor/recorder.py#L442).
A single low reading therefore does not create a false reset in Home Assistant.
Missing or out-of-range counter samples clear a
pending confirmation. Baselines are held in memory and confirmed again after an
integration restart.

Read errors that happen to produce plausible values cannot all be identified by
these checks. Repeated, consistent but incorrect startup values or resets can
still pass confirmation. There is no smoothing of otherwise plausible power
measurements. Limits are shared across the supported Force H3X variants rather
than inferred from an individual installation's configuration.

## Installing and verifying the change

Copy the updated `custom_components/pylon_fh3x` folder, including `validation.py`,
into Home Assistant's `custom_components` directory and restart Home Assistant.
Allow two successful polls for energy and cycle counters to appear. Existing bad
history/statistics are not changed by this update.

Run the regression suite from the repository root:

```sh
python -m unittest discover -s tests -v
```

Tests feed simulated register replies through the actual coordinator, including
700,000,000 W spikes, signed integer extremes, invalid floats, malformed replies,
derived sensors, counter spikes/resets and recovery. Home Assistant and transport
imports are stubbed; no physical battery or running Home Assistant is required.
Sensor platform import tests also check the separation between `UnitOfPower` and
`UnitOfApparentPower`. VA sensors must use `UnitOfApparentPower.VOLT_AMPERE`;
using `UnitOfPower.VOLT_AMPERE` prevents the sensor platform from loading.
