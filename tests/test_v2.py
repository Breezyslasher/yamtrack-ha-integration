"""Tests for next episodes, the library sensors and the media browser."""

from __future__ import annotations

from datetime import timedelta
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from homeassistant.components.media_player import DATA_COMPONENT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent
from homeassistant.util import dt as dt_util

from .conftest import DETAILS
from custom_components.yamtrack.const import DOMAIN, INTENT_PLAY_MEDIA
from custom_components.yamtrack.next_episode import (
    async_find_next_episode,
    async_unaired_episodes,
)

SEVERANCE = {
    "title": "Severance",
    "image": "https://img/severance.jpg",
    "tracked": True,
    "consumptions": [{"status": 1}],
    "details": {"first_air_date": "2022-02-17", "last_episode_season": 2},
    "related": {
        "seasons": [
            {"item": {"season_number": 0}, "status": None},
            {"item": {"season_number": 2}, "status": 1},
            {"item": {"season_number": 1}, "status": 3},
            {"item": {"season_number": 3}, "status": None},
        ]
    },
}


def episode(number: int, *, watched: bool, title: str | None = None) -> dict:
    """Build a season episode like the Yamtrack API returns."""
    return {
        "item": {
            "media_id": "95396",
            "media_type": "episode",
            "season_number": 2,
            "episode_number": number,
            "title": title or f"Episode {number}",
            "image": f"https://img/s2e{number}.jpg",
        },
        "tracked": watched,
    }


SEASON_2 = [
    episode(3, watched=False, title="Who Is Alive?"),
    episode(1, watched=True),
    episode(2, watched=True),
]


def media(media_type: str, media_id: str, title: str, **extra: Any) -> dict:
    """Build a media list entry."""
    return {
        "item": {
            "media_id": media_id,
            "source": "tmdb",
            "media_type": media_type,
            "title": title,
            "image": f"https://img/{media_id}.jpg",
        },
        "progress": extra.get("progress", 0),
        "score": extra.get("score"),
        "end_date": extra.get("end_date"),
    }


WATCHING = [media("tv", "95396", "Severance"), media("movie", "438631", "Dune")]
MANUAL_SHOW = {
    **media("tv", "1", "Home videos"),
    "item": {
        **media("tv", "1", "x")["item"],
        "source": "manual",
        "title": "Home videos",
    },
}
COMPLETED = [
    media("movie", "603", "The Matrix", score=9, end_date="2026-10-20T20:00:00Z"),
]
WATCHLIST = [media("movie", "841", "Dune"), media("tv", "1396", "Breaking Bad")]


@pytest.fixture
def library_api(mock_api: dict[str, AsyncMock]) -> dict[str, AsyncMock]:
    """Give the mocked API a library with a show in progress."""

    async def details(media_type, source, media_id):
        if str(media_id) == "95396":
            return SEVERANCE
        return dict(DETAILS[str(media_id)])

    async def media_list(*, status=None, sort=None, limit=20, exclude=None):
        return {0: WATCHLIST, 1: [*WATCHING, MANUAL_SHOW], 3: COMPLETED}.get(status, [])

    async def season_episodes(source, media_id, season_number):
        return SEASON_2 if season_number == 2 else []

    async def search(media_type, query, limit):
        if media_type == "tv":
            return [
                {
                    "media_id": "95396",
                    "source": "tmdb",
                    "media_type": "tv",
                    "title": "Severance",
                }
            ]
        return []

    mock_api["details"].side_effect = details
    mock_api["media_list"].side_effect = media_list
    mock_api["season_episodes"].side_effect = season_episodes
    mock_api["search"].side_effect = search
    mock_api["lists"].return_value = [
        {"id": 7, "name": "Movie night", "image": "https://img/list.jpg"}
    ]
    mock_api["list_items"].return_value = [
        media("movie", "603", "The Matrix"),
        media("book", "1", "Dune (novel)"),
    ]
    return mock_api


