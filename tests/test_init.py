"""Tests for the yamtrack.search and yamtrack.play actions."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError

from .conftest import PROVIDERS
from custom_components.yamtrack.api import YamtrackAuthError
from custom_components.yamtrack.const import (
    CONF_INCLUDE_FREE,
    CONF_LAUNCH_METHOD,
    CONF_PREFER_LIBRARY,
    CONF_SERVICES,
    DOMAIN,
    EVENT_PLAY_STARTED,
)


async def play(hass: HomeAssistant, **data: Any) -> dict[str, Any]:
    """Call yamtrack.play and return its response."""
    return await hass.services.async_call(
        DOMAIN, "play", data, blocking=True, return_response=True
    )


async def test_setup_auth_failure(hass: HomeAssistant, mock_api) -> None:
    """A rejected key starts reauth."""
    mock_api["validate_auth"].side_effect = YamtrackAuthError("bad")
    entry = MockConfigEntry(domain=DOMAIN, data={"url": "http://x", "api_key": "k"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert any(entry.async_get_active_flows(hass, {"reauth"}))


async def test_search(hass: HomeAssistant, setup_entry, mock_api) -> None:
    """Search returns years and providers."""
    response = await hass.services.async_call(
        DOMAIN,
        "search",
        {"query": "Dune 1984"},
        blocking=True,
        return_response=True,
    )
    results = response["results"]
    assert results[0]["media_id"] == "841"
    assert results[0]["year"] == 1984
    assert results[0]["providers"][1] == {
        "name": "Netflix",
        "link": "https://www.netflix.com/watch/81000001",
        "kind": "subscription",
    }
    assert results[0]["providers"][0]["kind"] == "other"
    mock_api["search"].assert_awaited_with("movie", "Dune", 5)


async def test_play_streaming(
    hass: HomeAssistant, setup_entry, mock_api, launch_calls: list[ServiceCall]
) -> None:
    """A title on my services opens its deep link and is tracked."""
    events = async_capture_events(hass, EVENT_PLAY_STARTED)
    response = await play(hass, query="Dune")

    assert response == {
        "title": "Dune",
        "played_via": "streaming",
        "provider": "Netflix",
        "link": "https://www.netflix.com/watch/81000001",
        "media_id": "438631",
        "media_type": "movie",
    }
    assert len(launch_calls) == 1
    assert launch_calls[0].domain == "remote"
    assert launch_calls[0].data == {
        "entity_id": "remote.living_room_tv",
        "activity": "https://www.netflix.com/watch/81000001",
    }
    mock_api["track"].assert_awaited_once_with("movie", "tmdb", "438631", 1)
    await hass.async_block_till_done()
    assert events[0].data["provider"] == "Netflix"


async def test_play_caches_responses(
    hass: HomeAssistant, setup_entry, mock_api, launch_calls
) -> None:
    """Search and providers are served from the cache the second time."""
    await play(hass, query="Dune")
    await play(hass, query="dune")
    assert mock_api["search"].await_count == 1
    assert mock_api["providers"].await_count == 1


async def test_play_updates_tracked_media(
    hass: HomeAssistant, setup_entry, mock_api, launch_calls
) -> None:
    """A tracked media that isn't in progress is patched."""

    async def details(media_type, source, media_id):
        return {"title": "Dune", "tracked": True, "consumptions": [{"status": 3}]}

    mock_api["details"].side_effect = details
    await play(hass, media_id="438631")
    mock_api["track"].assert_not_awaited()
    mock_api["update_status"].assert_awaited_once_with("movie", "tmdb", "438631", 1)


async def test_play_adb(hass: HomeAssistant, options, mock_api, launch_calls) -> None:
    """ADB launches an intent with the quoted link."""
    options[CONF_LAUNCH_METHOD] = "adb"
    options["target"] = "media_player.fire_tv"
    entry = MockConfigEntry(
        domain=DOMAIN, data={"url": "http://x", "api_key": "k"}, options=options
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)

    await play(hass, query="Dune")
    assert launch_calls[0].domain == "androidtv"
    assert launch_calls[0].data["command"] == (
        "am start -a android.intent.action.VIEW -d "
        "https://www.netflix.com/watch/81000001"
    )


