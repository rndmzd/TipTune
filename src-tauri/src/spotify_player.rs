use std::sync::atomic::{AtomicUsize, Ordering};
use tauri::{
    webview::{NewWindowResponse, WebviewBuilder},
    AppHandle, LogicalPosition, LogicalSize, Manager, Webview, WebviewUrl, WebviewWindowBuilder,
};

const PLAYER: &str = "spotify-player";
const PLAYER_URL: &str = "https://open.spotify.com/";
const PARKED_POSITION: LogicalPosition<f64> = LogicalPosition::new(-10000.0, 0.0);
static POPUP_ID: AtomicUsize = AtomicUsize::new(0);

pub fn create(app: &AppHandle) -> Result<Webview, String> {
    if let Some(player) = app.get_webview(PLAYER) {
        return Ok(player);
    }
    let window = app.get_window("main").ok_or("Main window is unavailable")?;
    let profile = app
        .path()
        .app_local_data_dir()
        .map_err(|e| e.to_string())?
        .join("spotify-profile");
    std::fs::create_dir_all(&profile).map_err(|e| e.to_string())?;
    let popup_app = app.clone();
    let player = window
        .add_child(
            WebviewBuilder::new(PLAYER, WebviewUrl::External(PLAYER_URL.parse().unwrap()))
                // Cookies and local storage survive process exits and application updates.
                .data_directory(profile)
                .initialization_script(crate::audio_output::SCRIPT)
                .on_page_load(|view, payload| {
                    if matches!(payload.event(), tauri::webview::PageLoadEvent::Finished) {
                        crate::audio_output::restore(view.clone());
                    }
                })
                .focused(false)
                .background_throttling(tauri::utils::config::BackgroundThrottlingPolicy::Disabled)
                .on_navigation(|url| url.scheme() == "https" || url.as_str() == "about:blank")
                .on_new_window(move |url, features| {
                    if url.scheme() != "https" && url.as_str() != "about:blank" {
                        return NewWindowResponse::Deny;
                    }
                    let label =
                        format!("spotify-login-{}", POPUP_ID.fetch_add(1, Ordering::Relaxed));
                    // Sharing the environment preserves cookies and the OAuth popup opener.
                    let popup = WebviewWindowBuilder::new(
                        &popup_app,
                        label,
                        WebviewUrl::External("about:blank".parse().unwrap()),
                    )
                    .window_features(features)
                    .title("Spotify — Sign in")
                    .inner_size(520.0, 720.0)
                    .on_navigation(|url| url.scheme() == "https" || url.as_str() == "about:blank")
                    .build();
                    match popup {
                        Ok(window) => NewWindowResponse::Create { window },
                        Err(error) => {
                            eprintln!("[spotify login] {error}");
                            NewWindowResponse::Deny
                        }
                    }
                }),
            PARKED_POSITION,
            LogicalSize::new(1000.0, 650.0),
        )
        .map_err(|e| e.to_string())?;
    // Keep the document visible to the browser while its native surface is
    // outside the window. Hiding it can defer Spotify's playback initialization.
    player.show().map_err(|e| e.to_string())?;
    Ok(player)
}

fn require_main(caller: &Webview) -> Result<(), String> {
    if caller.label() == "main" {
        Ok(())
    } else {
        Err("Only the TipTune interface can control this view".into())
    }
}

// Async commands avoid WebView2 deadlocks when creating a child view on Windows.
#[tauri::command]
pub async fn spotify_player_initialize(app: AppHandle, webview: Webview) -> Result<(), String> {
    require_main(&webview)?;
    create(&app).map(|_| ())
}

#[tauri::command]
pub async fn spotify_player_show(
    app: AppHandle,
    webview: Webview,
    x: f64,
    y: f64,
    width: f64,
    height: f64,
) -> Result<(), String> {
    require_main(&webview)?;
    if ![x, y, width, height].iter().all(|value| value.is_finite())
        || x < 0.0
        || y < 0.0
        || width <= 0.0
        || height <= 0.0
    {
        return Err("Invalid player bounds".into());
    }
    let player = create(&app)?;
    player
        .set_position(LogicalPosition::new(x, y))
        .map_err(|e| e.to_string())?;
    player
        .set_size(LogicalSize::new(width, height))
        .map_err(|e| e.to_string())?;
    player.show().map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn spotify_player_hide(app: AppHandle, webview: Webview) -> Result<(), String> {
    require_main(&webview)?;
    if let Some(player) = app.get_webview(PLAYER) {
        player
            .set_position(PARKED_POSITION)
            .map_err(|e| e.to_string())?;
        player.show().map_err(|e| e.to_string())?;
        // The parked child must not retain keyboard input from the Spotify page.
        webview.set_focus().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
pub async fn spotify_player_reload(app: AppHandle, webview: Webview) -> Result<(), String> {
    require_main(&webview)?;
    create(&app)?
        .navigate(PLAYER_URL.parse().unwrap())
        .map_err(|e| e.to_string())
}