class FakeApi:
    """Just enough of YamtrackApi for the next episode lookup."""

    def __init__(self, details=SEVERANCE, episodes=SEASON_2, events=()):
        self._details = details
        self._episodes = episodes
        self._events = list(events)
        self.seasons_read: list[int] = []

    async def details(self, media_type, source, media_id):
        return self._details

    async def season_episodes(self, source, media_id, season_number):
        self.seasons_read.append(season_number)
        return self._episodes if season_number == 2 else []

    async def calendar(self, start_date, end_date, limit=200):
        return self._events


async def test_next_episode_skips_watched_specials_and_completed() -> None:
    """The first unwatched episode of the first unfinished season is next."""
    api = FakeApi()
    found = await async_find_next_episode(api, "95396")
    assert found.label == "S02E03"
    assert found.episode_title == "Who Is Alive?"
    assert found.show_title == "Severance"
    # Specials and the completed first season aren't read, nor seasons not aired
    assert api.seasons_read == [2]


async def test_next_episode_none_when_untracked_or_unaired() -> None:
    """Untracked shows and episodes that haven't aired have no next episode."""
    assert (
        await async_find_next_episode(FakeApi({**SEVERANCE, "tracked": False}), "95396")
        is None
    )
    assert (
        await async_find_next_episode(FakeApi(), "95396", unaired={("95396", 2, 3)})
        is None
    )


async def test_next_episode_none_when_all_watched() -> None:
    """A show with everything aired watched has no next episode."""
    api = FakeApi(episodes=[episode(1, watched=True)])
    assert await async_find_next_episode(api, "95396") is None


async def test_unaired_from_calendar() -> None:
    """Only future calendar releases count as unaired."""
    now = dt_util.utcnow()
    api = FakeApi(
        events=[
            {
                "item": {"media_id": "95396", "season_number": 2, "episode_number": 4},
                "datetime": (now + timedelta(days=3)).isoformat(),
            },
            {
                "item": {"media_id": "95396", "season_number": 2, "episode_number": 3},
                "datetime": (now - timedelta(days=3)).isoformat(),
            },
            {
                "item": {"media_id": "1", "season_number": 1},
                "content_number": 5,
                "datetime": (now + timedelta(days=1)).isoformat(),
            },
        ]
    )
    assert await async_unaired_episodes(api) == {("95396", 2, 4), ("1", 1, 5)}


async def test_sensors(hass: HomeAssistant, library_api, setup_entry) -> None:
    """The sensors show what is being watched, up next and completed."""
    watching = hass.states.get("sensor.yamtrack_currently_watching")
    assert watching.state == "3"
    assert [i["title"] for i in watching.attributes["items"]] == [
        "Severance",
        "Dune",
        "Home videos",
    ]

    up_next = hass.states.get("sensor.yamtrack_up_next")
    assert up_next.state == "Severance S02E03"
    assert up_next.attributes["items"][0]["episode_title"] == "Who Is Alive?"
    assert up_next.attributes["entity_picture"] == "https://img/s2e3.jpg"

    completed = hass.states.get("sensor.yamtrack_recently_completed")
    assert completed.state == "The Matrix"
    assert completed.attributes["items"][0]["score"] == 9


