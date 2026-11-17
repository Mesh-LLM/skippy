//! Host-local exact-source executables, backed by the compiler's content cache.
use super::*;
use crate::{artifact, execution};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{fs, path::PathBuf};

pub fn run() -> DynResult<()> {
    let sha = std::env::var("JIANYANG_COMMIT")?;
    if execution::capture("git", &["rev-parse".into(), "HEAD".into()], &[])?.trim() != sha {
        return Err("build source differs from admitted JY commit".into());
    }
    execution::run(
        "git",
        &["diff".into(), "--exit-code".into(), "HEAD".into()],
        &[],
        30,
    )?;
    let inputs = json!({"schema":1,"source":sha,"os":std::env::consts::OS,"arch":std::env::consts::ARCH,
        "rust":execution::capture("rustc", &["-vV".into()], &[])?,
        "cuda":execution::capture("nvcc", &["--version".into()], &[])?,
        "cxx":execution::capture("c++", &["--version".into()], &[])?,
        "cmake":execution::capture("cmake", &["--version".into()], &[])?,
        "backend":"cuda","architectures":"121","link":"static","debug":0,
        "environment":build_environment()});
    let key = hex::encode(Sha256::digest(serde_json::to_vec(&inputs)?));
    let home = PathBuf::from(std::env::var_os("HOME").ok_or("HOME missing")?);
    let root = home.join(".cache/skippy/inference-build-v1");
    fs::create_dir_all(&root)?;
    let _lock = lock(&root.join(format!("{key}.lock")))?;
    let cached = root.join(&key);
    if cached.exists() {
        match restore(&cached, &key, &sha) {
            Ok(()) => {
                println!("Inference executable cache HIT: {key}");
                return Ok(());
            }
            Err(error) => {
                eprintln!("Rejecting invalid inference cache entry: {error}");
                fs::remove_dir_all(&cached)?;
            }
        }
    }
    println!("Inference executable cache MISS: {key}");
    let workspace = std::env::current_dir()?.display().to_string();
    let compiler_cache = home.join(".cache/skippy/compiler");
    let mut env = vec![
        ("SCCACHE_DIR", compiler_cache.display().to_string()),
        ("SCCACHE_BASEDIR", workspace),
        ("CARGO_INCREMENTAL", "0".into()),
        ("SCCACHE_SERVER_PORT", "4227".into()),
        ("SCCACHE_CACHE_SIZE", "20G".into()),
        ("CARGO_PROFILE_DEV_DEBUG", "0".into()),
    ];
    if execution::capture("sccache", &["--version".into()], &[]).is_ok() {
        env.push(("RUSTC_WRAPPER", "sccache".into()));
    }
    execution::run("just", &["inference-build-uncached".into()], &env, 7200)?;
    let mut identity = super::provenance::identity(&sha)?;
    identity["cache_key"] = key.clone().into();
    identity["build_inputs"] = inputs;
    identity["cache_hit"] = false.into();
    write_json_file(Path::new("target/inference-build.json"), &identity)?;
    let temp = tempfile::Builder::new()
        .prefix("publish-")
        .tempdir_in(&root)?;
    for name in ["skippy", "skippy-correctness"] {
        fs::copy(Path::new("target/debug").join(name), temp.path().join(name))?;
    }
    write_json_file(&temp.path().join("identity.json"), &identity)?;
    fs::rename(temp.path(), &cached)?;
    if env.iter().any(|(name, _)| *name == "RUSTC_WRAPPER") {
        execution::run("sccache", &["--show-stats".into()], &env, 30)?;
    }
    Ok(())
}

fn build_environment() -> std::collections::BTreeMap<String, String> {
    std::env::vars()
        .filter(|(key, _)| {
            matches!(
                key.as_str(),
                "CC" | "CXX"
                    | "CUDACXX"
                    | "CFLAGS"
                    | "CXXFLAGS"
                    | "RUSTFLAGS"
                    | "CARGO_ENCODED_RUSTFLAGS"
            ) || key.starts_with("LLAMA_")
                || key.starts_with("SKIPPY_")
        })
        .collect()
}

fn restore(cache: &Path, key: &str, sha: &str) -> DynResult<()> {
    let mut identity = validated_entry(cache, key, sha)?;
    fs::create_dir_all("target/debug")?;
    for name in ["skippy", "skippy-correctness"] {
        fs::copy(cache.join(name), Path::new("target/debug").join(name))?;
    }
    identity["cache_hit"] = true.into();
    write_json_file(Path::new("target/inference-build.json"), &identity)
}

fn validated_entry(cache: &Path, key: &str, sha: &str) -> DynResult<Value> {
    let identity: Value = serde_json::from_slice(&fs::read(cache.join("identity.json"))?)?;
    if identity["cache_key"] != key || identity["stack_commit"] != sha {
        return Err("cache source or toolchain key mismatch".into());
    }
    for (name, field) in [
        ("skippy", "cli_sha256"),
        ("skippy-correctness", "correctness_sha256"),
    ] {
        if identity[field] != artifact::sha256(&cache.join(name))? {
            return Err("cached executable hash mismatch".into());
        }
    }
    Ok(identity)
}

pub fn lock(path: &Path) -> DynResult<fs::File> {
    let file = fs::OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(false)
        .open(path)?;
    #[cfg(unix)]
    {
        use std::os::fd::AsRawFd;
        if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX) } != 0 {
            return Err(std::io::Error::last_os_error().into());
        }
    }
    Ok(file)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn cache_rejects_stale_source_toolchains_and_corrupted_executables() {
        let cache = tempfile::tempdir().unwrap();
        for name in ["skippy", "skippy-correctness"] {
            fs::write(cache.path().join(name), b"source-built executable").unwrap();
        }
        let digest = artifact::sha256(&cache.path().join("skippy")).unwrap();
        write_json_file(&cache.path().join("identity.json"), &json!({"stack_commit":"source-a","cache_key":"toolchain-a","cli_sha256":digest,"correctness_sha256":digest})).unwrap();
        assert!(validated_entry(cache.path(), "toolchain-a", "source-a").is_ok());
        assert!(validated_entry(cache.path(), "toolchain-b", "source-a").is_err());
        assert!(validated_entry(cache.path(), "toolchain-a", "source-b").is_err());
        fs::write(cache.path().join("skippy-correctness"), b"corrupted").unwrap();
        assert!(validated_entry(cache.path(), "toolchain-a", "source-a").is_err());
    }
}
