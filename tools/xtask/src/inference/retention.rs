use super::*;
use std::fs;
pub fn archive(directory: &Path, output: &Path) -> DynResult<()> {
    if !directory.is_dir() {
        return Ok(());
    }
    let mut index = 0;
    summaries(directory, output, &mut index)?;
    let file = fs::File::create(output.join("agentx.tar.gz"))?;
    let encoder = flate2::write::GzEncoder::new(file, flate2::Compression::default());
    let mut archive = tar::Builder::new(encoder);
    archive.append_dir_all("agentx", directory)?;
    archive.into_inner()?.finish()?;
    Ok(())
}
fn summaries(directory: &Path, output: &Path, index: &mut usize) -> DynResult<()> {
    for entry in fs::read_dir(directory)? {
        let path = entry?.path();
        if path.is_dir() {
            summaries(&path, output, index)?;
        } else if path
            .file_name()
            .is_some_and(|n| n == "profile_export_aiperf.json")
        {
            fs::copy(path, output.join(format!("agentx-profile-{index}.json")))?;
            *index += 1;
        }
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn nested_metrics_and_request_evidence_survive_flat_uploads() {
        let root = tempfile::tempdir().unwrap();
        let output = root.path().join("out");
        let data = output.join("agentx/run-0");
        fs::create_dir_all(&data).unwrap();
        fs::write(data.join("profile_export_aiperf.json"), "{}").unwrap();
        fs::write(data.join("requests.jsonl"), "{\"request\":1}\n").unwrap();
        archive(&output.join("agentx"), &output).unwrap();
        assert!(output.join("agentx-profile-0.json").is_file());
        let stream =
            flate2::read::GzDecoder::new(fs::File::open(output.join("agentx.tar.gz")).unwrap());
        let mut tar = tar::Archive::new(stream);
        assert!(
            tar.entries().unwrap().any(|entry| entry
                .unwrap()
                .path()
                .unwrap()
                .ends_with("requests.jsonl"))
        );
    }
}
