"""Title resolution, provider selection and launchers."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import json
import logging
import re
import shlex
from typing import Any

from rapidfuzz import fuzz, utils

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .api import YamtrackError
from .const import (
    CONF_INCLUDE_FREE,
    CONF_LAUNCH_METHOD,
    CONF_PLEX_ENTITY,
    CONF_PLEX_MOVIE_LIBRARY,
    CONF_PLEX_TV_LIBRARY,
    CONF_PREFER_LIBRARY,
    CONF_REGION,
    CONF_SERVICES,
    CONF_TARGET,
    CONF_TRACK,
    DEFAULT_INCLUDE_FREE,
    DEFAULT_LAUNCH_METHOD,
    DEFAULT_PLEX_MOVIE_LIBRARY,
    DEFAULT_PLEX_TV_LIBRARY,
    DEFAULT_PREFER_LIBRARY,
    DEFAULT_REGION,
    DEFAULT_TRACK,
    ERROR_LAUNCH_FAILED,
    ERROR_NO_PROVIDER,
    ERROR_NOT_FOUND,
    FREE_SERVICES,
    LAUNCH_ADB,
    LAUNCH_ANDROID_TV_REMOTE,
    LAUNCH_PLEX_ONLY,
    MATCH_THRESHOLD,
    MEDIA_TV,
    PLAYED_VIA_PLEX,
    PLAYED_VIA_STREAMING,
    PREFERENCE_AUTO,
    PREFERENCE_LIBRARY,
    PREFERENCE_STREAMING,
    SEARCH_LIMIT,
    SOURCE_TMDB,
    STATUS_IN_PROGRESS,
    YEAR_MATCH_BOOST,
    YEAR_MISMATCH_PENALTY,
)
from .coordinator import YamtrackCoordinator

_LOGGER = logging.getLogger(__name__)

_YEAR_RE = re.compile(r"\(?\b((?:19|20)\d{2})\)?\s*$")
_SUFFIX_RE = re.compile(r"\s*(standard )?with ads$")


@dataclass
class Candidate:
    """A search result scored against the query."""

    media_id: str
    source: str
    media_type: str
    title: str
    image: str | None = None
    year: int | None = None
    score: float = 0.0


@dataclass
class PlayResult:
    """Outcome of yamtrack.play."""

    title: str | None = None
    played_via: str | None = None
    provider: str | None = None
    link: str | None = None
    media_id: str | None = None
    media_type: str | None = None
    error: str | None = None
    message: str | None = None

    @property
    def success(self) -> bool:
        """Return whether playback started."""
        return self.error is None

    def as_dict(self) -> dict[str, Any]:
        """Return the action response."""
        data = asdict(self)
        if self.success:
            data.pop("error")
            data.pop("message")
        return data


def get_options(options: dict[str, Any]) -> dict[str, Any]:
    """Return the entry options with defaults filled in."""
    return {
        CONF_REGION: DEFAULT_REGION,
        CONF_SERVICES: [],
        CONF_INCLUDE_FREE: DEFAULT_INCLUDE_FREE,
        CONF_PREFER_LIBRARY: DEFAULT_PREFER_LIBRARY,
        CONF_TARGET: None,
        CONF_LAUNCH_METHOD: DEFAULT_LAUNCH_METHOD,
        CONF_PLEX_ENTITY: None,
        CONF_PLEX_MOVIE_LIBRARY: DEFAULT_PLEX_MOVIE_LIBRARY,
        CONF_PLEX_TV_LIBRARY: DEFAULT_PLEX_TV_LIBRARY,
        CONF_TRACK: DEFAULT_TRACK,
        **{key: value for key, value in options.items() if value is not None},
    }


# --- Title resolution ---------------------------------------------------------


def split_year(query: str) -> tuple[str, int | None]:
    """Split a trailing year from a query: "Dune 2021" -> ("Dune", 2021)."""
    query = query.strip()
    match = _YEAR_RE.search(query)
    if match is None:
        return query, None
    title = query[: match.start()].strip()
    # A title that is only a year, like "1917", stays a title
    if not title:
        return query, None
    return title, int(match.group(1))


def year_from_details(details: dict[str, Any] | None) -> int | None:
    """Return the release year of a media from its details."""
    info = (details or {}).get("details") or {}
    date = info.get("release_date") or info.get("first_air_date")
    if isinstance(date, str) and len(date) >= 4 and date[:4].isdigit():
        return int(date[:4])
    return None


def score_title(query: str, title: str) -> float:
    """Return how well a title matches the query, from 0 to 100."""
    return fuzz.WRatio(query, title, processor=utils.default_process)


def rank_candidates(
    query_title: str, year: int | None, candidates: list[Candidate]
) -> list[Candidate]:
    """Score candidates against the query, best first.

    Search order breaks ties so the most relevant TMDB result wins.
    """
    for index, candidate in enumerate(candidates):
        score = score_title(query_title, candidate.title) - index * 0.5
        if year is not None and candidate.year is not None:
            if candidate.year == year:
                score += YEAR_MATCH_BOOST
            else:
                score -= YEAR_MISMATCH_PENALTY
        candidate.score = score
    return sorted(candidates, key=lambda c: c.score, reverse=True)


async def search_candidates(
    coordinator: YamtrackCoordinator,
    query: str,
    media_type: str,
    limit: int,
    *,
    with_years: bool = True,
) -> list[Candidate]:
    """Search Yamtrack and return ranked candidates."""
    title, year = split_year(query)
    results = await coordinator.search(media_type, title, limit)
    candidates = [
        Candidate(
            media_id=str(result.get("media_id")),
            source=result.get("source") or SOURCE_TMDB,
            media_type=result.get("media_type") or media_type,
            title=result.get("title") or "",
            image=result.get("image"),
        )
        for result in results[:limit]
    ]
    if with_years or year is not None:
        await _fill_years(coordinator, candidates)
    return rank_candidates(title, year, candidates)


async def _fill_years(
    coordinator: YamtrackCoordinator, candidates: list[Candidate]
) -> None:
    """Fetch the details of every candidate to know its year."""
    details = await asyncio.gather(
        *(coordinator.details(c.media_type, c.source, c.media_id) for c in candidates),
        return_exceptions=True,
    )
    for candidate, detail in zip(candidates, details, strict=True):
        if isinstance(detail, dict):
            candidate.year = year_from_details(detail)
        elif isinstance(detail, Exception):
            _LOGGER.debug("No details for %s: %s", candidate.media_id, detail)


async def resolve_title(
    coordinator: YamtrackCoordinator, query: str, media_type: str
) -> Candidate | None:
    """Return the best match for the query, None below the threshold."""
    candidates = await search_candidates(
        coordinator, query, media_type, SEARCH_LIMIT, with_years=False
    )
    if not candidates or candidates[0].score < MATCH_THRESHOLD:
        return None
    return candidates[0]


async def candidate_from_id(
    coordinator: YamtrackCoordinator, media_id: str, media_type: str
) -> Candidate | None:
    """Build a candidate from a TMDB ID."""
    try:
        details = await coordinator.details(media_type, SOURCE_TMDB, media_id)
    except YamtrackError as err:
        _LOGGER.debug("No details for %s %s: %s", media_type, media_id, err)
        return None
    return Candidate(
        media_id=str(media_id),
        source=SOURCE_TMDB,
        media_type=media_type,
        title=details.get("title") or str(media_id),
        image=details.get("image"),
        year=year_from_details(details),
        score=100.0,
    )


# --- Provider selection -------------------------------------------------------


def normalize_service(name: str | None) -> str:
    """Normalize a provider name for comparison."""
    name = (name or "").casefold().strip()
    name = _SUFFIX_RE.sub("", name)
    name = name.replace("+", " plus")
    return re.sub(r"[^a-z0-9]", "", name)


def provider_kind(
    provider_name: str | None, services: list[str], include_free: bool
) -> str | None:
    """Return "subscription" or "free" when the provider can be used, else None."""
    normalized = normalize_service(provider_name)
    if normalized in {normalize_service(s) for s in services}:
        return "subscription"
    if include_free and normalized in {normalize_service(s) for s in FREE_SERVICES}:
        return "free"
    return None


def select_offer(
    providers: dict[str, Any] | None, services: list[str], include_free: bool
) -> dict[str, Any] | None:
    """Return the first no charge offer on one of my services with a link."""
    offers = sorted(
        (providers or {}).get("no_charge") or [],
        key=lambda o: (
            o.get("display_priority") if o.get("display_priority") is not None else 9999
        ),
    )
    for offer in offers:
        if not offer.get("link"):
            continue
        if provider_kind(offer.get("provider_name"), services, include_free):
            return offer
    return None


# Rewrites that turn a title page into a link that starts playback
_LINK_REWRITES: dict[str, list[tuple[re.Pattern[str], str]]] = {
    "netflix": [
        (
            re.compile(
                r"^https?://(?:www\.)?netflix\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?title/(\d+).*$"
            ),
            r"https://www.netflix.com/watch/\1",
        ),
    ],
}


def rewrite_link(provider_name: str | None, link: str) -> str:
    """Apply the per-provider link rewrites."""
    for pattern, replacement in _LINK_REWRITES.get(
        normalize_service(provider_name), []
    ):
        if pattern.match(link):
            return pattern.sub(replacement, link)
    return link


def summarize_providers(
    providers: dict[str, Any] | None, services: list[str], include_free: bool
) -> list[dict[str, Any]]:
    """Return the no charge providers as {name, link, kind} for yamtrack.search."""
    return [
        {
            "name": offer.get("provider_name"),
            "link": rewrite_link(offer.get("provider_name"), offer["link"])
            if offer.get("link")
            else None,
            "kind": provider_kind(offer.get("provider_name"), services, include_free)
            or "other",
        }
        for offer in (providers or {}).get("no_charge") or []
    ]


# --- Launchers ----------------------------------------------------------------


class LaunchError(HomeAssistantError):
    """Playback could not be started."""


async def launch_link(
    hass: HomeAssistant, method: str, target: str | None, link: str
) -> None:
    """Open a deep link on the TV."""
    if not target:
        raise LaunchError("No launch target configured")
    if method == LAUNCH_ANDROID_TV_REMOTE:
        domain, service = "remote", "turn_on"
        data = {"entity_id": target, "activity": link}
    elif method == LAUNCH_ADB:
        domain, service = "androidtv", "adb_command"
        data = {
            "entity_id": target,
            "command": (
                "am start -a android.intent.action.VIEW -d " + shlex.quote(link)
            ),
        }
    else:
        raise LaunchError(f"Launch method {method} cannot open links")

    if not hass.services.has_service(domain, service):
        raise LaunchError(f"Action {domain}.{service} is not available")
    try:
        await hass.services.async_call(domain, service, data, blocking=True)
    except HomeAssistantError as err:
        raise LaunchError(str(err)) from err


def plex_payload(media_type: str, library: str, title: str) -> tuple[str, str]:
    """Return the media content type and plex:// ID for a title."""
    # Plex picks the exact title when several match, which needs the "title" key
    if media_type == MEDIA_TV:
        query = {"library_name": library, "title": title, "libtype": "show"}
        content_type = "episode"
    else:
        query = {"library_name": library, "title": title, "libtype": "movie"}
        content_type = "movie"
    return content_type, "plex://" + json.dumps(query)


