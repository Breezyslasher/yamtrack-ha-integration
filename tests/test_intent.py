"""Tests for the YamtrackPlayMedia intent."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, entity_registry as er, intent

from custom_components.yamtrack.const import INTENT_PLAY_MEDIA


async def handle(hass: HomeAssistant, **slots: str) -> intent.IntentResponse:
    """Run the intent with the given slots."""
    return await intent.async_handle(
        hass,
        "test",
        INTENT_PLAY_MEDIA,
        {name: {"value": value} for name, value in slots.items()},
    )


async def test_intent_plays(
    hass: HomeAssistant, setup_entry, mock_api, launch_calls
) -> None:
    """The spoken reply names the provider."""
    response = await handle(hass, media="Dune")
    assert response.speech["plain"]["speech"] == "Playing Dune on Netflix."


async def test_intent_area_target(
    hass: HomeAssistant,
    setup_entry,
    mock_api,
    launch_calls,
    area_registry: ar.AreaRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """An area maps to the remote in that area."""
    bedroom = area_registry.async_create("Bedroom")
    entry = entity_registry.async_get_or_create(
        "remote", "androidtv_remote", "bedroom_tv"
    )
    entity_registry.async_update_entity(entry.entity_id, area_id=bedroom.id)

    await handle(hass, media="Dune", area="bedroom")
    assert launch_calls[0].data["entity_id"] == entry.entity_id


async def test_intent_tries_tv(
    hass: HomeAssistant, setup_entry, mock_api, launch_calls
) -> None:
    """A title that isn't a movie is looked up as a show."""

    async def search(media_type, query, limit):
        if media_type == "movie":
            return []
        return [
            {
                "media_id": "95396",
                "source": "tmdb",
                "media_type": "tv",
                "title": "Severance",
            }
        ]

    async def details(media_type, source, media_id):
        return {"title": "Severance", "tracked": False, "consumptions": []}

    mock_api["search"].side_effect = search
    mock_api["details"].side_effect = details
    response = await handle(hass, media="Severance")
    assert response.speech["plain"]["speech"] == "Playing Severance on Netflix."


async def test_intent_not_found(
    hass: HomeAssistant, setup_entry, mock_api, launch_calls
) -> None:
    """Unknown titles get a clear reply."""
    mock_api["search"].return_value = []
    response = await handle(hass, media="Nothing Here")
    assert response.speech["plain"]["speech"] == "I couldn't find Nothing Here."


async def test_intent_no_provider(
    hass: HomeAssistant, setup_entry, mock_api, launch_calls
) -> None:
    """Titles on no service say so."""
    setup_entry.runtime_data.coordinator.clear()
    hass.config_entries.async_update_entry(
        setup_entry,
        options={**setup_entry.options, "services": [], "plex_entity": None},
    )
    await hass.async_block_till_done()
    response = await handle(hass, media="Dune")
    assert response.speech["plain"]["speech"] == (
        "I couldn't find Dune on your services."
    )
