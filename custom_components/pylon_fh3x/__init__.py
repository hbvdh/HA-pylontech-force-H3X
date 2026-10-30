"""The Pylontech Force H3X integration."""
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN, CONF_HOST, CONF_PORT, CONF_CELL_DATA
from .coordinator import PylontechCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.NUMBER, Platform.SELECT, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Pylontech Force H3X from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Read the Modbus endpoint stored by the config flow.
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]

    # Initialize the shared coordinator used by all entity platforms.
    coordinator = PylontechCoordinator(hass, host, port)
    # Optional module/cell data from the BMS, off unless enabled in the options.
    coordinator.cell_data_enabled = bool(entry.options.get(CONF_CELL_DATA, False))

    
    await coordinator.async_config_entry_first_refresh()

    
    hass.data[DOMAIN][entry.entry_id] = coordinator

    
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload when the options change so the cell sensors appear or disappear.
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration after an options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        
        coordinator = hass.data[DOMAIN][entry.entry_id]
        if coordinator.client.connected:
            coordinator.client.close()
            
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
