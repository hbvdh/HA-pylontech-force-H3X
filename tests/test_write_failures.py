"""An unconfirmed write must raise, so UI and automations see the failure."""
import asyncio
from dataclasses import dataclass
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


@dataclass
class DescriptionStub:
    key: str = ""
    name: str | None = None
    icon: str | None = None
    options: list | None = None
    native_unit_of_measurement: str | None = None
    device_class: str | None = None
    native_min_value: float | None = None
    native_max_value: float | None = None
    native_step: float | None = None
    mode: str | None = None


class CoordinatorEntityStub:
    def __init__(self, coordinator):
        self.coordinator = coordinator

    def _handle_coordinator_update(self):
        pass


class HomeAssistantErrorStub(Exception):
    pass


def load_platform(name):
    package = f"fh3x_write_test_{name}"
    modules = {mod: ModuleType(mod) for mod in (
        "homeassistant", "homeassistant.components",
        f"homeassistant.components.{name}", "homeassistant.config_entries",
        "homeassistant.const", "homeassistant.core", "homeassistant.exceptions",
        "homeassistant.helpers", "homeassistant.helpers.entity_platform",
        "homeassistant.helpers.update_coordinator", package,
    )}
    component = modules[f"homeassistant.components.{name}"]
    component.NumberEntity = component.SwitchEntity = component.SelectEntity = object
    component.NumberEntityDescription = DescriptionStub
    component.SwitchEntityDescription = DescriptionStub
    component.SelectEntityDescription = DescriptionStub
    component.NumberMode = SimpleNamespace(BOX="box", SLIDER="slider")
    modules["homeassistant.config_entries"].ConfigEntry = object
    modules["homeassistant.const"].PERCENTAGE = "%"
    modules["homeassistant.core"].HomeAssistant = object
    modules["homeassistant.core"].callback = lambda func: func
    modules["homeassistant.exceptions"].HomeAssistantError = HomeAssistantErrorStub
    modules["homeassistant.helpers.entity_platform"].AddEntitiesCallback = object
    modules["homeassistant.helpers.update_coordinator"].CoordinatorEntity = CoordinatorEntityStub
    modules[package].__path__ = [str(
        Path(__file__).resolve().parents[1] / "custom_components" / "pylon_fh3x"
    )]
    with patch.dict(sys.modules, modules):
        return importlib.import_module(f"{package}.{name}")


def make_entity(cls, description, data, success):
    coordinator = SimpleNamespace(
        data=dict(data),
        async_write_register=AsyncMock(return_value=success),
        async_write_register_32bit=AsyncMock(return_value=success),
    )
    entry = SimpleNamespace(data={"host": "test"}, entry_id="test", title="Test")
    entity = cls(coordinator, description, entry)
    entity.state_writes = 0

    def write_state():
        entity.state_writes += 1

    entity.async_write_ha_state = write_state
    return entity


class NumberWriteTests(unittest.TestCase):
    def setUp(self):
        self.platform = load_platform("number")
        self.descriptions = {d.key: d for d in self.platform.NUMBER_TYPES}

    def _entity(self, key, data, success):
        return make_entity(
            self.platform.PylontechNumber, self.descriptions[key], data, success
        )

    def test_failed_write_raises_and_keeps_state(self):
        entity = self._entity("charge_limit_soc", {"charge_limit_soc": 90}, False)
        with self.assertRaises(HomeAssistantErrorStub):
            asyncio.run(entity.async_set_native_value(80))
        self.assertEqual(entity.coordinator.data["charge_limit_soc"], 90)
        self.assertEqual(entity.state_writes, 0)

    def test_failed_32bit_write_raises_and_keeps_state(self):
        entity = self._entity(
            "meter_export_power_max", {"meter_export_power_max": -5000}, False
        )
        with self.assertRaises(HomeAssistantErrorStub):
            asyncio.run(entity.async_set_native_value(-3000))
        self.assertEqual(entity.coordinator.data["meter_export_power_max"], -5000)

    def test_confirmed_write_updates_state(self):
        entity = self._entity("charge_discharge_power", {}, True)
        asyncio.run(entity.async_set_native_value(-50))
        entity.coordinator.async_write_register.assert_awaited_once_with(
            address=40901, value=-500, slave=2
        )
        self.assertEqual(entity.coordinator.data["charge_discharge_power"], -500)
        self.assertEqual(entity.state_writes, 1)


class SwitchWriteTests(unittest.TestCase):
    def setUp(self):
        self.platform = load_platform("switch")
        self.description = next(
            d for d in self.platform.SWITCH_TYPES if d.key == "period_1"
        )

    def _entity(self, data, success):
        return make_entity(
            self.platform.PylontechSwitch, self.description, data, success
        )

    def test_failed_turn_on_raises_and_keeps_state(self):
        entity = self._entity({"period_1": 0}, False)
        with self.assertRaises(HomeAssistantErrorStub):
            asyncio.run(entity.async_turn_on())
        self.assertEqual(entity.coordinator.data["period_1"], 0)
        self.assertEqual(entity.state_writes, 0)

    def test_failed_turn_off_raises_and_keeps_state(self):
        entity = self._entity({"period_1": 1}, False)
        with self.assertRaises(HomeAssistantErrorStub):
            asyncio.run(entity.async_turn_off())
        self.assertEqual(entity.coordinator.data["period_1"], 1)

    def test_confirmed_turn_on_and_off_update_state(self):
        entity = self._entity({"period_1": 0}, True)
        asyncio.run(entity.async_turn_on())
        self.assertEqual(entity.coordinator.data["period_1"], 1)
        asyncio.run(entity.async_turn_off())
        self.assertEqual(entity.coordinator.data["period_1"], 0)
        self.assertEqual(entity.state_writes, 2)


class SelectWriteTests(unittest.TestCase):
    def setUp(self):
        self.platform = load_platform("select")

    def test_failed_mode_write_raises_and_keeps_state(self):
        entity = make_entity(
            self.platform.PylontechSelect, self.platform.SELECT_TYPES[0],
            {"ems_mode": "0"}, False,
        )
        with self.assertRaises(HomeAssistantErrorStub):
            asyncio.run(entity.async_select_option("User mode"))
        self.assertEqual(entity.coordinator.data["ems_mode"], "0")
        self.assertEqual(entity.state_writes, 0)


if __name__ == "__main__":
    unittest.main()
