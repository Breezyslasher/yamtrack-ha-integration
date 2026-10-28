"""Yamtrack media player: browse Yamtrack in the media browser and launch on the TV.

A media source can only give a player a stream URL, which can't start a
streaming app on a TV. So the media browser uses this entity: pick "Yamtrack"
as the player, browse up next, currently watching, the watchlist and lists,
and playing an item runs yamtrack.play on the configured TV.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.media_player import (
    BrowseMedia,
    MediaClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import YamtrackError
from .const import (
    BROWSE_LIMIT,
    EVENT_PLAY_STARTED,
    MEDIA_MOVIE,
    MEDIA_TV,
    NON_VIDEO_TYPES,
    SOURCE_TMDB,
    STATUS_PLANNING,
)
from .coordinator import media_item
from .entity import YamtrackEntity
from .next_episode import NextEpisode

_LOGGER = logging.getLogger(__name__)

ROOT = "root"
UP_NEXT = "up_next"
WATCHING = "watching"
WATCHLIST = "watchlist"
LISTS = "lists"
LIST_PREFIX = "list/"

FOLDERS = {
    UP_NEXT: "Up next",
    WATCHING: "Currently watching",
    WATCHLIST: "Watchlist",
    LISTS: "Lists",
}


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Yamtrack media player."""
    async_add_entities([YamtrackMediaPlayer(entry.runtime_data.library, entry)])


def content_id(media_type: str, source: str, media_id: str) -> str:
    """Return the content ID of a title: "movie/tmdb/438631"."""
    return f"{media_type}/{source}/{media_id}"


def parse_content_id(value: str) -> tuple[str, str, str]:
    """Split a title content ID, raising when it isn't one."""
    parts = value.split("/")
    if len(parts) != 3 or parts[0] not in (MEDIA_MOVIE, MEDIA_TV) or not parts[2]:
        raise HomeAssistantError(f"Not a Yamtrack title: {value}")
    return parts[0], parts[1], parts[2]


def folder(content: str, title: str, children: list[BrowseMedia] | None = None):
    """Return a browsable folder."""
    return BrowseMedia(
        media_class=MediaClass.DIRECTORY,
        media_content_id=content,
        media_content_type=MediaType.APPS,
        title=title,
        can_play=False,
        can_expand=True,
        children=children,
        children_media_class=MediaClass.MOVIE,
    )


def title_node(item: dict[str, Any], title: str | None = None) -> BrowseMedia:
    """Return a playable movie or show."""
    is_tv = item["media_type"] == MEDIA_TV
    return BrowseMedia(
        media_class=MediaClass.TV_SHOW if is_tv else MediaClass.MOVIE,
        media_content_id=content_id(
            item["media_type"], item.get("source") or SOURCE_TMDB, item["media_id"]
        ),
        media_content_type=MediaType.TVSHOW if is_tv else MediaType.MOVIE,
        title=title or item.get("title") or item["media_id"],
        can_play=True,
        can_expand=False,
        thumbnail=item.get("image"),
    )


def episode_node(episode: NextEpisode) -> BrowseMedia:
    """Return a playable next episode."""
    name = f"{episode.show_title} · {episode.label}"
    if episode.episode_title:
        name += f" · {episode.episode_title}"
    return BrowseMedia(
        media_class=MediaClass.EPISODE,
        media_content_id=(
            f"{content_id(MEDIA_TV, SOURCE_TMDB, episode.media_id)}"
            f"/{episode.season_number}/{episode.episode_number}"
        ),
        media_content_type=MediaType.EPISODE,
        title=name,
        can_play=True,
        can_expand=False,
        thumbnail=episode.image,
    )


def is_video(item: dict[str, Any]) -> bool:
    """Return whether an item is a TMDB movie or show, the ones that can play."""
    return (
        item.get("media_type") in (MEDIA_MOVIE, MEDIA_TV)
        and (item.get("source") or SOURCE_TMDB) == SOURCE_TMDB
    )


