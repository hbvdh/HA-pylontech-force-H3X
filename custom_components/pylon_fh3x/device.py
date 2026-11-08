"""Shared device-registry info for all entity platforms."""
from .const import DOMAIN, MANUFACTURER, MODEL


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
