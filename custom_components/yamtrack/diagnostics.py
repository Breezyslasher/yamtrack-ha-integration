"""Diagnostics for Yamtrack."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_API_KEY

TO_REDACT = {CONF_API_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics with the API key redacted."""
    coordinator = entry.runtime_data.coordinator
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "cache_size": coordinator.cache_size,
        "library": {
            "watching": len(library.data.watching),
            "completed": len(library.data.completed),
            "up_next": [episode.as_dict() for episode in library.data.up_next],
            "unaired": len(library.data.unaired),
        }
        if (library := entry.runtime_data.library).data
        else None,
    }
