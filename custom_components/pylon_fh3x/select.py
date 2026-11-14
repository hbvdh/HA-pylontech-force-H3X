"""Select platform for Pylontech Force H3X."""
from dataclasses import dataclass
import logging

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .device import device_info, unique_id_base
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Map raw register values to user-facing EMS modes.
# 0-5 come from the Modbus protocol document. Dynamic mode is not in that
# document: the Pylontech app's "Intelligent Dynamic" mode (app V1.1.50+,
# BMS V0.26+, a cloud tariff algorithm) leaves an undocumented code in 40907.
# 262 (0x0106) was read on a live system (PCS V1.0.0.43, BMS V0.34) with the
# app on Dynamic: low byte 6, high byte 0x01 (meaning unknown). The exact value
# is matched, not just the low byte, so an unseen high-byte combination stays
# unknown and is logged instead of being shown as a documented mode.
EMS_MODE_OPTIONS = {
    "0": "Self-Consumption",
    "1": "Back up mode",
    "2": "Off-Grid mode",
    "3": "Feed in priority mode",
    "4": "User mode",
    "5": "PN-Customer mode",
    "262": "Dynamic mode",
}

# Modes that can be shown but not written from Home Assistant. Dynamic mode is
# switched on in the Pylontech app only; writing its code over Modbus is not
# documented.
READ_ONLY_EMS_MODES = frozenset({"Dynamic mode"})

# Reverse mapping used when writing a selected mode.
EMS_MODE_OPTIONS_REVERSE = {
    v: int(k) for k, v in EMS_MODE_OPTIONS.items() if v not in READ_ONLY_EMS_MODES
}

@dataclass
class PylontechSelectEntityDescription(SelectEntityDescription):
    """Describe a writable Pylontech select entity."""
    register_address: int = 0
    slave_id: int = 2


SELECT_TYPES: tuple[PylontechSelectEntityDescription, ...] = (
    PylontechSelectEntityDescription(
        key="ems_mode",
        name="EMS Mode",
        icon="mdi:state-machine",
        register_address=40907,
        slave_id=2,
        # Unique labels: several raw values can share one label.
        options=list(dict.fromkeys(EMS_MODE_OPTIONS.values())),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Pylontech select based on a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        PylontechSelect(coordinator, description, entry)
        for description in SELECT_TYPES
    ]

    async_add_entities(entities)


class PylontechSelect(CoordinatorEntity, SelectEntity):
    """Representation of a Pylontech select dropdown."""

    _attr_has_entity_name = True
    entity_description: PylontechSelectEntityDescription

    def __init__(
        self,
        coordinator,
        description: PylontechSelectEntityDescription,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator)
        self.entity_description = description
        # Last unmapped raw value that was logged, so it is logged once.
        self._warned_raw_value = None

        self._attr_unique_id = f"{DOMAIN}_{unique_id_base(entry)}_{description.key}"
        self._attr_device_info = device_info(coordinator, entry)

    def _raw_value(self) -> int | None:
        """Raw register value as an int, or None when not read."""
        data = self.coordinator.data or {}
        raw_value = data.get(self.entity_description.key)
        try:
            return int(raw_value) if raw_value is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def current_option(self) -> str | None:
        """Return the option matching the current register value."""
        raw_value = self._raw_value()
        if raw_value is None:
            return None
        return EMS_MODE_OPTIONS.get(str(raw_value))

    @property
    def extra_state_attributes(self) -> dict:
        """Expose the raw register value, also when it has no known name."""
        return {"raw_value": self._raw_value()}

    def check_unmapped_value(self) -> None:
        """Log an unmapped register value once, until it changes."""
        raw_value = self._raw_value()
        if raw_value is None or str(raw_value) in EMS_MODE_OPTIONS:
            self._warned_raw_value = None
            return
        if raw_value != self._warned_raw_value:
            self._warned_raw_value = raw_value
            _LOGGER.warning(
                "Unknown EMS mode value %s in register %s; the select shows "
                "unknown. Charge/Discharge Power Ref (40901) raw value: %s",
                raw_value,
                self.entity_description.register_address,
                (self.coordinator.data or {}).get("charge_discharge_power"),
            )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Check for unmapped values on every poll, then update the state."""
        self.check_unmapped_value()
        super()._handle_coordinator_update()

    async def async_select_option(self, option: str) -> None:
        """Write the selected EMS mode to the inverter."""
        if option in READ_ONLY_EMS_MODES:
            raise HomeAssistantError(
                f"{option} can only be switched on in the Pylontech app"
            )
        int_value = EMS_MODE_OPTIONS_REVERSE.get(option)
        if int_value is None:
            raise HomeAssistantError(f"Unknown EMS mode: {option}")

        success = await self.coordinator.async_write_register(
            address=self.entity_description.register_address,
            value=int_value,
            slave=self.entity_description.slave_id
        )

        if not success:
            raise HomeAssistantError(
                f"The H3X did not confirm EMS mode '{option}' (register "
                f"{self.entity_description.register_address}). The mode may "
                "not have been changed; see the Home Assistant log."
            )
        # Update local state immediately instead of waiting for the next poll.
        self.coordinator.data[self.entity_description.key] = str(int_value)
        self.async_write_ha_state()
