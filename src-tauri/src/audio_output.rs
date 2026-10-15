use std::sync::atomic::{AtomicBool, Ordering};
use tauri::{AppHandle, Manager, Webview};

pub const SCRIPT: &str = include_str!("audio_output.js");
static ENABLED: AtomicBool = AtomicBool::new(false);
static ROUTING: tauri::async_runtime::Mutex<()> = tauri::async_runtime::Mutex::const_new(());

#[cfg(windows)]
async fn cdp(
    view: &Webview,
    method: &str,
    parameters: serde_json::Value,
) -> Result<serde_json::Value, String> {
    use std::{sync::mpsc, time::Duration};
    use webview2_com::CallDevToolsProtocolMethodCompletedHandler;
    use windows::core::HSTRING;
    let (sender, receiver) = mpsc::sync_channel(1);
    let method = method.to_string();
    let parameters = parameters.to_string();
    view.with_webview(move |platform| unsafe {
        let immediate_sender = sender.clone();
        let callback =
            CallDevToolsProtocolMethodCompletedHandler::create(Box::new(move |result, payload| {
                let _ = sender.send(result.map(|_| payload).map_err(|error| error.to_string()));
                Ok(())
            }));
        let result = platform.controller().CoreWebView2().and_then(|core| {
            core.CallDevToolsProtocolMethod(
                &HSTRING::from(method),
                &HSTRING::from(parameters),
                &callback,
            )
        });
        if let Err(error) = result {
            let _ = immediate_sender.send(Err(error.to_string()));
        }
    })
    .map_err(|error| error.to_string())?;
    let payload = tauri::async_runtime::spawn_blocking(move || {
        receiver.recv_timeout(Duration::from_secs(10))
    })
    .await
    .map_err(|error| error.to_string())?
    .map_err(|_| "Audio output setup timed out. Try again.".to_string())??;
    serde_json::from_str(&payload).map_err(|error| error.to_string())
}

async fn configure_view(view: &Webview, enabled: bool) -> Result<(), String> {
    #[cfg(windows)]
    {
        let url = view.url().map_err(|error| error.to_string())?;
        let trusted = match view.label() {
            "spotify-player" => {
                url.scheme() == "https" && url.host_str() == Some("open.spotify.com")
            }
            "main" => matches!(
                url.host_str(),
                Some("127.0.0.1" | "localhost" | "tauri.localhost")
            ),
            _ => false,
        };
        if !trusted {
            return Ok(());
        }
        if enabled {
            // Grant speaker selection only. Microphone and camera access remain unchanged.
            cdp(
                view,
                "Browser.setPermission",
                serde_json::json!({
                    "permission": {"name": "speaker-selection"}, "setting": "granted",
                    "origin": url.origin().ascii_serialization()
                }),
            )
            .await?;
        }
        let result = cdp(
            view,
            "Runtime.evaluate",
            serde_json::json!({
                "expression": format!("{SCRIPT}\nwindow.__tiptuneAudioOutput.configure({enabled})"),
                "awaitPromise": true, "returnByValue": true
            }),
        )
        .await?;
        if result.get("exceptionDetails").is_some() {
            return Err("Could not route TipTune audio. Check that VB-CABLE is enabled in Windows sound settings.".into());
        }
    }
    #[cfg(not(windows))]
    if enabled {
        return Err("VB-CABLE routing is available in the Windows desktop app.".into());
    }
    Ok(())
}

pub fn restore(view: Webview) {
    tauri::async_runtime::spawn(async move {
        let _guard = ROUTING.lock().await;
        if let Err(error) = configure_view(&view, ENABLED.load(Ordering::SeqCst)).await {
            eprintln!("[audio output] {error}");
        }
    });
}

#[tauri::command]
pub async fn audio_output_configure(
    app: AppHandle,
    webview: Webview,
    enabled: bool,
) -> Result<(), String> {
    if webview.label() != "main" {
        return Err("Only the TipTune interface can configure audio output".into());
    }
    let _guard = ROUTING.lock().await;
    let player = app.get_webview("spotify-player");
    let result = async {
        configure_view(&webview, enabled).await?;
        if let Some(player) = player.as_ref() {
            configure_view(player, enabled).await?;
        }
        Ok::<(), String>(())
    }
    .await;
    if let Err(error) = result {
        eprintln!("[audio output] {error}");
        ENABLED.store(false, Ordering::SeqCst);
        let _ = configure_view(&webview, false).await;
        if let Some(player) = player.as_ref() {
            let _ = configure_view(player, false).await;
        }
        return Err(error);
    }
    ENABLED.store(enabled, Ordering::SeqCst);
    Ok(())
}