async def test_play_show_next_episode_from_plex(
    hass: HomeAssistant, library_api, options, launch_calls
) -> None:
    """A show plays its next episode from Plex."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    options["prefer_library"] = True
    entry = MockConfigEntry(
        domain=DOMAIN, data={"url": "http://x", "api_key": "k"}, options=options
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)

    response = await intent.async_handle(
        hass, "test", INTENT_PLAY_MEDIA, {"media": {"value": "Severance"}}
    )
    assert response.speech["plain"]["speech"] == (
        "Playing Severance season 2 episode 3 from Plex."
    )
    payload = json.loads(
        launch_calls[0].data["media_content_id"].removeprefix("plex://")
    )
    assert payload == {
        "library_name": "TV Shows",
        "show.title": "Severance",
        "season.index": 2,
        "episode.index": 3,
        "libtype": "episode",
    }


async def test_play_show_streaming_reports_episode(
    hass: HomeAssistant, library_api, setup_entry, launch_calls
) -> None:
    """Streaming opens the show and the response names the next episode."""
    response = await hass.services.async_call(
        DOMAIN,
        "play",
        {"media_id": "95396", "media_type": "tv"},
        blocking=True,
        return_response=True,
    )
    assert response["provider"] == "Netflix"
    assert (response["season_number"], response["episode_number"]) == (2, 3)
    assert response["episode_title"] == "Who Is Alive?"
    assert launch_calls[0].domain == "remote"


async def test_play_explicit_episode(
    hass: HomeAssistant, library_api, options, launch_calls
) -> None:
    """A given season and episode is played instead of the next one."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    options["prefer_library"] = True
    entry = MockConfigEntry(
        domain=DOMAIN, data={"url": "http://x", "api_key": "k"}, options=options
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)

    response = await hass.services.async_call(
        DOMAIN,
        "play",
        {"query": "Severance", "media_type": "tv", "season": 1, "episode": 1},
        blocking=True,
        return_response=True,
    )
    assert (response["season_number"], response["episode_number"]) == (1, 1)
    payload = json.loads(
        launch_calls[0].data["media_content_id"].removeprefix("plex://")
    )
    assert (payload["season.index"], payload["episode.index"]) == (1, 1)


async def test_movie_response_has_no_episode(
    hass: HomeAssistant, setup_entry, mock_api, launch_calls
) -> None:
    """Movies don't get episode fields."""
    response = await hass.services.async_call(
        DOMAIN, "play", {"query": "Dune"}, blocking=True, return_response=True
    )
    assert "season_number" not in response


def launcher(hass: HomeAssistant):
    """Return the Yamtrack media player entity."""
    return hass.data[DATA_COMPONENT].get_entity("media_player.yamtrack")


async def test_browse(hass: HomeAssistant, library_api, setup_entry) -> None:
    """The media browser shows the Yamtrack folders and titles."""
    player = launcher(hass)

    root = await player.async_browse_media()
    assert [c.title for c in root.children] == [
        "Up next",
        "Currently watching",
        "Watchlist",
        "Lists",
    ]

    up_next = await player.async_browse_media(None, "up_next")
    assert up_next.children[0].title == "Severance · S02E03 · Who Is Alive?"
    assert up_next.children[0].media_content_id == "tv/tmdb/95396/2/3"

    watching = await player.async_browse_media(None, "watching")
    assert [c.media_content_id for c in watching.children] == [
        "tv/tmdb/95396",
        "movie/tmdb/438631",
    ]

    watchlist = await player.async_browse_media(None, "watchlist")
    assert [c.title for c in watchlist.children] == ["Dune", "Breaking Bad"]
    assert library_api["media_list"].await_args.kwargs["status"] == 0

    lists = await player.async_browse_media(None, "lists")
    assert lists.children[0].media_content_id == "list/7"
    items = await player.async_browse_media(None, "list/7")
    # Books and other non-video items are left out
    assert [c.title for c in items.children] == ["The Matrix"]

    with pytest.raises(HomeAssistantError):
        await player.async_browse_media(None, "nope")


async def test_media_player_plays_on_tv(
    hass: HomeAssistant, library_api, setup_entry, launch_calls
) -> None:
    """Playing from the browser launches on the TV and shows the title."""
    player = launcher(hass)

    await player.async_play_media("movie", "movie/tmdb/438631")
    await hass.async_block_till_done()
    assert launch_calls[0].data["activity"] == "https://www.netflix.com/watch/81000001"
    state = hass.states.get("media_player.yamtrack")
    assert state.attributes["media_title"] == "Dune"

    await player.async_play_media("episode", "tv/tmdb/95396/2/3")
    await hass.async_block_till_done()
    state = hass.states.get("media_player.yamtrack")
    assert state.attributes["media_title"] == "Severance S02E03"

    with pytest.raises(HomeAssistantError):
        await player.async_play_media("movie", "book/x/1")
