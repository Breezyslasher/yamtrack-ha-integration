"""Async client for the Yamtrack REST API."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from .const import REQUEST_RETRIES, REQUEST_TIMEOUT, SOURCE_TMDB

_LOGGER = logging.getLogger(__name__)

API_PREFIX = "/api/v1"


class YamtrackError(Exception):
    """Base error for the Yamtrack API."""


class YamtrackConnectionError(YamtrackError):
    """Yamtrack could not be reached."""


class YamtrackAuthError(YamtrackError):
    """The API key was rejected."""


class YamtrackNotFoundError(YamtrackError):
    """The requested resource does not exist."""


class YamtrackApiError(YamtrackError):
    """Yamtrack answered with an unexpected error."""

    def __init__(self, status: int, message: str) -> None:
        """Store the HTTP status with the message."""
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class YamtrackApi:
    """Thin wrapper around the Yamtrack endpoints the integration uses."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        api_key: str,
        *,
        retry_delay: float = 1.0,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._base = url.rstrip("/")
        self._api_key = api_key
        self._retry_delay = retry_delay

    @property
    def url(self) -> str:
        """Return the base URL of the Yamtrack instance."""
        return self._base

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        auth: bool = True,
        retries: int = REQUEST_RETRIES,
        allow_status: tuple[int, ...] = (),
    ) -> Any:
        """Send a request, retrying on 5xx responses only.

        Statuses in allow_status return their JSON body instead of raising.
        """
        url = f"{self._base}{API_PREFIX}{path}"
        headers = {"Accept": "application/json"}
        if auth:
            headers["X-API-Key"] = self._api_key

        for attempt in range(retries + 1):
            try:
                async with self._session.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                ) as resp:
                    if resp.status in allow_status:
                        return await resp.json(content_type=None)
                    if resp.status >= 500 and attempt < retries:
                        _LOGGER.debug(
                            "Yamtrack %s %s returned %s, retrying",
                            method,
                            path,
                            resp.status,
                        )
                        await asyncio.sleep(self._retry_delay * (attempt + 1))
                        continue
                    if resp.status in (401, 403):
                        raise YamtrackAuthError("Invalid API key")
                    if resp.status == 404:
                        raise YamtrackNotFoundError(path)
                    if resp.status >= 400:
                        raise YamtrackApiError(resp.status, await resp.text())
                    if resp.status == 204:
                        return None
                    return await resp.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError) as err:
                raise YamtrackConnectionError(
                    f"Error talking to Yamtrack at {self._base}: {err}"
                ) from err

        # The loop always returns or raises; this keeps type checkers happy
        raise YamtrackConnectionError(f"Yamtrack at {self._base} kept failing")

    async def health(self) -> dict[str, Any]:
        """Return the health check report.

        Yamtrack answers 500 with the report when one of its checks (database,
        cache, Celery, Redis) fails, so that body is returned, not raised.
        """
        try:
            return await self._request(
                "GET", "/health/", auth=False, retries=0, allow_status=(500,)
            )
        except ValueError:
            # A 500 that isn't JSON, like a proxy error page
            raise YamtrackApiError(500, "Health check failed") from None

    async def validate_auth(self) -> None:
        """Make one authenticated call to check the API key."""
        await self._request("GET", "/media/", params={"limit": 1})

    async def search(
        self, media_type: str, query: str, limit: int
    ) -> list[dict[str, Any]]:
        """Search TMDB through Yamtrack."""
        data = await self._request(
            "GET",
            f"/search/{media_type}/",
            params={"search": query, "source": SOURCE_TMDB, "limit": limit},
        )
        return list((data or {}).get("results") or [])

    async def details(
        self, media_type: str, source: str, media_id: str
    ) -> dict[str, Any]:
        """Return the metadata and the tracking state of a media."""
        return await self._request("GET", f"/media/{media_type}/{source}/{media_id}/")

    async def providers(
        self, media_type: str, source: str, media_id: str, region: str
    ) -> dict[str, Any]:
        """Return the no charge and paid watch providers of a media."""
        return await self._request(
            "GET",
            f"/media/{media_type}/{source}/{media_id}/providers/",
            params={"region": region},
        )

    async def track(
        self, media_type: str, source: str, media_id: str, status: int
    ) -> dict[str, Any]:
        """Start tracking a media with a status."""
        return await self._request(
            "POST",
            f"/media/{media_type}/",
            json={"source": source, "media_id": media_id, "status": status},
        )

    async def update_status(
        self, media_type: str, source: str, media_id: str, status: int
    ) -> dict[str, Any]:
        """Change the status of a tracked media."""
        return await self._request(
            "PATCH",
            f"/media/{media_type}/{source}/{media_id}/",
            json={"status": status},
        )

    async def media_list(
        self,
        *,
        status: int | None = None,
        sort: str | None = None,
        limit: int = 20,
        exclude: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return the user's tracked media, optionally filtered by status."""
        params: dict[str, Any] = {"limit": limit}
        if status is not None:
            params["status"] = status
        if sort:
            params["sort"] = sort
        if exclude:
            params["exclude"] = ",".join(exclude)
        data = await self._request("GET", "/media/", params=params)
        return list((data or {}).get("results") or [])

    async def lists(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return the user's custom lists."""
        data = await self._request("GET", "/lists/", params={"limit": limit})
        return list((data or {}).get("results") or [])

    async def list_items(self, list_id: int, limit: int = 200) -> list[dict[str, Any]]:
        """Return the items of a custom list."""
        data = await self._request(
            "GET", f"/lists/{list_id}/items/", params={"limit": limit}
        )
        return list((data or {}).get("results") or [])

    async def season_episodes(
        self, source: str, media_id: str, season_number: int
    ) -> list[dict[str, Any]]:
        """Return the episodes of a season with whether each one was watched."""
        data = await self._request(
            "GET",
            f"/media/tv/{source}/{media_id}/{season_number}/episodes/",
            params={"limit": 200},
        )
        return list((data or {}).get("results") or [])

    async def calendar(
        self, start_date: str, end_date: str, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Return the release events of tracked media between two dates."""
        data = await self._request(
            "GET",
            "/calendar/",
            params={"start_date": start_date, "end_date": end_date, "limit": limit},
        )
        return list((data or {}).get("results") or [])
