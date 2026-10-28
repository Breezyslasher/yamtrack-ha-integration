"""Fixtures for the Yamtrack tests."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant, ServiceCall

from custom_components.yamtrack.const import (
    CONF_API_KEY,
    CONF_INCLUDE_FREE,
    CONF_LAUNCH_METHOD,
    CONF_PLEX_ENTITY,
    CONF_PREFER_LIBRARY,
    CONF_REGION,
    CONF_SERVICES,
    CONF_TARGET,
    CONF_TRACK,
    CONF_URL,
    DOMAIN,
)

URL = "http://yamtrack.local:8000"
API_KEY = "secret-key"

SEARCH_DUNE = [
    {
        "media_id": "438631",
        "source": "tmdb",
        "media_type": "movie",
        "title": "Dune",
        "image": "https://img/dune2021.jpg",
    },
    {
        "media_id": "841",
        "source": "tmdb",
        "media_type": "movie",
        "title": "Dune",
        "image": "https://img/dune1984.jpg",
    },
    {
        "media_id": "693134",
        "source": "tmdb",
        "media_type": "movie",
        "title": "Dune: Part Two",
        "image": None,
    },
]

DETAILS = {
    "438631": {
        "title": "Dune",
        "image": "https://img/dune2021.jpg",
        "tracked": False,
        "consumptions": [],
        "details": {"release_date": "2021-09-15", "runtime": "2h 35m"},
    },
    "841": {
        "title": "Dune",
        "image": "https://img/dune1984.jpg",
        "tracked": False,
        "consumptions": [],
        "details": {"release_date": "1984-12-14"},
    },
    "693134": {
        "title": "Dune: Part Two",
        "image": None,
        "tracked": False,
        "consumptions": [],
        "details": {"release_date": "2024-02-27"},
    },
}


def provider(name: str, priority: int, link: str | None) -> dict[str, Any]:
    """Build a provider entry like the Yamtrack API returns."""
    return {
        "provider_id": priority,
        "provider_name": name,
        "image": None,
        "display_priority": priority,
        "link": link,
        "rent_price": None,
        "buy_price": None,
        "prices": None,
    }


PROVIDERS = {
    "region": "US",
    "link": "https://www.themoviedb.org/movie/438631/watch",
    "no_charge": [
        provider("Max", 1, "https://play.max.com/movie/abc"),
        provider("Netflix", 0, "http://www.netflix.com/title/81000001"),
    ],
    "paid": [provider("Apple TV", 4, "https://tv.apple.com/movie/dune")],
}

DEFAULT_OPTIONS = {
    CONF_REGION: "US",
    CONF_SERVICES: ["Netflix", "Hulu"],
    CONF_INCLUDE_FREE: False,
    CONF_PREFER_LIBRARY: False,
    CONF_TARGET: "remote.living_room_tv",
    CONF_LAUNCH_METHOD: "android_tv_remote",
    CONF_PLEX_ENTITY: "media_player.plex_living_room",
    CONF_TRACK: True,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom integrations in every test."""


@pytest.fixture
def options() -> dict[str, Any]:
    """Return the entry options, tests may change them before setup."""
    return dict(DEFAULT_OPTIONS)


@pytest.fixture
def mock_api() -> Generator[dict[str, AsyncMock]]:
    """Patch the Yamtrack client methods."""

    async def details(media_type, source, media_id):
        return dict(DETAILS[str(media_id)])

    mocks = {
        "health": AsyncMock(return_value={"status": "ok"}),
        "validate_auth": AsyncMock(return_value=None),
        "search": AsyncMock(return_value=SEARCH_DUNE),
        "details": AsyncMock(side_effect=details),
        "providers": AsyncMock(return_value=PROVIDERS),
        "track": AsyncMock(return_value={}),
        "update_status": AsyncMock(return_value={}),
    }
    with patch.multiple("custom_components.yamtrack.api.YamtrackApi", **mocks):
        yield mocks


@pytest.fixture
def launch_calls(hass: HomeAssistant) -> list[ServiceCall]:
    """Register fake launcher actions and record their calls."""
    calls: list[ServiceCall] = []

    async def record(call: ServiceCall) -> None:
        calls.append(call)

    hass.services.async_register("remote", "turn_on", record)
    hass.services.async_register("androidtv", "adb_command", record)
    hass.services.async_register("media_player", "play_media", record)
    return calls


@pytest.fixture
async def setup_entry(
    hass: HomeAssistant, mock_api: dict[str, AsyncMock], options: dict[str, Any]
) -> MockConfigEntry:
    """Set up a Yamtrack config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_URL: URL, CONF_API_KEY: API_KEY},
        options=options,
        unique_id=URL,
        title=URL,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
