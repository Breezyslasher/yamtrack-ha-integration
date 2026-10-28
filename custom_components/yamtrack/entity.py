"""Base entity for Yamtrack."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_URL, DOMAIN
from .coordinator import YamtrackLibraryCoordinator


class YamtrackEntity(CoordinatorEntity[YamtrackLibraryCoordinator]):
    """An entity of the Yamtrack service device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: YamtrackLibraryCoordinator, entry: ConfigEntry, key: str
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Yamtrack",
            manufacturer="Yamtrack",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=entry.data[CONF_URL],
        )
