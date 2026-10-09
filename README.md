# TipTune

TipTune turns tips into queued music requests (Spotify and/or YouTube) and provides a local dashboard for playback control, setup, and OBS overlays.

This repo contains:

- A **desktop app** (Tauri v2) for macOS/Linux/Windows.
- A **Python sidecar** (built with PyInstaller) that runs the TipTune service + local Web UI API.
- A **React Web UI** bundled into the desktop app.
- A **tag-triggered GitHub Actions release pipeline** (starting with `v0.1.0`) to publish builds for macOS/Linux/Windows.

---

## Documentation

- [Quick Start](docs/QUICK_START.md)
- [User Manual](docs/USER_MANUAL.md)

## Getting TipTune

- **Desktop app (recommended)**
  - Download the latest build from GitHub Releases.
  - Launch TipTune; it will start the bundled TipTune service automatically.

- **Run from source (developers / power users)**
  - See [Development](#development) below.

---

## First-run setup (recommended path)

The desktop app opens **Spotify** at startup. Sign in to the web player, then use **Setup Wizard** in the top navigation to configure TipTune. The player keeps its login between launches, subject to Spotify session expiry. Dashboard pages redirect to the wizard until setup is complete.

The Setup Wizard walks you through:

- Spotify credentials + OAuth authorization
- Events API (optional)
- OpenAI (optional but recommended for AI-assisted parsing)
- Google Custom Search (optional)
- OBS connection + creating required text sources (optional)
- General settings (song/skip costs, overlay duration)

If you need to rerun setup:

- Open the Setup page and add `?rerun=1`.

---

## Web UI

TipTune runs a local HTTP server (default `http://127.0.0.1:8765`). The desktop UI uses it as its backend.

Pages:

- `/spotify`
  - Embedded Spotify web player (desktop), loaded at startup with a persistent login profile
- `/` and `/settings`
  - Full configuration editor (including secrets fields)
  - Playback device selection
  - OBS overlay management tools
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

---

## Configuration (`config.ini`)

TipTune reads settings from a single `config.ini` file.

`config.ini` is intentionally ignored by git (see `.gitignore`). Do not commit or share it.

### Where the config file lives

TipTune picks the config path in this order:

- **Override**: `TIPTUNE_CONFIG` environment variable
- **Packaged (frozen) runtime**
  - **Portable config**: `config.ini` next to the executable (if present)
  - Otherwise: a per-user config directory (currently based on `APPDATA` semantics)
- **Development**: `<repo_root>/config.ini`

If the chosen `config.ini` does not exist yet, TipTune will seed it from the bundled `config.ini.example` on first write.

### Cache files

Spotipy OAuth tokens are stored in a writable cache file named `.cache` alongside the selected `config.ini` (or overridden by `TIPTUNE_SPOTIPY_CACHE`).

Environment overrides:

- `TIPTUNE_CONFIG`: full path to the `config.ini` file
- `TIPTUNE_CACHE_DIR`: directory for TipTune cache usage
- `TIPTUNE_SPOTIPY_CACHE`: full path to the Spotipy token cache file (defaults to `<config_dir>/.cache`)
- `TIPTUNE_WEB_HOST`: override Web UI bind host
- `TIPTUNE_WEB_PORT`: override Web UI bind port

### Config sections

TipTune uses these sections/keys (see `config.ini.example`):

- `[Spotify]` (required for playback)
  - `client_id`, `redirect_url`, `playback_device_id`
- `[OpenAI]` (optional)
  - `api_key`, `model`
- `[Events API]` (optional)
  - `url`, `max_requests_per_minute`
- `[Search]` (optional)
  - `google_api_key`, `google_cx`
- `[OBS]` (optional)
  - `enabled`, `host`, `port`, `password`, `scene_name`
- `[Web]` (optional)
  - `host`, `port`
- `[Music]` (required)
  - `source`
- `[General]` (required)
  - `song_cost`, `multi_request_tips`, `allow_source_override_in_request_message`, `skip_song_cost`, `request_overlay_duration`, `request_history_size`, `setup_complete`, `auto_check_updates`, `show_debug_data`, `debug_log_to_file`, `debug_log_path`

Request history retains the latest 1,000 entries by default. Change **Settings → General → Request history size** (or `General.request_history_size`) to any positive whole number. Changes apply when saved; lowering the size removes the oldest excess entries. History, exports, and Stats use the configured capacity.

### Multi-song tips

By default, TipTune allows **multiple song requests in a single tip** when the tip amount is a multiple of `General.song_cost`.

This is controlled by:

- `General.multi_request_tips=true|false`

Behavior:

- If `multi_request_tips=true`:
  - A tip of `song_cost` requests 1 song.
  - A tip of `2*song_cost` requests 2 songs.
  - TipTune computes: `request_count = tip_amount // song_cost` (minimum 1).
- If `multi_request_tips=false`:
  - Only an **exact** `tip_amount == song_cost` triggers a single song request.
  - Multiples (like `2*song_cost`) do **not** trigger song requests.

---

## Music sources

TipTune supports two music sources:

- **Spotify**
  - Full search + playback control through the Spotify Web API.
  - Requires Spotify app setup + authorization.
- **YouTube**
  - Search + playback via `yt-dlp` and an in-dashboard audio player.
  - Does not require Spotify credentials.

Choose the default source in:

- `Music.source=spotify|youtube`

You can also override per-tip by including the word `spotify` or `youtube` in the tip message (controlled by `General.allow_source_override_in_request_message`, default `true`).

---

## Spotify setup

TipTune controls playback and queues tracks via the Spotify Web API.

Steps:

1. Create a Spotify app: `https://developer.spotify.com/dashboard`
1. Add a Redirect URI matching your config (default):

    - `http://127.0.0.1:8888/callback`

1. In TipTune Setup Wizard:

    - Enter `client_id`, `redirect_url`
    - Click **Connect Spotify** and complete login in your browser

1. In Settings:

    - Choose the playback device and click **Apply + Save** (writes `Spotify.playback_device_id`)

Notes:

- Spotify playback control typically requires Spotify Premium.
- Settings shows Spotify API connection and device errors. The Spotify tab's website login is separate from this authorization. Use the same account for both.
- If devices fail to load, read the error beside **Available devices**. For `invalid_client`, click **Connect Spotify** or **Reconnect Spotify** to replace the saved API login. If authorization still fails, verify the app exists and is enabled in the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) and its Client ID matches Settings. Save any credential changes before reconnecting. A successful empty response has separate guidance to start playback and refresh.
- The redirect URL must be `http` and must use `127.0.0.1` or `localhost` with an explicit port.

