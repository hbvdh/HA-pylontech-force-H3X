"""Shared device-registry info for all entity platforms."""
from .const import CONF_HOST, CONF_UNIQUE_ID_BASE, DOMAIN, MANUFACTURER, MODEL


def unique_id_base(entry) -> str:
    """Stable part of every entity unique ID of this config entry.

    Existing installations keep exactly the unique IDs they already have: the
    base is the host the entry was created with. Reconfigure stores that host
    as CONF_UNIQUE_ID_BASE before it changes the host, so the IDs stay the same.
    """
    return entry.data.get(CONF_UNIQUE_ID_BASE) or entry.data[CONF_HOST]


def device_info(coordinator, entry) -> dict:
    """Device info with firmware/model from the coordinator when available."""
    builder = getattr(coordinator, "build_device_info", None)
    if callable(builder):
        return builder(entry)
    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": entry.title,
        "manufacturer": MANUFACTURER,
        "model": MODEL,
    }
