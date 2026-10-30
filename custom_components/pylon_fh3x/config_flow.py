"""Config flow for Pylontech Force H3X integration."""
import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN, DEFAULT_PORT, DEFAULT_NAME, CONF_CELL_DATA

_LOGGER = logging.getLogger(__name__)


DATA_SCHEMA = vol.Schema(
    {
        vol.Required("host"): str,
        vol.Required("port", default=DEFAULT_PORT): int,
    }
)

async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the supplied connection settings."""

    if len(data["host"]) < 3:
        raise CannotConnect

    # Use a stable, user-friendly title for the config entry.
    return {"title": DEFAULT_NAME}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Pylontech Force H3X."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Return the options flow."""
        return OptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Options: switch the optional BMS module/cell data on or off."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(CONF_CELL_DATA, False)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {vol.Required(CONF_CELL_DATA, default=current): bool}
            ),
        )
