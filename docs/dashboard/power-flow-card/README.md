# Power flow card (button-card)

An animated power flow diagram for the Force H3X, built as a single
[button-card](https://github.com/custom-cards/button-card). It shows roof PV,
the grid, an optional AC-coupled PV inverter, the H3X, the battery and the
house. Moving dashes and an arrowhead show the direction of each flow; the
speed follows the power. Below a threshold a line turns grey and stops.

Labels on the card are in Dutch.

## Requirements

- This integration (`pylon_fh3x`).
- **button-card**, installed via HACS (Frontend / Dashboard).

## Files

| File | Use |
|------|-----|
| `power-flow-h3x-only.yaml` | H3X only: grid, PV, battery and load all come from the integration. Start here. |
| `power-flow-dtsu-ct2.yaml` | Example with external meter data: DTSU666-6CT read over MQTT, CT2 for an AC-coupled PV inverter (shed PV), own template sensors. |
| `card.js` | Shared card code (the part between `[[[` and `]]]`). |
| `build.py` | Builds both YAML files from `card.js` and the configs inside the script. |

## Installation

1. Install button-card via HACS and reload the browser.
2. Open your dashboard, **Edit dashboard → Add card → Manual**.
3. Paste the full contents of one of the YAML files and save.
4. Check the `CFG` block at the top of the code (see below) against your own
   entity IDs.

To update later: open the card, **Edit**, switch to the code editor, select
all, paste the new version and save.

## Configuration (`CFG` block)

| Key | Meaning |
|-----|---------|
| `netLabel` | Title of the grid block. |
| `net` | Grid power in W, import positive. Several sensors are added up. |
| `netPhases` | Shown per phase under the grid block (empty list = hidden). |
| `schuur` | AC-coupled PV power sensors. Empty list = block hidden, card gets shorter. |
| `pv` | Total PV power on the H3X (DC). |
| `pvStrings` | Strings shown under the PV block, as `[label, sensor]`. |
| `ac` | H3X AC power (delivering positive). |
| `ems` | EMS mode select. |
| `heat` | Heatsink temperature (°F is converted to °C). |
| `batP` | Battery power, discharging positive. |
| `soc`, `batV`, `batA` | Battery SoC, voltage and current. |
| `batKwh` | Remaining energy sensor, or `null` to calculate `SoC × capacity`. |
| `capacity` | Battery capacity in kWh (only used when `batKwh` is `null`). |
| `huis` | Total house load. |
| `idle` | Threshold in W below which a line is shown idle. |
| `netIdle` | Threshold in W for the grid (import / export / balanced). |

Power sensors in kW or kVA are converted to W automatically.

### Sign conventions

These match the integration: grid import positive, battery discharging
positive, H3X AC delivering positive, and `load = AC + grid`. The AC-coupled
PV value is shown as an absolute value, because the sign of a CT depends on
how it is fitted (Pylontech CTs point towards the device they measure).

## Changing the card

Edit `card.js` (shared code) or a config in `build.py`, run
`python build.py`, and commit the regenerated YAML files together with the
change, so the two versions never drift apart.
