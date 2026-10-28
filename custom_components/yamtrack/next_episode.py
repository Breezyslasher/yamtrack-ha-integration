"""Find the next episode to watch from Yamtrack progress."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
import logging
from typing import Any

from homeassistant.util import dt as dt_util

from .api import YamtrackApi, YamtrackError
from .const import SOURCE_TMDB, STATUS_COMPLETED

_LOGGER = logging.getLogger(__name__)

# (media_id, season_number, episode_number) of episodes that haven't aired
type Unaired = set[tuple[str, int, int]]


@dataclass
class NextEpisode:
    """The next unwatched episode of a show."""

    media_id: str
    show_title: str
    season_number: int
    episode_number: int
    episode_title: str | None = None
    image: str | None = None

    @property
    def label(self) -> str:
        """Return "S02E03"."""
        return f"S{self.season_number:02d}E{self.episode_number:02d}"

    def as_dict(self) -> dict[str, Any]:
        """Return the episode with its label."""
        return {**asdict(self), "label": self.label}


async def async_unaired_episodes(api: YamtrackApi) -> Unaired:
    """Return the episodes of tracked shows that air in the next year.

    Yamtrack doesn't give episode air dates, but its calendar has the upcoming
    releases of tracked shows. Failures return an empty set.
    """
    now = dt_util.utcnow()
    try:
        events = await api.calendar(
            now.date().isoformat(), (now + timedelta(days=365)).date().isoformat()
        )
    except YamtrackError as err:
        _LOGGER.debug("Couldn't read the Yamtrack calendar: %s", err)
        return set()

    unaired: Unaired = set()
    for event in events:
        item = event.get("item") or {}
        released = dt_util.parse_datetime(event.get("datetime") or "")
        season = item.get("season_number")
        episode = item.get("episode_number") or event.get("content_number")
        if released is None or released <= now or season is None or episode is None:
            continue
        unaired.add((str(item.get("media_id")), int(season), int(episode)))
    return unaired


async def async_find_next_episode(
    api: YamtrackApi,
    media_id: str,
    *,
    unaired: Unaired | None = None,
    details: dict[str, Any] | None = None,
    source: str = SOURCE_TMDB,
) -> NextEpisode | None:
    """Return the first unwatched, aired episode of a tracked show.

    Seasons are walked in order, skipping specials and completed seasons, up to
    the season of the last aired episode. None when the show isn't tracked,
    everything aired is watched, or the next episode hasn't aired yet.
    """
    if details is None:
        details = await api.details("tv", source, media_id)
    if not details.get("tracked"):
        return None

    last_aired_season = (details.get("details") or {}).get("last_episode_season")
    seasons = sorted(
        (
            ((season.get("item") or {}).get("season_number"), season)
            for season in (details.get("related") or {}).get("seasons") or []
        ),
        key=lambda pair: pair[0] or 0,
    )
    for number, season in seasons:
        if not number or number < 1:
            continue  # Specials
        if last_aired_season is not None and number > last_aired_season:
            break
        if season.get("status") == STATUS_COMPLETED:
            continue

        episodes = await api.season_episodes(source, media_id, number)
        for episode in sorted(
            episodes, key=lambda e: (e.get("item") or {}).get("episode_number") or 0
        ):
            if episode.get("tracked"):
                continue
            item = episode.get("item") or {}
            episode_number = item.get("episode_number")
            if episode_number is None:
                continue
            if unaired and (str(media_id), number, episode_number) in unaired:
                return None
            return NextEpisode(
                media_id=str(media_id),
                show_title=details.get("title") or str(media_id),
                season_number=number,
                episode_number=episode_number,
                episode_title=item.get("title"),
                image=item.get("image") or details.get("image"),
            )
    return None
