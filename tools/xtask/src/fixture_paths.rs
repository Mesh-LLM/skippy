#[cfg(test)]
pub(crate) fn examples_dir() -> std::path::PathBuf {
    std::env::current_exe()
        .expect("test executable")
        .parent()
        .expect("deps directory")
        .parent()
        .expect("target profile")
        .join("examples")
}
