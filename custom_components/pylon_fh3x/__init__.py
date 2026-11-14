"""The Pylontech Force H3X integration."""
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN,
    CONF_HOST,
    CONF_PORT,
    CONF_BATTERY_DETAILS,
    DEFAULT_BATTERY_DETAILS,
)
from .coordinator import PylontechCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR, Platform.BINARY_SENSOR, Platform.NUMBER, Platform.SELECT, Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Pylontech Force H3X from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Read the Modbus endpoint stored by the config flow.
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]

    # Initialize the shared coordinator used by all entity platforms.
    battery_details = entry.options.get(
        CONF_BATTERY_DETAILS, DEFAULT_BATTERY_DETAILS
    )
    coordinator = PylontechCoordinator(hass, host, port, battery_details)
    # Lets the coordinator update the device registry (firmware, model).
    coordinator.config_entry_ref = entry

    try:
        await coordinator.async_config_entry_first_refresh()
    except BaseException:
        # Setup failed or was cancelled: Home Assistant retries with a new
        # coordinator, so this connection must not stay open in the meantime.
        coordinator.close_client()
        raise

    
    hass.data[DOMAIN][entry.entry_id] = coordinator

    
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload when the options (Configure) change.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        coordinator.close_client()

    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration after the options were changed."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is not None and coordinator.battery_details == entry.options.get(
        CONF_BATTERY_DETAILS, DEFAULT_BATTERY_DETAILS
    ):
        # Not an options change (e.g. Reconfigure, which reloads by itself):
        # reloading here as well would restart the integration twice.
        return
    await hass.config_entries.async_reload(entry.entry_id)
