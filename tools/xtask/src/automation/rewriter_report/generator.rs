//! Validate native generator evidence for first-pass completeness and idempotence.
use serde::Deserialize;
use std::{collections::BTreeMap, path::Path};

#[derive(Debug, thiserror::Error)]
pub(in crate::automation) enum Error {
    #[error("cannot read generator report: {0}")]
    Read(#[from] std::io::Error),
    #[error("invalid producer report: {0:?}")]
    Contract(Vec<String>),
    #[error("rewriter emitted no builder records")]
    Empty,
    #[error("first rewriter pass received transformed model builders: {0}")]
    Inherited(String),
    #[error("rewriter refused decoder builders: {0}")]
    Refused(String),
    #[error("second rewriter pass still has edits: {0}")]
    Remaining(String),
}
#[derive(Clone, Copy)]
pub(in crate::automation) enum Pass {
    First,
    Second,
}
#[derive(Deserialize)]
struct Report {
    builders: Vec<Builder>,
    #[serde(default)]
    summary: Counts,
}
#[derive(Deserialize)]
struct Builder {
    #[serde(default)]
    file: Option<String>,
    verdict: Verdict,
    #[serde(default)]
    unsupported_reason: Option<String>,
}
#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum Verdict {
    AlreadyTransformed,
    Transformable,
    UnsupportedShape,
    Error,
    SupportedAuxiliary,
    SupportedWholeModel,
}
#[derive(Default, Deserialize)]
struct Counts {
    transformable: Option<i64>,
    already_transformed: Option<i64>,
    supported_auxiliary: Option<i64>,
    supported_whole_model: Option<i64>,
    unsupported_shape: Option<i64>,
    error: Option<i64>,
}
pub(in crate::automation) type Summary = BTreeMap<&'static str, u128>;
pub(in crate::automation) fn load(path: &Path, pass: Pass) -> Result<Summary, Error> {
    let bytes = std::fs::read(path)?;
    if bytes.len() > 16 * 1024 * 1024 {
        return Err(Error::Contract(vec!["report exceeds 16 MiB".into()]));
    }
    let report: Report =
        serde_json::from_slice(&bytes).map_err(|e| Error::Contract(vec![e.to_string()]))?;
    if report.builders.is_empty() {
        return Err(Error::Empty);
    }
    gate(&report.builders, pass)?;
    let counts = report.summary;
    let mut summary = Summary::new();
    for (name, count) in [
        ("transformable", counts.transformable),
        ("already_transformed", counts.already_transformed),
        ("supported_auxiliary", counts.supported_auxiliary),
        ("supported_whole_model", counts.supported_whole_model),
        ("unsupported_shape", counts.unsupported_shape),
        ("error", counts.error),
    ] {
        if let Some(count) = count {
            summary.insert(
                name,
                u128::try_from(count)
                    .map_err(|_| Error::Contract(vec![format!("negative count: {name}")]))?,
            );
        }
    }
    Ok(summary)
}
fn gate(builders: &[Builder], pass: Pass) -> Result<(), Error> {
    let (mut inherited, mut refused, mut remaining) = (Vec::new(), Vec::new(), Vec::new());
    for builder in builders {
        let file = builder.file.as_deref().unwrap_or("<unknown>");
        match builder.verdict {
            Verdict::AlreadyTransformed => inherited.push(file),
            Verdict::Transformable => remaining.push(file),
            Verdict::UnsupportedShape | Verdict::Error => refused.push(format!(
                "{file}: {}",
                builder
                    .unsupported_reason
                    .as_deref()
                    .unwrap_or(match builder.verdict {
                        Verdict::UnsupportedShape => "unsupported_shape",
                        _ => "error",
                    })
            )),
            Verdict::SupportedAuxiliary | Verdict::SupportedWholeModel => {}
        }
    }
    match pass {
        Pass::First if !inherited.is_empty() => Err(Error::Inherited(inherited.join(", "))),
        Pass::First if !refused.is_empty() => Err(Error::Refused(refused.join(", "))),
        Pass::Second if !remaining.is_empty() => Err(Error::Remaining(remaining.join(", "))),
        _ => Ok(()),
    }
}
#[cfg(test)]
#[path = "generator_tests.rs"]
mod tests;