async def launch_plex(
    hass: HomeAssistant,
    entity_id: str | None,
    media_type: str,
    library: str,
    title: str,
) -> None:
    """Play a title from the Plex library."""
    if not entity_id:
        raise LaunchError("No Plex client configured")
    content_type, content_id = plex_payload(media_type, library, title)
    try:
        await hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": entity_id,
                "media_content_type": content_type,
                "media_content_id": content_id,
            },
            blocking=True,
        )
    except HomeAssistantError as err:
        raise LaunchError(str(err)) from err


# --- Play ---------------------------------------------------------------------


async def async_search(
    coordinator: YamtrackCoordinator,
    options: dict[str, Any],
    query: str,
    media_type: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Return search results with years and providers."""
    options = get_options(options)
    candidates = await search_candidates(coordinator, query, media_type, limit)
    providers = await asyncio.gather(
        *(
            coordinator.providers(
                c.media_type, c.source, c.media_id, options[CONF_REGION]
            )
            for c in candidates
        ),
        return_exceptions=True,
    )
    return [
        {
            "media_id": c.media_id,
            "source": c.source,
            "media_type": c.media_type,
            "title": c.title,
            "year": c.year,
            "image": c.image,
            "providers": summarize_providers(
                p if isinstance(p, dict) else None,
                options[CONF_SERVICES],
                options[CONF_INCLUDE_FREE],
            ),
        }
        for c, p in zip(candidates, providers, strict=True)
    ]


async def async_play(
    hass: HomeAssistant,
    coordinator: YamtrackCoordinator,
    options: dict[str, Any],
    *,
    query: str | None = None,
    media_id: str | None = None,
    media_type: str = "movie",
    target: str | None = None,
    source_preference: str = PREFERENCE_AUTO,
) -> PlayResult:
    """Find a title and start it on the TV."""
    options = get_options(options)
    method = options[CONF_LAUNCH_METHOD]
    target = target or options[CONF_TARGET]
    # With plex_only the launch target is the Plex client itself
    plex_client = (
        (target or options[CONF_PLEX_ENTITY])
        if method == LAUNCH_PLEX_ONLY
        else options[CONF_PLEX_ENTITY]
    )
    library = (
        options[CONF_PLEX_TV_LIBRARY]
        if media_type == MEDIA_TV
        else options[CONF_PLEX_MOVIE_LIBRARY]
    )

    # 1. Resolve the title
    if media_id:
        candidate = await candidate_from_id(coordinator, media_id, media_type)
    elif query:
        candidate = await resolve_title(coordinator, query, media_type)
    else:
        candidate = None
    if candidate is None:
        name = query or media_id or ""
        return PlayResult(
            title=name,
            media_type=media_type,
            error=ERROR_NOT_FOUND,
            message=f"Couldn't find {name}",
        )

    result = PlayResult(
        title=candidate.title, media_id=candidate.media_id, media_type=media_type
    )

    # Decide which sources to try, in order
    use_streaming = (
        method != LAUNCH_PLEX_ONLY and source_preference != PREFERENCE_LIBRARY
    )
    use_plex = bool(plex_client) and source_preference != PREFERENCE_STREAMING
    library_first = source_preference == PREFERENCE_LIBRARY or (
        source_preference == PREFERENCE_AUTO and options[CONF_PREFER_LIBRARY]
    )
    order = []
    if use_plex and library_first:
        order.append(PLAYED_VIA_PLEX)
    if use_streaming:
        order.append(PLAYED_VIA_STREAMING)
    if use_plex and not library_first:
        order.append(PLAYED_VIA_PLEX)

    launch_errors: list[str] = []
    for source in order:
        if source == PLAYED_VIA_PLEX:
            try:
                await launch_plex(
                    hass, plex_client, media_type, library, candidate.title
                )
            except LaunchError as err:
                _LOGGER.debug("Plex couldn't play %s: %s", candidate.title, err)
                launch_errors.append(f"Plex: {err}")
                continue
            result.played_via = PLAYED_VIA_PLEX
            result.provider = "Plex"
            break

        # Streaming
        try:
            providers = await coordinator.providers(
                media_type, candidate.source, candidate.media_id, options[CONF_REGION]
            )
        except YamtrackError as err:
            _LOGGER.warning("Couldn't fetch providers for %s: %s", candidate.title, err)
            continue
        offer = select_offer(
            providers, options[CONF_SERVICES], options[CONF_INCLUDE_FREE]
        )
        if offer is None:
            continue
        link = rewrite_link(offer.get("provider_name"), offer["link"])
        try:
            await launch_link(hass, method, target, link)
        except LaunchError as err:
            _LOGGER.warning(
                "Couldn't open %s on %s: %s", offer.get("provider_name"), target, err
            )
            launch_errors.append(f"{offer.get('provider_name')}: {err}")
            continue
        result.played_via = PLAYED_VIA_STREAMING
        result.provider = offer.get("provider_name")
        result.link = link
        break

    if result.played_via is None:
        if launch_errors:
            result.error = ERROR_LAUNCH_FAILED
            result.message = f"Couldn't start {candidate.title}: " + "; ".join(
                launch_errors
            )
        else:
            result.error = ERROR_NO_PROVIDER
            result.message = f"Couldn't find {candidate.title} on your services"
        return result

    if options[CONF_TRACK]:
        await mark_in_progress(coordinator, candidate)
    return result


async def mark_in_progress(
    coordinator: YamtrackCoordinator, candidate: Candidate
) -> None:
    """Set the Yamtrack status to in progress, tracking the media if needed."""
    api = coordinator.api
    args = (candidate.media_type, candidate.source, candidate.media_id)
    try:
        details = await api.details(*args)
        if not details.get("tracked"):
            await api.track(*args, STATUS_IN_PROGRESS)
            return
        statuses = [c.get("status") for c in details.get("consumptions") or []]
        if STATUS_IN_PROGRESS not in statuses:
            await api.update_status(*args, STATUS_IN_PROGRESS)
    except YamtrackError as err:
        _LOGGER.warning("Couldn't mark %s as in progress: %s", candidate.title, err)
