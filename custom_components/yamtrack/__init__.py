"""The Yamtrack integration: find a title and start it on the TV."""

from __future__ import annotations

from dataclasses import dataclass
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.intent import async_register as async_register_intent
from homeassistant.helpers.typing import ConfigType

from .api import YamtrackApi, YamtrackAuthError, YamtrackError
from .const import (
    ATTR_LIMIT,
    ATTR_MEDIA_ID,
    ATTR_MEDIA_TYPE,
    ATTR_QUERY,
    ATTR_SOURCE_PREFERENCE,
    ATTR_TARGET,
    CONF_API_KEY,
    CONF_URL,
    DOMAIN,
    EVENT_PLAY_STARTED,
    MEDIA_MOVIE,
    MEDIA_TYPES,
    PREFERENCE_AUTO,
    SEARCH_LIMIT,
    SERVICE_PLAY,
    SERVICE_SEARCH,
    SOURCE_PREFERENCES,
)
from .coordinator import YamtrackCoordinator
from .intent import YamtrackPlayMediaIntent
from .play import PlayResult, async_play, async_search

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class YamtrackData:
    """Runtime data of a config entry."""

    coordinator: YamtrackCoordinator


type YamtrackConfigEntry = ConfigEntry[YamtrackData]

SEARCH_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_QUERY): cv.string,
        vol.Optional(ATTR_MEDIA_TYPE, default=MEDIA_MOVIE): vol.In(MEDIA_TYPES),
        vol.Optional(ATTR_LIMIT, default=SEARCH_LIMIT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=20)
        ),
    }
)

PLAY_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(ATTR_QUERY): cv.string,
            vol.Optional(ATTR_MEDIA_ID): vol.All(vol.Coerce(str), cv.string),
            vol.Optional(ATTR_MEDIA_TYPE, default=MEDIA_MOVIE): vol.In(MEDIA_TYPES),
            vol.Optional(ATTR_TARGET): cv.entity_id,
            vol.Optional(ATTR_SOURCE_PREFERENCE, default=PREFERENCE_AUTO): vol.In(
                SOURCE_PREFERENCES
            ),
        }
    ),
    cv.has_at_least_one_key(ATTR_QUERY, ATTR_MEDIA_ID),
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions and the Assist intent."""

    async def handle_search(call: ServiceCall) -> ServiceResponse:
        entry = get_loaded_entry(hass)
        try:
            results = await async_search(
                entry.runtime_data.coordinator,
                dict(entry.options),
                call.data[ATTR_QUERY],
                call.data[ATTR_MEDIA_TYPE],
                call.data[ATTR_LIMIT],
            )
        except YamtrackError as err:
            raise HomeAssistantError(f"Yamtrack search failed: {err}") from err
        return {"results": results}

    async def handle_play(call: ServiceCall) -> ServiceResponse:
        result = await run_play(
            hass,
            query=call.data.get(ATTR_QUERY),
            media_id=call.data.get(ATTR_MEDIA_ID),
            media_type=call.data[ATTR_MEDIA_TYPE],
            target=call.data.get(ATTR_TARGET),
            source_preference=call.data[ATTR_SOURCE_PREFERENCE],
        )
        if not result.success and not call.return_response:
            raise HomeAssistantError(result.message or result.error)
        return result.as_dict() if call.return_response else None

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEARCH,
        handle_search,
        schema=SEARCH_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_PLAY,
        handle_play,
        schema=PLAY_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    async_register_intent(hass, YamtrackPlayMediaIntent())
    return True


async def async_setup_entry(hass: HomeAssistant, entry: YamtrackConfigEntry) -> bool:
    """Connect to Yamtrack."""
    api = YamtrackApi(
        async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_API_KEY]
    )
    try:
        await api.validate_auth()
    except YamtrackAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except YamtrackError as err:
        raise ConfigEntryNotReady(str(err)) from err

    entry.runtime_data = YamtrackData(coordinator=YamtrackCoordinator(api))
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: YamtrackConfigEntry) -> bool:
    """Unload a config entry."""
    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: YamtrackConfigEntry
) -> None:
    """Drop cached providers when the region or services change."""
    entry.runtime_data.coordinator.clear()


def get_loaded_entry(hass: HomeAssistant) -> YamtrackConfigEntry:
    """Return the loaded Yamtrack config entry."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry
    raise ServiceValidationError(
        "Yamtrack is not set up or not loaded",
        translation_domain=DOMAIN,
        translation_key="not_loaded",
    )


async def run_play(hass: HomeAssistant, **kwargs) -> PlayResult:
    """Run yamtrack.play and fire the started event."""
    entry = get_loaded_entry(hass)
    try:
        result = await async_play(
            hass, entry.runtime_data.coordinator, dict(entry.options), **kwargs
        )
    except YamtrackError as err:
        raise HomeAssistantError(f"Yamtrack request failed: {err}") from err
    if result.success:
        hass.bus.async_fire(EVENT_PLAY_STARTED, result.as_dict())
    return result
