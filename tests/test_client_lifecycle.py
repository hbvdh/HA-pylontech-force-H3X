"""The integration alone owns the Modbus connection: no background reconnect,
and the connection is always released on unload or failed setup."""
import asyncio
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class FakeModbusClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.connected = False
        self.close = MagicMock()


class CoordinatorStub:
    def __init__(self, *args, **kwargs):
        self.data = {}


def load():
    modules = {name: ModuleType(name) for name in (
        "pymodbus", "pymodbus.client", "pymodbus.exceptions",
        "homeassistant", "homeassistant.config_entries", "homeassistant.const",
        "homeassistant.core", "homeassistant.helpers",
        "homeassistant.helpers.update_coordinator", "fh3x_lifecycle_test",
    )}
    modules["pymodbus.client"].AsyncModbusTcpClient = FakeModbusClient
    modules["pymodbus.exceptions"].ModbusException = type(
        "ModbusException", (Exception,), {}
    )
    modules["homeassistant.config_entries"].ConfigEntry = object
    modules["homeassistant.const"].Platform = SimpleNamespace(
        SENSOR="sensor", BINARY_SENSOR="binary_sensor", NUMBER="number",
        SELECT="select", SWITCH="switch",
    )
    modules["homeassistant.core"].HomeAssistant = object
    framework = modules["homeassistant.helpers.update_coordinator"]
    framework.DataUpdateCoordinator = CoordinatorStub
    framework.UpdateFailed = type("UpdateFailed", (Exception,), {})
    modules["fh3x_lifecycle_test"].__path__ = [str(
        Path(__file__).resolve().parents[1] / "custom_components" / "pylon_fh3x"
    )]
    with patch.dict(sys.modules, modules):
        # The integration's __init__.py, loaded as a submodule of the stub
        # package so its relative imports resolve.
        return (
            importlib.import_module("fh3x_lifecycle_test.__init__"),
            importlib.import_module("fh3x_lifecycle_test.coordinator"),
        )


class ClientLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.package, self.coordinator_module = load()

    def _hass_and_entry(self):
        hass = SimpleNamespace(
            data={},
            config_entries=SimpleNamespace(
                async_forward_entry_setups=AsyncMock(),
                async_unload_platforms=AsyncMock(return_value=True),
            ),
        )
        entry = SimpleNamespace(
            entry_id="entry1", data={"host": "test", "port": 502}, options={},
            async_on_unload=lambda func: None,
            add_update_listener=lambda func: None,
        )
        return hass, entry

    def test_pymodbus_auto_reconnect_is_disabled(self):
        coordinator = self.coordinator_module.PylontechCoordinator(None, "test", 502)
        self.assertEqual(coordinator.client.kwargs["reconnect_delay"], 0)
        self.assertEqual(coordinator.client.kwargs["retries"], 0)

    def test_close_client_closes_even_when_not_connected(self):
        coordinator = self.coordinator_module.PylontechCoordinator(None, "test", 502)
        coordinator.client.connected = False
        coordinator.close_client()
        coordinator.client.close.assert_called_once_with()

    def test_close_client_never_raises(self):
        coordinator = self.coordinator_module.PylontechCoordinator(None, "test", 502)
        coordinator.client.close.side_effect = RuntimeError("boom")
        coordinator.close_client()

    def test_failed_first_refresh_closes_the_client(self):
        hass, entry = self._hass_and_entry()
        created = []

        class FailingCoordinator(self.coordinator_module.PylontechCoordinator):
            def __init__(inner, *args, **kwargs):
                super().__init__(*args, **kwargs)
                created.append(inner)

            async def async_config_entry_first_refresh(inner):
                raise RuntimeError("H3X not answering")

        with patch.object(self.package, "PylontechCoordinator", FailingCoordinator):
            with self.assertRaises(RuntimeError):
                asyncio.run(self.package.async_setup_entry(hass, entry))
        created[0].client.close.assert_called_once_with()
        self.assertEqual(hass.data[self.package.DOMAIN], {})

    def test_unload_closes_a_disconnected_client(self):
        hass, entry = self._hass_and_entry()
        coordinator = self.coordinator_module.PylontechCoordinator(None, "test", 502)
        coordinator.client.connected = False
        hass.data[self.package.DOMAIN] = {entry.entry_id: coordinator}
        self.assertTrue(asyncio.run(self.package.async_unload_entry(hass, entry)))
        coordinator.client.close.assert_called_once_with()
        self.assertEqual(hass.data[self.package.DOMAIN], {})


class UpdateListenerTests(unittest.TestCase):
    """The update listener reloads for an options change only."""

    def setUp(self):
        self.package, _ = load()

    def _run(self, running_details, options):
        hass = SimpleNamespace(
            data={self.package.DOMAIN: {"e1": SimpleNamespace(battery_details=running_details)}},
            config_entries=SimpleNamespace(async_reload=AsyncMock()),
        )
        entry = SimpleNamespace(entry_id="e1", options=options)
        asyncio.run(self.package._async_update_listener(hass, entry))
        return hass.config_entries.async_reload

    def test_reconfigure_does_not_reload_a_second_time(self):
        # Reconfigure changed entry.data and reloads by itself; options unchanged.
        self._run(True, {"battery_details": True}).assert_not_awaited()
        self._run(False, {}).assert_not_awaited()

    def test_changed_option_reloads(self):
        self._run(False, {"battery_details": True}).assert_awaited_once_with("e1")
        self._run(True, {"battery_details": False}).assert_awaited_once_with("e1")

    def test_reloads_when_no_coordinator_is_running(self):
        hass = SimpleNamespace(
            data={}, config_entries=SimpleNamespace(async_reload=AsyncMock())
        )
        entry = SimpleNamespace(entry_id="e1", options={})
        asyncio.run(self.package._async_update_listener(hass, entry))
        hass.config_entries.async_reload.assert_awaited_once_with("e1")


class OptionChangedDuringSetupTests(unittest.TestCase):
    """An option saved while the integration was loading is not lost."""

    def setUp(self):
        self.package, self.coordinator_module = load()

    def _setup(self, options_after_refresh):
        hass = SimpleNamespace(
            data={},
            config_entries=SimpleNamespace(
                async_forward_entry_setups=AsyncMock(),
                async_schedule_reload=MagicMock(),
            ),
        )
        entry = SimpleNamespace(
            entry_id="entry1", data={"host": "test", "port": 502}, options={},
            async_on_unload=lambda func: None,
            add_update_listener=lambda func: None,
        )

        class Coordinator(self.coordinator_module.PylontechCoordinator):
            async def async_config_entry_first_refresh(inner):
                # The user saves Configure while the first read is running.
                entry.options = options_after_refresh

        with patch.object(self.package, "PylontechCoordinator", Coordinator):
            self.assertTrue(asyncio.run(self.package.async_setup_entry(hass, entry)))
        return hass.config_entries.async_schedule_reload

    def test_option_changed_during_setup_schedules_one_reload(self):
        self._setup({"battery_details": True}).assert_called_once_with("entry1")

    def test_unchanged_option_schedules_nothing(self):
        self._setup({}).assert_not_called()
        self._setup({"battery_details": False}).assert_not_called()


if __name__ == "__main__":
    unittest.main()
