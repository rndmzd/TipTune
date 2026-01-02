# TipTune

Turn tips into queued music requests with OBS integration.

## Quick start

- Copy `config.ini.example` to `config.ini`.
- Fill in each section below.
- Install dependencies and run:

  ```bash
  pip install -r requirements.txt
  python app.py
  ```

`config.ini` is intentionally ignored by git (see `.gitignore`). Do not commit or share it.

## Packaging (PyInstaller)

- Build dependencies:

  ```bash
  pip install -r requirements.txt -r requirements-build.txt
  ```

- Build executable:

  ```bash
  pyinstaller TipTune.spec --clean
  ```

The built executable is written to `dist/TipTune.exe`.

### Packaged runtime files

When running as a packaged exe, TipTune looks for `config.ini` in this order:

- **Portable config**: `config.ini` next to `TipTune.exe` (if present)
- **User config**: `%APPDATA%\TipTune\config.ini`

If `config.ini` does not exist yet, TipTune will seed it from the bundled `config.ini.example` on first write.

Spotipy OAuth tokens are stored in a writable cache file named `.cache` alongside the selected `config.ini`.

### Environment overrides

- `TIPTUNE_CONFIG`: full path to the `config.ini` file
- `TIPTUNE_CACHE_DIR`: directory for TipTune cache usage (if needed)
- `TIPTUNE_SPOTIPY_CACHE`: full path to the Spotipy token cache file (defaults to `<config_dir>\.cache`)

## Configuration (`config.ini`)

TipTune reads settings from `config.ini` (same folder as `app.py`). The following sections are used by the code:

- `[Events API]` (required)
- `[OpenAI]` (required)
- `[Spotify]` (required for song requests / AutoDJ)
- `[Search]` (optional)
- `[OBS]` (optional)
- `[Web]` (optional)
- `[General]` (required)

### 1) Events API (Chaturbate)

TipTune polls the Chaturbate Events API long-poll feed for `tip` events.

- **Where to get it**
  - Log into your Chaturbate account.
  - Go to your settings page that lists your **Events API token** (the UI location can change; it is commonly under something like **Settings** / **Apps & Bots** / **Events API**).
  - Copy your Events API token.
  - Useful links:
    - `https://chaturbate.com/apps/api/docs/index.html`
    - `https://chaturbate.com/apps/api/docs/rest.html`

- **What to set**
  - Set `Events API.url` to:

    `https://eventsapi.chaturbate.com/events/<your_username>/<your_token>/`

  - Keep the trailing `/`.
  - `max_requests_per_minute` is a local throttle to avoid over-polling.

- **Security note**
  - Treat the Events API token like a password. Anyone with it can subscribe to your room events.

### 2) OpenAI

OpenAI is used to extract song + artist information from the tip message when a Spotify URL/URI is not present.

- **Where to get it**
  - Create an account at `https://platform.openai.com/`.
  - Create (or choose) a Project.
  - Create a Project API key.
  - Useful links:
    - `https://platform.openai.com/api-keys`

- **What to set**
  - Set `OpenAI.api_key` to your API key.
  - Set `OpenAI.model` to the model you want to use (the default in the config template is `gpt-5`).

### 3) Spotify (required for playback / queuing)

Spotify is used to search tracks and control playback/queue via the Web API.

- **Prerequisites**
  - A Spotify account.
  - For playback control, a Spotify Premium account is typically required by Spotify.
  - At least one active Spotify device (Spotify app open somewhere) on the same account.

- **Where to get `client_id` / `client_secret`**
  - Go to the Spotify Developer dashboard: `https://developer.spotify.com/dashboard`
  - Create an app.
  - Copy the app’s **Client ID** and **Client Secret**.

- **Redirect URI (very important)**
  - In your Spotify app settings, add this Redirect URI:

    `http://127.0.0.1:8888/callback`

  - The value in `Spotify.redirect_url` must match the Spotify app setting exactly.

- **What to set**
  - `Spotify.client_id` = Client ID
  - `Spotify.client_secret` = Client Secret
  - `Spotify.redirect_url` = redirect URI (see above)
  - Optional: `Spotify.playback_device_id`
    - You can leave this blank and select a device from the Web UI once the app is running.

After TipTune is running, open the Web UI and select a playback device (this writes `Spotify.playback_device_id` into `config.ini`).

- **First run OAuth login**
  - On first run, Spotipy will require you to authorize the app.
  - Because the code uses `open_browser=False`, you may need to manually open the printed URL and paste the final redirected URL back into the prompt.

- **Scopes used by TipTune**
  - `user-modify-playback-state`
  - `user-read-playback-state`
  - `user-read-currently-playing`
  - `user-read-private`

### 4) Google Custom Search (optional, improves artist lookup)

TipTune can optionally use Google Custom Search to help identify the artist when a song request contains only a title.

- **Where to get it**
  - Create a Google Cloud project: `https://console.cloud.google.com/`
  - Enable the **Custom Search API** for the project.
  - Create an API key.
  - Create a **Programmable Search Engine** and copy its **Search engine ID** (also called `cx`).
    - If you want broad results, configure it to search the entire web.

Some Google projects may require billing to be enabled to use the API reliably.

- **What to set**
  - `Search.google_api_key` = your Google API key
  - `Search.google_cx` = your Programmable Search Engine ID (`cx`)

### 5) OBS WebSocket (optional, for overlays/scenes)

If enabled, TipTune can connect to OBS via OBS WebSocket.

- **Where to get it**
  - Install OBS Studio.
  - In OBS, enable the WebSocket server (commonly under **Tools → WebSocket Server Settings**).
  - Set a password and note the port.

- **What to set**
  - `OBS.enabled` = `true` or `false`
  - `OBS.host` = `localhost` (or the machine running OBS)
  - `OBS.port` = the OBS WebSocket port (often `4455` unless you changed it)
  - `OBS.password` = the password you configured in OBS

Scene names are mapped in `scenes.yaml`. Make sure the scene names in that file match your OBS scenes.

### 6) Web UI (optional)

TipTune starts a small local web UI.

- `Web.host` defaults to `127.0.0.1`
- `Web.port` defaults to `8765`

Once running, open `http://127.0.0.1:8765/`.

### 7) General

These settings control how tips map to requests:

- `General.song_cost`: tip token amount (or multiple) that triggers a song request
- `General.skip_song_cost`: tip token amount (or multiple) that triggers a skip
- `General.request_overlay_duration`: overlay duration (seconds)
