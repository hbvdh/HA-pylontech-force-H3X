"""Repair flow: confirm that a replaced H3X may take over this entry."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_SERIAL, DOMAIN


class ReplacementConfirmFlow(RepairsFlow):
    """Ask once whether the H3X that answers now is the replacement."""

    def __init__(self, entry_id: str | None, serial: str | None) -> None:
        self._entry_id = entry_id
        self._serial = serial

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            self._accept()
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={"found": str(self._serial)},
        )

    def _accept(self) -> None:
        """Continue with the new H3X; entities and history are kept."""
        if not self._entry_id or not self._serial:
            return
        coordinator = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if coordinator is not None:
            coordinator.accept_replacement(self._serial)
            self.hass.async_create_task(coordinator.async_request_refresh())
            return
        # Integration not loaded right now: store the serial number and let
        # a reload pick it up.
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is not None:
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_SERIAL: self._serial}
            )
            self.hass.config_entries.async_schedule_reload(self._entry_id)


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the fix flow for an identity_mismatch issue."""
    data = data or {}
    return ReplacementConfirmFlow(data.get("entry_id"), data.get("serial"))