---

## YouTube setup

If you want to use YouTube as the music source:

- Set `Music.source=youtube` (Setup Wizard → General Settings).
- Install Python deps (`pip install -r requirements.txt`).
- Download bundled binaries (`node scripts/fetch-binaries.mjs`) to get current `yt-dlp`, Deno (for YouTube's JavaScript challenges), and ffmpeg. For an unbundled setup, install current `yt-dlp` and Deno on your PATH; Node 22+ is also supported as a fallback.
- Packaged builds include `yt-dlp` and Deno automatically; users do not need to install either separately.

TipTune only streams from allowed YouTube hosts (for example `youtube.com`, `*.youtube.com`, and `youtu.be`).

---

## OBS integration (optional)

TipTune can display Now Playing, upcoming songs, requester acknowledgments, warnings,
and notices through **one transparent Browser Source**. Browser display works with
Spotify, YouTube, and mixed queues and does not play additional audio.

Steps:

1. Install OBS Studio.
1. Enable obs-websocket in OBS (commonly under **Tools → WebSocket Server Settings**).
1. Configure TipTune (Setup Wizard or Settings):

    - `OBS.enabled=true`
    - `OBS.host`, `OBS.port` (often `4455`), `OBS.password`

1. Open **Stream information display** in Setup Wizard or Settings. Choose a scene and source name, save settings, then click **Add browser overlay** (or **Switch to browser overlay** for existing text setups).
1. Use the **Full** or **Compact** preset and adjust position, scale, colors, visible panels, and motion. Preview buttons affect only the preview; **Send … to OBS** buttons appear on the actual source. Saved appearance changes apply live. Use **Inspect panels** to read the preview at a comfortable size on smaller screens.
1. Optional: use **Create Spotify audio capture** (Windows) to set up an Application Audio Capture input for `Spotify.exe`.
1. Optional: use **Create TipTune audio capture** (Windows) to set up an Application Audio Capture input for `TipTune.exe` (for YouTube playback audio sync in OBS).

`scenes.yaml` defines scene metadata used by the project.

For manual setup, select **Browser display**, save, then add a Browser Source in
OBS with the URL shown in Settings (normally `http://127.0.0.1:8765/overlay`). Set
its width and height to your OBS base canvas and use 30 FPS. Leave **Shutdown
source when not visible** and **Refresh browser when scene becomes active** off.
Manual setup does not require an OBS WebSocket connection. Start TipTune first;
if OBS loaded the page before TipTune started, refresh the source. When enabled,
the OBS connection attempts a source refresh after backend startup.

Fresh installations use Browser display. Existing configurations without
`[Overlay].mode` retain legacy text display when OBS was enabled, or Off when it
was disabled. **Switch to browser overlay** verifies the browser input first,
then hides legacy text items in the selected scene and retains their inputs.
Select **Legacy text sources** and save to switch back. Other scenes are retained;
hide any old text items there when reusing the browser input across scenes.

The page reads cached display state through `/api/overlay/state` and
`/api/overlay/events`; it never controls playback or reads raw tip messages.
Temporary alerts expire independently. On connection loss, alerts clear and
persistent panels hide after 15 seconds without a heartbeat. Recovery displays
current state without replaying expired announcements. See `[Overlay]` in
`config.ini.example` for all appearance settings.

Developer checks: run the Python regression suite and `npm run webui:test`.
The browser suite uses an isolated illustrative backend; install its browser
once with `npx playwright install chromium`.

---

## App updates (desktop)

The desktop app includes the Tauri updater plugin and can check for updates from GitHub release artifacts.

In Settings → **App Updates**:

- Click **Check for Updates**
- If an update is available, click **Download + Install**

Windows closes TipTune while the installer runs. The desktop owns the complete
backend process tree, including PyInstaller workers and playback helpers, so
normal exit, an update, or forced app termination releases the local server port.
The backend aborts startup if that port is already occupied.

Update metadata endpoint is configured in `src-tauri/tauri.conf.json` under `plugins.updater.endpoints`.

---

## Development

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

Notes:

- Tauri will run a `beforeDevCommand` that builds/prepares the Python sidecar and spawns `npm run webui:dev`.
- The Web UI dev server runs on `http://127.0.0.1:5173`.
- The Python service runs the API/UI backend on `http://127.0.0.1:8765` by default.

Build the desktop app locally:

```bash
npm run build
```

This will:

- Build/prepare the Python sidecar into `src-tauri/binaries/`.
- Build the Web UI into `webui/dist`.
- Produce Tauri bundles for your platform.

`package-lock.json` and `src-tauri/Cargo.lock` are committed so local and CI builds
use the same dependency versions. Update Tauri's CLI, API, Rust crates, and plugin
pairs together, and commit both lockfiles. CI and releases pass Cargo's `--locked`
flag so dependency resolution cannot silently change during packaging.

To enforce the same lockfile check locally, including in PowerShell:

```powershell
node node_modules/@tauri-apps/cli/tauri.js build -- --locked
```

Windows CI uses 7-Zip to inspect both installer payloads and rejects missing or
incorrect Tauri installer metadata. MSI and NSIS builds must retain their
installer identity so the Settings updater selects the appropriate package.

Windows lifecycle regression checks (after building the sidecar):

```powershell
cargo test --locked --manifest-path src-tauri/Cargo.toml --test windows_lifecycle
python scripts/check-packaged-lifecycle.py --backend src-tauri/binaries/TipTune-x86_64-pc-windows-msvc.exe
```

These use isolated settings and check normal shutdown, updater-style process
exit, forced termination, and an occupied HTTP port. The packaged check exercises
both PyInstaller processes and verifies that the server port is released.

---

## Releases (macOS / Linux / Windows)

Starting with **`v0.1.0`**, releases are built by GitHub Actions when you push a tag matching `v*`.

The workflow is: `.github/workflows/release-from-tag.yml`.

### Making a release

1. Ensure versions match

    - `package.json` `version`
    - `src-tauri/tauri.conf.json` `version`
    - `src-tauri/Cargo.toml` `version`

1. Commit the version bump
1. Create and push a tag

```bash
git tag -a v0.1.0 -m "v0.1.0"
git push origin v0.1.0
```

GitHub Actions will:

- Build desktop artifacts for macOS, Linux, and Windows.
- Create a GitHub Release for the tag.
- Upload platform installers/bundles and updater metadata (such as `latest.json`).

Typical output formats (varies by platform/runner configuration):

- Windows: `.msi` / installer artifacts
- macOS: `.dmg`
- Linux: `.AppImage` / `.deb`

### Version bump helper

This repo includes a script to bump `x.y.z -> x.(y+1).0` across the versioned files and optionally tag:

```bash
npm run version:bump-minor
```

Options:

- `--dry-run`
- `--allow-dirty`
- `--no-commit`
- `--no-tag`

### Required GitHub Secrets

The release workflow is configured to use Tauri updater signing secrets:

- `TAURI_SIGNING_PRIVATE_KEY`
- `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`

Optional (macOS signing/notarization) secrets are listed in the workflow but commented out.
