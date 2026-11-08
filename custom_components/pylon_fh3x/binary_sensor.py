"""Binary sensor platform for Pylontech Force H3X (inverter problem)."""
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .alarms import PCS_FAULTS, active_issues, combined_bits
from .const import DOMAIN
from .device import device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the inverter problem binary sensor."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PylontechProblemSensor(coordinator, entry)])


class PylontechProblemSensor(CoordinatorEntity, BinarySensorEntity):
    """On when the inverter reports a fault code or fault bits."""

    _attr_has_entity_name = True
    _attr_name = "Inverter Problem"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{entry.data['host']}_inverter_problem"
        self._attr_device_info = device_info(coordinator, entry)

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data or {}
        code = data.get("fault_code")
        bits = combined_bits(data, "fault_bits")
        if code is None and bits is None:
            return None
        return bool(code) or bool(bits)

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        bits = combined_bits(data, "fault_bits")
        return {
            "fault_code": data.get("fault_code"),
            "active_faults": active_issues(bits, PCS_FAULTS) if bits is not None else [],
        }
