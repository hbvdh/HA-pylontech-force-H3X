"""EMS mode select: known, Dynamic (read-only) and unmapped register values."""
import asyncio
from dataclasses import dataclass
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


class SelectEntityStub:
    pass


@dataclass
class SelectEntityDescriptionStub:
    key: str = ""
    name: str | None = None
    icon: str | None = None
    options: list | None = None


class CoordinatorEntityStub:
    def __init__(self, coordinator):
        self.coordinator = coordinator

    def _handle_coordinator_update(self):
        self.state_writes = getattr(self, "state_writes", 0) + 1


class HomeAssistantErrorStub(Exception):
    pass


def load_select():
    modules = {name: ModuleType(name) for name in (
        "homeassistant", "homeassistant.components", "homeassistant.components.select",
        "homeassistant.config_entries", "homeassistant.core", "homeassistant.exceptions",
        "homeassistant.helpers", "homeassistant.helpers.entity_platform",
        "homeassistant.helpers.update_coordinator", "fh3x_select_test",
    )}
    modules["homeassistant.components.select"].SelectEntity = SelectEntityStub
    modules["homeassistant.components.select"].SelectEntityDescription = SelectEntityDescriptionStub
    modules["homeassistant.config_entries"].ConfigEntry = object
    modules["homeassistant.core"].HomeAssistant = object
    modules["homeassistant.core"].callback = lambda func: func
    modules["homeassistant.exceptions"].HomeAssistantError = HomeAssistantErrorStub
    modules["homeassistant.helpers.entity_platform"].AddEntitiesCallback = object
    modules["homeassistant.helpers.update_coordinator"].CoordinatorEntity = CoordinatorEntityStub
    modules["fh3x_select_test"].__path__ = [str(
        Path(__file__).resolve().parents[1] / "custom_components" / "pylon_fh3x"
    )]
    with patch.dict(sys.modules, modules):
        return importlib.import_module("fh3x_select_test.select")


def make_select(platform, data):
    coordinator = SimpleNamespace(data=data, async_write_register=AsyncMock(return_value=True))
    entry = SimpleNamespace(data={"host": "test"}, entry_id="test", title="Test")
    entity = platform.PylontechSelect(coordinator, platform.SELECT_TYPES[0], entry)
    entity.async_write_ha_state = lambda: None
    return entity


class EmsModeSelectTests(unittest.TestCase):
    def setUp(self):
        self.platform = load_select()

    def test_documented_modes_map_and_expose_raw_value(self):
        entity = make_select(self.platform, {"ems_mode": "4"})
        self.assertEqual(entity.current_option, "User mode")
        self.assertEqual(entity.extra_state_attributes, {"raw_value": 4})

    def test_dynamic_mode_is_shown(self):
        # 262 = 0x0106, read live with the app on Dynamic.
        entity = make_select(self.platform, {"ems_mode": "262"})
        self.assertEqual(entity.current_option, "Dynamic mode")
        with self.assertNoLogs(self.platform._LOGGER, "WARNING"):
            entity._handle_coordinator_update()
        options = self.platform.SELECT_TYPES[0].options
        self.assertEqual(options.count("Dynamic mode"), 1)
        self.assertEqual(len(options), len(set(options)))

    def test_unseen_values_stay_unknown(self):
        for raw in (6, 0x0100):  # bare low byte, other high-byte combination
            with self.subTest(raw=raw):
                entity = make_select(self.platform, {"ems_mode": str(raw)})
                self.assertIsNone(entity.current_option)

    def test_unmapped_value_is_unknown_with_raw_value_and_logged_once(self):
        entity = make_select(self.platform, {"ems_mode": "9", "charge_discharge_power": -1000})
        self.assertIsNone(entity.current_option)
        self.assertEqual(entity.extra_state_attributes, {"raw_value": 9})
        with self.assertLogs(self.platform._LOGGER, "WARNING") as logs:
            entity._handle_coordinator_update()
        self.assertIn("Unknown EMS mode value 9", logs.output[0])
        self.assertIn("-1000", logs.output[0])
        with self.assertNoLogs(self.platform._LOGGER, "WARNING"):
            entity._handle_coordinator_update()
        self.assertEqual(entity.state_writes, 2)

    def test_unmapped_value_logged_again_after_known_value(self):
        entity = make_select(self.platform, {"ems_mode": "9"})
        with self.assertLogs(self.platform._LOGGER, "WARNING"):
            entity._handle_coordinator_update()
        entity.coordinator.data["ems_mode"] = "0"
        with self.assertNoLogs(self.platform._LOGGER, "WARNING"):
            entity._handle_coordinator_update()
        entity.coordinator.data["ems_mode"] = "9"
        with self.assertLogs(self.platform._LOGGER, "WARNING"):
            entity._handle_coordinator_update()

    def test_missing_value_is_unknown_without_warning(self):
        entity = make_select(self.platform, {})
        self.assertIsNone(entity.current_option)
        self.assertEqual(entity.extra_state_attributes, {"raw_value": None})
        with self.assertNoLogs(self.platform._LOGGER, "WARNING"):
            entity._handle_coordinator_update()

    def test_dynamic_mode_cannot_be_written(self):
        entity = make_select(self.platform, {"ems_mode": "0"})
        with self.assertRaises(HomeAssistantErrorStub):
            asyncio.run(entity.async_select_option("Dynamic mode"))
        entity.coordinator.async_write_register.assert_not_called()

    def test_writable_mode_is_written(self):
        entity = make_select(self.platform, {"ems_mode": "6"})
        asyncio.run(entity.async_select_option("User mode"))
        entity.coordinator.async_write_register.assert_awaited_once_with(
            address=40907, value=4, slave=2
        )
        self.assertEqual(entity.coordinator.data["ems_mode"], "4")


if __name__ == "__main__":
    unittest.main()
