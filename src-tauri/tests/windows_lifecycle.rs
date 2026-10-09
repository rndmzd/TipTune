#![cfg(windows)]

#[path = "../src/windows_job.rs"]
mod windows_job;

use std::{
    fs,
    net::TcpListener,
    os::windows::{
        io::{AsRawHandle, FromRawHandle, OwnedHandle},
        process::CommandExt,
    },
    path::PathBuf,
    process::{Child, Command, Stdio},
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use windows_job::ProcessJob;
use windows_sys::Win32::{
    Foundation::WAIT_OBJECT_0,
    System::Threading::{OpenProcess, WaitForSingleObject, PROCESS_SYNCHRONIZE},
};

const WORKER: &str = r#"
from utils.process_lifecycle import join_desktop_job
join_desktop_job()
import json, os, pathlib, socket, subprocess, sys, time
server = socket.socket()
server.bind(('127.0.0.1', 0)); server.listen()
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], creationflags=subprocess.CREATE_NO_WINDOW)
pathlib.Path(os.environ['TIPTUNE_TEST_READY']).write_text(json.dumps({'root': os.getpid(), 'child': child.pid, 'port': server.getsockname()[1]}))
time.sleep(60)
"#;

struct TestChild(Child);
impl Drop for TestChild {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn worker(job: &ProcessJob, ready: &str) -> TestChild {
    let python = std::env::var("TIPTUNE_TEST_PYTHON").unwrap_or_else(|_| "python".into());
    let child = Command::new(python)
        .args(["-c", WORKER])
        .current_dir(PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().unwrap())
        .env("TIPTUNE_WINDOWS_JOB_NAME", &job.name)
        .env("TIPTUNE_TEST_READY", ready)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .creation_flags(0x08000000)
        .spawn()
        .unwrap();
    let child = TestChild(child);
    job.assign(child.0.id()).unwrap();
    child
}

fn case_files() -> (PathBuf, PathBuf) {
    let prefix = format!(
        "tiptune-lifecycle-{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    );
    (
        std::env::temp_dir().join(format!("{prefix}.json")),
        std::env::temp_dir().join(format!("{prefix}.gate")),
    )
}

fn read_ready(path: &PathBuf) -> serde_json::Value {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        if let Ok(text) = fs::read_to_string(path) {
            if let Ok(value) = serde_json::from_str(&text) {
                return value;
            }
        }
        assert!(
            Instant::now() < deadline,
            "Process fixture did not become ready"
        );
        thread::sleep(Duration::from_millis(25));
    }
}

fn process_handles(ready: &serde_json::Value) -> Vec<OwnedHandle> {
    ["root", "child"]
        .into_iter()
        .map(|key| {
            let handle =
                unsafe { OpenProcess(PROCESS_SYNCHRONIZE, 0, ready[key].as_u64().unwrap() as u32) };
            assert!(!handle.is_null(), "Could not hold {key} process handle");
            unsafe { OwnedHandle::from_raw_handle(handle) }
        })
        .collect()
}

fn assert_stopped(handles: Vec<OwnedHandle>, ready: &serde_json::Value) {
    for handle in handles {
        assert_eq!(
            unsafe { WaitForSingleObject(handle.as_raw_handle(), 5000) },
            WAIT_OBJECT_0
        );
    }
    TcpListener::bind(("127.0.0.1", ready["port"].as_u64().unwrap() as u16))
        .expect("Backend port still held");
}

#[test]
fn normal_shutdown_terminates_the_tree_and_releases_its_port() {
    let job = ProcessJob::new().unwrap();
    let (path, _) = case_files();
    let _child = worker(&job, path.to_str().unwrap());
    let ready = read_ready(&path);
    let handles = process_handles(&ready);
    job.terminate().unwrap();
    job.terminate().unwrap();
    assert_stopped(handles, &ready);
    fs::remove_file(path).unwrap();
}

#[test]
fn dropping_the_last_job_handle_terminates_the_tree() {
    let job = ProcessJob::new().unwrap();
    let (path, _) = case_files();
    let _child = worker(&job, path.to_str().unwrap());
    let ready = read_ready(&path);
    let handles = process_handles(&ready);
    drop(job);
    assert_stopped(handles, &ready);
    fs::remove_file(path).unwrap();
}

fn abrupt_exit(mode: &str) {
    let (path, gate) = case_files();
    let mut owner = TestChild(
        Command::new(std::env::current_exe().unwrap())
            .args(["--ignored", "--exact", "owner_fixture", "--nocapture"])
            .env("TIPTUNE_TEST_READY", &path)
            .env("TIPTUNE_TEST_GATE", &gate)
            .env("TIPTUNE_TEST_EXIT_MODE", mode)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .creation_flags(0x08000000)
            .spawn()
            .unwrap(),
    );
    let ready = read_ready(&path);
    let handles = process_handles(&ready);
    if mode == "updater" {
        fs::write(&gate, "exit").unwrap();
    } else {
        owner.0.kill().unwrap();
    }
    owner.0.wait().unwrap();
    assert_stopped(handles, &ready);
    let _ = fs::remove_file(path);
    let _ = fs::remove_file(gate);
}

#[test]
fn updater_style_process_exit_bypasses_drop_but_stops_the_tree() {
    abrupt_exit("updater");
}

#[test]
fn forced_desktop_termination_stops_the_tree() {
    abrupt_exit("forced");
}

#[test]
fn cleanup_does_not_terminate_processes_outside_the_backend_job() {
    let python = std::env::var("TIPTUNE_TEST_PYTHON").unwrap_or_else(|_| "python".into());
    let mut unrelated = TestChild(
        Command::new(python)
            .args(["-c", "import time; time.sleep(60)"])
            .creation_flags(0x08000000)
            .spawn()
            .unwrap(),
    );
    let job = ProcessJob::new().unwrap();
    let (path, _) = case_files();
    let _child = worker(&job, path.to_str().unwrap());
    let ready = read_ready(&path);
    let handles = process_handles(&ready);
    job.terminate().unwrap();
    assert_stopped(handles, &ready);
    assert!(unrelated.0.try_wait().unwrap().is_none());
    fs::remove_file(path).unwrap();
}

#[test]
#[ignore = "Subprocess fixture, launched by lifecycle tests"]
fn owner_fixture() {
    let Ok(mode) = std::env::var("TIPTUNE_TEST_EXIT_MODE") else {
        return;
    };
    let job = ProcessJob::new().unwrap();
    let ready = std::env::var("TIPTUNE_TEST_READY").unwrap();
    let gate = PathBuf::from(std::env::var("TIPTUNE_TEST_GATE").unwrap());
    let _child = if let Ok(executable) = std::env::var("TIPTUNE_TEST_BACKEND_EXE") {
        let child = TestChild(
            Command::new(executable)
                .env("TIPTUNE_WINDOWS_JOB_NAME", &job.name)
                .env("TIPTUNE_PARENT_PID", std::process::id().to_string())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .creation_flags(0x08000000)
                .spawn()
                .unwrap(),
        );
        job.assign(child.0.id()).unwrap();
        fs::write(
            &ready,
            serde_json::json!({"root": child.0.id()}).to_string(),
        )
        .unwrap();
        child
    } else {
        worker(&job, &ready)
    };
    loop {
        if gate.exists() {
            if mode == "updater" {
                std::process::exit(0);
            }
            if mode == "normal" {
                job.terminate().unwrap();
                return;
            }
        }
        thread::sleep(Duration::from_millis(25));
    }
}
