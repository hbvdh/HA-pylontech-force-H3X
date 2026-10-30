"""Sensors for BMS module and cell data (optional, see the integration options)."""
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfElectricPotential, UnitOfTemperature
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL

# (key, name, unit, device_class, decimals)
MODULE_SENSORS = (
    ("voltage", "Voltage", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE),
    ("temperature", "Temperature", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
    ("cell_voltage_min", "Cell Voltage Min", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE),
    ("cell_voltage_max", "Cell Voltage Max", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE),
    ("cell_voltage_avg", "Cell Voltage Average", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE),
    ("cell_voltage_delta_mv", "Cell Voltage Delta", "mV", None),
    ("cell_temperature_min", "Cell Temperature Min", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
    ("cell_temperature_max", "Cell Temperature Max", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
)
# The delta sensor also carries the full per-cell lists for dashboards.
ATTRIBUTE_SENSOR_KEY = "cell_voltage_delta_mv"


def _module(coordinator, number):
    data = coordinator.cell_data
    if not data:
        return None
    for module in data["modules"]:
        if module["number"] == number:
            return module
    return None


def _device_info(entry):
    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": entry.title,
        "manufacturer": MANUFACTURER,
        "model": MODEL,
    }


class ModuleSensor(CoordinatorEntity, SensorEntity):
    """One value for one battery module."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, number, definition):
        super().__init__(coordinator)
        key, name, unit, device_class = definition
        self._number = number
        self._key = key
        self._attr_name = f"BMS Module {number} {name}"
        self._attr_unique_id = f"{DOMAIN}_{entry.data['host']}_bms_module_{number:02d}_{key}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_device_info = _device_info(entry)

    @property
    def available(self):
        return _module(self.coordinator, self._number) is not None

    @property
    def native_value(self):
        module = _module(self.coordinator, self._number)
        return None if module is None else module.get(self._key)

    @property
    def extra_state_attributes(self):
        if self._key != ATTRIBUTE_SENSOR_KEY:
            return None
        module = _module(self.coordinator, self._number)
        if module is None:
            return None
        age = self.coordinator.cell_data_age
        return {
            "cell_count": len(module["cell_voltages"]),
            "cell_voltages": module["cell_voltages"],
            "cell_temperatures": module["cell_temperatures"],
            # Indication only: the BMS reports no SOC per cell, this comes from
            # a generic LFP voltage curve.
            "cell_soc_estimate": module["cell_soc_estimate"],
            "seconds_since_read": None if age is None else round(age),
        }


class CellSensor(CoordinatorEntity, SensorEntity):
    """Voltage or temperature of a single cell (disabled by default)."""

    _attr_has_entity_name = True
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, entry, number, cell, kind):
        super().__init__(coordinator)
        self._number = number
        self._cell = cell
        self._kind = kind  # "voltage" or "temperature"
        label = "Voltage" if kind == "voltage" else "Temperature"
        self._attr_name = f"BMS Module {number} Cell {cell} {label}"
        self._attr_unique_id = (
            f"{DOMAIN}_{entry.data['host']}_bms_module_{number:02d}_cell_{cell:02d}_{kind}"
        )
        if kind == "voltage":
            self._attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
            self._attr_device_class = SensorDeviceClass.VOLTAGE
        else:
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_device_info = _device_info(entry)

    def _values(self):
        module = _module(self.coordinator, self._number)
        if module is None:
            return None
        return module["cell_voltages" if self._kind == "voltage" else "cell_temperatures"]

    @property
    def available(self):
        values = self._values()
        return values is not None and self._cell <= len(values)

    @property
    def native_value(self):
        values = self._values()
        if values is None or self._cell > len(values):
            return None
        return values[self._cell - 1]


def build_cell_entities(coordinator, entry):
    """Create the module and cell entities for the layout read from the BMS."""
    data = coordinator.cell_data
    if not data:
        return []
    entities = []
    for number in range(1, data["module_count"] + 1):
        for definition in MODULE_SENSORS:
            entities.append(ModuleSensor(coordinator, entry, number, definition))
        for cell in range(1, data["cells_per_module"] + 1):
            entities.append(CellSensor(coordinator, entry, number, cell, "voltage"))
            entities.append(CellSensor(coordinator, entry, number, cell, "temperature"))
    return entities
