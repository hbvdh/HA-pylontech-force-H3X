# Pylontech Force H3X Integration for Home Assistant via Modbus TCP

![HACS Custom](https://img.shields.io/badge/HACS-Custom_Repository-orange.svg?style=for-the-badge)
![Version](https://img.shields.io/github/v/release/hbvdh/HA-pylontech-force-H3X?style=for-the-badge&label=version)
![Maintenance](https://img.shields.io/badge/Maintained%3F-yes-green.svg?style=for-the-badge)

A native, UI-configurable Home Assistant custom integration for the **Pylontech Force H3X** hybrid inverter and its battery (BMS). It connects directly to the inverter over Modbus TCP and reads dozens of registers in a few well-spaced blocks, so the inverter's Wi-Fi/LAN dongle is not overloaded.

> Originally created by [wietse108](https://github.com/wietse108/HA-pylontech-force-H3X). This repository has since been developed further independently and is maintained here.

---

## ✨ Features

- **UI setup:** add the inverter from the Home Assistant integrations page. No YAML.
- **Efficient polling:** reads the inverter (slave 2) and the BMS (slave 1) every 5 seconds in a few optimised, spaced blocks to prevent Modbus timeouts. Less urgent data is read every few minutes or hourly; see [optional reads](docs/optional-reads.md).
- **Controls:** set the EMS mode, charge/discharge power and SOC limits, and switch the charge/discharge schedules on or off.
- **Verified writes:** every write is read back from the inverter. If the H3X does not confirm it, Home Assistant shows an error instead of a value that was never applied. A write that has started always runs to the end, even when the automation that sent it is cancelled.
- **One device:** all sensors and controls are grouped under a single device, with model, serial number and firmware versions on the device page.
- **Energy Dashboard ready:** energy and power sensors have the correct `state_class` and `device_class`.
- **Resilient connection:** the integration owns the single Modbus connection, reconnects by itself, and polls less often while the dongle does not answer (every 60 s after three failed polls) until it answers again.
- **Checks which H3X answers:** the integration remembers the serial number of your H3X. If another device answers at the configured address (for example after a router change), nothing is written and a Repair issue explains what to do. See [Troubleshooting](#-troubleshooting).
- **Measurement validation:** malformed reads and implausible values are rejected before they reach Home Assistant; energy counters are checked for impossible jumps. See [validation behaviour and limits](docs/telemetry-validation.md).
- **Address changes without losing history:** a new IP address can be entered via **Reconfigure**; entities, history and automations are kept.

## 📊 Included Sensors & Controls

This integration pulls comprehensive data from both the Inverter and the internal Battery Management System (BMS), and allows you to control the system.

**🎛️ Controls & Settings:**
* **EMS Mode Selector:** Switch between modes like `Self-Consumption`, `Back up mode`, and `User mode`. `Dynamic mode` (the Pylontech app's Intelligent Dynamic tariff mode, not in the Modbus document) (raw value 262 = 0x0106) is shown but can only be switched on in the app. The raw register value is available as the `raw_value` attribute; an unknown value is logged once as a warning.
* **Charge/Discharge Power Ref:** Set the exact percentage (-100% to 100%) for battery charging or discharging.
* **SOC Limits:** Sliders for setting the `Charge Limit SOC` and `Discharge Limit SOC (EPS)`.
* **Period Schedules:** Turn your pre-programmed Charging Schedules ON/OFF

**📡 Read-Only Sensors:**
* **🌞 Solar (PV):** Voltage, Current, and Power for PV1, PV2, and PV3, plus Total PV Energy.
* **⚡ Grid & AC:** Grid Import/Export, AC Total Power, Load Power, Phase Voltages (R/S/T), AC Frequency, and Total Grid Energy.
* **📶 Per-Phase Grid & Load:** Grid Current and Grid Power per phase (R/S/T), plus derived Load Power per phase (R/S/T) — useful for spotting phase imbalance.
* **🔋 Battery (Inverter perspective):** Charge/Discharge Power, Voltage, Current, SOC, and Total Charge/Discharge Energy.
* **🧠 BMS (Internal):** Cell Voltage Max/Min, BMS Temperature, BMS SOC, State of Health (SOH), and total battery cycles.
* **🔬 Extended battery statistics (optional, off by default):** BMS summary (which cell is highest/lowest, cell temperature min/max, module and cell count, charge/discharge limits, BMS alarm, energy charged/discharged today, terminal temperature, plus insulation resistance as diagnostic), plus all individual cell voltages and cell temperatures. Switch on under **Configure** on the integration card. The cell values are exposed as one average sensor each, with all cells in the `cells` attribute (not recorded).
* **🛠️ Status:** Inverter Status, Battery Status, Grid Mode (Grid-tied / Backup, register 30160), and internal temperatures. Isolation Resistance of the PV/DC side (register 30142, diagnostic).
* **🔋 EPS (backup) output:** EPS Voltage, Frequency, Power and Output Energy (registers 30170–30181), plus EPS Enabled (diagnostic, read-only, register 40904), from blocks that are already read every poll. EPS Current per phase comes from the extended monitor block (30600, every 5 minutes).
* **🔌 AC-coupled PV / third-party meter:** Third Party Meter Power and Energy (registers 30110/30112, CT2 of a DTSU666-6CT or similar). Plus AC Coupled PV Power (total, and per phase R/S/T) from the extended monitor block (30600, every 5 minutes, newer firmware only).
* **🚨 Faults & warnings (diagnostic):** Inverter Problem (binary), Inverter Faults and Inverter Warnings as readable text with the raw bits as attributes, Fault Code, Warning Code, Firmware Update State.
* **🏷️ Device information:** model, serial number, PCS firmware and BMS/HMI version on the device page, plus diagnostic sensors for the PCS, AFCI, BMS and HMI versions and the PCS rated output power.
* **📏 Grid Import Power Limit (diagnostic):** the import limit configured in the inverter (register 40937, read-only).

**Optional reads.** Some blocks (device information, fault bits, import limit, extended monitor block, battery statistics) are read every few minutes or hourly instead of every 5 seconds. Intervals, what happens when the firmware does not support a block, and logging are described in [docs/optional-reads.md](docs/optional-reads.md).

## 📈 Dashboard cards

Ready-made [button-card](https://github.com/custom-cards/button-card) configurations:

- **Power flow card:** animated diagram of PV, grid, battery, H3X and house, with arrows showing the direction of each flow. See [docs/dashboard/power-flow-card](docs/dashboard/power-flow-card/README.md).
- **Battery and cell card:** BMS summary plus every cell per module (voltage, deviation, temperature). Works with any entity prefix and any module count. Needs Extended battery statistics on. See [docs/dashboard/battery-cells-card](docs/dashboard/battery-cells-card/README.md).

---

## ⚠️ Important: Charge & Discharge Control

If you want to use **Charge/Discharge Power Ref** to force the battery to charge or discharge, **set `EMS Mode` to `User mode` first**. In any other mode (like `Self-Consumption`) the inverter's own logic overrides the command and resets the value.

**Automations that write to the H3X:** use `mode: queued` instead of `mode: restart`. With `restart`, a new trigger cancels the running automation while it may be writing. The integration finishes the write anyway, but with `queued` each run simply waits for the previous one.

---

## 📅 EMS Charge/Discharge Schedules

If you have configured Charge/Discharge period schedules (up to 4 of them) in the Pylontech app, you can enable or disable those schedules directly from Home Assistant.

⚠️ This integration can currently only toggle schedules on/off — it cannot view or edit the schedule contents (times, days, power settings).

### 📱 Where to configure schedules

Schedules must be configured in the Pylontech app:

* Device → Setting → Energy Management → Charge/Discharge Time Control

Configure each period as needed (time of day, days of week, etc), click Save, then use Home Assistant to activate or deactivate them.

### ⚙️ How it works
Each schedule appears in Home Assistant as below, and can be turned ON or OFF as required
* switch.pylontech_force_h3x_period_1
* switch.pylontech_force_h3x_period_2
* switch.pylontech_force_h3x_period_3
* switch.pylontech_force_h3x_period_4

### ⚠️ Important Note - No overlap allowed
Active schedule timings must not overlap. Overlapping periods may fail to activate or behave unpredictably.
Recommended approach: use clean boundaries between periods. For example:
* Period 1: 07:00 → 10:59
* Period 2: 11:00 → 16:59

App remains the source of truth, and all schedule details must be configured in the app first.

---

## 🚀 Installation

### Option 1: Via HACS (recommended)

1. Open **HACS** in Home Assistant.
2. Click the three dots (⋮) in the top right corner and select **Custom repositories**.
3. Paste the URL of this repository, select **Integration** as the type and click **Add**.
4. Search for **Pylontech Force H3X** in HACS and click **Download**.
5. **Restart Home Assistant.**

### Option 2: Manual installation

1. Download the latest release from this repository.
2. Copy the `custom_components/pylon_fh3x` folder into your Home Assistant `custom_components` directory.
3. **Restart Home Assistant.**

---

## ⚙️ Configuration

1. In Home Assistant, go to **Settings** > **Devices & services**.
2. Click **+ Add Integration**.
3. Search for **Pylontech Force H3X**.
4. Enter the **IP address** of the inverter and the **port** (default `502`).
5. Click **Submit**. The sensors appear right away.

**Afterwards:**
- **Configure** (on the integration card): turn **Extended battery statistics** on or off (BMS summary, all cell voltages and cell temperatures). Off by default. When turned off, these sensors are no longer provided and existing ones become unavailable; you can remove them from the entity list.
- **⋮ → Reconfigure**: change the IP address or port, e.g. when the inverter got a new IP. The connection is tested first; entities and history are kept.

> **⚠️ Modbus TCP allows only one connection.** The dongle usually accepts **one active Modbus connection at a time**. If an old YAML Modbus configuration or another script also reads from the inverter, remove it and restart Home Assistant before adding this integration. Otherwise the connection is refused.

---

## ✅ Tested systems

| Model | PCS firmware | BMS / HMI |
|-------|--------------|-----------|
| FH3X5KHY1PEU | V1.0.0.43 | V0.34 / V0.1 |
| FH3X10KHY3PEU | V1.0.0.26 | V0.34 / V0.1 |

Developed and tested on Home Assistant 2026.11. Other models and firmware versions are likely to work; some optional blocks (such as 30600) are only answered by newer firmware and are then switched off automatically.

The register map used is in [docs/modbus-protocol.md](docs/modbus-protocol.md).

---

## 🐛 Troubleshooting

Logs are under **Settings > System > Logs**. For more detail, open the integration and choose **Enable debug logging**.

Common issues:
- **`Connection refused` / `Unable to connect to H3X`:** another device or script is already connected to the inverter's Modbus port, or the dongle is offline. The integration keeps trying and resumes by itself.
- **Timeouts:** poor Wi-Fi signal to the inverter dongle. A LAN connection is more reliable.
- **Inverter got a new IP address** (e.g. after a router or firewall change, or the dongle switched between LAN and Wi-Fi): use **⋮ → Reconfigure** on the integration card. Entities and history are kept.
- **`Another H3X answers` under Settings → System → Repairs:** the integration found a different serial number at the configured address. Nothing is read or written until this is cleared up. If your H3X was replaced (e.g. under warranty), open the repair and select **Submit**: the integration continues with the new unit and all entities, history and automations are kept. If it was not replaced, another device took over the address: use **⋮ → Reconfigure** to enter the address of your own H3X.
- **A write shows an error (`The H3X did not confirm …`):** the inverter did not accept or confirm the value. Check that the EMS mode allows it (see *Important: Charge & Discharge Control* above) and try again.
- **`Referenced entities ... are missing or not currently available` right after a restart:** an automation tried to write before the integration had finished starting. Harmless; the next run works.

Feel free to open an issue on this repository if you run into a bug.

---

## 🤝 Contributing & credits

Pull requests are welcome: more registers, fixes, improvements or translations.

- Original integration by [wietse108](https://github.com/wietse108/HA-pylontech-force-H3X).
- Fault and warning bit names, and a useful cross-check of the register map, come from the official [Pylontech Research integration](https://github.com/Pylontech-Research/HA-pylontech-force-H3X) (MIT).

Licensed under the GNU GPL v3, see [LICENSE](LICENSE).

*Disclaimer: this is a community-built integration and is not officially affiliated with or endorsed by Pylontech.*

---

## 🌐 Language / Taal

The integration's dialogs (setup, Configure, Reconfigure) and its Repair issue support **English** and **Dutch** using Home Assistant's built-in translations. They follow the **language of your Home Assistant user profile**, without a separate language selector.

Sensor and control names, EMS mode choices, and inverter/battery state values are intentionally kept in English, so entity IDs and existing automations stay the same.

**Nederlands:** De vensters (instellen, Configure en Reconfigure) en de Repair-melding volgen de taal van je Home Assistant-gebruikersprofiel. Sensornamen, bediening en statuswaarden blijven Engels, zodat bestaande automatiseringen blijven werken.
