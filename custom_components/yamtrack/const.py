"""Constants for the Yamtrack integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "yamtrack"

# Config entry data
CONF_URL: Final = "url"
CONF_API_KEY: Final = "api_key"

# Options
CONF_REGION: Final = "region"
CONF_SERVICES: Final = "services"
CONF_INCLUDE_FREE: Final = "include_free"
CONF_PREFER_LIBRARY: Final = "prefer_library"
CONF_TARGET: Final = "target"
CONF_LAUNCH_METHOD: Final = "launch_method"
CONF_PLEX_ENTITY: Final = "plex_entity"
CONF_PLEX_MOVIE_LIBRARY: Final = "plex_movie_library"
CONF_PLEX_TV_LIBRARY: Final = "plex_tv_library"
CONF_TRACK: Final = "track"

DEFAULT_REGION: Final = "US"
DEFAULT_INCLUDE_FREE: Final = False
DEFAULT_PREFER_LIBRARY: Final = True
DEFAULT_PLEX_MOVIE_LIBRARY: Final = "Movies"
DEFAULT_PLEX_TV_LIBRARY: Final = "TV Shows"
DEFAULT_TRACK: Final = True

LAUNCH_ANDROID_TV_REMOTE: Final = "android_tv_remote"
LAUNCH_ADB: Final = "adb"
LAUNCH_PLEX_ONLY: Final = "plex_only"
LAUNCH_METHODS: Final = [LAUNCH_ANDROID_TV_REMOTE, LAUNCH_ADB, LAUNCH_PLEX_ONLY]
DEFAULT_LAUNCH_METHOD: Final = LAUNCH_ANDROID_TV_REMOTE

MEDIA_MOVIE: Final = "movie"
MEDIA_TV: Final = "tv"
MEDIA_TYPES: Final = [MEDIA_MOVIE, MEDIA_TV]
SOURCE_TMDB: Final = "tmdb"

PREFERENCE_AUTO: Final = "auto"
PREFERENCE_STREAMING: Final = "streaming"
PREFERENCE_LIBRARY: Final = "library"
SOURCE_PREFERENCES: Final = [PREFERENCE_AUTO, PREFERENCE_STREAMING, PREFERENCE_LIBRARY]

# Yamtrack media status values used by the API
STATUS_IN_PROGRESS: Final = 1

# Results of yamtrack.play
PLAYED_VIA_STREAMING: Final = "streaming"
PLAYED_VIA_PLEX: Final = "plex"
ERROR_NOT_FOUND: Final = "not_found"
ERROR_NO_PROVIDER: Final = "no_provider"
ERROR_LAUNCH_FAILED: Final = "launch_failed"

EVENT_PLAY_STARTED: Final = "yamtrack_play_started"

SERVICE_SEARCH: Final = "search"
SERVICE_PLAY: Final = "play"

INTENT_PLAY_MEDIA: Final = "YamtrackPlayMedia"

ATTR_QUERY: Final = "query"
ATTR_MEDIA_ID: Final = "media_id"
ATTR_MEDIA_TYPE: Final = "media_type"
ATTR_LIMIT: Final = "limit"
ATTR_TARGET: Final = "target"
ATTR_SOURCE_PREFERENCE: Final = "source_preference"

REQUEST_TIMEOUT: Final = 10
REQUEST_RETRIES: Final = 2
CACHE_TTL: Final = timedelta(hours=6)
NO_LINKS_WARNING_INTERVAL: Final = timedelta(days=1)

SEARCH_LIMIT: Final = 5
MATCH_THRESHOLD: Final = 70
YEAR_MATCH_BOOST: Final = 20
YEAR_MISMATCH_PENALTY: Final = 10

# Titles available on many services, looked up to fill the "My services" list
POPULAR_TITLES: Final = [
    (MEDIA_TV, "66732"),  # Stranger Things
    (MEDIA_TV, "82856"),  # The Mandalorian
    (MEDIA_TV, "76479"),  # The Boys
    (MEDIA_TV, "100088"),  # The Last of Us
    (MEDIA_TV, "136315"),  # The Bear
    (MEDIA_TV, "95396"),  # Severance
    (MEDIA_TV, "71912"),  # The Witcher
    (MEDIA_TV, "1396"),  # Breaking Bad
]

# Offered in the services list even when the lookups return nothing
COMMON_SERVICES: Final = [
    "Netflix",
    "Amazon Prime Video",
    "Disney Plus",
    "Max",
    "Hulu",
    "Apple TV Plus",
    "Paramount Plus",
    "Peacock Premium",
]

# Free ad-supported services, used when "include free services" is on
FREE_SERVICES: Final = [
    "Tubi TV",
    "Pluto TV",
    "The Roku Channel",
    "Plex",
    "Amazon Freevee",
    "Crackle",
    "Kanopy",
    "Hoopla",
    "YouTube Free",
]
