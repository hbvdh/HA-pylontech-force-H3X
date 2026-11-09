"""Sensor platform for Pylontech Force H3X."""
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfApparentPower,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .alarms import (
    FIRMWARE_UPDATE_STATE_MAP,
    PCS_FAULTS,
    PCS_WARNINGS,
    active_issues,
    combined_bits,
    issues_state,
)
from .device import device_info
from .const import DOMAIN, MANUFACTURER, MODEL

# All sensors are defined here. Each 'key' must match exactly the key
# coordinator.py uses in the data dictionary.
SENSOR_TYPES: tuple[SensorEntityDescription, ...] = (
    # --- Status sensors ---
    SensorEntityDescription(
        key="inverter_status",
        name="Inverter Status",
        icon="mdi:information-outline",
    ),
    SensorEntityDescription(
        key="battery_status",
        name="Battery Status",
        icon="mdi:battery-information",
    ),

    # --- PV (Zonnepanelen) ---
    SensorEntityDescription(
        key="pv1_voltage", name="PV1 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="pv1_current", name="PV1 Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="pv2_voltage", name="PV2 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="pv2_current", name="PV2 Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="pv3_voltage", name="PV3 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="pv3_current", name="PV3 Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="pv_total_power", name="PV Total Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="pv_total_energy", name="PV Total Energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),

    #pv power
    SensorEntityDescription(
        key="pv1_power", name="PV1 Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="pv2_power", name="PV2 Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="pv3_power", name="PV3 Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),

    # --- Power & Net ---
    SensorEntityDescription(
        key="ac_total_power", name="AC Total Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="grid_total_power", name="Grid Total Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="load_power", name="Load Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="total_grid_import", name="Total Grid Import",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SensorEntityDescription(
        key="total_grid_export", name="Total Grid Export",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SensorEntityDescription(
        key="grid_voltage_r", name="Grid Voltage R",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="grid_voltage_s", name="Grid Voltage S",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="grid_voltage_t", name="Grid Voltage T",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="grid_current_r", name="Grid Current R",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="grid_current_s", name="Grid Current S",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="grid_current_t", name="Grid Current T",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="grid_power_r", name="Grid Power R",
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="grid_power_s", name="Grid Power S",
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="grid_power_t", name="Grid Power T",
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="load_power_r", name="Load Power R",
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="load_power_s", name="Load Power S",
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="load_power_t", name="Load Power T",
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="ac_frequency", name="AC Frequency",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
    ),

    # --- Battery (inverter registers) ---
    SensorEntityDescription(
        key="battery_power", name="Battery Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="battery_voltage", name="Battery Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="battery_current", name="Battery Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="battery_soc", name="Battery SOC",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="total_battery_charge", name="Total Battery Charge",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SensorEntityDescription(
        key="total_battery_discharge", name="Total Battery Discharge",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),

    # --- BMS Registers ---
    SensorEntityDescription(
        key="bms_voltage", name="BMS Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="bms_temperature", name="BMS Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="bms_soc", name="BMS SOC",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="bms_soh", name="BMS SOH",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-heart-variant",
    ),
    SensorEntityDescription(
        key="bms_cycles", name="BMS Cycles",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-sync",
    ),
    SensorEntityDescription(
        key="bms_cell_voltage_max", name="BMS Cell Voltage Max",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="bms_cell_voltage_min", name="BMS Cell Voltage Min",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    
    # --- Inverter general ---
    SensorEntityDescription(
        key="inverter_temperature", name="Inverter Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),

    SensorEntityDescription(
        key="heatsink_temperature", name="Heatsink Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),

    # --- EPS (backup) output, registers 30170-30181 ---
    SensorEntityDescription(
        key="eps_voltage", name="EPS Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="eps_frequency", name="EPS Frequency",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="eps_power", name="EPS Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="eps_output_energy", name="EPS Output Energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
    ),
)


# --- Optional 30600 block: AC-coupled PV (only when READ_MONITOR_EXT is on) ---
# Disabled by default: the Third Party Meter values already cover AC-coupled PV.
ACC_SENSOR_TYPES: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="acc_power_total", name="AC Coupled PV Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    *(
        SensorEntityDescription(
            key=f"acc_power_{phase}", name=f"AC Coupled PV Power {phase.upper()}",
            native_unit_of_measurement=UnitOfPower.WATT,
            device_class=SensorDeviceClass.POWER,
            state_class=SensorStateClass.MEASUREMENT,
            entity_registry_enabled_default=False,
        )
        for phase in "rst"
    ),
)

# --- Optional 30600 block: EPS current (only when READ_MONITOR_EXT is on) ---
EPS_CURRENT_SENSOR_TYPES: tuple[SensorEntityDescription, ...] = tuple(
    SensorEntityDescription(
        key=f"eps_current_{phase}", name=f"EPS Current {phase.upper()}",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        # S and T only exist on a three-phase H3X
        entity_registry_enabled_default=(phase == "r"),
    )
    for phase in "rst"
)

# --- Optional reads: third-party meter, codes, limits -------------------------
OPTIONAL_SENSOR_TYPES: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="third_party_meter_power", name="Third Party Meter Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="third_party_meter_energy", name="Third Party Meter Energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SensorEntityDescription(
        key="grid_import_power_limit", name="Grid Import Power Limit",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:transmission-tower-import",
    ),
    SensorEntityDescription(
        key="fault_code", name="Fault Code",
        entity_category=EntityCategory.DIAGNOSTIC, icon="mdi:alert-circle-outline",
    ),
    SensorEntityDescription(
        key="warning_code", name="Warning Code",
        entity_category=EntityCategory.DIAGNOSTIC, icon="mdi:alert-outline",
    ),
)


# --- Static device information (re-read hourly) -----------------------------
FIRMWARE_UPDATE_STATE_SENSOR = SensorEntityDescription(
    key="firmware_update_state", name="Firmware Update State",
    entity_category=EntityCategory.DIAGNOSTIC, icon="mdi:update",
)

IDENTITY_SENSOR_TYPES: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="pcs_software_version", name="PCS Software Version",
        entity_category=EntityCategory.DIAGNOSTIC, icon="mdi:chip",
    ),
    SensorEntityDescription(
        key="pcs_afci_firmware_version", name="AFCI Firmware Version",
        entity_category=EntityCategory.DIAGNOSTIC, icon="mdi:chip",
    ),
    SensorEntityDescription(
        key="bms_main_version", name="BMS Main Version",
        entity_category=EntityCategory.DIAGNOSTIC, icon="mdi:chip",
    ),
    SensorEntityDescription(
        key="bms_hmi_version", name="BMS HMI Version",
        entity_category=EntityCategory.DIAGNOSTIC, icon="mdi:chip",
    ),
    SensorEntityDescription(
        key="pcs_max_output_power", name="PCS Max Output Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

# --- Decoded fault / warning bits ------------------------------------------------
ISSUE_SENSOR_TYPES: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="fault_bits", name="Inverter Faults",
        entity_category=EntityCategory.DIAGNOSTIC, icon="mdi:alert-circle",
    ),
    SensorEntityDescription(
        key="warning_bits", name="Inverter Warnings",
        entity_category=EntityCategory.DIAGNOSTIC, icon="mdi:alert",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Pylontech sensor based on a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    # Create a PylontechSensor for each description above
    descriptions = SENSOR_TYPES + OPTIONAL_SENSOR_TYPES
    # AC-coupled PV only exists when the 30600 block is read; otherwise these
    # sensors would stay empty.
    if getattr(coordinator, "ac_coupled_pv_enabled", False):
        descriptions += ACC_SENSOR_TYPES
    if getattr(coordinator, "eps_current_enabled", False):
        descriptions += EPS_CURRENT_SENSOR_TYPES
    entities = [
        PylontechSensor(coordinator, description, entry)
        for description in descriptions
    ]
    entities.append(
        PylontechFirmwareStateSensor(coordinator, FIRMWARE_UPDATE_STATE_SENSOR, entry)
    )
    entities += [
        PylontechIdentitySensor(coordinator, description, entry)
        for description in IDENTITY_SENSOR_TYPES
    ]
    entities += [
        PylontechIssueSensor(coordinator, description, entry)
        for description in ISSUE_SENSOR_TYPES
    ]

    async_add_entities(entities)


class PylontechSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Pylontech Force H3X sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        description: SensorEntityDescription,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        
        # Unique ID in the entity registry
        self._attr_unique_id = f"{DOMAIN}_{entry.data['host']}_{description.key}"

        # Groups all sensors under one device in Home Assistant
        self._attr_device_info = device_info(coordinator, entry)

    @property
    def native_value(self):
        """Return the state of the sensor."""
        # Value from the data dictionary built in coordinator.py
        return self.coordinator.data.get(self.entity_description.key)


class PylontechFirmwareStateSensor(PylontechSensor):
    """Firmware update state (30152) as text, raw value as attribute."""

    @property
    def _raw(self):
        return (self.coordinator.data or {}).get(self.entity_description.key)

    @property
    def native_value(self):
        raw = self._raw
        if raw is None:
            return None
        return FIRMWARE_UPDATE_STATE_MAP.get(raw, f"Unknown ({raw})")

    @property
    def extra_state_attributes(self):
        return {"code": self._raw}


class PylontechIdentitySensor(PylontechSensor):
    """Static device information (re-read hourly to catch firmware updates)."""

    @property
    def native_value(self):
        return getattr(self.coordinator, "device_identity", {}).get(
            self.entity_description.key
        )

    @property
    def available(self) -> bool:
        return self.native_value is not None


class PylontechIssueSensor(PylontechSensor):
    """Active faults or warnings as readable text, raw bits as attributes."""

    @property
    def _bits(self):
        return combined_bits(self.coordinator.data or {}, self.entity_description.key)

    @property
    def _names(self):
        return PCS_FAULTS if self.entity_description.key == "fault_bits" else PCS_WARNINGS

    @property
    def native_value(self):
        bits = self._bits
        if bits is None:
            return None
        return issues_state(active_issues(bits, self._names))

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        bits = self._bits
        code_key = "fault_code" if self.entity_description.key == "fault_bits" else "warning_code"
        return {
            "active_issues": active_issues(bits, self._names) if bits is not None else [],
            "raw_bits": bits,
            "code": data.get(code_key),
        }
