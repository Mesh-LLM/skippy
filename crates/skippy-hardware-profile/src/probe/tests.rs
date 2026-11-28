use std::process::Command;
use std::sync::mpsc;
use std::time::Duration;

const CHILD: &str = "probe::tests::exiting_probe_child";

#[test]
fn exiting_probe_child() {
    if std::env::var_os("SKIPPY_TEST_EXITING_PROBE").is_some() {
        print!("probe-ran");
    }
}

/// `first_line` keeps only the first record, so a malformed later record
/// cannot make the caller reject the whole sample.
#[test]
fn first_line_keeps_only_the_first_record() {
    assert_eq!(
        super::first_record(b"RAM 123/4096MB\n\xffbroken later record\n".to_vec()),
        b"RAM 123/4096MB"
    );
    assert_eq!(super::first_record(b"no newline".to_vec()), b"no newline");
}

/// #3067: a probe tool that exits by itself must be reaped and return its
/// output. On Windows the job-object wait used to block forever here, which
/// hung every hardware survey.
#[test]
fn probe_that_exits_on_its_own_returns_its_output() {
    let (done_tx, done_rx) = mpsc::channel();
    std::thread::spawn(move || {
        let result = super::output(
            Command::new(std::env::current_exe().unwrap())
                .args(["--exact", CHILD, "--nocapture"])
                .env("SKIPPY_TEST_EXITING_PROBE", "1"),
        );
        let _ = done_tx.send(result);
    });
    let output = done_rx
        .recv_timeout(Duration::from_secs(60))
        .expect("a probe that exits on its own must return")
        .unwrap();
    assert!(output.status.success(), "{output:?}");
    assert!(
        String::from_utf8_lossy(&output.stdout).contains("probe-ran"),
        "{output:?}"
    );
}

const SLEEPER: &str = "probe::tests::sleeping_probe_child";
#[cfg(windows)]
const OWNER: &str = "probe::tests::owner_that_exits_mid_probe";

#[test]
fn sleeping_probe_child() {
    if std::env::var_os("SKIPPY_TEST_SLEEPING_PROBE").is_some() {
        std::thread::sleep(Duration::from_secs(120));
    }
}

/// Starts a probe that would outlive it, then exits without stopping it, as a
/// CLI does when it is done before the background survey.
#[test]
fn owner_that_exits_mid_probe() {
    if std::env::var_os("SKIPPY_TEST_PROBE_OWNER_EXITS").is_none() {
        return;
    }
    std::thread::spawn(|| {
        let _ = super::output(
            Command::new(std::env::current_exe().unwrap())
                .args(["--exact", SLEEPER, "--nocapture"])
                .env("SKIPPY_TEST_SLEEPING_PROBE", "1"),
        );
    });
    std::thread::sleep(Duration::from_secs(2));
    std::process::exit(0);
}

/// A probe still running when the process that started it exits must die with
/// it. On Windows it otherwise kept that process's inherited stdout and stderr
/// open, so whoever read them waited for the probe: a `skippy serve
/// --print-effective-config` caller waited for a stuck HIP probe.
#[cfg(windows)]
#[test]
fn a_probe_dies_with_the_process_that_started_it() {
    use std::io::Read;

    let mut owner = Command::new(std::env::current_exe().unwrap())
        .args(["--exact", OWNER, "--nocapture"])
        .env("SKIPPY_TEST_PROBE_OWNER_EXITS", "1")
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .spawn()
        .unwrap();
    let mut stdout = owner.stdout.take().unwrap();
    let (closed_tx, closed_rx) = mpsc::channel();
    std::thread::spawn(move || {
        let mut sink = Vec::new();
        let _ = stdout.read_to_end(&mut sink);
        let _ = closed_tx.send(());
    });
    owner.wait().unwrap();
    closed_rx
        .recv_timeout(Duration::from_secs(30))
        .expect("the owner's stdout must close when it exits, not when its probe does");
}
