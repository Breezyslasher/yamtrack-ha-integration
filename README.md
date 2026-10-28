# Yamtrack for Home Assistant

Say or type "play Dune on the TV" and have it start. This custom integration
finds the title through the [Yamtrack](https://github.com/Breezyslasher/Yamtrack)
API, then opens it on your TV from a streaming service you pay for (JustWatch deep
link) or from your Plex library. It also marks the title as in progress in Yamtrack.

## Requirements

| Component | Version |
|---|---|
| Yamtrack | `Breezyslasher/Yamtrack`, the `justwatch` image tag (`feat/justwatch-links`, which includes `feat/add-api`) |
| Yamtrack env | `JUSTWATCH_ENABLED=True` for deep links |
| Home Assistant | 2024.8 or newer |
| Launch target | [Android TV Remote](https://www.home-assistant.io/integrations/androidtv_remote/), [Android TV (ADB)](https://www.home-assistant.io/integrations/androidtv/), or [Plex](https://www.home-assistant.io/integrations/plex/) |

Roku, Samsung Tizen and LG webOS don't accept web links this way and aren't supported.

## Install

1. In HACS, add this repository as a custom repository (category: Integration) and install **Yamtrack**.
2. Restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → Yamtrack**.

### Setup

1. **Connection**: your Yamtrack URL (for example `http://192.168.1.10:8000`) and API key
   (Yamtrack → Settings → Integrations). The integration checks `/api/v1/health/` and one authenticated call.
2. **Region**: a two letter country code (default `US`). Availability is per country.
3. **Preferences** (also under **Configure** later):
   - **My services**: filled from the providers of a few popular titles. You can type names that are missing.
   - **Include free ad-supported services**: also use Tubi, Pluto TV, The Roku Channel and similar.
   - **Prefer the Plex library first**: try Plex before streaming.
   - **Launch method**: `android_tv_remote` (Google TV), `adb` (Fire TV, Android boxes) or `plex_only`.
   - **Default launch target**: a `remote` entity for Android TV Remote, a `media_player` for ADB.
   - **Plex client** and **Plex movie / TV library** names for the library fallback.
   - **Mark as in progress on launch**.

## Actions

### `yamtrack.search`

Returns matching titles with their year and no-charge providers.

```yaml
action: yamtrack.search
data:
  query: Dune 2021
  media_type: movie   # or tv
  limit: 5
response_variable: found
```

Response: `{"results": [{media_id, source, media_type, title, year, image, providers: [{name, link, kind}]}]}`.
`kind` is `subscription` (on My services), `free` (a free service, when enabled) or `other`.

### `yamtrack.play`

```yaml
action: yamtrack.play
data:
  query: Dune 2021        # or media_id: "438631" (TMDB ID) to skip the search
  media_type: movie       # or tv
  target: remote.living_room_tv   # optional, defaults to the configured target
  source_preference: auto # auto, streaming or library
response_variable: played
```

For a show, add `season` and `episode` to play a specific episode. Without them the show plays its
next unwatched episode (see [TV episodes](#tv-episodes)).

Response on success: `{title, played_via, provider, link, media_id, media_type, image}`, plus
`season_number`, `episode_number` and `episode_title` for a show's episode. `played_via` is
`streaming` or `plex`. On failure: `{error, message, ...}` with `error` one of
`not_found`, `no_provider`, `launch_failed`. Without a `response_variable`, a failure raises an error instead.

Each successful play fires the event `yamtrack_play_started` with the response as its data.

## How a title is picked and played

1. Search Yamtrack (TMDB) and score the results against the query with `rapidfuzz`. A trailing year
   ("Dune 1984") picks the matching release. Weak matches return `not_found`.
2. If **Prefer the Plex library first** is on, try Plex: `media_player.play_media` with
   `plex://{"library_name": "Movies", "title": "Dune", "libtype": "movie"}`.
3. Fetch the watch providers and walk the no-charge offers in display priority order. Take the first
   one on My services that has a link. Paid (rent/buy) offers are never used.
4. Rewrite the link where needed, for example Netflix `/title/{id}` becomes `/watch/{id}` so playback starts right away.
5. Launch it:
   - `android_tv_remote`: `remote.turn_on` with `activity: <link>`
   - `adb`: `androidtv.adb_command` with `am start -a android.intent.action.VIEW -d '<link>'`.
     Netflix gets extra intent options (`-e source 30 com.netflix.ninja/.MainActivity`) so the movie starts
     instead of only opening the app.

   With Android TV Remote, Netflix often opens without playing, because only a link can be sent that way.
   Use the ADB method (the [Android TV](https://www.home-assistant.io/integrations/androidtv/) integration
   with ADB debugging enabled on the TV) if you want Netflix titles to start playing.
6. If no offer matched or the launch failed, and Plex wasn't tried yet, try Plex.
7. Set the Yamtrack status to In progress (starts tracking it if needed).

## TV episodes

When you play a show you track in Yamtrack, the integration finds the next episode to watch. It goes
through the seasons in order, skips specials and seasons marked completed, and picks the first episode
you haven't watched. It stops at episodes that haven't aired yet, using the Yamtrack calendar.

- **Plex** plays that exact episode (`show.title`, `season.index`, `episode.index`).
- **Streaming services** open the show, because JustWatch only has links for whole shows. Netflix and most
  apps then resume where your profile left off.
- The response, the `yamtrack_play_started` event and the spoken reply name the episode: "Playing
  Severance season 2 episode 3 on Netflix."

Shows you don't track, or where everything aired is watched, play as before.

## Sensors

Updated every 15 minutes and after each play. Each one lists up to 10 entries in its `items` attribute,
and its picture is the first entry's image.

| Sensor | State | `items` |
|---|---|---|
| `sensor.yamtrack_currently_watching` | Number of movies and shows in progress | `title`, `media_type`, `media_id`, `image`, `progress` |
| `sensor.yamtrack_up_next` | Next episode of the most recently updated show, like `Severance S02E03` | `show_title`, `season_number`, `episode_number`, `episode_title`, `label`, `image` |
| `sensor.yamtrack_recently_completed` | Last movie or show you completed | `title`, `media_type`, `end_date`, `score`, `image` |

Up next looks at the 10 most recently updated shows in progress.

## Media browser

Open **Media** in the sidebar and choose **Yamtrack** as the player (bottom right). You can browse:

- **Up next**: the next episode of each show you're watching
- **Currently watching**
- **Watchlist**: movies and shows marked *Planning*
- **Lists**: your Yamtrack lists

Press play on a title and it launches on your TV the same way `yamtrack.play` does. The Yamtrack player shows
the last title started. This is a media player entity rather than a media source: a media source can
only hand a player a stream URL, which can't start a streaming app on a TV.

## Assist

The integration registers the `YamtrackPlayMedia` intent. Copy
[`custom_sentences/en/yamtrack.yaml`](custom_sentences/en/yamtrack.yaml) to
`<config>/custom_sentences/en/` and restart. Example sentences:

- "play Dune on the living room TV"
- "put on The Matrix"
- "watch the show Severance on the bedroom TV"

When an area is named, the integration uses the `remote` (Android TV Remote) or `media_player` (ADB, Plex)
entity in that area, and the default target otherwise. Without "the show", a title that isn't found as a
movie is tried as a TV show. Replies: "Playing Dune on Netflix.", "Playing Dune from Plex.",
"I couldn't find Dune on your services."

## Blueprints

Ready-made automation templates. Click a button to import one, or copy the files from
[`blueprints/`](blueprints) into `<config>/blueprints/`. Then go to **Settings → Automations & scenes → Blueprints**
and create an automation (or script) from it.

| Blueprint | What it does | Import |
|---|---|---|
| **Play what I type** (`automation/yamtrack/play_from_text.yaml`) | Type a title into an `input_text` helper and it starts on the TV. Pick the media type, TV and source, run actions when it starts or fails, and clear the box afterwards. | [![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FBreezyslasher%2Fyamtrack-ha-integration%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fyamtrack%2Fplay_from_text.yaml) |
| **When playback starts** (`automation/yamtrack/playback_started.yaml`) | Runs your actions on every `yamtrack_play_started` event, like dimming the lights. Can be limited to movies or shows, and to streaming or Plex. | [![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FBreezyslasher%2Fyamtrack-ha-integration%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fyamtrack%2Fplayback_started.yaml) |
| **Play a title** (`script/yamtrack/play_title.yaml`) | A script with a `query` field for dashboard buttons and other automations. It returns the play result and can notify your phone when nothing could be played. | [![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FBreezyslasher%2Fyamtrack-ha-integration%2Fblob%2Fmain%2Fblueprints%2Fscript%2Fyamtrack%2Fplay_title.yaml) |

Actions in these blueprints get a `result` variable with the `yamtrack.play` response, for example
`{{ result.title }}`, `{{ result.provider }}`, or `{{ result.error }}` when it failed.

For **Play what I type**, first create the text box: **Settings → Devices & services → Helpers → Create helper → Text**.

A dashboard button for the **Play a title** script:

```yaml
type: button
name: Dune
icon: mdi:movie-open-play
tap_action:
  action: perform-action
  perform_action: script.play_title
  data:
    query: Dune 2021
```

## Caching and errors

- Search, details and provider responses are cached for 6 hours. Changing the options clears the cache.
- Requests time out after 10 seconds and are retried twice on 5xx responses only.
- When JustWatch is down, Yamtrack returns providers without links. The integration logs one warning a day and uses Plex.
- **Download diagnostics** on the integration gives the config with the API key redacted.

## Development

```bash
python -m venv venv && venv/bin/pip install -r requirements_test.txt ruff
venv/bin/pytest
venv/bin/ruff check . && venv/bin/ruff format --check .
```

## Roadmap

- **v2** (done): browse Yamtrack in the media browser, next episodes for shows, up next / watching / completed sensors.
- **v3**: mark as completed when playback ends, rent or buy prompts for paid-only titles.
