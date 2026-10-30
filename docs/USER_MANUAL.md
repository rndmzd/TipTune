# TipTune User Manual

TipTune turns tips into a **music request queue** for Spotify and YouTube. Use the dashboard to manage playback, connect your tip source, and display stream information in OBS.

New to TipTune? Start with [Get started](#get-started). Before going live, use the [Before your stream](#before-your-stream) checklist. For a problem, jump to [Troubleshooting](#troubleshooting).

---

## Table of contents

- [What TipTune is](#what-tiptune-is)
- [Get started](#get-started)
- [Before your stream](#before-your-stream)
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
- [Stats](#stats)
- [OBS integration](#obs-integration)
- [Updates](#updates)
- [Configuration reference](#configuration-reference)
- [Environment variables](#environment-variables)
- [Logging](#logging)
- [Troubleshooting](#troubleshooting)

---

## What TipTune is

TipTune consists of:

- A **desktop app** that starts its local service automatically.
- A **Python sidecar service** that:
  - Polls an Events API for tip events.
  - Parses tip messages into song requests.
  - Uses Spotify and/or YouTube to search tracks and control playback.
  - Optionally drives OBS overlays via obs-websocket.
- A **React Web UI** served locally by the Python service.

---

## Get started

1. Install and launch TipTune, then complete the [Setup Wizard](/setup). You can choose **Set up later** for Spotify and the Events API to try manual YouTube playback first.
2. Choose your default **Music source** in **Settings → Playback**. One queue can contain both Spotify and YouTube songs.
3. For Spotify, connect your API account during setup and configure an **OpenAI API key** for request playback. Open [Spotify](/spotify) and sign in with the same Spotify account. The website and API use separate logins. On Windows, TipTune automatically selects and activates **Web Player (Microsoft Edge)** when Spotify makes it available.
4. In the [Dashboard](/), select **Add Track**, choose a source, search for a song, then use **+** to add it. Confirm that you can hear it before setting up incoming tips.
5. To receive tips, save your **Events API** URL. Configure an **OpenAI API key** for text requests and Spotify request playback. Google Search and OBS are optional. See [Setup Wizard](#setup-wizard) for details.
6. Set **Song cost** and **Skip song cost** in **Settings → General**, then select **Save changes**. Advertise the exact song price: one qualifying tip requests one song.

Packaged desktop builds include the YouTube playback tools. You do not need to install Python, yt-dlp, Deno, or ffmpeg separately to use an installed build.

## Before your stream

- Open TipTune and confirm that the Dashboard queue is **Running**. **Pause Queue** stops automatic queue advancement; use the playback **Pause** button when you want to pause the audio itself.
- Play one manually added song. For Spotify, check the active device; for YouTube, confirm that playback is audible.
- Open [Events](/events) and confirm incoming events from your configured tip source. A successful manual add tests playback, but does not test tip delivery or message parsing.
- Check your saved song and skip prices. With the default costs, **27 tokens** requests one song and **51 tokens** requests a skip. A **54-token** tip does not buy two songs.
- If using OBS, check the actual scene and the audio meter. A connected browser display alone does not prove the source is visible or that the stream has audio.
- Keep TipTune running while taking requests. Use [History](/history) to investigate a failed request; use [Stats](/stats) to review retained request activity.

---

## Quick mental model

- **Incoming tips** arrive via the configured **Events API** endpoint.
- TipTune decides:
  - Is this a **song request**?
  - Is this a **skip request**?
- For song requests:
  - TipTune extracts at most one song from the tip message.
  - The extracted song is resolved to a Spotify track URI or a YouTube URL, depending on the active music source.
  - The resolved item is added to TipTune’s internal queue and played via the appropriate source.
- TipTune can also:
  - Show overlays in OBS (requester, warnings, general, now playing).
  - Show a live event stream and processing history.

---

## Install & launch

### Desktop app (recommended)

- Download the latest installer from [GitHub Releases](https://github.com/rndmzd/TipTune/releases/latest).
- Launch TipTune.

TipTune starts the service automatically. A new installation opens the **Setup Wizard**; configured installations open the Dashboard. Sign in to the embedded player from the **Spotify** tab after connecting your Spotify API account, or choose **Set up later** to start with YouTube.

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
  - Embedded Spotify web player in the desktop app; sign in from this tab
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
  - This manual, with a **Jump to section** menu and **Back to top** button

### Using Help

Use **Jump to section** or the table of contents to move through the manual. The header and section menu stay visible while the manual scrolls. **Back to top** returns to the beginning. Links to TipTune pages open in the dashboard; external websites open in a separate tab or window.

If Help cannot load or shows no content, select **Retry**. If it still cannot load, restart TipTune and open Help again.

### Setup redirect behavior

Until setup is marked complete, TipTune redirects the Dashboard, Settings, Events, History, and Stats to `/setup`. Help and the Spotify tab remain available.

- **Rerun setup later**: `/setup?rerun=1`
- **Bypass redirect**: add `?dashboard=1` to `/` or `/settings`.

---

## Setup Wizard

The Setup Wizard is the recommended path for first-time configuration.

It guides you through:

- Spotify credentials + authorization
- Events API (optional, but required if you want TipTune to receive tips)
- OpenAI (required for text requests and Spotify request playback; optional for trying manual YouTube playback)
- Google Custom Search (optional)
- OBS integration (optional)
- General settings (song/skip costs, overlay duration)

### Spotify step

You will enter:

- `Spotify.client_id`
- `Spotify.redirect_url`

Then click **Connect Spotify** and complete login in your browser. Open the **Spotify** tab separately to sign in to the embedded player with the same account.

### Music

- `Music.source`
  - `spotify` or `youtube`
  - Controls the default music provider used for searching and playback.

YouTube playback does not require Spotify credentials. A direct `youtube.com/watch?v=…` or `youtu.be/…` link in a request using the YouTube source can bypass text extraction; ordinary title-and-artist requests require OpenAI configuration. If your default source is Spotify, include the word `youtube` to select YouTube for that request when source override is enabled.

### General step (Finish)

On the final step the wizard writes `General.setup_complete=true`.

---

## How tips become actions

TipTune treats each tip event (method `tip`) as a candidate for:

- **Song request**
- **Skip current song**
- **Ignore** (not a request)

### Song request vs skip request

The decision is based on the tip amount and your configured costs:

- **Song request** if `tip_amount == General.song_cost`
- **Skip request** if `tip_amount % General.skip_song_cost == 0`

If a tip matches **both**, TipTune treats it as a **song request** (song requests take precedence).

For example, with the default song cost of 27 and skip cost of 51:

| Tip amount | Result |
| --- | --- |
| 27 | Request the first identifiable song in the message |
| 54 | No song request or skip |
| 51 or 102 | Attempt to skip the current song once |

These examples change when you change your prices. Multiples of the skip price trigger one skip action, not a series of skips.

### One song per tip

Each qualifying tip requests at most the first identifiable song in its message.
The amount must match `General.song_cost` exactly. With `song_cost=27`, a 27-token
tip requests one song, and a 54-token tip does not trigger a song request.

### Tip message parsing rules

For song requests, TipTune reads the tip note/message text.

- If the message is **blank**, the request is marked failed and a warning overlay can be shown.
- Short titles (including one or two characters) and meaningful punctuation are preserved.

Extraction behavior:

- If the message contains a **Spotify track URI** (`spotify:track:...`) or a **Spotify track link** (`https://open.spotify.com/track/...`) and Spotify is available, TipTune can use that directly.
- If YouTube is the active source and the message contains a YouTube URL, TipTune can use that directly.
- Otherwise TipTune uses the **OpenAI Responses API with strict Structured Outputs** to extract at most one song. It uses the first identifiable request in message order and does not invent missing songs.
- Spotify links and text are considered together, returning at most one request. Equivalent links are deduplicated.
- For Spotify requests, Spotify is searched first. Exact title-and-artist matches are accepted directly; otherwise the model chooses the best relevant match from actual Spotify candidates. Requested versions require supporting catalog metadata; studio recordings are preferred when no version is specified. Explicit artist requests are not replaced by unrelated cover or tribute artists. Very short titles must match the catalog title exactly. If no eligible track is found, the request fails instead of silently substituting a different title or recording.
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

- **Pause** (⏸)
  - Pauses the currently playing track without affecting the queue.
- **Play** (▶)
  - Resumes the paused track.
- **Seek**
  - Seeks to a position within the currently playing track.

### Add Track (manual)

Use this for testing or manual queueing:

- Click **Add Track**
- Choose Spotify or YouTube in the source selector and search by title and artist
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

### Playback while using other pages

The header's compact player provides the current title, artist, playback controls, and seek bar while you use Settings, History, and other pages. The embedded Spotify player remains loaded when you leave its tab. Keep the TipTune app open for YouTube playback and request handling.

---

## Settings

Open: `/settings`

Settings panels are stacked in one column. Use the menu on the left to jump to a
panel; smaller windows use a **Settings section** selector. **Save changes** stays
pinned at the top when you edit settings. Moving between panels preserves your
unsaved changes.

Settings are grouped into:

- Playback (music source, source override, and Spotify playback device)
- General (request history size and song/skip costs)
- Logging (dashboard/history debug output, file logs, and error reporting)
- Events API
- OpenAI
- Spotify
- Search
- OBS
- Stream information display (including OBS overlay duration)
- OBS audio sources (when the OBS connection is enabled)
- App Updates (desktop only)

### Secrets handling

Some settings are treated as secrets (entered as password fields). In the UI:

- Leaving a secret field blank typically means **“keep existing value”** rather than overwriting with empty.

### Playback Device

TipTune needs a Spotify device to control playback.

Steps:

- Sign in to the embedded player or open Spotify on your intended device, using the account authorized for TipTune.
- On Windows, TipTune automatically prefers **Web Player (Microsoft Edge)** when it is available and unrestricted, including if it appears after startup. Activation preserves the current play/pause state.
- To choose another device for the current session, use the controls below. A saved device remains the fallback when the embedded player is unavailable.
- In TipTune Settings:
  - Click **Refresh**
  - Select a device
  - Click **Apply + Save** (writes `Spotify.playback_device_id`)

If the list is empty, start playback briefly in Spotify and refresh. Read any error beside **Available devices** first: an API authorization failure needs a different fix from a successful response with no devices. See [Spotify authorization problems](#spotify-authorization-problems).

### Events API

- `Events API.url`
  - The endpoint TipTune polls for events.
  - If this is blank, TipTune won’t receive tips.
- `Events API.max_requests_per_minute`
  - Rate limiting for polling.

### OpenAI

- `OpenAI.api_key`
  - Required for extracting ordinary title-and-artist requests and Spotify-link requests from tip messages. Spotify request playback also needs this configuration. Manual YouTube search and playback do not need this key.
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

- `OBS.enabled` enables the OBS WebSocket connection for automatic setup and source management. Browser display is controlled separately by **Show stream information display** (`Overlay.mode`).
- `OBS.host`, `OBS.port`, `OBS.password` configure obs-websocket.
- `OBS.scene_name` selects the scene used for automatic overlay and audio-source setup.

When enabled, Settings provides the OBS connection, **Add to OBS** scene/browser-source setup, **Stream information display** previews and send buttons, and **OBS audio sources**. Configured sections collapse and retain their status labels. If OBS is closed, the page waits for OBS instead of reporting downstream sources as missing.

### Search (Google Custom Search)

Optional fallback for Spotify requests when Spotify cannot identify a plausible match. These requests search Spotify first; YouTube requests use YouTube lookup directly.

Used to improve song metadata when the artist is missing.

- `Search.google_api_key`
- `Search.google_cx`

### General

- `General.song_cost`
- `General.skip_song_cost`
- `General.request_history_size`

The **Playback** panel places **Allow Source Override In Request Message** just
below **Music source** (`General.allow_source_override_in_request_message`).
**OBS overlay duration (seconds)** is in **Stream information display**
(`General.request_overlay_duration`). **Automatically check for updates** remains
in **App Updates** (`General.auto_check_updates`).

### Logging settings

- `General.show_debug_data` (UI toggle)
- `General.show_history_debug` (off by default)
- `General.send_error_reports` (off by default)
- `General.debug_log_to_file`
- `General.debug_log_path`

Logging notes:

- `debug_log_to_file=true` enables `DEBUG` level file logging.
- `debug_log_to_file=false` disables file logging, unless an environment override explicitly forces it. Normal console logging remains at `INFO`.
- `debug_log_path` defaults to `<app_dir>/logs/tiptune-debug.log` when empty.
- Relative paths are resolved from `<app_dir>` (the TipTune app/executable directory).

`General.allow_source_override_in_request_message` controls whether tip messages can override the default music source.

- When `true` (default): including the word `spotify` or `youtube` in a tip message overrides the default `Music.source` for that request.
- When `false`: source keywords in tip messages are ignored; all requests use `Music.source`.

`General.show_debug_data` toggles the display of debug information in the UI.

**Automatically send error reports to the TipTune developer** is a separate opt-in setting. Save after enabling it to send technical exception types, code locations, app/OS versions, failure stages, and provider status/request IDs through Sentry. Usernames, tip messages, credentials, raw error text, and complete logs/History records are excluded. Reports retry in the background and expire after seven days; turning reporting off and saving deletes unsent reports. **Send test report** queues a synthetic report and shows its ID. Desktop launcher failures and unexpected service exits send on the next successful launch while reporting remains enabled.

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

Use **Export** before clearing if you want a JSON copy. Clearing removes saved request records and changes the data available to Stats; it does not remove songs from the queue.

After changing **Limit**, select **Refresh** to load that many entries. **Export** always includes all retained entries, regardless of the displayed limit.

TipTune saves the latest **1,000 request entries** by default. Set **Settings → General → Request history size** to change the capacity (`General.request_history_size` in `config.ini`). Use a positive whole number. Saving applies the new capacity immediately; lowering it removes the oldest excess entries. History and Stats can load up to this capacity, and Export includes all retained entries.

Typical statuses:

- `added`
- `failed`

Failures include an `error` field such as:

- `blank tip message`
- `spotify track not found`
- `not available in market`

An **added** result means the song entered the queue. It does not confirm that Spotify or YouTube successfully started playing it; check the Dashboard for playback errors.

### Diagnose a failed request

1. Open **Settings → Logging**, enable **Show debug output in history**, then **Save changes**.
2. Return to History and expand **Song extraction debug details** on a failed entry.
3. Check the failure stage, selected source, configuration availability, model response, and API status when present. Older entries may have no saved diagnostics.

Diagnostics are saved even when this display setting is off, and are included in History exports. Credentials are redacted and large fields may be truncated, but exports can still contain usernames, tip messages, and model request/response content. Review them before sharing. Displaying diagnostics does not enable automatic error reporting.

---

## Stats

Open: [Stats](/stats)

Stats summarizes saved request history by user and song. Choose a **Time window**, filter by **Status**, or search for a user, song, artist, or tip message. Select a user or song row to narrow the results; **Clear selection** removes that selection. Select a column heading to change sorting.

The default time window is **Last 30 days**. **All** means all retained history, within the loaded **History limit**. Select **Refresh** after changing that limit to load the requested number of entries. Clearing history or lowering its capacity also reduces what Stats can report. Stats is a request-processing summary, not a complete record of all tips or proof that each queued song played.

---

## OBS integration

TipTune provides one transparent Browser Source for Now Playing, upcoming songs,
request acknowledgments, warnings, and queue notices. The OBS WebSocket
connection enables automatic setup; manual browser display works without it.

### Browser display

In Settings or the Setup Wizard, enable **Show stream information display**. Save the OBS connection, choose a scene and source name in **Add to OBS**, save, then select **Add browser overlay**. Source-name conflicts are reported instead of overwriting unrelated sources. Existing text items in the selected scene are hidden when the browser display is activated; their inputs are retained. Adding again reuses the browser input and preserves its scene-item transforms.

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

Appearance is stored in `[Overlay]` in `config.ini`. The display checkbox controls browser display. Existing legacy text inputs are retained for manual cleanup in OBS; the wizard and Settings use browser display. Other scenes are retained, so hide old text items there when reusing the browser input.

### Song request advertisements

In **Settings → Stream information display → Advertisements**, enable **Show song request advertisements** and save. The browser overlay periodically explains how to request a song and shows your saved song and skip prices. This feature defaults to off and requires browser display to be enabled.

- **Interval** defaults to 300 seconds; allowed range: 60–86,400 seconds.
- **Duration** defaults to 15 seconds; allowed range: 5–30 seconds.
- The first advertisement waits one full interval. Further intervals are measured between advertisement starts.
- **Preview advertisement** uses your draft prices and affects only the local preview. Save your changes to apply them to the stream.
- Advertisements follow the overlay's layout, colors, opacity, and Motion setting, and respect reduced motion.

### Prereqs

- Install OBS Studio.
- Enable obs-websocket in OBS.
  - Commonly: **Tools → WebSocket Server Settings**

### Audio capture inputs (Windows)

One **TipTune Audio** source captures YouTube and the embedded Spotify player through a dedicated **VB-CABLE** in the Windows desktop app.

1. Install [VB-CABLE from VB-Audio](https://vb-audio.com/Cable/): extract the download, run the installer as administrator, then restart Windows.
2. Keep Windows' default output on your usual speakers or headphones. In **OBS → Settings → Audio**, select your listening device as the monitoring device. Dedicate CABLE Input to TipTune. Exclude the monitoring device from Desktop Audio or Audio Output Capture sources to avoid capturing TipTune twice.
3. In the Setup Wizard's OBS sources step or Settings → **OBS audio sources**, enable **Capture TipTune audio in OBS**. Save, then select **Set up audio capture**. The wizard has **Save OBS setup** so you can save without leaving the step.
4. Play a YouTube or Spotify request and check the **TipTune Audio** meter in OBS. If the meter moves but you hear nothing, check OBS's monitoring device, source mute, and volume.

TipTune selects **CABLE Input** for its embedded players and captures **CABLE Output** in OBS, with source monitoring enabled. When OBS is closed or the capture is unavailable, TipTune uses its normal output. Spotify API authorization and the embedded player's login are separate; sign in to both with the same account. A new user can choose **Set up later** for Spotify and the Events API and start with manual YouTube playback.

### Testing

In Settings:

- Use the preview buttons to check drafts locally, then **Send request/warning/notice to OBS** to check the actual overlay.

Overlay duration is controlled by:

- `General.request_overlay_duration`

---

## Updates

In the desktop app, open **Settings → App Updates → Check for Updates**. If an update is available, TipTune offers to download and install it. You can also use **Download + Install** after an update has been found. Save settings changes before starting an update and relaunch TipTune afterward if it does not reopen automatically.

**Automatically check for updates** controls startup checks; save after changing it. Update installation is available in the desktop app, not when viewing the dashboard in an ordinary browser.

For a manual installation, close TipTune completely, install the newer build from [GitHub Releases](https://github.com/rndmzd/TipTune/releases/latest), then launch TipTune again. If a leftover service occupies its port, follow [Old pages or backend processes remain after closing TipTune](#old-pages-or-backend-processes-remain-after-closing-tiptune).

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
  - `enabled`, `host`, `port`, `password`, `scene_name`, `use_virtual_cable`
- `[Overlay]`
  - `mode` (`browser`, `text`, or `off`), `source_name`, `layout`, `position`
  - `scale`, `margin`, `queue_length`, `opacity`, `motion`
  - `show_now_playing`, `show_queue`, `show_alerts`, `show_artwork`
  - `accent_color`, `background_color`, `text_color`
  - `show_advertisements`, `advertisement_interval`, `advertisement_duration`
- `[Web]`
  - `host`, `port`
- `[Music]`
  - `source`
- `[General]`
  - `song_cost`, `skip_song_cost`, `allow_source_override_in_request_message`, `request_overlay_duration`, `request_history_size`, `setup_complete`, `auto_check_updates`, `show_debug_data`, `show_history_debug`, `send_error_reports`, `debug_log_to_file`, `debug_log_path`

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

TipTune normally writes `INFO` logs to standard output. To collect a debug file, enable **Settings → Logging → Write debug logs to file**, choose a path if needed, then **Save changes**. This is separate from showing saved diagnostics in History and from automatically sending error reports.

### Default location

With debug file logging enabled and no custom path, TipTune writes to:

- `<app_dir>/logs/tiptune-debug.log`
  - `<app_dir>` is the TipTune app/executable directory (for example, where `TipTune.exe` lives).

### Log level behavior

- The normal console log level is `INFO`; file logging is off by default.
- Enabling `General.debug_log_to_file` enables a log file at `DEBUG` level.
- Turning it off stops file logging unless `TIPTUNE_LOG_PATH` and `TIPTUNE_LOG_PATH_FORCE` explicitly force a file destination. Existing log files are not deleted.
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

1. `TIPTUNE_LOG_PATH` when `TIPTUNE_LOG_PATH_FORCE` is enabled
2. `General.debug_log_path`
3. `TIPTUNE_LOG_PATH` when debug file logging is enabled and no config path is set
4. `TIPTUNE_DEFAULT_LOG_PATH`
5. `<app_dir>/logs/tiptune-debug.log`

The configured/default paths apply when debug file logging is enabled. Review logs before sharing them, since they can contain operational details.

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

### Spotify playback did not start — queue paused

TipTune keeps the song queued when Spotify cannot start it. Check **Settings → Spotify** for authorization errors, then **Settings → Playback → Available devices** for the intended device. Sign in to the player with the same account, refresh devices, and use **Apply + Save** if choosing a different device. Confirm OpenAI is configured, then return to the Dashboard and select **Resume Queue**. A History result of **added** does not prove playback started.

### Tips are not being processed

1. Check **Settings → Events API**: the URL must be set and saved.
2. Open [Events](/events) and verify that new tips arrive. If none arrive, check the tip-source URL and its access token.
3. Check the exact song price in **Settings → General**. A multiple of the song price does not request extra songs.
4. Open [History](/history). A failed entry means the tip reached request processing; inspect its error or [saved diagnostics](#diagnose-a-failed-request).
5. For ordinary text or Spotify-link requests, configure OpenAI. Confirm the selected model is available to your API account. Manual YouTube playback can work while text parsing is unconfigured.

### Requests failing with “blank tip message”

TipTune requires a tip note/message for song requests.

If the message is blank, TipTune will fail the request and may warn that the note could have been removed due to blocked words.

### Requests failing with “spotify track not found”

- Include both artist and song title in the message when possible.
- Try providing a Spotify track link.

### YouTube will not play

- Add a YouTube song manually from the Dashboard to distinguish a playback problem from a text-parsing problem.
- Check that playback is not paused and that Windows or your browser has not muted TipTune. If a browser blocks autoplay, use the player's **Play** control.
- Packaged builds include yt-dlp, Deno, and ffmpeg. Check for a TipTune update before trying a video that still fails. Some videos may be unavailable or restricted; try another song.
- For a source checkout, install the Python dependencies and run `node scripts/fetch-binaries.mjs` to fetch the playback tools. An unbundled setup needs current yt-dlp and Deno on PATH, or Node.js 22+ as the JavaScript-runtime fallback.

### OBS not connected

- Confirm obs-websocket is enabled.
- Confirm host/port/password match OBS settings.
- Use Settings → Refresh OBS status.

### Overlay is blank or showing old information

- Enable **Show stream information display** and save. OBS WebSocket connection and browser display are separate settings.
- Start TipTune, then refresh the OBS Browser Source. Confirm its URL matches the one shown in **Add to OBS** and its dimensions match the OBS base canvas.
- Check that the source is visible in the scene you are broadcasting. The display-client count proves a connection, not visibility in your final output.
- Use **Preview …** to check drafts locally. Use **Send … to OBS** when you intend to test the actual display.
- For advertisements, enable them and save, then allow one full configured interval for the first advertisement.

### OBS has no TipTune audio, or audio is doubled

The Browser Source displays information; it does not carry music. Follow [Audio capture inputs (Windows)](#audio-capture-inputs-windows) for embedded Spotify and YouTube.

- If the **TipTune Audio** meter is still, check the saved capture setting, VB-CABLE installation, and **Set up audio capture** result.
- If the meter moves but you hear nothing, check the source's mute/volume and **OBS → Settings → Audio → Monitoring Device**.
- If audio is doubled, check whether Desktop Audio or another capture source is also capturing the OBS monitoring output. Keep Windows' default output on your usual speakers or headphones.

### A saved setting appears unchanged

Select **Save changes** at the top and wait for success before leaving Settings. **Apply + Save** handles Spotify device selection separately. For History diagnostics, reload History after saving the display toggle. If the installed app still shows an older interface after an update, close all TipTune windows and relaunch it.

### Old pages or backend processes remain after closing TipTune

On Windows, the desktop owns its backend process tree and stops it on normal
exit, during an update, and if the desktop is terminated unexpectedly. A second
backend aborts startup if the server port is occupied.

If upgrading from an older version that already left a backend running, close
TipTune completely before installing the new version. In Task Manager, end only
the leftover TipTune processes from that installation, then relaunch TipTune.
Refresh the OBS browser source if it loaded an old error page. Other Python,
OBS, and Spotify applications should remain running.

---
## Spotify web player in the desktop app

The desktop app initializes Spotify in the background at startup. Use **Spotify** in the top navigation to show the player and sign in. The player stays loaded while you use the Dashboard or other pages.

Sign in directly on Spotify's page with the same account you connected during TipTune setup. Spotify API credentials authorize API access; they cannot sign you into Spotify's website. The desktop app keeps the player's cookies and local storage between launches. Spotify can still expire or revoke a session, and signing out requires another login.

On Windows, TipTune automatically selects and activates **Web Player (Microsoft Edge)** when Spotify reports it as available and unrestricted, including if it appears after startup. This preserves its current play/pause state. To use another device for the current session, select it under **Settings → Playback** and choose **Apply + Save**. Start a song in the player if needed to make the device available, then resume your request queue. Launching TipTune does not by itself start a new song.

**Reload player** returns to Spotify's home page without clearing your login. Login popups belong to Spotify; closing one does not close TipTune. Some login providers may restrict embedded browsers; Spotify's direct email login is available on its sign-in page. If your machine cannot play protected content in its embedded browser, use Spotify in a supported external browser and select that device in Settings.
