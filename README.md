Fork from https://github.com/wietse108/HA-pylontech-force-H3X

# Pylontech Force H3X Integration for Home Assistant via Modbus TCP

![HACS Custom](https://img.shields.io/badge/HACS-Custom_Repository-orange.svg?style=for-the-badge)
![Version](https://img.shields.io/github/v/release/hbvdh/HA-pylontech-force-H3X?style=for-the-badge&label=version)
![Maintenance](https://img.shields.io/badge/Maintained%3F-yes-green.svg?style=for-the-badge)

A fully native, UI-configurable Home Assistant custom integration for the **Pylontech Force H3X** inverter and BMS system. 

Say goodbye to complex, manual Modbus YAML configurations! This integration connects directly to your inverter over Modbus TCP, utilizing smart chunk-reading to poll dozens of registers efficiently without overloading the inverter's WiFi dongle.

---

## ✨ Features

- **UI Config Flow:** Set up your inverter in seconds via the Home Assistant integrations page. No YAML required!
- **Smart Chunk Polling:** Reads all required data from both the Inverter (Slave 2) and the BMS (Slave 1) in highly optimized, delay-spaced blocks to prevent Modbus timeouts.
- **Interactive Controls:** Not just reading data! Set your EMS mode, charge/discharge power limits, and SOC limits directly from the Home Assistant UI.
- **Auto-Discovery:** Automatically creates and groups all sensors and controls under a single Device in your dashboard.
- **Energy Dashboard Ready:** All energy/power sensors are configured with the correct `state_class` and `device_class` to work out-of-the-box with the Home Assistant Energy Dashboard.
- **Resilient Connection:** Built-in auto-reconnect and error handling.
- **Measurement validation:** Rejects malformed reads and implausible telemetry before sending values to Home Assistant. Derived power uses validated inputs; energy counters are checked for impossible jumps. See [validation behaviour and limits](docs/telemetry-validation.md).

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

**Optional reads.** Besides the normal 5 s polling, some blocks are read less often, each with its own interval at the top of `coordinator.py` (0 = off): device information (`READ_IDENTITY`, `READ_DEVICE_INFO_EXT`, `READ_BMS_DEVICE_INFO`) every hour to pick up firmware updates; fault bits (`READ_FAULT_BITS`), import limit (`READ_SITE_LIMIT`) and the extended monitor block at 30600 (`READ_MONITOR_EXT`: EPS current, AC-coupled PV) every 5 minutes. The battery blocks (BMS summary every 5 minutes, cell voltages and cell temperatures every 15 minutes) are only read when **Extended battery statistics** is on under **Configure**. The master switch `ENABLE_OPTIONAL_READS` turns all optional reads off. The fault and warning codes themselves come every 5 s with the normal 30100 block; the fault bits only add which fault it is. Each block gets a single attempt per interval. No answer pauses the block for an hour, after which one new attempt is made; three refusals in a row (register not supported by the firmware) switch it off until the next restart. While a block is paused or off, its sensors show unknown instead of a stale value. Optional failures never trigger the backoff and are skipped in any poll where a normal read failed. A failed optional read is always logged as a warning; successful reads (address, duration) are logged at debug level, so turn on **Enable debug logging** for the integration to see every read. The 30600 block is only answered by newer firmware; on older firmware it is switched off after three refusals. Firmware Update State is shown as text (Idle / Download finished / Update finished) with the raw value as attribute. Fault and warning bit names come from the official [Pylontech Research integration](https://github.com/Pylontech-Research/HA-pylontech-force-H3X) (MIT).

## 📈 Dashboard cards

Ready-made [button-card](https://github.com/custom-cards/button-card) configurations:

- **Power flow card:** animated diagram of PV, grid, battery, H3X and house, with arrows showing the direction of each flow. See [docs/dashboard/power-flow-card](docs/dashboard/power-flow-card/README.md).
- **Battery and cell card:** BMS summary plus every cell per module (voltage, deviation, temperature). Works with any entity prefix and any module count. Needs Extended battery statistics on. See [docs/dashboard/battery-cells-card](docs/dashboard/battery-cells-card/README.md).

---

## ⚠️ Important Note: Charge & Discharge Control

If you want to use the **Charge/Discharge Power Ref** setting to manually force the battery to charge or discharge, **you MUST set the `EMS Mode` to `User mode`**. 
If the system is set to any other mode (like `Self-Consumption`), the inverter's internal logic will override your command and revert the setting back to its default value!

After the writing new settings, not all of the entities show the right value. I'm still working on that

---
## 📅 EMS Charge/Discharge Schedules (with v1.1.4)

If you have configured Charge/Discharge period schedules (up to 4 of them) in the Pylontech app, you can now enable or disable those schedules directly from Home Assistant.

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
Active schedules timings must not overlap. Overlapping periods may fail to activate or behave unpredictably.
Recommended approach
Use clean boundaries between periods. For example:
* Period 1: 07:00 → 10:59
* Period 2: 11:00 → 16:59

App remains the source of truth, and all schedule details must be configured in the app first.

---

## 🚀 Installation

### Option 1: Via HACS (Recommended)
This integration is easily installable via the [Home Assistant Community Store (HACS)](https://hacs.xyz/).

1. Open Home Assistant and navigate to **HACS** > **Integrations**.
2. Click the three dots (`...`) in the top right corner and select **Custom repositories**.
3. Paste the URL of this GitHub repository.
4. Select **Integration** as the category and click **Add**.
5. Close the popup, search for **Pylontech Force H3X** in HACS, and click **Download**.
6. **Restart Home Assistant.**

### Option 2: Manual Installation
1. Download the latest release from this repository.
2. Copy the `custom_components/pylon_fh3x` folder into your Home Assistant `custom_components` directory.
3. **Restart Home Assistant.**

---

## ⚙️ Configuration

1. In Home Assistant, go to **Settings** > **Devices & Services**.
2. Click the **+ Add Integration** button in the bottom right.
3. Search for **Pylontech Force H3X**.
4. Enter the **IP Address** of your inverter and the **Port** (default is `502`).
5. Click **Submit**. Your sensors will appear immediately!

**Afterwards:**
- **Configure** (on the integration card): turn **Extended battery statistics** on or off (BMS summary, all cell voltages and cell temperatures). Off by default. When turned off, these sensors are no longer provided and existing ones become unavailable; you can remove them from the entity list.
- **⋮ → Reconfigure**: change the IP address or port, e.g. when the inverter got a new IP. Entities and history are kept.

> **⚠️ Important Notice regarding Modbus TCP:** > Modbus TCP usually allows only **one active connection at a time**. If you have an old YAML Modbus configuration in your `configuration.yaml` or another script reading from the inverter, you MUST disable/remove it and restart Home Assistant before adding this integration. Otherwise, the connection will be refused.

---

## 🔮 Roadmap
- [x] Read Inverter Holding Registers (Power, Voltage, Energy)
- [x] Read BMS Registers (Cell voltages, Temperatures, SOH)
- [x] Add `number` and `select` platforms to enable writing to the inverter (EMS mode, SOC limits, Charge/Discharge power).
- [ ] Add support for scheduling via Time of Use registers.

## 🤝 Contributing & Help Welcome!
This is an open-source community project, and **help is always welcome!** Whether you want to add more Modbus registers, improve the code, fix a bug, or translate the integration into more languages: feel free to fork the repository and open a Pull Request.

## 🐛 Troubleshooting & Support
If the integration fails to connect, check your Home Assistant logs (`home-assistant.log`). 
Common issues:
- `Connection refused`: Another device or script is already connected to the inverter's Modbus port.
- `Timeout`: Poor WiFi signal to the inverter dongle.
- Inverter got a new IP address (e.g. after a router or firewall change, or the dongle switched between LAN and Wi-Fi): use **⋮ → Reconfigure** on the integration card. Entities and history are kept.
- `Another H3X answers` under **Settings → System → Repairs**: the integration remembers the serial number of your H3X and found a different one at the configured address. Nothing is read or written until you decide. If your H3X was replaced (e.g. under warranty), open the repair and select **Submit**: the integration continues with the new unit and all entities, history and automations are kept. If it was not replaced, another device took over the address: use **⋮ → Reconfigure** to enter the address of your own H3X.

Feel free to open an issue on this repository if you encounter any bugs!

---
*Disclaimer: This is a community-built integration and is not officially affiliated with or endorsed by Pylontech.*

## Language / Taal

The integration's dialogs (setup, Configure and Reconfigure) support **English** and **Dutch** using
Home Assistant's built-in translations. Its dialogs follow the **language of your
Home Assistant user profile** (and Home Assistant's fallback), without a separate
language selector.

Sensor and control names, EMS mode choices, and inverter/battery state values are
intentionally left unchanged in English. No entity IDs, unique IDs, Modbus
registers or runtime logic are modified.

**Nederlands:** De vensters (instellen, Configure en Reconfigure) volgen de taal van je Home
Assistant-gebruikersprofiel. Sensornamen, bediening en statuswaarden blijven
Engels, zodat bestaande automatiseringen blijven werken.
