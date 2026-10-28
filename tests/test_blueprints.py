"""Run the shipped blueprints end to end."""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest
from pytest_homeassistant_custom_component.common import async_capture_events

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

BLUEPRINTS = Path(__file__).parent.parent / "blueprints"


@pytest.fixture(autouse=True)
def blueprint_config_dir(hass: HomeAssistant, tmp_path: Path) -> None:
    """Point the config dir at a copy of the repo's blueprints."""
    shutil.copytree(BLUEPRINTS, tmp_path / "blueprints")
    hass.config.config_dir = str(tmp_path)


async def setup_text_automation(hass: HomeAssistant, **inputs) -> None:
    """Set up an input_text and the "play what I type" automation."""
    assert await async_setup_component(
        hass, "input_text", {"input_text": {"tv_title": {"name": "TV title"}}}
    )
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "use_blueprint": {
                    "path": "yamtrack/play_from_text.yaml",
                    "input": {"text_entity": "input_text.tv_title", **inputs},
                }
            }
        },
    )


async def type_title(hass: HomeAssistant, title: str) -> None:
    """Type a title into the text box."""
    await hass.services.async_call(
        "input_text",
        "set_value",
        {"entity_id": "input_text.tv_title", "value": title},
        blocking=True,
    )
    await hass.async_block_till_done()


async def test_play_from_text(
    hass: HomeAssistant, setup_entry, mock_api, launch_calls
) -> None:
    """Typing a title plays it, runs the started actions and clears the box."""
    started = async_capture_events(hass, "test_started")
    await setup_text_automation(
        hass,
        target="remote.bedroom_tv",
        started_actions=[
            {"event": "test_started", "event_data": {"title": "{{ result.title }}"}}
        ],
    )

    await type_title(hass, "Dune")

    assert launch_calls[0].data["entity_id"] == "remote.bedroom_tv"
    assert started[0].data == {"title": "Dune"}
    assert hass.states.get("input_text.tv_title").state == ""


async def test_play_from_text_failure(
    hass: HomeAssistant, setup_entry, mock_api, launch_calls
) -> None:
    """A title that can't be played runs the failure actions."""
    failed = async_capture_events(hass, "test_failed")
    await setup_text_automation(
        hass,
        clear_text=False,
        failed_actions=[
            {"event": "test_failed", "event_data": {"error": "{{ result.error }}"}}
        ],
    )

    await type_title(hass, "The Notebook")

    assert not launch_calls
    assert failed[0].data == {"error": "not_found"}
    assert hass.states.get("input_text.tv_title").state == "The Notebook"


async def test_play_from_text_default_target(
    hass: HomeAssistant, setup_entry, mock_api, launch_calls
) -> None:
    """Without a TV input the configured target is used."""
    await setup_text_automation(hass)
    await type_title(hass, "Dune")
    assert launch_calls[0].data["entity_id"] == "remote.living_room_tv"


async def test_playback_started_filters(hass: HomeAssistant) -> None:
    """Actions run only for the chosen media type and source."""
    ran = async_capture_events(hass, "test_ran")
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "use_blueprint": {
                    "path": "yamtrack/playback_started.yaml",
                    "input": {
                        "media_type": "movie",
                        "played_via": "plex",
                        "started_actions": [
                            {
                                "event": "test_ran",
                                "event_data": {"title": "{{ result.title }}"},
                            }
                        ],
                    },
                }
            }
        },
    )

    for media_type, played_via, title in (
        ("movie", "plex", "Dune"),
        ("movie", "streaming", "Alien"),
        ("tv", "plex", "Severance"),
    ):
        hass.bus.async_fire(
            "yamtrack_play_started",
            {"title": title, "media_type": media_type, "played_via": played_via},
        )
    await hass.async_block_till_done()

    assert [event.data["title"] for event in ran] == ["Dune"]


async def test_play_title_script(
    hass: HomeAssistant, setup_entry, mock_api, launch_calls
) -> None:
    """The script plays the title and returns the play result."""
    notified = []
    hass.services.async_register(
        "notify", "phone", lambda call: notified.append(call.data)
    )
    assert await async_setup_component(
        hass,
        "script",
        {
            "script": {
                "play_title": {
                    "use_blueprint": {
                        "path": "yamtrack/play_title.yaml",
                        "input": {"notify_action": "notify.phone"},
                    }
                }
            }
        },
    )

    response = await hass.services.async_call(
        "script",
        "play_title",
        {"query": "Dune"},
        blocking=True,
        return_response=True,
    )
    assert response["provider"] == "Netflix"
    assert not notified

    response = await hass.services.async_call(
        "script",
        "play_title",
        {"query": "The Notebook"},
        blocking=True,
        return_response=True,
    )
    assert response["error"] == "not_found"
    assert notified[0]["message"] == "Couldn't find The Notebook"
