"""Tests for the config and options flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import API_KEY, URL
from custom_components.yamtrack.api import (
    YamtrackApiError,
    YamtrackAuthError,
    YamtrackConnectionError,
    YamtrackNotFoundError,
)
from custom_components.yamtrack.const import DOMAIN

PREFERENCES = {
    "services": ["Netflix"],
    "include_free": False,
    "prefer_library": True,
    "launch_method": "android_tv_remote",
    "target": "remote.living_room_tv",
    "plex_entity": "media_player.plex",
    "plex_movie_library": "Movies",
    "plex_tv_library": "TV Shows",
    "track": True,
}


async def test_full_flow(hass: HomeAssistant, mock_api) -> None:
    """The flow validates, offers looked up services and creates the entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": URL + "/", "api_key": API_KEY}
    )
    assert result["step_id"] == "region"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"region": "usa"}
    )
    assert result["errors"] == {"region": "invalid_region"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"region": "us"}
    )
    assert result["step_id"] == "preferences"
    services = result["data_schema"].schema["services"].config["options"]
    assert "Max" in services and "Netflix" in services
    mock_api["providers"].assert_awaited()

    # ADB needs a media player
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**PREFERENCES, "launch_method": "adb"}
    )
    assert result["errors"] == {"target": "target_media_player"}

    with patch("custom_components.yamtrack.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], PREFERENCES
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"url": URL, "api_key": API_KEY}
    assert result["options"] == {"region": "US", **PREFERENCES}


async def test_connection_errors(hass: HomeAssistant, mock_api) -> None:
    """Connection and auth errors are shown on the form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    mock_api["health"].side_effect = YamtrackConnectionError
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": URL, "api_key": API_KEY}
    )
    assert result["errors"] == {"base": "cannot_connect"}

    mock_api["health"].side_effect = None
    mock_api["validate_auth"].side_effect = YamtrackAuthError
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": URL, "api_key": API_KEY}
    )
    assert result["errors"] == {"base": "invalid_auth"}


async def test_options_flow(hass: HomeAssistant, setup_entry, mock_api) -> None:
    """Options can be changed and clear the cache."""
    coordinator = setup_entry.runtime_data.coordinator
    await coordinator.search("movie", "Dune", 5)
    assert coordinator.cache_size == 1

    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"region": "gb"}
    )
    assert result["step_id"] == "preferences"
    # Cleared entity pickers are left out of the submitted form
    preferences = {**PREFERENCES, "launch_method": "plex_only"}
    preferences.pop("target")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], preferences
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert setup_entry.options["region"] == "GB"
    assert setup_entry.options["target"] is None
    assert coordinator.cache_size == 0


async def test_reauth(hass: HomeAssistant, setup_entry, mock_api) -> None:
    """A new API key can be entered."""
    result = await setup_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"api_key": "new-key"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert setup_entry.data["api_key"] == "new-key"


async def test_failing_health_check_does_not_block(
    hass: HomeAssistant, mock_api, caplog
) -> None:
    """A failing Celery check is logged, the working API key lets setup go on."""
    mock_api["health"].return_value = {
        "status": "unavailable",
        "checks": {
            "Database(alias='default')": {"status": "ok", "error": None},
            "Ping": {"status": "error", "error": "No Celery workers"},
        },
    }
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": URL, "api_key": API_KEY}
    )
    assert result["step_id"] == "region"
    assert "No Celery workers" in caplog.text


async def test_api_errors_are_specific(hass: HomeAssistant, mock_api) -> None:
    """A missing API and an API error get their own messages."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    mock_api["validate_auth"].side_effect = YamtrackNotFoundError("/media/")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": URL, "api_key": API_KEY}
    )
    assert result["errors"] == {"base": "api_not_found"}

    mock_api["validate_auth"].side_effect = YamtrackApiError(500, "Server Error")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": URL, "api_key": API_KEY}
    )
    assert result["errors"] == {"base": "unhealthy"}
    assert result["description_placeholders"]["error"] == "HTTP 500: Server Error"
