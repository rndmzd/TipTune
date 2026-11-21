use std::{
    io::{self, Write},
    path::Path,
};
use tauri::Webview;
use tauri_plugin_dialog::DialogExt;
use tempfile::NamedTempFile;

fn write_export(path: &Path, contents: &[u8]) -> io::Result<()> {
    let directory = path
        .parent()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "Choose a file in a folder."))?;
    // An interrupted export must leave any existing file intact.
    let mut file = NamedTempFile::new_in(directory)?;
    file.write_all(contents)?;
    file.as_file().sync_all()?;
    file.persist(path)?;
    Ok(())
}

#[tauri::command]
pub async fn save_history_export(
    webview: Webview,
    filename: String,
    contents: String,
) -> Result<Option<String>, String> {
    if webview.label() != "main" {
        return Err("Only the main TipTune window can export history.".into());
    }
    let window = webview.window();
    tauri::async_runtime::spawn_blocking(move || {
        let name = Path::new(&filename)
            .file_name()
            .and_then(|name| name.to_str())
            .ok_or("Invalid export filename.")?;
        let selection = window
            .dialog()
            .file()
            .set_parent(&window)
            .set_title("Export request history")
            .set_file_name(name)
            .add_filter("JSON", &["json"])
            .blocking_save_file();
        let Some(selection) = selection else {
            return Ok(None);
        };
        let path = selection.into_path().map_err(|error| error.to_string())?;
        write_export(&path, contents.as_bytes())
            .map_err(|error| format!("Could not write {}. {error}", path.display()))?;
        Ok(Some(path.display().to_string()))
    })
    .await
    .map_err(|error| error.to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn saves_and_replaces_the_selected_file_and_cleans_up_failed_writes() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("request history.json");
        write_export(&path, b"previous export").unwrap();
        let contents = r#"{"history":[{"song":"Dégel 🎵"}]}"#.as_bytes();
        write_export(&path, contents).unwrap();
        assert_eq!(fs::read(&path).unwrap(), contents);

        let folder = directory.path().join("folder");
        fs::create_dir(&folder).unwrap();
        assert!(write_export(&folder, contents).is_err());
        assert!(folder.is_dir());
        assert_eq!(fs::read_dir(directory.path()).unwrap().count(), 2);
    }
}