async def test_play_falls_back_to_plex(
    hass: HomeAssistant, options, mock_api, launch_calls
) -> None:
    """With no offer on my services, the Plex library is used."""
    options[CONF_SERVICES] = ["Hulu"]
    entry = MockConfigEntry(
        domain=DOMAIN, data={"url": "http://x", "api_key": "k"}, options=options
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)

    response = await play(hass, query="Dune")
    assert response["played_via"] == "plex"
    assert launch_calls[0].domain == "media_player"
    assert launch_calls[0].data["media_content_id"].startswith("plex://")


async def test_play_library_first(
    hass: HomeAssistant, options, mock_api, launch_calls
) -> None:
    """Prefer library first tries Plex before fetching providers."""
    options[CONF_PREFER_LIBRARY] = True
    entry = MockConfigEntry(
        domain=DOMAIN, data={"url": "http://x", "api_key": "k"}, options=options
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)

    response = await play(hass, query="Dune")
    assert response["played_via"] == "plex"
    mock_api["providers"].assert_not_awaited()


async def test_play_library_miss_uses_streaming(
    hass: HomeAssistant, options, mock_api
) -> None:
    """When Plex doesn't have the title, streaming is next."""
    options[CONF_PREFER_LIBRARY] = True
    entry = MockConfigEntry(
        domain=DOMAIN, data={"url": "http://x", "api_key": "k"}, options=options
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)

    async def plex_fails(call: ServiceCall) -> None:
        raise HomeAssistantError("No movie results in 'Movies'")

    remote = AsyncMock()
    hass.services.async_register("media_player", "play_media", plex_fails)
    hass.services.async_register("remote", "turn_on", remote)

    response = await play(hass, query="Dune")
    assert response["played_via"] == "streaming"
    assert response["provider"] == "Netflix"


async def test_play_not_found(
    hass: HomeAssistant, setup_entry, mock_api, launch_calls
) -> None:
    """Poor matches return not_found."""
    response = await play(hass, query="The Notebook")
    assert response["error"] == "not_found"
    assert not launch_calls


async def test_play_no_provider(
    hass: HomeAssistant, options, mock_api, launch_calls
) -> None:
    """Without offers or Plex the result is no_provider."""
    options[CONF_SERVICES] = []
    options.pop("plex_entity")
    entry = MockConfigEntry(
        domain=DOMAIN, data={"url": "http://x", "api_key": "k"}, options=options
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)

    response = await play(hass, query="Dune")
    assert response["error"] == "no_provider"
    mock_api["track"].assert_not_awaited()


async def test_play_launch_failed(hass: HomeAssistant, setup_entry, mock_api) -> None:
    """Launch errors are reported, and raise without a response."""

    async def fail(call: ServiceCall) -> None:
        raise HomeAssistantError("TV is off")

    hass.services.async_register("remote", "turn_on", fail)
    hass.services.async_register("media_player", "play_media", fail)

    response = await play(hass, query="Dune")
    assert response["error"] == "launch_failed"
    assert "TV is off" in response["message"]

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "play", {"query": "Dune"}, blocking=True)


async def test_play_free_service(
    hass: HomeAssistant, options, mock_api, launch_calls
) -> None:
    """Free services are used when enabled."""
    options[CONF_SERVICES] = []
    options[CONF_INCLUDE_FREE] = True
    options.pop("plex_entity")
    mock_api["providers"].return_value = {
        **PROVIDERS,
        "no_charge": [
            {
                "provider_name": "Pluto TV",
                "display_priority": 5,
                "link": "https://pluto.tv/x",
            }
        ],
    }
    entry = MockConfigEntry(
        domain=DOMAIN, data={"url": "http://x", "api_key": "k"}, options=options
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)

    response = await play(hass, query="Dune")
    assert response["provider"] == "Pluto TV"


async def test_no_links_warns_once(
    hass: HomeAssistant, setup_entry, mock_api, launch_calls, caplog
) -> None:
    """Providers without links log one warning."""
    mock_api["providers"].return_value = {
        "no_charge": [
            {"provider_name": "Netflix", "display_priority": 0, "link": None}
        ],
        "paid": [],
    }
    await play(hass, query="Dune")
    setup_entry.runtime_data.coordinator.clear()
    await play(hass, query="Dune")
    assert caplog.text.count("without links") == 1


async def test_diagnostics_redacts_key(hass: HomeAssistant, setup_entry) -> None:
    """The API key is redacted."""
    from custom_components.yamtrack.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    diagnostics = await async_get_config_entry_diagnostics(hass, setup_entry)
    assert diagnostics["entry"]["data"]["api_key"] == "**REDACTED**"
