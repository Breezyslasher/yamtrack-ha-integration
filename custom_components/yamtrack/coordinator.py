"""Caching layer in front of the Yamtrack API."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import YamtrackApi, YamtrackAuthError, YamtrackError
from .const import (
    CACHE_TTL,
    LIBRARY_ITEMS,
    LIBRARY_UPDATE_INTERVAL,
    MEDIA_TV,
    NO_LINKS_WARNING_INTERVAL,
    NON_VIDEO_TYPES,
    SOURCE_TMDB,
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    UP_NEXT_MAX_SHOWS,
)
from .next_episode import (
    NextEpisode,
    Unaired,
    async_find_next_episode,
    async_unaired_episodes,
)

_LOGGER = logging.getLogger(__name__)


class YamtrackCoordinator:
    """Cache search, details and provider responses for six hours."""

    def __init__(self, api: YamtrackApi) -> None:
        """Initialize the cache."""
        self.api = api
        self._cache: dict[tuple, tuple[Any, Any]] = {}
        self._last_no_links_warning = None

    async def _cached(self, key: tuple, fetch: Callable[[], Awaitable[Any]]) -> Any:
        """Return a fresh cached value or fetch and store it. Errors are not cached."""
        now = dt_util.utcnow()
        if (hit := self._cache.get(key)) is not None and hit[0] > now:
            return hit[1]
        value = await fetch()
        self._cache[key] = (now + CACHE_TTL, value)
        return value

    async def search(
        self, media_type: str, query: str, limit: int
    ) -> list[dict[str, Any]]:
        """Search, cached per query."""
        key = ("search", media_type, query.casefold().strip(), limit)
        return await self._cached(
            key, lambda: self.api.search(media_type, query, limit)
        )

    async def details(
        self, media_type: str, source: str, media_id: str
    ) -> dict[str, Any]:
        """Media details, cached per ID."""
        key = ("details", media_type, source, str(media_id))
        return await self._cached(
            key, lambda: self.api.details(media_type, source, str(media_id))
        )

    async def providers(
        self, media_type: str, source: str, media_id: str, region: str
    ) -> dict[str, Any]:
        """Watch providers, cached per ID and region."""
        key = ("providers", media_type, source, str(media_id), region)
        data = await self._cached(
            key,
            lambda: self.api.providers(media_type, source, str(media_id), region),
        )
        self._check_links(data)
        return data

    def _check_links(self, data: dict[str, Any]) -> None:
        """Warn once a day when providers come back without any deep link."""
        offers = [*(data.get("no_charge") or []), *(data.get("paid") or [])]
        if not offers or any(offer.get("link") for offer in offers):
            return
        now = dt_util.utcnow()
        last = self._last_no_links_warning
        if last is None or now - last >= NO_LINKS_WARNING_INTERVAL:
            self._last_no_links_warning = now
            _LOGGER.warning(
                "Yamtrack returned watch providers without links. JustWatch may "
                "be down or JUSTWATCH_ENABLED is off; falling back to Plex"
            )

    def clear(self) -> None:
        """Drop every cached response."""
        self._cache.clear()

    @property
    def cache_size(self) -> int:
        """Return the number of cached responses."""
        return len(self._cache)


def media_item(entry: dict[str, Any]) -> dict[str, Any]:
    """Flatten a tracked media from the Yamtrack media list."""
    item = entry.get("item") or {}
    return {
        "media_id": str(item.get("media_id")),
        "source": item.get("source"),
        "media_type": item.get("media_type"),
        "title": item.get("title"),
        "image": item.get("image"),
        "progress": entry.get("progress"),
        "score": entry.get("score"),
        "end_date": entry.get("end_date"),
    }


@dataclass
class LibraryData:
    """What the sensors and the media browser show."""

    watching: list[dict[str, Any]] = field(default_factory=list)
    completed: list[dict[str, Any]] = field(default_factory=list)
    up_next: list[NextEpisode] = field(default_factory=list)
    unaired: Unaired = field(default_factory=set)


class YamtrackLibraryCoordinator(DataUpdateCoordinator[LibraryData]):
    """Poll what is being watched, up next and recently completed."""

    def __init__(self, hass: HomeAssistant, api: YamtrackApi) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Yamtrack library",
            update_interval=LIBRARY_UPDATE_INTERVAL,
        )
        self.api = api

    async def _async_update_data(self) -> LibraryData:
        """Fetch the library from Yamtrack."""
        try:
            watching, completed, unaired = await asyncio.gather(
                self.api.media_list(
                    status=STATUS_IN_PROGRESS,
                    sort="updated_desc",
                    limit=LIBRARY_ITEMS * 2,
                    exclude=NON_VIDEO_TYPES,
                ),
                self.api.media_list(
                    status=STATUS_COMPLETED,
                    sort="end_date_desc",
                    limit=LIBRARY_ITEMS,
                    exclude=NON_VIDEO_TYPES,
                ),
                async_unaired_episodes(self.api),
            )
        except YamtrackAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except YamtrackError as err:
            raise UpdateFailed(str(err)) from err

        watching_items = [media_item(entry) for entry in watching]
        # Manually added shows have no TMDB seasons to look through
        shows = [
            i
            for i in watching_items
            if i["media_type"] == MEDIA_TV and i["source"] == SOURCE_TMDB
        ]
        found = await asyncio.gather(
            *(
                async_find_next_episode(self.api, show["media_id"], unaired=unaired)
                for show in shows[:UP_NEXT_MAX_SHOWS]
            ),
            return_exceptions=True,
        )
        up_next = []
        for show, episode in zip(shows, found, strict=False):
            if isinstance(episode, Exception):
                _LOGGER.debug("No next episode for %s: %s", show["title"], episode)
            elif episode is not None:
                up_next.append(episode)

        return LibraryData(
            watching=watching_items,
            completed=[media_item(entry) for entry in completed],
            up_next=up_next,
            unaired=unaired,
        )
