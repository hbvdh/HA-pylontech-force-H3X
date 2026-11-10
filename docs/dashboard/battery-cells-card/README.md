# Battery and cell card (button-card)

An overview of the Force H3X battery built as a single
[button-card](https://github.com/custom-cards/button-card): BMS summary at the
top (SOC, remaining energy, voltage, current, SOH, cycles, temperatures, cell
delta, charge/discharge limits, BMS alarm) with the charge/discharge state
beside it, and below that one column per battery module showing every cell.

Each cell is drawn as a small vessel filled to the current SOC. Its shade shows
how far the cell is from the average voltage; a cell that deviates 15 mV or
more turns red and shows the deviation. Cells are grouped per temperature
sensor (8 cells), with that group's temperature in each cell.

Labels on the card are in Dutch.

## Requirements

- This integration (`pylon_fh3x`), version 1.3.1 or later.
- **Extended battery statistics** switched on: *Settings → Devices & services →
  Pylontech Force H3X → Configure*. Without it there is no cell data and the
  card says so.
- **button-card**, installed via HACS (Frontend / Dashboard).

## Installation

1. Install button-card via HACS and reload the browser.
2. Open your dashboard, **Edit dashboard → Add card → Manual**.
3. Paste the full contents of `battery-cells-card.yaml` and save.

No editing needed:

- **Entity IDs** are found by the end of their ID (`_bms_soc`,
  `_bms_cell_voltage_average`, …), so any prefix works
  (`sensor.pylontech_force_h3x_…`, `sensor.kelder_pylontech_force_h3x_…`, …).
- **Module count** comes from the BMS; if that sensor is missing it is derived
  from the cell count (32 cells per module). Tested with 2 and 3 modules.
- **Units**: power and energy are shown in kW/kWh whether the sensor uses W/Wh
  or kW/kWh; temperatures use the sensor's own unit.
- The top row is always 2/3 BMS info and 1/3 charge/discharge.

To update later: open the card, **Edit**, switch to the code editor, select
all, paste the new version and save.

## Settings

At the top of the code (`---- settings ----`) and in `variables`:

| Setting | Default | Meaning |
|---------|---------|---------|
| `max_width` | `0` | Maximum card width in px, `0` = full width. |
| `GREEN` | `#4caf50` | Fill colour of the cells. |
| `STEP_MV` | `2` | mV per shade step from the average. |
| `RED_MV` | `15` | Deviation (mV) from which a cell turns red. |
| `GROUP` | `8` | Cells per temperature sensor. |
| `CELLS_PER_MODULE` | `32` | Used only when the module count sensor is missing. |

## Note on updates

Because entity IDs differ per system, the card uses `triggers_update: all` and
re-renders on any state change in Home Assistant. On a slow phone you can
replace `all` with a list of your own battery sensors.
