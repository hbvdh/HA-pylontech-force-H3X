"""Switch platform for Pylontech Force H3X."""
from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .device import device_info, unique_id_base
from .const import DOMAIN

@dataclass
class PylontechSwitchEntityDescription(SwitchEntityDescription):
    """Describe a writable Pylontech switch entity."""
    register_address: int = 0
    slave_id: int = 2


SWITCH_TYPES: tuple[PylontechSwitchEntityDescription, ...] = (
    PylontechSwitchEntityDescription(
        key="heat_pump",
        name="Heat Pump",
        icon="mdi:heat-pump",
        register_address=40848,
        slave_id=2,
    ),
    PylontechSwitchEntityDescription(
        key="period_1",
        name="Period 1",
        icon="mdi:calendar-clock",
        register_address=40908,
        slave_id=2,
    ),
    PylontechSwitchEntityDescription(
        key="period_2",
        name="Period 2",
        icon="mdi:calendar-clock",
        register_address=40914,
        slave_id=2,
    ),
    PylontechSwitchEntityDescription(
        key="period_3",
        name="Period 3",
        icon="mdi:calendar-clock",
        register_address=40920,
        slave_id=2,
    ),
    PylontechSwitchEntityDescription(
        key="period_4",
        name="Period 4",
        icon="mdi:calendar-clock",
        register_address=40926,
        slave_id=2,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Pylontech switch based on a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        PylontechSwitch(coordinator, description, entry)
        for description in SWITCH_TYPES
    ]

    async_add_entities(entities)


class PylontechSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of a Pylontech switch."""

    _attr_has_entity_name = True
    entity_description: PylontechSwitchEntityDescription

    def __init__(
        self,
        coordinator,
        description: PylontechSwitchEntityDescription,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self.entity_description = description
        
        self._attr_unique_id = f"{DOMAIN}_{unique_id_base(entry)}_{description.key}"
        self._attr_device_info = device_info(coordinator, entry)

    @property
    def is_on(self) -> bool | None:
        """Return true if the switch is on."""
        value = self.coordinator.data.get(self.entity_description.key)
        if value is None:
            return None
        return value == 1

    async def async_turn_on(self, **kwargs) -> None:
        """Enable the associated inverter setting."""
        success = await self.coordinator.async_write_register(
            address=self.entity_description.register_address,
            value=1,
            slave=self.entity_description.slave_id
        )
        if not success:
            raise HomeAssistantError(
                f"The H3X did not confirm the new value for "
                f"'{self.entity_description.name}' (register "
                f"{self.entity_description.register_address}). The setting may "
                "not have been applied; see the Home Assistant log."
            )
        self.coordinator.data[self.entity_description.key] = 1
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Disable the associated inverter setting."""
        success = await self.coordinator.async_write_register(
            address=self.entity_description.register_address,
            value=0,
            slave=self.entity_description.slave_id
        )
        if not success:
            raise HomeAssistantError(
                f"The H3X did not confirm the new value for "
                f"'{self.entity_description.name}' (register "
                f"{self.entity_description.register_address}). The setting may "
                "not have been applied; see the Home Assistant log."
            )
        self.coordinator.data[self.entity_description.key] = 0
        self.async_write_ha_state()
