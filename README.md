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

Response on success: `{title, played_via, provider, link, media_id, media_type}`, where
`played_via` is `streaming` or `plex`. On failure: `{error, message, ...}` with `error` one of
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
   - `adb`: `androidtv.adb_command` with `am start -a android.intent.action.VIEW -d '<link>'`
6. If no offer matched or the launch failed, and Plex wasn't tried yet, try Plex.
7. Set the Yamtrack status to In progress (starts tracking it if needed).

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

- **v2**: browse Yamtrack lists in the media browser, next episode deep links for shows, up next / watching sensors.
- **v3**: mark as completed when playback ends, rent or buy prompts for paid-only titles.
