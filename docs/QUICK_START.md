# TipTune Quick Start

This guide gets you from **install** to **taking song requests** as quickly as possible.

---

## 1) Install TipTune

### Option A: Desktop app (recommended)

- Download the latest release from **GitHub Releases**.
- Install/run TipTune.

TipTune will start a local service and open the UI automatically.

### Option B: Run from source (developers/power users)

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

- The Web UI dev server runs on `http://127.0.0.1:5173`.
- The Python service runs the API/UI backend on `http://127.0.0.1:8765` by default.

---

## 2) Complete the Setup Wizard

When TipTune starts, it will redirect to the **Setup Wizard** until setup is marked complete.

- Open: `http://127.0.0.1:8765/setup`
- Rerun later: `http://127.0.0.1:8765/setup?rerun=1`

If you need to bypass the redirect temporarily:

- Dashboard: `http://127.0.0.1:8765/?dashboard=1`
- Settings: `http://127.0.0.1:8765/settings?dashboard=1`

---

## 3) Spotify: configure + connect

TipTune can use Spotify for search and playback control.

### 3.1 Create a Spotify Developer app

- Go to `https://developer.spotify.com/dashboard`
- Create an app
- Add a Redirect URI that matches your config (default):

```text
http://127.0.0.1:8888/callback
```

### 3.2 Enter credentials in TipTune

In the Setup Wizard:

- Set `Spotify.client_id`
- Set `Spotify.redirect_url`

### 3.3 Authorize

Click **Connect Spotify** and complete the login/consent flow in your browser.

### 3.4 Select a playback device

Go to **Settings → Playback Device**:

- Make sure Spotify is open on a device (PC, phone, etc.)
- Click **Refresh**
- Select a device
- Click **Apply + Save**

---

## 4) OpenAI: enable tip parsing (recommended)

TipTune uses OpenAI to parse song requests from tip messages.

In the Setup Wizard or Settings:

- Set `OpenAI.api_key`
- Optionally set `OpenAI.model` (example: `gpt-5-mini`)

---

## 5) Events API: connect your tip source

TipTune polls an Events API endpoint for incoming tips.

In Setup Wizard or Settings:

- Set `Events API.url`
- Optionally tune `Events API.max_requests_per_minute`

---

## 6) Verify it’s working

### Check the queue dashboard

Open:

- `http://127.0.0.1:8765/`

If you want in-app documentation or troubleshooting info, open:

- `http://127.0.0.1:8765/help`

You should see:

- Queue status
- Now playing
- Up next

### Test a manual add

In the dashboard:

- Click **Add Track**
- Search a track
- Click **+** to add it

---

## 7) (Optional) YouTube as your music source

TipTune can also use YouTube for search and playback.

1. In Setup Wizard → **General Settings** (or Settings → Music), set:

   - `Music.source=youtube`

1. Make sure TipTune has `yt-dlp` available in its runtime.

Notes:

- TipTune only streams from allowed YouTube hosts (for example `youtube.com`, `*.youtube.com`, and `youtu.be`).
- The Dashboard will play YouTube audio using an in-page audio player.

---

## 8) (Optional) OBS overlays

In Settings:

- Set `OBS.enabled=true`
- Set `OBS.host`, `OBS.port`, `OBS.password`
- Click **Create missing text sources**
- (Windows) optionally click **Create Spotify audio capture**
- Use **Test overlays** to confirm they display correctly

---

## Common gotchas

- **Spotify redirect URL must match exactly** (scheme/host/port/path) and must be `http://127.0.0.1:<port>/...` or `http://localhost:<port>/...`.
- **No devices found**: open Spotify on a device and start playback so Spotify registers an active device.
- **Blank tip message**: TipTune treats this as a failed request and can show a warning overlay.
