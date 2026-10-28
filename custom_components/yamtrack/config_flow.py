"""Config and options flow for Yamtrack."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import YamtrackApi, YamtrackAuthError, YamtrackConnectionError, YamtrackError
from .const import (
    COMMON_SERVICES,
    CONF_API_KEY,
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
    CONF_URL,
    DOMAIN,
    LAUNCH_METHODS,
    POPULAR_TITLES,
    SOURCE_TMDB,
)
from .play import get_options

_LOGGER = logging.getLogger(__name__)

PREFERENCE_KEYS = (
    CONF_SERVICES,
    CONF_INCLUDE_FREE,
    CONF_PREFER_LIBRARY,
    CONF_TARGET,
    CONF_LAUNCH_METHOD,
    CONF_PLEX_ENTITY,
    CONF_PLEX_MOVIE_LIBRARY,
    CONF_PLEX_TV_LIBRARY,
    CONF_TRACK,
)


async def validate_connection(
    hass: HomeAssistant, url: str, api_key: str
) -> dict[str, str]:
    """Check the health endpoint and the API key, returning form errors."""
    api = YamtrackApi(async_get_clientsession(hass), url, api_key)
    try:
        await api.health()
        await api.validate_auth()
    except YamtrackAuthError:
        return {"base": "invalid_auth"}
    except YamtrackConnectionError:
        return {"base": "cannot_connect"}
    except YamtrackError as err:
        _LOGGER.debug("Yamtrack validation failed: %s", err)
        return {"base": "unhealthy"}
    return {}


async def fetch_service_names(api: YamtrackApi, region: str) -> list[str]:
    """Collect provider names from the providers of a few popular titles."""
    results = await asyncio.gather(
        *(
            api.providers(media_type, SOURCE_TMDB, media_id, region)
            for media_type, media_id in POPULAR_TITLES
        ),
        return_exceptions=True,
    )
    names: set[str] = set()
    for result in results:
        if isinstance(result, dict):
            names.update(
                offer["provider_name"]
                for offer in result.get("no_charge") or []
                if offer.get("provider_name")
            )
    return sorted(names, key=str.casefold)


def region_schema(region: str) -> vol.Schema:
    """Return the region form."""
    return vol.Schema({vol.Required(CONF_REGION, default=region): str})


def clean_region(region: str) -> str | None:
    """Return an upper case ISO 3166-1 alpha-2 code, None when invalid."""
    region = region.strip().upper()
    return region if len(region) == 2 and region.isalpha() else None


def preferences_schema(
    current: Mapping[str, Any], service_names: list[str]
) -> vol.Schema:
    """Return the preferences form, pre-filled with the current values."""
    options = get_options(dict(current))
    names = sorted(
        {*service_names, *COMMON_SERVICES, *options[CONF_SERVICES]},
        key=str.casefold,
    )

    def suggested(key: str) -> dict[str, Any]:
        return {"suggested_value": options.get(key)}

    return vol.Schema(
        {
            vol.Optional(
                CONF_SERVICES, default=options[CONF_SERVICES]
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=names,
                    multiple=True,
                    custom_value=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(CONF_INCLUDE_FREE, default=options[CONF_INCLUDE_FREE]): bool,
            vol.Required(
                CONF_PREFER_LIBRARY, default=options[CONF_PREFER_LIBRARY]
            ): bool,
            vol.Required(
                CONF_LAUNCH_METHOD, default=options[CONF_LAUNCH_METHOD]
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=LAUNCH_METHODS,
                    translation_key=CONF_LAUNCH_METHOD,
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
            vol.Optional(
                CONF_TARGET, description=suggested(CONF_TARGET)
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["remote", "media_player"])
            ),
            vol.Optional(
                CONF_PLEX_ENTITY, description=suggested(CONF_PLEX_ENTITY)
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="media_player")
            ),
            vol.Required(
                CONF_PLEX_MOVIE_LIBRARY, default=options[CONF_PLEX_MOVIE_LIBRARY]
            ): str,
            vol.Required(
                CONF_PLEX_TV_LIBRARY, default=options[CONF_PLEX_TV_LIBRARY]
            ): str,
            vol.Required(CONF_TRACK, default=options[CONF_TRACK]): bool,
        }
    )


def validate_preferences(user_input: dict[str, Any]) -> dict[str, str]:
    """Check that the chosen launch method has what it needs."""
    method = user_input.get(CONF_LAUNCH_METHOD)
    if method == "plex_only":
        if not user_input.get(CONF_PLEX_ENTITY) and not user_input.get(CONF_TARGET):
            return {CONF_PLEX_ENTITY: "plex_entity_required"}
        return {}
    target = user_input.get(CONF_TARGET)
    if not target:
        return {CONF_TARGET: "target_required"}
    expected = "remote" if method == "android_tv_remote" else "media_player"
    if not target.startswith(f"{expected}."):
        return {CONF_TARGET: f"target_{expected}"}
    return {}


def preference_values(user_input: dict[str, Any]) -> dict[str, Any]:
    """Keep the preference keys, with None for cleared entity pickers."""
    return {key: user_input.get(key) for key in PREFERENCE_KEYS}


class YamtrackConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up Yamtrack."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._data: dict[str, Any] = {}
        self._region: str = "US"
        self._service_names: list[str] = []

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return YamtrackOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the Yamtrack URL and API key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].strip().rstrip("/")
            await self.async_set_unique_id(url.lower())
            self._abort_if_unique_id_configured()
            errors = await validate_connection(self.hass, url, user_input[CONF_API_KEY])
            if not errors:
                self._data = {CONF_URL: url, CONF_API_KEY: user_input[CONF_API_KEY]}
                return await self.async_step_region()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_URL): selector.TextSelector(
                            selector.TextSelectorConfig(
                                type=selector.TextSelectorType.URL
                            )
                        ),
                        vol.Required(CONF_API_KEY): selector.TextSelector(
                            selector.TextSelectorConfig(
                                type=selector.TextSelectorType.PASSWORD
                            )
                        ),
                    }
                ),
                user_input,
            ),
            errors=errors,
        )

    async def async_step_region(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the region, then look up the services available there."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if region := clean_region(user_input[CONF_REGION]):
                self._region = region
                api = YamtrackApi(
                    async_get_clientsession(self.hass),
                    self._data[CONF_URL],
                    self._data[CONF_API_KEY],
                )
                self._service_names = await fetch_service_names(api, region)
                return await self.async_step_preferences()
            errors[CONF_REGION] = "invalid_region"

        return self.async_show_form(
            step_id="region", data_schema=region_schema(self._region), errors=errors
        )

    async def async_step_preferences(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the services and the launch settings."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = validate_preferences(user_input)
            if not errors:
                return self.async_create_entry(
                    title=self._data[CONF_URL],
                    data=self._data,
                    options={
                        CONF_REGION: self._region,
                        **preference_values(user_input),
                    },
                )

        return self.async_show_form(
            step_id="preferences",
            data_schema=preferences_schema(user_input or {}, self._service_names),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Ask for a new API key when the old one is rejected."""
        self._data = dict(entry_data)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate the new API key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await validate_connection(
                self.hass, self._data[CONF_URL], user_input[CONF_API_KEY]
            )
            if not errors:
                entry = self.hass.config_entries.async_get_entry(
                    self.context["entry_id"]
                )
                return self.async_update_reload_and_abort(
                    entry,
                    data={**self._data, CONF_API_KEY: user_input[CONF_API_KEY]},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    )
                }
            ),
            errors=errors,
        )


class YamtrackOptionsFlow(OptionsFlow):
    """Edit the region and preferences."""

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the options flow."""
        self._entry = entry
        self._region: str = entry.options.get(CONF_REGION, "US")
        self._service_names: list[str] = []

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the region."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if region := clean_region(user_input[CONF_REGION]):
                self._region = region
                api = YamtrackApi(
                    async_get_clientsession(self.hass),
                    self._entry.data[CONF_URL],
                    self._entry.data[CONF_API_KEY],
                )
                self._service_names = await fetch_service_names(api, region)
                return await self.async_step_preferences()
            errors[CONF_REGION] = "invalid_region"

        return self.async_show_form(
            step_id="init", data_schema=region_schema(self._region), errors=errors
        )

    async def async_step_preferences(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the services and the launch settings."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = validate_preferences(user_input)
            if not errors:
                return self.async_create_entry(
                    data={CONF_REGION: self._region, **preference_values(user_input)}
                )

        return self.async_show_form(
            step_id="preferences",
            data_schema=preferences_schema(
                user_input or self._entry.options, self._service_names
            ),
            errors=errors,
        )