class YamtrackMediaPlayer(YamtrackEntity, MediaPlayerEntity):
    """Browse Yamtrack and launch titles on the TV."""

    _attr_name = None
    _attr_supported_features = (
        MediaPlayerEntityFeature.BROWSE_MEDIA | MediaPlayerEntityFeature.PLAY_MEDIA
    )
    _attr_state = MediaPlayerState.IDLE

    def __init__(self, coordinator, entry) -> None:
        """Initialize the media player."""
        super().__init__(coordinator, entry, "launcher")
        self._attr_media_title = None
        self._attr_media_image_url = None
        self._attr_media_content_type = None

    async def async_added_to_hass(self) -> None:
        """Show the last title started through Yamtrack."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_PLAY_STARTED, self._handle_play_started)
        )

    @callback
    def _handle_play_started(self, event: Event) -> None:
        data = event.data
        title = data.get("title")
        if data.get("season_number") is not None:
            title = f"{title} S{data['season_number']:02d}E{data['episode_number']:02d}"
        self._attr_media_title = title
        self._attr_media_image_url = data.get("image")
        self._attr_media_content_type = (
            MediaType.TVSHOW if data.get("media_type") == MEDIA_TV else MediaType.MOVIE
        )
        self.async_write_ha_state()

    async def async_browse_media(
        self,
        media_content_type: MediaType | str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """Return a Yamtrack folder."""
        content = media_content_id or ROOT
        try:
            if content == ROOT:
                return folder(
                    ROOT,
                    "Yamtrack",
                    [folder(key, name) for key, name in FOLDERS.items()],
                )
            if content == UP_NEXT:
                episodes = (
                    self.coordinator.data.up_next if self.coordinator.data else []
                )
                return folder(
                    UP_NEXT, FOLDERS[UP_NEXT], [episode_node(e) for e in episodes]
                )
            if content == WATCHING:
                items = self.coordinator.data.watching if self.coordinator.data else []
                return folder(
                    WATCHING,
                    FOLDERS[WATCHING],
                    [title_node(i) for i in items if is_video(i)],
                )
            api = self.coordinator.api
            if content == WATCHLIST:
                entries = await api.media_list(
                    status=STATUS_PLANNING,
                    sort="added_desc",
                    limit=BROWSE_LIMIT,
                    exclude=NON_VIDEO_TYPES,
                )
                return folder(
                    WATCHLIST,
                    FOLDERS[WATCHLIST],
                    [title_node(i) for i in map(media_item, entries) if is_video(i)],
                )
            if content == LISTS:
                lists = await api.lists()
                return folder(
                    LISTS,
                    FOLDERS[LISTS],
                    [
                        BrowseMedia(
                            media_class=MediaClass.DIRECTORY,
                            media_content_id=f"{LIST_PREFIX}{custom['id']}",
                            media_content_type=MediaType.APPS,
                            title=custom.get("name") or str(custom["id"]),
                            can_play=False,
                            can_expand=True,
                            thumbnail=custom.get("image"),
                        )
                        for custom in lists
                    ],
                )
            if content.startswith(LIST_PREFIX):
                list_id = int(content.removeprefix(LIST_PREFIX))
                entries = await api.list_items(list_id, BROWSE_LIMIT)
                return folder(
                    content,
                    "List",
                    [title_node(i) for i in map(media_item, entries) if is_video(i)],
                )
        except (YamtrackError, ValueError) as err:
            raise HomeAssistantError(f"Couldn't browse Yamtrack: {err}") from err
        raise HomeAssistantError(f"Unknown Yamtrack folder: {content}")

    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """Launch a title, or a show's episode, on the TV."""
        # Imported here as __init__ imports the platforms' dependencies
        from . import run_play  # noqa: PLC0415

        parts = media_id.split("/")
        season = episode = None
        if len(parts) == 5:
            season, episode = int(parts[3]), int(parts[4])
            parts = parts[:3]
        kind, _source, title_id = parse_content_id("/".join(parts))

        result = await run_play(
            self.hass,
            media_id=title_id,
            media_type=kind,
            season=season,
            episode=episode,
        )
        if not result.success:
            raise HomeAssistantError(result.message or result.error)
