#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  tauri::Builder::default()
    .plugin(tauri_plugin_shell::init())
    .setup(|app| {
      #[cfg(not(mobile))]
      {
        use tauri_plugin_shell::process::CommandEvent;
        use tauri_plugin_shell::ShellExt;

        let sidecar_command = app.shell().sidecar("TipTune")?;
        let (mut rx, child) = sidecar_command.spawn()?;

        tauri::async_runtime::spawn(async move {
          let _child = child;
          while let Some(event) = rx.recv().await {
            match event {
              CommandEvent::Stdout(_) => {}
              CommandEvent::Stderr(_) => {}
              CommandEvent::Error(_) => {}
              CommandEvent::Terminated(_) => break,
              _ => {}
            }
          }
        });
      }

      Ok(())
    })
    .run(tauri::generate_context!())
    .expect("error while running tauri application");
}
