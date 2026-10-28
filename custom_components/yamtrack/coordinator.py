"""Caching layer in front of the Yamtrack API."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging
from typing import Any

from homeassistant.util import dt as dt_util

from .api import YamtrackApi
from .const import CACHE_TTL, NO_LINKS_WARNING_INTERVAL

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
