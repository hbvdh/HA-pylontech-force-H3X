"""Import the actual sensor platform with strict Home Assistant API stubs."""
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


class SensorEntityStub:
    pass


class CoordinatorEntityStub:
    def __init__(self, coordinator):
        self.coordinator = coordinator


def load_sensor():
    """Keep power and apparent-power constants separate, as Home Assistant does.

    Reference: home-assistant/core, homeassistant/const.py (2026.9.0).
    Stubs deliberately do not invent constants via __getattr__.
    """
    modules = {name: ModuleType(name) for name in (
        "homeassistant", "homeassistant.components", "homeassistant.components.sensor",
        "homeassistant.config_entries", "homeassistant.const", "homeassistant.core",
        "homeassistant.helpers", "homeassistant.helpers.entity_platform",
        "homeassistant.helpers.update_coordinator", "fh3x_sensor_test",
    )}
    sensor = modules["homeassistant.components.sensor"]
    sensor.SensorDeviceClass = SimpleNamespace(**{name: name.lower() for name in (
        "VOLTAGE", "CURRENT", "POWER", "APPARENT_POWER", "ENERGY", "FREQUENCY",
        "BATTERY", "TEMPERATURE",
    )})
    sensor.SensorStateClass = SimpleNamespace(MEASUREMENT="measurement", TOTAL_INCREASING="total_increasing")
    sensor.SensorEntity = SensorEntityStub
    sensor.SensorEntityDescription = SimpleNamespace
    constants = modules["homeassistant.const"]
    constants.PERCENTAGE = "%"
    for name, values in {
        "UnitOfPower": {"WATT": "W", "KILO_WATT": "kW"},
        "UnitOfApparentPower": {"VOLT_AMPERE": "VA"},
        "UnitOfElectricCurrent": {"AMPERE": "A"},
        "UnitOfElectricPotential": {"VOLT": "V"},
        "UnitOfEnergy": {"KILO_WATT_HOUR": "kWh"},
        "UnitOfFrequency": {"HERTZ": "Hz"},
        "UnitOfTemperature": {"CELSIUS": "°C"},
    }.items():
        setattr(constants, name, SimpleNamespace(**values))
    modules["homeassistant.config_entries"].ConfigEntry = object
    modules["homeassistant.core"].HomeAssistant = object
    modules["homeassistant.core"].callback = lambda func: func
    modules["homeassistant.helpers.entity_platform"].AddEntitiesCallback = object
    modules["homeassistant.helpers.update_coordinator"].CoordinatorEntity = CoordinatorEntityStub
    modules["fh3x_sensor_test"].__path__ = [str(
        Path(__file__).resolve().parents[1] / "custom_components" / "pylon_fh3x"
    )]
    with patch.dict(sys.modules, modules):
        return importlib.import_module("fh3x_sensor_test.sensor")


class SensorTests(unittest.TestCase):
    def test_platform_imports_and_uses_valid_power_units(self):
        platform = load_sensor()
        for description in platform.SENSOR_TYPES:
            with self.subTest(key=description.key):
                device_class = getattr(description, "device_class", None)
                if device_class == "apparent_power":
                    self.assertEqual(description.native_unit_of_measurement, "VA")
                elif device_class == "power":
                    self.assertEqual(description.native_unit_of_measurement, "W")

    def test_rejected_value_is_unknown_and_recovers_in_sensor(self):
        platform = load_sensor()
        description = next(item for item in platform.SENSOR_TYPES if item.key == "total_grid_import")
        coordinator = SimpleNamespace(data={"total_grid_import": 2025.52})
        entry = SimpleNamespace(data={"host": "test"}, entry_id="test", title="Test")
        sensor = platform.PylontechSensor(coordinator, description, entry)
        self.assertEqual(sensor.native_value, 2025.52)
        coordinator.data = {}
        self.assertIsNone(sensor.native_value)
        coordinator.data = {"total_grid_import": 2025.53}
        self.assertEqual(sensor.native_value, 2025.53)


class CellSensorTests(unittest.TestCase):
    def setUp(self):
        self.platform = load_sensor()

    def _data(self):
        module = {
            "number": 1, "voltage": 52.8, "temperature": 25.0,
            "cell_voltages": [3.3, 3.31], "cell_temperatures": [25.0, 25.1],
            "cell_soc_estimate": [50.0, 60.0], "cell_voltage_min": 3.3,
            "cell_voltage_max": 3.31, "cell_voltage_avg": 3.305,
            "cell_voltage_delta_mv": 10, "cell_temperature_min": 25.0,
            "cell_temperature_max": 25.1,
        }
        return {"modules": [module], "module_count": 1, "cells_per_module": 2}

    def test_entities_follow_layout_and_cells_are_disabled_by_default(self):
        build = self.platform.build_cell_entities
        entry = SimpleNamespace(data={"host": "test"}, entry_id="test", title="Test")
        coordinator = SimpleNamespace(cell_data=self._data(), cell_data_age=12.4)
        entities = build(coordinator, entry)
        # 1 module * (8 module sensors + 2 cells * 2) = 12
        self.assertEqual(len(entities), 12)
        cells = [e for e in entities if "Cell 1 " in e._attr_name or "Cell 2 " in e._attr_name]
        self.assertEqual(len(cells), 4)
        self.assertTrue(all(e._attr_entity_registry_enabled_default is False for e in cells))
        ids = [e._attr_unique_id for e in entities]
        self.assertEqual(len(ids), len(set(ids)))

    def test_values_attributes_and_unavailable_when_stale(self):
        entry = SimpleNamespace(data={"host": "test"}, entry_id="test", title="Test")
        coordinator = SimpleNamespace(cell_data=self._data(), cell_data_age=12.4)
        entities = self.platform.build_cell_entities(coordinator, entry)
        delta = next(e for e in entities if e._attr_unique_id.endswith("module_01_cell_voltage_delta_mv"))
        self.assertEqual(delta.native_value, 10)
        attrs = delta.extra_state_attributes
        self.assertEqual(attrs["cell_voltages"], [3.3, 3.31])
        self.assertEqual(attrs["seconds_since_read"], 12)
        cell2 = next(e for e in entities if e._attr_unique_id.endswith("cell_02_voltage"))
        self.assertEqual(cell2.native_value, 3.31)
        coordinator.cell_data = None
        self.assertFalse(delta.available)
        self.assertIsNone(delta.native_value)
        self.assertFalse(cell2.available)
