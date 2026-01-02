#[cfg(not(mobile))]
use std::sync::Mutex;

#[cfg(not(mobile))]
use tauri::{Manager, RunEvent, WindowEvent};

#[cfg(not(mobile))]
use tauri_plugin_shell::{process::CommandChild, ShellExt};

#[cfg(all(not(mobile), windows))]
use std::process::Command;

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
    .plugin(tauri_plugin_shell::init())
    .setup(|app| {
      #[cfg(not(mobile))]
      {
        use tauri_plugin_shell::process::CommandEvent;

        let sidecar_command = app
          .shell()
          .sidecar("TipTune")?
          .env("TIPTUNE_PARENT_PID", std::process::id().to_string());
        let (mut rx, child) = sidecar_command.spawn()?;

        app.manage(SidecarState(Mutex::new(Some(child))));
        let app_handle = app.handle().clone();

        tauri::async_runtime::spawn(async move {
          while let Some(event) = rx.recv().await {
            match event {
              CommandEvent::Stdout(_) => {}
              CommandEvent::Stderr(_) => {}
              CommandEvent::Error(_) => {}
              CommandEvent::Terminated(_) => {
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
