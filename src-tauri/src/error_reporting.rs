//! Record technical desktop failures for the Python Sentry sender on next launch.
//! No network client, panic text, log files, or application data is collected here.
use std::{
    env, fs, io,
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
    time::{SystemTime, UNIX_EPOCH},
};

use serde_json::json;

static NEXT_ID: AtomicU64 = AtomicU64::new(0);
const DESTINATION: &str = include_str!("../../sentry.json");

#[derive(Clone)]
pub struct ReportingPaths {
    pub config: PathBuf,
    pub cache: PathBuf,
}

fn env_path(name: &str) -> Option<PathBuf> {
    env::var_os(name)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
}

pub fn runtime_paths() -> Option<ReportingPaths> {
    let home = env_path("USERPROFILE").or_else(|| env_path("HOME"));
    let roaming = env_path("APPDATA").or_else(|| home.as_ref().map(|p| p.join("AppData/Roaming")));
    let local = env_path("LOCALAPPDATA").or_else(|| home.as_ref().map(|p| p.join("AppData/Local")));
    let portable = env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|p| p.join("config.ini")))
        .filter(|p| p.is_file());
    Some(ReportingPaths {
        config: env_path("TIPTUNE_CONFIG")
            .or(portable)
            .or_else(|| roaming.map(|p| p.join("TipTune/config.ini")))?,
        cache: env_path("TIPTUNE_CACHE_DIR").or_else(|| local.map(|p| p.join("TipTune/.cache")))?,
    })
}

fn consent_from_ini(text: &str) -> bool {
    let mut general = false;
    let mut enabled = None;
    for line in text.trim_start_matches('\u{feff}').lines() {
        let line = line.trim();
        if line.starts_with('#') || line.starts_with(';') || line.is_empty() {
            continue;
        }
        if line.starts_with('[') {
            general = line == "[General]";
            continue;
        }
        if general {
            if let Some((key, value)) = line.split_once('=').or_else(|| line.split_once(':')) {
                if key.trim().eq_ignore_ascii_case("send_error_reports") {
                    // Invalid or duplicate consent fails closed, like ConfigParser.
                    if enabled.is_some() {
                        return false;
                    }
                    enabled = Some(matches!(
                        value.trim().to_ascii_lowercase().as_str(),
                        "true" | "1" | "yes" | "on"
                    ));
                }
            }
        }
    }
    enabled.unwrap_or(false)
}

fn opted_in(paths: &ReportingPaths) -> bool {
    fs::read_to_string(&paths.config)
        .map(|text| consent_from_ini(&text))
        .unwrap_or(false)
}

fn configured() -> bool {
    let dsn = env::var("TIPTUNE_SENTRY_DSN").ok().unwrap_or_else(|| {
        serde_json::from_str::<serde_json::Value>(DESTINATION)
            .ok()
            .and_then(|value| value["dsn"].as_str().map(str::to_owned))
            .unwrap_or_default()
    });
    !dsn.trim().is_empty()
}

fn report_file(path: &Path) -> bool {
    path.file_name()
        .and_then(|name| name.to_str())
        .map(|name| {
            name.strip_suffix(".json")
                .map(|id| {
                    id.len() == 32
                        && id
                            .bytes()
                            .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
                })
                .unwrap_or(false)
        })
        .unwrap_or(false)
}

