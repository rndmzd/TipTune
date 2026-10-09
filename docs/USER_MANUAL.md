# TipTune User Manual

TipTune turns tip events into a **music request queue** (Spotify and/or YouTube) and provides a **local dashboard** for setup, queue control, status, and OBS overlays.

---

## Table of contents

- [What TipTune is](#what-tiptune-is)
- [What's changed recently](#whats-changed-recently)
- [Quick mental model](#quick-mental-model)
- [Install & launch](#install--launch)
- [Web UI pages](#web-ui-pages)
- [Spotify web player](#spotify-web-player-in-the-desktop-app)
- [Setup Wizard](#setup-wizard)
- [How tips become actions](#how-tips-become-actions)
- [Dashboard (Queue)](#dashboard-queue)
- [Settings](#settings)
- [Events](#events)
- [History](#history)
- [OBS integration](#obs-integration)
- [Configuration reference](#configuration-reference)
- [Environment variables](#environment-variables)
- [Logging](#logging)
- [Troubleshooting](#troubleshooting)

---

## What TipTune is

TipTune consists of:

- A **desktop app** (Tauri v2) for macOS/Linux/Windows.
- A **Python sidecar service** that:
  - Polls an Events API for tip events.
  - Parses tip messages into song requests.
  - Uses Spotify and/or YouTube to search tracks and control playback.
  - Optionally drives OBS overlays via obs-websocket.
- A **React Web UI** served locally by the Python service.

---

## What's changed recently

This section summarizes major behavior changes that were introduced since this manual was last updated.

### Unified queue (Spotify + YouTube)

TipTune now supports a unified queue that can contain items from multiple sources.

- Configure the default source with `Music.source=spotify|youtube`.
- The Dashboard/Settings UI is source-aware.
- Tip messages can override the default source by including the word `spotify` or `youtube` (when `General.allow_source_override_in_request_message` is `true`, the default).

### YouTube search + playback

YouTube is supported as a first-class source:

- Searching YouTube uses `yt-dlp`.
- Playback is handled in the Dashboard via an audio player that streams from `/api/youtube/stream`.
- TipTune only streams from allowed YouTube hosts (for example `youtube.com`, `*.youtube.com`, and `youtu.be`).
- Install Python deps (`pip install -r requirements.txt`).
- Download bundled binaries (`node scripts/fetch-binaries.mjs`) to get `yt-dlp` (and ffmpeg), or ensure `yt-dlp` is on your PATH.
- Packaged builds already bundle `yt-dlp`.

### Setup Wizard now includes Music source

The Setup Wizard includes a **Music source** choice under **General Settings**, which writes `Music.source`.

### Source override toggle

Tip messages can override the default music source by including the word `spotify` or `youtube`. This behavior is now controlled by `General.allow_source_override_in_request_message` (default `true`). Set to `false` to ignore source keywords in tip messages.

### TipTune audio capture for OBS

TipTune can now create an Application Audio Capture input in OBS targeting `TipTune.exe`. This is used to route YouTube playback audio into OBS. See [OBS integration](#obs-integration).

### Dashboard playback controls

The Dashboard now includes playback-level controls (pause/resume playback, seek) in addition to queue-level controls (pause/resume queue).

### History management

The History page now supports clearing all request history.

### Device status improvements

The Dashboard more proactively refreshes Spotify device state so the UI is less likely to show an incorrect `(none)` device state while Spotify is actually available.

### Queue state persistence and migration

Queue state is persisted under the TipTune cache directory. Older persisted formats (from earlier Spotify-only / YouTube-only queue implementations) are automatically migrated into the unified queue format.

---

## Quick mental model

- **Incoming tips** arrive via the configured **Events API** endpoint.
- TipTune decides:
  - Is this a **song request**?
  - Is this a **skip request**?
  - How many requests does it represent?
- For song requests:
  - TipTune extracts one or more songs from the tip message.
  - Each extracted song is resolved to a Spotify track URI or a YouTube URL, depending on the active music source.
  - The resolved item is added to TipTune’s internal queue and played via the appropriate source.
- TipTune can also:
  - Show overlays in OBS (requester, warnings, general, now playing).
  - Show a live event stream and processing history.

---

## Install & launch

### Desktop app (recommended)

- Download the latest build from GitHub Releases.
- Launch TipTune.

TipTune starts the service automatically and opens the Spotify page. Sign in to the player and use **Setup Wizard** in the top navigation to configure TipTune on first launch.

### From source (developers/power users)

Prereqs:

- Node.js 20
- Rust (stable)
- Python 3.11

Install deps:

```bash
npm ci
pip install -r requirements.txt
pip install -r requirements-build.txt
```

Run the desktop dev app:

```bash
npm run dev
```

Build locally:

```bash
npm run build
```

---

## Web UI pages

The UI is served from a local HTTP server (default `http://127.0.0.1:8765`).

- `/`
  - Dashboard (queue + now playing)
- `/spotify`
  - Spotify web player, automatically opened in the desktop app
- `/settings`
  - Full settings editor (including secrets)
  - Playback device selection
  - OBS overlay tools
  - App update check/install (desktop only)
- `/setup`
  - Setup Wizard
- `/events`
  - Recent events + live SSE stream
- `/history`
  - Request processing history (success/failure details)
- `/stats`
  - Aggregated stats from request history
- `/help`
  - In-app Help viewer (renders `docs/USER_MANUAL.md`)

### Setup redirect behavior

Until setup is marked complete, TipTune redirects most pages to `/setup`.

- **Rerun setup later**: `/setup?rerun=1`
- **Bypass redirect**: add `?dashboard=1` to `/` or `/settings`.

---

## Setup Wizard

The Setup Wizard is the recommended path for first-time configuration.

It guides you through:

- Spotify credentials + authorization
- Events API (optional, but required if you want TipTune to receive tips)
- OpenAI (optional, recommended for accurate parsing)
- Google Custom Search (optional)
- OBS integration (optional)
- General settings (song/skip costs, overlay duration)

### Spotify step

You will enter:

- `Spotify.client_id`
- `Spotify.redirect_url`

### Music

- `Music.source`
  - `spotify` or `youtube`
  - Controls the default music provider used for searching and playback.

Then:

- Click **Connect Spotify** and complete login in your browser.

### General step (Finish)

On the final step the wizard writes `General.setup_complete=true`.

---

## How tips become actions

TipTune treats each tip event (method `tip`) as a candidate for:

- **Song request(s)**
- **Skip current song**
- **Ignore** (not a request)

### Song request vs skip request

The decision is based on tip amount modulo your configured costs:

- **Song request** if `tip_amount % General.song_cost == 0`
- **Skip request** if `tip_amount % General.skip_song_cost == 0`

If a tip matches **both**, TipTune treats it as a **song request** (song requests take precedence).

### Request count (multiple requests in one tip)

For song requests, TipTune computes the number of requested songs.

Behavior depends on `General.multi_request_tips`:

- If `General.multi_request_tips=true` (default):
  - `request_count = tip_amount // General.song_cost` (minimum 1)
  - A tip that is a multiple of `song_cost` can request multiple songs.
- If `General.multi_request_tips=false`:
  - Only an exact `tip_amount == General.song_cost` triggers a song request.
  - Multiples (like `2 * song_cost`) do not increase request count.

That `request_count` determines how many songs TipTune attempts to extract from the tip message.

Example:

- If `song_cost=27` and `multi_request_tips=true`:
  - A `27` token tip requests **1 song**.
  - A `54` token tip requests **2 songs**.
- If `song_cost=27` and `multi_request_tips=false`:
  - A `27` token tip requests **1 song**.
  - A `54` token tip requests **1 song**.

### Tip message parsing rules

For song requests, TipTune reads the tip note/message text.

- If the message is **blank**, the request is marked failed and a warning overlay can be shown.
- Short titles (including one or two characters) and meaningful punctuation are preserved.

Extraction behavior:

- If the message contains a **Spotify track URI** (`spotify:track:...`) or a **Spotify track link** (`https://open.spotify.com/track/...`) and Spotify is available, TipTune can use that directly.
- If YouTube is the active source and the message contains a YouTube URL, TipTune can use that directly.
- Otherwise TipTune uses the **OpenAI Responses API with strict Structured Outputs** to extract **up to** `request_count` songs in message order. It never fills unused slots with invented songs.
- Mixed Spotify links and text are processed together. Equivalent links are deduplicated. Successful requests can continue when another lookup fails.
- Spotify is searched first. Exact title-and-artist matches are accepted directly; otherwise the model chooses the best relevant match from actual Spotify candidates. Requested versions require supporting catalog metadata; studio recordings are preferred when no version is specified. Explicit artist requests are not replaced by unrelated cover or tribute artists. Very short titles must match the catalog title exactly. If no eligible track is found, the request fails instead of silently substituting a different title or recording.
- If Spotify finds no plausible match and Google keys are configured, TipTune uses up to three **Google Custom Search** results as evidence for one further Spotify search. Google results cannot be queued directly.
- Missing artists are filled from the selected Spotify track. Best-effort matching can still choose the wrong interpretation of an ambiguous title; including the artist or a track link is more precise.
- Unrelated chatter, model/API errors, and unmatched songs are recorded as failures rather than searched as entire messages. Only successful queue insertions are recorded as added.

### Market availability check

After resolving a Spotify URI, TipTune checks market availability.

- Searches and the final track check use the Spotify account market, falling back to US when the account country is unavailable. Unplayable/restricted tracks and failed availability lookups are rejected.

---

## Dashboard (Queue)

Open: `/`

The dashboard shows:

- Queue status (Running / Paused)
- Active playback device status (Spotify)
- Now playing
- Up next queue

### Queue controls

- **Pause Queue**
  - Pauses the queue logic. The current song can finish first.
- **Resume Queue**
  - Unpauses queue logic.
- **Next / Skip**
  - Advances the queue to the next track.
- **Refresh**
  - Refreshes the dashboard state.

### Playback controls

- **Pause Playback**
  - Pauses the currently playing track without affecting the queue.
- **Resume Playback**
  - Resumes the paused track.
- **Seek**
  - Seeks to a position within the currently playing track.

### Add Track (manual)

Use this for testing or manual queueing:

- Click **Add Track**
- Search Spotify or YouTube (depending on selected source)
- Click **+** to add to the end of the queue

### Reorder / remove

- Drag the handle to reorder (or use Up/Down buttons).
- Use Delete/Remove to remove a queued item.

### Send “Now Playing” to OBS

In Browser display mode, **Send info to OBS** announces the current Spotify or
YouTube song through the browser overlay. It works without an OBS WebSocket
connection. Legacy text mode requires OBS to be connected.

### Queue persistence

TipTune persists queue state to a JSON file under the TipTune cache directory (e.g. `queue_state.json`).

---

## Settings

Open: `/settings`

Settings are grouped into:

- Playback Device
- Events API
- OpenAI
- Spotify
- Music
- OBS
- Search
- General
- App Updates (desktop only)

### Secrets handling

Some settings are treated as secrets (entered as password fields). In the UI:

- Leaving a secret field blank typically means **“keep existing value”** rather than overwriting with empty.

### Playback Device

TipTune needs a Spotify device to control playback.

Steps:

- Open Spotify on your intended device.
- Start playback briefly (helps Spotify register an active device).
- In TipTune Settings:
  - Click **Refresh**
  - Select a device
  - Click **Apply + Save** (writes `Spotify.playback_device_id`)

### Events API

- `Events API.url`
  - The endpoint TipTune polls for events.
  - If this is blank, TipTune won’t receive tips.
- `Events API.max_requests_per_minute`
  - Rate limiting for polling.

### OpenAI

- `OpenAI.api_key`
  - Enables AI-assisted parsing.
- `OpenAI.model`
  - Model name (default for new or blank settings: `gpt-6-luna`). Saved choices are preserved. `gpt-6-sol` and `gpt-6-astra` are also supported when available to your API account. The model must support Responses Structured Outputs.

### Spotify

- `Spotify.client_id`
- `Spotify.redirect_url`

Redirect URL rules:

- Must be `http://127.0.0.1:<port>/<path>` or `http://localhost:<port>/<path>`
- Must include an explicit port
- Must match exactly what you configured in the Spotify Developer Dashboard

### OBS

- `OBS.enabled` toggles all OBS features.
- `OBS.host`, `OBS.port`, `OBS.password` configure obs-websocket.
- `OBS.scene_name` controls which scene TipTune targets when creating/validating required text sources.

When enabled, Settings provides:

- **OBS overlay status**
  - Connected/not connected
  - Current scene and main scene
  - Required text source presence
- **Create missing text sources**
- **Create Spotify audio capture** (Windows, Application Audio Capture for `Spotify.exe`)
- **Create TipTune audio capture** (Windows, Application Audio Capture for `TipTune.exe` — routes YouTube playback audio into OBS)
- **Test overlays**

### Search (Google Custom Search)

Optional fallback when Spotify cannot identify a plausible match. Normal requests search Spotify first.

Used to improve song metadata when the artist is missing.

- `Search.google_api_key`
- `Search.google_cx`

### General

- `General.song_cost`
- `General.skip_song_cost`
- `General.multi_request_tips`
- `General.allow_source_override_in_request_message`
- `General.request_overlay_duration` (seconds)
- `General.auto_check_updates` (UI toggle)
- `General.show_debug_data` (UI toggle)
- `General.debug_log_to_file`
- `General.debug_log_path`

Logging notes:

- `debug_log_to_file=true` enables `DEBUG` level file logging.
- `debug_log_to_file=false` keeps file logging at `INFO` level.
- `debug_log_path` defaults to `<app_dir>/logs/tiptune-debug.log` when empty.
- Relative paths are resolved from `<app_dir>` (the TipTune app/executable directory).

`General.multi_request_tips` controls whether a single tip can request multiple songs when the tip amount is a multiple of `song_cost`.

- When `true` (default):
  - Example: if `song_cost=27`, then `54` tokens requests **2 songs**.
- When `false`:
  - Example: if `song_cost=27`, then only `27` tokens requests **1 song**.
  - `54` tokens does **not** request 2 songs.

`General.allow_source_override_in_request_message` controls whether tip messages can override the default music source.

- When `true` (default): including the word `spotify` or `youtube` in a tip message overrides the default `Music.source` for that request.
- When `false`: source keywords in tip messages are ignored; all requests use `Music.source`.

`General.show_debug_data` toggles the display of debug information in the UI.

---

## Events

Open: `/events`

This page shows:

- Recent events
- A live stream via **Server-Sent Events (SSE)** from `/api/events/sse`

Use it to confirm that TipTune is receiving and processing the Events API payloads.

---

## History

Open: `/history`

This page shows recent processing results for song requests.

You can **clear all history** using the clear button.

TipTune saves the latest **1,000 request entries** by default. Set **Settings → General → Request history size** to change the capacity (`General.request_history_size` in `config.ini`). Use a positive whole number. Saving applies the new capacity immediately; lowering it removes the oldest excess entries. History and Stats can load up to this capacity, and Export includes all retained entries.

Typical statuses:

- `added`
- `failed`

Failures include an `error` field such as:

- `blank tip message`
- `spotify track not found`
- `not available in market`

---

## OBS integration

TipTune provides one transparent Browser Source for Now Playing, upcoming songs,
request acknowledgments, warnings, and queue notices. The OBS WebSocket
connection enables automatic setup; manual browser display works without it.

### Browser display

Open **Stream information display** in Settings or the Setup Wizard. Fresh
installations use **Browser display**. Save connection details and appearance
changes, select an OBS scene, then use **Add browser overlay**. Existing users
can use **Switch to browser overlay**: creation is verified before changing
modes, and the four old text items are hidden in the selected scene while their
inputs are retained. Source-name conflicts are reported instead of overwriting
unrelated sources. Adding again reuses the input and preserves existing scene
item transforms. Add the same existing input to other scenes as needed.

For manual setup, copy the displayed URL (normally
`http://127.0.0.1:8765/overlay`) into an OBS Browser Source. Match the OBS base
canvas width and height, use 30 FPS, and leave both **Shutdown source when not
visible** and **Refresh browser when scene becomes active** off. TipTune and OBS
must run on the same computer. Keep the source fitted to the canvas; position
its panels through TipTune's controls.

The Full and Compact presets support four corner positions, 50–150% scale,
0–200 reference pixels of edge margin, 1–5 upcoming songs, panel/artwork toggles,
three colors, panel opacity, and optional subtle motion. Layout scales to the
browser viewport and fits portrait and landscape canvases. Requester names
appear when recorded with a request; older or manually added songs omit unknown
names. No playback progress or countdown is displayed.

**Sample** preview uses illustrative songs; **Live** preview uses the current
queue. Unsaved styling and **Preview …** buttons affect only the preview.
**Full canvas** checks position and scale; **Inspect panels** provides readable, scrollable detail without changing broadcast settings.
Saved styling applies immediately to connected displays. **Send … to OBS**
buttons deliberately send a test to the actual overlay. OBS connection, source
configuration, and display-client count are separate indicators; a connected
display client does not by itself prove the source is visible on your stream.

Start TipTune first. If OBS loaded the browser before the backend was ready,
refresh the source; with the OBS connection enabled, TipTune attempts a startup
refresh automatically. On a lost connection, alerts clear immediately and
persistent content hides after 15 seconds without a heartbeat. Reconnection
restores current state without replaying expired messages. Alert durations use
`General.request_overlay_duration` by default; at most three alerts are visible
at once, with a bounded queue for bursts.

Appearance is stored in `[Overlay]` in `config.ini`. Configurations without
`Overlay.mode` retain text mode when OBS was enabled or Off otherwise. To switch
back, select **Legacy text sources** and save; the existing input names remain
available. Other scenes are retained during migration, so hide old text items
there when reusing the browser input. Audio continues through the existing
Spotify and TipTune audio-capture sources.

### Prereqs

- Install OBS Studio.
- Enable obs-websocket in OBS.
  - Commonly: **Tools → WebSocket Server Settings**

### Legacy text sources

TipTune expects text sources with these names:

- `SongRequester`
- `WarningOverlay`
- `GeneralOverlay`
- `NowPlayingOverlay`

You can create them manually, but the easiest path is:

- Settings → **Create missing text sources**

After creation:

- Go to OBS and set size/position of each text source.

### Audio capture inputs (Windows)

TipTune can also create Application Audio Capture inputs in OBS:

- **Spotify Audio** — captures `Spotify.exe` audio for Spotify playback.
- **TipTune Audio** — captures `TipTune.exe` audio for YouTube playback (routes the in-app YouTube audio player into OBS).

Use the corresponding buttons in Settings:

- **Create Spotify audio capture**
- **Create TipTune audio capture**

### Testing

In Settings:

- Use **Test overlays** to confirm each overlay appears.

Overlay duration is controlled by:

- `General.request_overlay_duration`

---

## Configuration reference

TipTune uses a single `config.ini` file.

- **Do not commit or share `config.ini`** (it contains secrets).

### Where config lives

TipTune picks the config path in this order:

- **Override**: `TIPTUNE_CONFIG`
- **Packaged (frozen) runtime**
  - `config.ini` next to the executable (portable) if present
  - otherwise a per-user config directory
- **Development**: `<repo_root>/config.ini`

If `config.ini` does not exist yet, TipTune seeds it from `config.ini.example` on first write.

### Config sections

See `config.ini.example` for the template.

- `[Spotify]`
  - `client_id`, `redirect_url`, `playback_device_id`
- `[OpenAI]`
  - `api_key`, `model`
- `[Events API]`
  - `url`, `max_requests_per_minute`
- `[Search]`
  - `google_api_key`, `google_cx`
- `[OBS]`
  - `enabled`, `host`, `port`, `password`, `scene_name`
- `[Overlay]`
  - `mode` (`browser`, `text`, or `off`), `source_name`, `layout`, `position`
  - `scale`, `margin`, `queue_length`, `opacity`, `motion`
  - `show_now_playing`, `show_queue`, `show_alerts`, `show_artwork`
  - `accent_color`, `background_color`, `text_color`
- `[Web]`
  - `host`, `port`
- `[Music]`
  - `source`
- `[General]`
  - `song_cost`, `skip_song_cost`, `multi_request_tips`, `allow_source_override_in_request_message`, `request_overlay_duration`, `setup_complete`, `auto_check_updates`, `show_debug_data`, `debug_log_to_file`, `debug_log_path`

---

## Environment variables

Supported environment overrides:

- `TIPTUNE_CONFIG`: full path to the `config.ini` file
- `TIPTUNE_CACHE_DIR`: directory for TipTune cache usage
- `TIPTUNE_SPOTIPY_CACHE`: full path to the Spotipy token cache file (defaults to `<config_dir>/.cache`)
- `TIPTUNE_WEB_HOST`: override Web UI bind host
- `TIPTUNE_WEB_PORT`: override Web UI bind port
- `TIPTUNE_LOG_LEVEL`: log level (example: `INFO`, `DEBUG`)
- `TIPTUNE_LOG_PATH`: write logs to a file at this path
- `TIPTUNE_LOG_LEVEL_FORCE`: apply `TIPTUNE_LOG_LEVEL` even if debug logging is disabled
- `TIPTUNE_LOG_PATH_FORCE`: honor `TIPTUNE_LOG_PATH` even if debug logging is disabled
- `TIPTUNE_DEFAULT_LOG_PATH`: override the default log path when `debug_log_path` is empty

---

## Logging

TipTune writes logs to both standard output and a log file.

### Default location

If you do not set a custom path, TipTune writes to:

- `<app_dir>/logs/tiptune-debug.log`
  - `<app_dir>` is the TipTune app/executable directory (for example, where `TipTune.exe` lives).

### Log level behavior

- Default file and console log level is `INFO`.
- Enabling `General.debug_log_to_file` switches file logging to `DEBUG`.
- `TIPTUNE_LOG_LEVEL` is only honored when:
  - `General.debug_log_to_file` is enabled, or
  - `TIPTUNE_LOG_LEVEL_FORCE` is set.

### Path resolution

- `General.debug_log_path` overrides the default log path.
- Relative paths are resolved from `<app_dir>`.
- Environment variables and `~` are expanded.
- `%CD%` is replaced with the current working directory.
- TipTune creates the parent directory for the log file if needed.

### Examples

- Windows: `C:\\TipTune\\logs\\tiptune-debug.log`
- macOS: `/Applications/TipTune/logs/tiptune-debug.log`
- Linux: `/opt/tiptune/logs/tiptune-debug.log`
- Relative path (resolved from `<app_dir>`): `logs\\tiptune-debug.log`
- Use `%CD%`: `%CD%\\logs\\tiptune-debug.log`
- Use env var: `%USERPROFILE%\\TipTune\\logs\\tiptune-debug.log`

### Override precedence (highest to lowest)

1. `TIPTUNE_LOG_PATH` (with optional `TIPTUNE_LOG_PATH_FORCE`)
2. `General.debug_log_path`
3. `TIPTUNE_DEFAULT_LOG_PATH`
4. `<app_dir>/logs/tiptune-debug.log`

---

## Troubleshooting

### Spotify authorization problems

- Settings → Spotify shows the API connection status and offers **Connect Spotify** / **Reconnect Spotify**. Save changes to Client ID or redirect URL before connecting. Device discovery refreshes automatically after authorization completes.
- The Spotify tab's website login and TipTune's API authorization are separate; both must use the same account.
- `invalid_client` means Spotify rejected API client authorization, including refreshes of older saved logins. Reconnect first to obtain a fresh login. If it still fails, verify that the app exists and is enabled in your [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) and its Client ID matches Settings; save any changes, then reconnect. Reloading the embedded player does not repair API authorization.
- An expired or revoked API authorization requires reconnecting. If Spotify denies API access, check the app's allowed users and account requirements in the Developer Dashboard.
- Verify `Spotify.redirect_url` matches exactly and is registered in the Spotify Developer Dashboard.
- Redirect URL must be `http` and use `127.0.0.1` or `localhost` with an explicit port.

### “No active device” / device not listed

- Read any error beside **Available devices** first. API/network failures are shown separately from a successful response containing no devices.
- Confirm the player is signed in to the same account authorized for TipTune.
- Open Spotify on the device you want to control.
- Start playback briefly.
- Return to Settings → Playback Device → **Refresh**.

### Tips are not being processed

- Check Settings → Events API:
  - `Events API.url` must be set.
- Open `/events` and verify events are streaming.

### Requests failing with “blank tip message”

TipTune requires a tip note/message for song requests.

If the message is blank, TipTune will fail the request and may warn that the note could have been removed due to blocked words.

### Requests failing with “spotify track not found”

- Include both artist and song title in the message when possible.
- Try providing a Spotify track link.

### OBS not connected

- Confirm obs-websocket is enabled.
- Confirm host/port/password match OBS settings.
- Use Settings → Refresh OBS status.

---
## Spotify web player in the desktop app

The desktop app opens its Spotify page at startup. Use **Spotify** in the top navigation to return to it. The player stays loaded while you use the dashboard or other pages.

Sign in directly on Spotify's page with the same account you connected during TipTune setup. Spotify API credentials authorize API access; they cannot sign you into Spotify's website. The desktop app keeps the player's cookies and local storage between launches. Spotify can still expire or revoke a session, and signing out requires another login.

After signing in, select the embedded **Web Player** in TipTune's **Settings → playback device** list. Start a song in the player if needed to make the device available, then resume your request queue. The app opens the player automatically but does not start music just by launching.

**Reload player** returns to Spotify's home page without clearing your login. Login popups belong to Spotify; closing one does not close TipTune. Some login providers may restrict embedded browsers; Spotify's direct email login is available on its sign-in page. If your machine cannot play protected content in its embedded browser, use Spotify in a supported external browser and select that device in Settings.
