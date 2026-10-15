#[cfg(not(mobile))]
use std::sync::Mutex;

#[cfg(not(mobile))]
use std::{env, fs};

#[cfg(not(mobile))]
use tauri::{Manager, RunEvent, WindowEvent};

#[cfg(not(mobile))]
use tauri_plugin_shell::{process::CommandChild, ShellExt};

#[cfg(all(not(mobile), windows))]
mod windows_job;

#[cfg(not(mobile))]
mod startup;

#[cfg(not(mobile))]
mod audio_output;

#[cfg(not(mobile))]
mod error_reporting;

#[cfg(not(mobile))]
struct RunningSidecar {
    child: CommandChild,
    #[cfg(windows)]
    job: windows_job::ProcessJob,
}

#[cfg(not(mobile))]
struct SidecarState(Mutex<Option<RunningSidecar>>);

#[cfg(not(mobile))]
mod spotify_player;

#[cfg(not(mobile))]
fn kill_sidecar(app: &tauri::AppHandle) {
    let Some(state) = app.try_state::<SidecarState>() else {
        return;
    };
    let running = state
        .0
        .lock()
        .unwrap_or_else(|error| error.into_inner())
        .take();
    if let Some(running) = running {
        #[cfg(windows)]
        if let Err(error) = running.job.terminate() {
            eprintln!("[sidecar shutdown] {error}");
            // Dropping the job also terminates its remaining members.
            if let Err(error) = running.child.kill() {
                eprintln!("[sidecar shutdown fallback] {error}");
            }
        }
        #[cfg(not(windows))]
        if let Err(error) = running.child.kill() {
            eprintln!("[sidecar shutdown] {error}");
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    #[cfg(not(mobile))]
    error_reporting::install_panic_hook();
    let builder = tauri::Builder::default();
    #[cfg(all(not(mobile), windows))]
    let builder = builder.plugin(tauri_plugin_single_instance::init(|app, _, _| {
        if let Some(window) = app.get_webview_window("main") {
            let _ = window.unminimize();
            let _ = window.show();
            let _ = window.set_focus();
        }
    }));
    #[cfg(not(mobile))]
    let builder = builder.invoke_handler(tauri::generate_handler![
        spotify_player::spotify_player_initialize,
        spotify_player::spotify_player_show,
        spotify_player::spotify_player_hide,
        spotify_player::spotify_player_reload,
        audio_output::audio_output_configure,
    ]);
    let app = builder
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            #[cfg(not(mobile))]
            {
                use tauri_plugin_shell::process::CommandEvent;
                use tauri_plugin_dialog::{DialogExt, MessageDialogKind};

                // Older versions do not have the single-instance guard. Do not
                // let this window silently connect to their surviving backend.
                if let Err(error) = startup::check_backend_port("127.0.0.1:8765") {
                    error_reporting::record("desktop.startup.port_busy", "BackendPortBusy", None);
                    eprintln!("[startup] {error}");
                    let exit_handle = app.handle().clone();
                    app.dialog()
                        .message("TipTune's background service port is already in use. Close all TipTune windows, then launch TipTune again. If the problem continues, restart Windows to close a leftover service.")
                        .title("TipTune could not start")
                        .kind(MessageDialogKind::Error)
                        .show(move |_| exit_handle.exit(1));
                    return Ok(());
                }

                let mut sidecar_log_path: Option<std::path::PathBuf> = None;
                if let Ok(data_dir) = app.path().app_data_dir() {
                    let _ = fs::create_dir_all(&data_dir);
                    sidecar_log_path = Some(data_dir.join("tiptune-sidecar.log"));
                }

                let sidecar_command = app
                    .shell()
                    .sidecar("TipTune")?
                    .env("TIPTUNE_PARENT_PID", std::process::id().to_string())
                    .env("TIPTUNE_WEB_HOST", "127.0.0.1")
                    .env("TIPTUNE_APP_VERSION", app.package_info().version.to_string())
                    .env("TIPTUNE_WEB_PORT", "8765");

                // The launcher and sender must read exactly the same consent and queue.
                let sidecar_command = if let Some(paths) = error_reporting::runtime_paths() {
                    sidecar_command.env("TIPTUNE_CONFIG", paths.config).env("TIPTUNE_CACHE_DIR", paths.cache)
                } else {
                    sidecar_command
                };

                let mut sidecar_command = if env::var("TIPTUNE_LOG_LEVEL").is_err() {
                    sidecar_command.env("TIPTUNE_LOG_LEVEL", "INFO")
                } else {
                    sidecar_command
                };

                if env::var("TIPTUNE_RESOURCE_DIR").is_err() {
                    if let Ok(rd) = app.path().resource_dir() {
                        sidecar_command = sidecar_command
                            .env("TIPTUNE_RESOURCE_DIR", rd.to_string_lossy().to_string());
                    }
                }

                // In `tauri dev` the CLI watches the project directory.
                // If the sidecar writes logs into the repo, it can trigger an infinite rebuild/restart loop.
                // In debug builds, always force the sidecar log file into the app data dir.
                if env::var("TIPTUNE_DEFAULT_LOG_PATH").is_err() {
                    if let Some(p) = &sidecar_log_path {
                        sidecar_command = sidecar_command
                            .env("TIPTUNE_DEFAULT_LOG_PATH", p.to_string_lossy().to_string());
                    }
                }

                #[cfg(windows)]
                let job = windows_job::ProcessJob::new()?;
                #[cfg(windows)]
                let sidecar_command = sidecar_command.env("TIPTUNE_WINDOWS_JOB_NAME", &job.name);

                let (mut rx, child) = sidecar_command.spawn().map_err(|error| {
                    error_reporting::record("desktop.sidecar.spawn.error", "SidecarSpawnError", None);
                    error
                })?;
                #[cfg(windows)]
                if let Err(error) = job.assign(child.pid()) {
                    let _ = child.kill();
                    return Err(error.into());
                }

                app.manage(SidecarState(Mutex::new(Some(RunningSidecar {
                    child,
                    #[cfg(windows)]
                    job,
                }))));
                if let Some(window) = app.get_webview_window("main") {
                    window.show()?;
                }
                let app_handle = app.handle().clone();

                tauri::async_runtime::spawn(async move {
                    while let Some(event) = rx.recv().await {
                        match event {
                            CommandEvent::Stdout(line) => {
                                let s = String::from_utf8_lossy(&line);
                                let s = s.trim_end_matches(&['\r', '\n'][..]);
                                println!("[sidecar stdout] {}", s);
                            }
                            CommandEvent::Stderr(line) => {
                                let s = String::from_utf8_lossy(&line);
                                let s = s.trim_end_matches(&['\r', '\n'][..]);
                                eprintln!("[sidecar stderr] {}", s);
                            }
                            CommandEvent::Error(err) => {
                                error_reporting::record("desktop.sidecar.error", "SidecarError", None);
                                eprintln!("[sidecar error] {}", err);
                            }
                            CommandEvent::Terminated(payload) => {
                                eprintln!("[sidecar terminated] {:?}", payload);
                                let unexpected = app_handle
                                    .try_state::<SidecarState>()
                                    .map(|state| state.0.lock().unwrap_or_else(|e| e.into_inner()).is_some())
                                    .unwrap_or(false);
                                kill_sidecar(&app_handle);
                                if unexpected {
                                    error_reporting::record("desktop.sidecar.terminated", "SidecarExited", payload.code);
                                    if let Some(window) = app_handle.get_webview_window("main") {
                                        let _ = window.hide();
                                    }
                                    let exit_handle = app_handle.clone();
                                    app_handle.dialog()
                                        .message("TipTune's background service stopped. Close TipTune and launch it again. If it still cannot start, share the tiptune-sidecar.log file for diagnosis.")
                                        .title("TipTune could not start")
                                        .kind(MessageDialogKind::Error)
                                        .show(move |_| exit_handle.exit(1));
                                }
                                break;
                            }
                            _ => {}
                        }
                    }
                });
            }

            Ok(())
        })
        .on_window_event(|window, event| {
            #[cfg(not(mobile))]
            {
                if window.label() == "main" && matches!(event, WindowEvent::CloseRequested { .. }) {
                    kill_sidecar(&window.app_handle());
                    window.app_handle().exit(0);
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| {
        #[cfg(not(mobile))]
        {
            if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
                kill_sidecar(app_handle);
            }
        }
    });
}
