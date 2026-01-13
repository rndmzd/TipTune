#[cfg(not(mobile))]
use std::sync::Mutex;

#[cfg(not(mobile))]
use std::{env, fs};

#[cfg(not(mobile))]
use tauri::{Manager, RunEvent, WindowEvent};

#[cfg(not(mobile))]
use tauri_plugin_shell::{process::CommandChild, ShellExt};

#[cfg(all(not(mobile), windows))]
use std::process::{Command, Stdio};

#[cfg(not(mobile))]
struct SidecarState(Mutex<Option<CommandChild>>);

#[cfg(not(mobile))]
fn kill_sidecar(app: &tauri::AppHandle) {
    if let Ok(mut guard) = app.state::<SidecarState>().0.lock() {
        if let Some(child) = guard.take() {
            #[cfg(windows)]
            {
                let pid = child.pid();
                let _ = Command::new("taskkill")
                    .args(["/PID", &pid.to_string(), "/T", "/F"])
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status();
            }

            #[cfg(not(windows))]
            {
                let _ = child.kill();
            }
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            #[cfg(not(mobile))]
            {
                use tauri_plugin_shell::process::CommandEvent;

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
                    .env("TIPTUNE_WEB_PORT", "8765");

                let mut sidecar_command = if env::var("TIPTUNE_LOG_LEVEL").is_err() {
                    sidecar_command.env("TIPTUNE_LOG_LEVEL", "INFO")
                } else {
                    sidecar_command
                };

                // In `tauri dev` the CLI watches the project directory.
                // If the sidecar writes logs into the repo, it can trigger an infinite rebuild/restart loop.
                // In debug builds, always force the sidecar log file into the app data dir.
                if env::var("TIPTUNE_DEFAULT_LOG_PATH").is_err() {
                    if let Some(p) = &sidecar_log_path {
                        sidecar_command = sidecar_command
                            .env("TIPTUNE_DEFAULT_LOG_PATH", p.to_string_lossy().to_string());
                    }
                }

                let (mut rx, child) = sidecar_command.spawn()?;

                app.manage(SidecarState(Mutex::new(Some(child))));
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
                                eprintln!("[sidecar error] {}", err);
                            }
                            CommandEvent::Terminated(payload) => {
                                eprintln!("[sidecar terminated] {:?}", payload);
                                kill_sidecar(&app_handle);
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
                if matches!(event, WindowEvent::CloseRequested { .. }) {
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
