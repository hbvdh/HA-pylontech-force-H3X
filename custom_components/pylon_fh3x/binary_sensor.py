"""Binary sensor platform for Pylontech Force H3X (inverter problem, EPS)."""
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
    """Set up the inverter problem and EPS enabled binary sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        PylontechProblemSensor(coordinator, entry),
        PylontechEpsEnabledSensor(coordinator, entry),
    ]
    if getattr(coordinator, "bms_system_enabled", False):
        entities.append(PylontechBmsAlarmSensor(coordinator, entry))
    async_add_entities(entities)


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


class PylontechEpsEnabledSensor(CoordinatorEntity, BinarySensorEntity):
    """EPS (backup) output enabled in the inverter settings (40904, read-only)."""

    _attr_has_entity_name = True
    _attr_name = "EPS Enabled"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:power-plug-battery"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{entry.data['host']}_eps_enabled"
        self._attr_device_info = device_info(coordinator, entry)

    @property
    def is_on(self) -> bool | None:
        value = (self.coordinator.data or {}).get("eps_enable")
        return None if value is None else bool(value)


BMS_ALARM_KEYS = (
    "bms_protection_status",
    "bms_alarm_status_1",
    "bms_alarm_status_2",
    "bms_error_code_1",
    "bms_error_code_2",
)


class PylontechBmsAlarmSensor(CoordinatorEntity, BinarySensorEntity):
    """BMS protection, alarm or error active (0x1100 block)."""

    _attr_has_entity_name = True
    _attr_name = "BMS Alarm"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{entry.data['host']}_bms_alarm"
        self._attr_device_info = device_info(coordinator, entry)

    @property
    def _codes(self) -> dict:
        data = self.coordinator.data or {}
        return {key: data.get(key) for key in BMS_ALARM_KEYS}

    @property
    def is_on(self) -> bool | None:
        codes = self._codes
        if any(value is None for value in codes.values()):
            return None
        return any(codes.values())

    @property
    def extra_state_attributes(self):
        return self._codes
