//! The build script's reading of paths printed by a Windows bash.
#![cfg(windows)]

#[allow(dead_code)]
#[path = "../build/native_link.rs"]
mod native_link;

#[test]
fn drive_prefixes_of_git_bash_and_cygwin_become_windows_paths() {
    assert_eq!(
        native_link::posix_drive_path("/c/Users/dev/.deps/llama.cpp/build-stage").as_deref(),
        Some("C:/Users/dev/.deps/llama.cpp/build-stage")
    );
    assert_eq!(
        native_link::posix_drive_path("/cygdrive/d/work/build-stage").as_deref(),
        Some("D:/work/build-stage")
    );
    assert_eq!(native_link::posix_drive_path("/c").as_deref(), Some("C:/"));
    assert_eq!(native_link::posix_drive_path("/tmp/build-stage"), None);
    assert_eq!(native_link::posix_drive_path("/cygdrive"), None);
}

#[test]
fn the_selected_bash_converts_its_own_paths() {
    let Some(bash) = native_link::windows_bash() else {
        eprintln!("no Windows bash on this machine; skipping");
        return;
    };
    // Ask the shell for a path in its own format, as build-llama.sh's `pwd`
    // does, then read it back.
    let windows = std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".to_string());
    let output = std::process::Command::new(&bash)
        .args(["-c", "cygpath -u \"$1\"", "cygpath", &windows])
        .output()
        .expect("run the selected bash");
    assert!(output.status.success(), "{} has no cygpath", bash.display());
    let printed = String::from_utf8(output.stdout).expect("UTF-8 path");
    let printed = printed.trim();
    assert!(printed.starts_with('/'), "{printed} is not a POSIX path");
    let native = native_link::native_path(printed);
    assert!(
        native.join("System32").is_dir(),
        "{} read {printed} as {}",
        bash.display(),
        native.display()
    );
}
