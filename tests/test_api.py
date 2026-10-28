"""Tests for the Yamtrack API client."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.yamtrack.api import (
    YamtrackApi,
    YamtrackApiError,
    YamtrackAuthError,
    YamtrackConnectionError,
    YamtrackNotFoundError,
)

URL = "http://yamtrack.local:8000"


def make_api(hass: HomeAssistant) -> YamtrackApi:
    """Return a client without retry delays."""
    return YamtrackApi(async_get_clientsession(hass), URL + "/", "key", retry_delay=0)


async def test_search_sends_key_and_params(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Search uses the API key header and returns the results."""
    aioclient_mock.get(
        f"{URL}/api/v1/search/movie/",
        json={"pagination": {}, "results": [{"media_id": "1", "title": "Dune"}]},
    )
    results = await make_api(hass).search("movie", "Dune", 5)
    assert results == [{"media_id": "1", "title": "Dune"}]
    _, url, _, headers = aioclient_mock.mock_calls[0]
    assert headers["X-API-Key"] == "key"
    assert url.query == {"search": "Dune", "source": "tmdb", "limit": "5"}


async def test_retries_5xx(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """5xx responses are tried three times in total."""
    aioclient_mock.get(f"{URL}/api/v1/media/movie/tmdb/1/", status=502)
    with pytest.raises(YamtrackApiError):
        await make_api(hass).details("movie", "tmdb", "1")
    assert aioclient_mock.call_count == 3


async def test_no_retry_on_4xx(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """4xx responses are not retried."""
    aioclient_mock.get(f"{URL}/api/v1/media/movie/tmdb/1/", status=404)
    with pytest.raises(YamtrackNotFoundError):
        await make_api(hass).details("movie", "tmdb", "1")
    assert aioclient_mock.call_count == 1


async def test_auth_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """403 means a bad key."""
    aioclient_mock.get(f"{URL}/api/v1/media/", status=403)
    with pytest.raises(YamtrackAuthError):
        await make_api(hass).validate_auth()


async def test_connection_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Timeouts become connection errors."""
    aioclient_mock.get(f"{URL}/api/v1/health/", exc=TimeoutError)
    with pytest.raises(YamtrackConnectionError):
        await make_api(hass).health()


async def test_track_and_update(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Tracking posts to the media type, updating patches the media."""
    aioclient_mock.post(f"{URL}/api/v1/media/movie/", json={}, status=201)
    aioclient_mock.patch(f"{URL}/api/v1/media/movie/tmdb/1/", json={})
    api = make_api(hass)
    await api.track("movie", "tmdb", "1", 1)
    await api.update_status("movie", "tmdb", "1", 1)
    assert aioclient_mock.mock_calls[0][2] == {
        "source": "tmdb",
        "media_id": "1",
        "status": 1,
    }
    assert aioclient_mock.mock_calls[1][2] == {"status": 1}
