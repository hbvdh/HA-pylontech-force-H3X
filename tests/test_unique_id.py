"""Entity unique IDs must survive a host change through Reconfigure."""
import asyncio
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

PACKAGE = "fh3x_unique_id_test"


class ConfigFlowStub:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__()

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_update_reload_and_abort(self, entry, data_updates=None):
        entry.data = {**entry.data, **(data_updates or {})}
        return {"type": "abort", "reason": "reconfigure_successful"}


def load():
    modules = {name: ModuleType(name) for name in (
        "voluptuous", "homeassistant", "homeassistant.config_entries",
        "homeassistant.core", "homeassistant.data_entry_flow",
        "homeassistant.exceptions", "homeassistant.helpers",
        "homeassistant.helpers.device_registry", PACKAGE,
    )}
    vol = modules["voluptuous"]
    vol.Schema = lambda schema: schema
    vol.Required = lambda key, default=None: key
    config_entries = modules["homeassistant.config_entries"]
    config_entries.ConfigFlow = ConfigFlowStub
    config_entries.OptionsFlow = object
    config_entries.ConfigEntry = object
    modules["homeassistant"].config_entries = config_entries
    modules["homeassistant.core"].HomeAssistant = object
    modules["homeassistant.core"].callback = lambda func: func
    modules["homeassistant.data_entry_flow"].FlowResult = dict
    modules["homeassistant.exceptions"].HomeAssistantError = Exception
    modules["homeassistant.helpers.device_registry"].DeviceInfo = dict
    modules[PACKAGE].__path__ = [str(
        Path(__file__).resolve().parents[1] / "custom_components" / "pylon_fh3x"
    )]
    with patch.dict(sys.modules, modules):
        return (
            importlib.import_module(f"{PACKAGE}.device"),
            importlib.import_module(f"{PACKAGE}.config_flow"),
        )


class UniqueIdBaseTests(unittest.TestCase):
    def setUp(self):
        self.device, self.config_flow = load()

    def _reconfigure(self, entry, user_input):
        flow = self.config_flow.ConfigFlow()
        flow.hass = None
        flow._get_reconfigure_entry = lambda: entry
        return asyncio.run(flow.async_step_reconfigure(user_input))

    def test_existing_entry_keeps_its_host_based_ids(self):
        entry = SimpleNamespace(data={"host": "192.0.2.10", "port": 502})
        self.assertEqual(self.device.unique_id_base(entry), "192.0.2.10")

    def test_host_change_keeps_the_unique_id_base(self):
        entry = SimpleNamespace(data={"host": "192.0.2.10", "port": 502})
        result = self._reconfigure(entry, {"host": "192.0.2.99", "port": 502})
        self.assertEqual(result["type"], "abort")
        self.assertEqual(entry.data["host"], "192.0.2.99")
        self.assertEqual(self.device.unique_id_base(entry), "192.0.2.10")

    def test_second_host_change_still_keeps_the_first_base(self):
        entry = SimpleNamespace(data={"host": "192.0.2.10", "port": 502})
        self._reconfigure(entry, {"host": "192.0.2.99", "port": 502})
        self._reconfigure(entry, {"host": "h3x.example", "port": 1502})
        self.assertEqual(entry.data["host"], "h3x.example")
        self.assertEqual(entry.data["port"], 1502)
        self.assertEqual(self.device.unique_id_base(entry), "192.0.2.10")

    def test_no_platform_builds_ids_from_the_host_directly(self):
        source = Path(__file__).resolve().parents[1] / "custom_components" / "pylon_fh3x"
        for name in ("sensor", "binary_sensor", "number", "select", "switch"):
            text = (source / f"{name}.py").read_text()
            self.assertNotIn("entry.data['host']", text, name)
            self.assertIn("unique_id_base(entry)", text, name)


class DeviceInfoTests(unittest.TestCase):
    """Entities get a DeviceInfo built once from plain fields."""

    def setUp(self):
        self.device, _ = load()

    def test_fields_from_the_coordinator_become_a_device_info(self):
        built = []

        class DeviceInfoStub(dict):
            def __init__(self, **fields):
                built.append(fields)
                super().__init__(**fields)

        coordinator = SimpleNamespace(
            build_device_info=lambda entry: {
                "identifiers": {("pylon_fh3x", entry.entry_id)},
                "name": entry.title,
                "serial_number": "ABC123456",
            }
        )
        entry = SimpleNamespace(entry_id="e1", title="Force H3X")
        with patch.object(self.device, "DeviceInfo", DeviceInfoStub):
            info = self.device.device_info(coordinator, entry)
        self.assertIsInstance(info, DeviceInfoStub)
        self.assertEqual(built, [{
            "identifiers": {("pylon_fh3x", "e1")},
            "name": "Force H3X",
            "serial_number": "ABC123456",
        }])

    def test_fallback_without_a_builder(self):
        entry = SimpleNamespace(entry_id="e1", title="Force H3X")
        info = self.device.device_info(SimpleNamespace(), entry)
        self.assertEqual(info["identifiers"], {("pylon_fh3x", "e1")})
        self.assertEqual(info["name"], "Force H3X")


if __name__ == "__main__":
    unittest.main()