fn write_event(
    paths: &ReportingPaths,
    event_type: &str,
    error_type: &str,
    code: Option<i32>,
    location: Option<(&str, u32)>,
) -> io::Result<()> {
    if !opted_in(paths) {
        return Ok(());
    }
    let elapsed = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let id = format!(
        "{:032x}",
        elapsed.as_nanos()
            ^ ((std::process::id() as u128) << 32)
            ^ NEXT_ID.fetch_add(1, Ordering::Relaxed) as u128
    );
    let now = elapsed.as_secs_f64();
    let mut exception = json!({"type": error_type, "value": event_type, "mechanism": {"type": "generic", "handled": false}});
    if let Some((file, line)) = location {
        let filename = file.rsplit(['/', '\\']).next().unwrap_or("native.rs");
        exception["stacktrace"] =
            json!({"frames": [{"filename": filename, "function": "native", "lineno": line}]});
    }
    let mut failure = json!({});
    if let Some(code) = code {
        failure["exit_code"] = json!(code);
    }
    let record = json!({
        "created_at": now, "attempts": 0, "retry_at": 0,
        "event": {
            "event_id": id, "timestamp": now, "level": "error", "platform": "native",
            "release": format!("tiptune@{}", env!("CARGO_PKG_VERSION")),
            "tags": {"component": "desktop", "event_type": event_type},
            "exception": {"values": [exception]}, "contexts": {"failure": failure}
        }
    });
    let queue = paths.cache.join("error-reports/pending");
    fs::create_dir_all(&queue)?;
    let mut files: Vec<_> = fs::read_dir(&queue)?
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|p| report_file(p))
        .collect();
    files.sort_by_key(|path| fs::metadata(path).and_then(|m| m.modified()).ok());
    for path in files.iter().take(files.len().saturating_sub(99)) {
        let _ = fs::remove_file(path);
    }
    let temporary = queue.join(format!("{id}.tmp"));
    fs::write(&temporary, serde_json::to_vec(&record)?)?;
    if opted_in(paths) {
        fs::rename(&temporary, queue.join(format!("{id}.json")))?;
    } else {
        let _ = fs::remove_file(temporary);
    }
    Ok(())
}

pub fn record(event_type: &str, error_type: &str, code: Option<i32>) {
    if configured() {
        if let Some(paths) = runtime_paths() {
            let _ = write_event(&paths, event_type, error_type, code, None);
        }
    }
}

pub fn install_panic_hook() {
    let original = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        if configured() {
            if let Some(paths) = runtime_paths() {
                let location = info
                    .location()
                    .map(|location| (location.file(), location.line()));
                let _ = write_event(&paths, "desktop.panic", "RustPanic", None, location);
            }
        }
        original(info);
    }));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn consent_requires_an_explicit_valid_setting() {
        for text in [
            "",
            "[General]\nsend_error_reports=false",
            "[Other]\nsend_error_reports=true",
            "[General]\nsend_error_reports=maybe",
            "[General]\nsend_error_reports=true\nsend_error_reports=false",
        ] {
            assert!(!consent_from_ini(text));
        }
        for value in ["true", "TRUE", "1", "yes", "on"] {
            assert!(consent_from_ini(&format!(
                "[General]\nsend_error_reports={value}"
            )));
        }
    }

    #[test]
    fn desktop_queue_is_consent_gated_and_contains_only_technical_metadata() {
        let root = env::temp_dir().join(format!(
            "tiptune-report-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let paths = ReportingPaths {
            config: root.join("config.ini"),
            cache: root.join("cache"),
        };
        fs::create_dir_all(&root).unwrap();
        write_event(
            &paths,
            "desktop.sidecar.terminated",
            "SidecarExited",
            Some(1),
            None,
        )
        .unwrap();
        assert!(!paths.cache.exists());
        fs::write(
            &paths.config,
            "[General]\nsend_error_reports=true\n[OpenAI]\napi_key=private-test-key",
        )
        .unwrap();
        write_event(
            &paths,
            "desktop.sidecar.terminated",
            "SidecarExited",
            Some(1),
            None,
        )
        .unwrap();
        let directory = paths.cache.join("error-reports/pending");
        let files: Vec<_> = fs::read_dir(&directory)
            .unwrap()
            .map(|entry| entry.unwrap().path())
            .collect();
        assert_eq!(files.len(), 1);
        let text = fs::read_to_string(&files[0]).unwrap();
        assert!(!text.contains("private-test-key"));
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(&text).unwrap()["event"]["contexts"]
                ["failure"]["exit_code"],
            1
        );
        fs::remove_file(&files[0]).unwrap();
        fs::remove_dir(&directory).unwrap();
        fs::remove_dir(paths.cache.join("error-reports")).unwrap();
        fs::remove_dir(&paths.cache).unwrap();
        fs::remove_file(&paths.config).unwrap();
        fs::remove_dir(&root).unwrap();
    }
}
