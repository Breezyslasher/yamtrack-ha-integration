"""Sensors: currently watching, up next and recently completed."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import LIBRARY_ITEMS
from .coordinator import LibraryData
from .entity import YamtrackEntity


def _watching_state(data: LibraryData) -> int:
    return len(data.watching)


def _watching_items(data: LibraryData) -> list[dict[str, Any]]:
    return data.watching[:LIBRARY_ITEMS]


def _up_next_state(data: LibraryData) -> str | None:
    if not data.up_next:
        return None
    episode = data.up_next[0]
    return f"{episode.show_title} {episode.label}"


def _up_next_items(data: LibraryData) -> list[dict[str, Any]]:
    return [episode.as_dict() for episode in data.up_next[:LIBRARY_ITEMS]]


def _completed_state(data: LibraryData) -> str | None:
    return data.completed[0]["title"] if data.completed else None


def _completed_items(data: LibraryData) -> list[dict[str, Any]]:
    return data.completed[:LIBRARY_ITEMS]


@dataclass(frozen=True, kw_only=True)
class YamtrackSensorDescription(SensorEntityDescription):
    """Describes a Yamtrack sensor."""

    value_fn: Callable[[LibraryData], Any]
    items_fn: Callable[[LibraryData], list[dict[str, Any]]]


SENSORS = (
    YamtrackSensorDescription(
        key="currently_watching",
        translation_key="currently_watching",
        native_unit_of_measurement="titles",
        value_fn=_watching_state,
        items_fn=_watching_items,
    ),
    YamtrackSensorDescription(
        key="up_next",
        translation_key="up_next",
        value_fn=_up_next_state,
        items_fn=_up_next_items,
    ),
    YamtrackSensorDescription(
        key="recently_completed",
        translation_key="recently_completed",
        value_fn=_completed_state,
        items_fn=_completed_items,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the sensors."""
    library = entry.runtime_data.library
    async_add_entities(
        YamtrackSensor(library, entry, description) for description in SENSORS
    )


class YamtrackSensor(YamtrackEntity, SensorEntity):
    """A sensor built from the Yamtrack library."""

    entity_description: YamtrackSensorDescription

    def __init__(self, coordinator, entry, description) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the state."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the items behind the state."""
        return {"items": self.entity_description.items_fn(self.coordinator.data)}

    @property
    def entity_picture(self) -> str | None:
        """Return the image of the first item."""
        items = self.entity_description.items_fn(self.coordinator.data)
        return items[0].get("image") if items else None
