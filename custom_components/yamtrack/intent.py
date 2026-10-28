"""Assist intent: "play {media} on the {area} tv"."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    area_registry as ar,
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
    intent,
)

from .const import (
    CONF_LAUNCH_METHOD,
    DEFAULT_LAUNCH_METHOD,
    ERROR_LAUNCH_FAILED,
    ERROR_NO_PROVIDER,
    ERROR_NOT_FOUND,
    INTENT_PLAY_MEDIA,
    LAUNCH_ANDROID_TV_REMOTE,
    MEDIA_MOVIE,
    MEDIA_TV,
    MEDIA_TYPES,
    PLAYED_VIA_PLEX,
)

_LOGGER = logging.getLogger(__name__)


class YamtrackPlayMediaIntent(intent.IntentHandler):
    """Find a title with Yamtrack and start it on the TV."""

    intent_type = INTENT_PLAY_MEDIA
    description = "Plays a movie or a TV show on the TV"
    slot_schema = {
        vol.Required("media"): cv.string,
        vol.Optional("area"): cv.string,
        vol.Optional("media_type"): vol.In(MEDIA_TYPES),
    }

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Handle the intent."""
        # Imported here as __init__ imports this module
        from . import get_loaded_entry, run_play  # noqa: PLC0415

        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)
        media = slots["media"]["value"].strip()
        response = intent_obj.create_response()

        entry = get_loaded_entry(hass)
        method = entry.options.get(CONF_LAUNCH_METHOD, DEFAULT_LAUNCH_METHOD)
        target = None
        if "area" in slots:
            target = find_area_target(hass, slots["area"]["value"], method)

        # Without a type in the sentence, a title that isn't a movie may be a show
        media_types = (
            [slots["media_type"]["value"]]
            if "media_type" in slots
            else [MEDIA_MOVIE, MEDIA_TV]
        )
        result = None
        for media_type in media_types:
            try:
                result = await run_play(
                    hass, query=media, media_type=media_type, target=target
                )
            except HomeAssistantError as err:
                _LOGGER.warning("Yamtrack play failed: %s", err)
                response.async_set_speech("Sorry, I couldn't reach Yamtrack.")
                return response
            if result.error != ERROR_NOT_FOUND:
                break

        response.async_set_speech(speech_for(result, media))
        return response


def speech_for(result, media: str) -> str:
    """Return the spoken reply for a play result."""
    if result is None or result.error == ERROR_NOT_FOUND:
        return f"I couldn't find {media}."
    if result.error == ERROR_NO_PROVIDER:
        return f"I couldn't find {result.title} on your services."
    if result.error == ERROR_LAUNCH_FAILED:
        return f"I couldn't start {result.title} on the TV."
    if result.played_via == PLAYED_VIA_PLEX:
        return f"Playing {result.title} from Plex."
    return f"Playing {result.title} on {result.provider}."


def find_area_target(hass: HomeAssistant, area_name: str, method: str) -> str | None:
    """Return the launch entity in an area, None to use the default target."""
    area_reg = ar.async_get(hass)
    area = area_reg.async_get_area(area_name) or area_reg.async_get_area_by_name(
        area_name
    )
    if area is None and hasattr(area_reg, "async_get_areas_by_alias"):
        areas = area_reg.async_get_areas_by_alias(area_name)
        area = areas[0] if areas else None
    if area is None:
        return None

    domain = "remote" if method == LAUNCH_ANDROID_TV_REMOTE else "media_player"
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    entries = list(er.async_entries_for_area(ent_reg, area.id))
    for device in dr.async_entries_for_area(dev_reg, area.id):
        entries.extend(
            entry
            for entry in er.async_entries_for_device(ent_reg, device.id)
            if entry.area_id is None
        )

    matches = [
        entry for entry in entries if entry.domain == domain and not entry.disabled
    ]
    if not matches:
        return None
    # Prefer the platform that matches the launch method
    preferred = {
        LAUNCH_ANDROID_TV_REMOTE: "androidtv_remote",
        "adb": "androidtv",
        "plex_only": "plex",
    }.get(method)
    matches.sort(key=lambda entry: entry.platform != preferred)
    return matches[0].entity_id
