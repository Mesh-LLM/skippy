use std::sync::OnceLock;

use anyhow::{Context, Result};
use serde::Deserialize;

use crate::package::SkippyPackageIdentity;

const ROSTER_JSON: &str = include_str!("split-certified.json");

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SplitCertificationAdmission {
    Certified,
    UncertifiedOverride,
}

impl SplitCertificationAdmission {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Certified => "certified",
            Self::UncertifiedOverride => "uncertified_override",
        }
    }
}

#[derive(Debug, Deserialize)]
struct SplitCertificationRoster {
    schema_version: u32,
    architectures: Vec<String>,
}

fn roster() -> Result<&'static SplitCertificationRoster> {
    static ROSTER: OnceLock<Result<SplitCertificationRoster, String>> = OnceLock::new();
    ROSTER
        .get_or_init(|| {
            serde_json::from_str(ROSTER_JSON)
                .map_err(|error| format!("parse bundled split certification roster: {error}"))
        })
        .as_ref()
        .map_err(|error| anyhow::anyhow!(error.clone()))
}

fn validate_schema(roster: &SplitCertificationRoster) -> Result<()> {
    anyhow::ensure!(
        roster.schema_version == 2,
        "unsupported split certification roster schema {}",
        roster.schema_version
    );
    Ok(())
}

fn architecture_is_certified(
    roster: &SplitCertificationRoster,
    architecture: &str,
) -> Result<bool> {
    validate_schema(roster).context("validate split certification roster schema")?;
    Ok(roster
        .architectures
        .iter()
        .any(|certified| certified == architecture))
}

pub fn require_split_certification(
    package: &SkippyPackageIdentity,
    architecture: &str,
    allow_uncertified: bool,
) -> Result<SplitCertificationAdmission> {
    require_with_roster(roster(), package, architecture, allow_uncertified)
}

fn require_with_roster(
    roster: Result<&SplitCertificationRoster>,
    package: &SkippyPackageIdentity,
    architecture: &str,
    allow_uncertified: bool,
) -> Result<SplitCertificationAdmission> {
    let certified = roster
        .context("load split certification roster")
        .and_then(|roster| architecture_is_certified(roster, architecture));
    match certified {
        Ok(true) => {}
        Ok(false) => return handle_uncertified(package, architecture, allow_uncertified, None),
        Err(error) => {
            return handle_uncertified(package, architecture, allow_uncertified, Some(error));
        }
    };
    tracing::info!(
        architecture,
        package_ref = package.package_ref,
        source_model_sha256 = package.source_model_sha256,
        manifest_sha256 = package.manifest_sha256,
        split_certification = "certified",
        "admitted split-serving model from architecture certification roster"
    );
    Ok(SplitCertificationAdmission::Certified)
}

fn handle_uncertified(
    package: &SkippyPackageIdentity,
    architecture: &str,
    allow_uncertified: bool,
    roster_error: Option<anyhow::Error>,
) -> Result<SplitCertificationAdmission> {
    if !allow_uncertified {
        if let Some(error) = roster_error {
            return Err(error).context(format!(
                "split serving certification cannot be established for {}; retry explicitly with plan-split --allow-uncertified-split to run it experimentally",
                package.package_ref
            ));
        }
        anyhow::bail!(
            "split serving architecture {architecture} is not certified for model artifact {} (source SHA-256 {}, manifest SHA-256 {}); retry explicitly with plan-split --allow-uncertified-split to run it experimentally",
            package.package_ref,
            package.source_model_sha256,
            package.manifest_sha256
        );
    }
    if let Some(error) = roster_error {
        tracing::warn!(
            package_ref = package.package_ref,
            architecture,
            source_model_sha256 = package.source_model_sha256,
            manifest_sha256 = package.manifest_sha256,
            error = %error,
            split_certification = "uncertified_override",
            "unsafe override admitted a split-serving model without a valid certification roster"
        );
    } else {
        tracing::warn!(
            package_ref = package.package_ref,
            architecture,
            source_model_sha256 = package.source_model_sha256,
            manifest_sha256 = package.manifest_sha256,
            split_certification = "uncertified_override",
            "unsafe override admitted an uncertified split-serving model"
        );
    }
    Ok(SplitCertificationAdmission::UncertifiedOverride)
}

#[cfg(test)]
mod tests {
    use super::*;

    // This fixture tests admission policy, not real-model qualification. The
    // llama upgrade owns the qualified architecture list.
    fn fixture_roster() -> SplitCertificationRoster {
        SplitCertificationRoster {
            schema_version: 2,
            architectures: vec!["inkling".into()],
        }
    }

    fn package(source_model_sha256: &str) -> SkippyPackageIdentity {
        SkippyPackageIdentity {
            package_ref: format!("local-gguf://sha256/{source_model_sha256}"),
            manifest_sha256: "1".repeat(64),
            source_model_path: Default::default(),
            source_model_sha256: source_model_sha256.to_string(),
            source_model_bytes: 1,
            source_files: Vec::new(),
            layer_weight_bytes: Vec::new(),
            layer_count: 1,
            activation_width: 1,
            tensor_count: 1,
            generation: None,
            publisher_defaults: None,
        }
    }

    #[test]
    fn admits_different_artifact_of_certified_architecture() {
        let roster = fixture_roster();
        validate_schema(&roster).unwrap();
        let architecture = &roster.architectures[0];
        assert_eq!(
            require_with_roster(Ok(&roster), &package(&"f".repeat(64)), architecture, false)
                .unwrap(),
            SplitCertificationAdmission::Certified
        );
    }

    #[test]
    fn bundled_roster_admits_a_listed_architecture_without_an_override() {
        let roster = roster().unwrap();
        assert_eq!(
            require_split_certification(
                &package(&"f".repeat(64)),
                &roster.architectures[0],
                false,
            )
            .unwrap(),
            SplitCertificationAdmission::Certified
        );
    }

    #[test]
    fn certification_is_independent_of_native_recipe_metadata() {
        let mut json = serde_json::json!({
            "schema_version": 2,
            "architectures": ["inkling"],
        });
        // Qualification metadata is provenance, so absent metadata and a
        // different upstream pin, patch queue or ABI cannot revoke a family.
        for recipe in [
            None,
            Some(serde_json::json!({
                "llama_upstream_sha": "0".repeat(40),
                "patch_queue_sha256": "0".repeat(64),
                "skippy_abi": "0.1.0",
            })),
            Some(serde_json::json!({
                "llama_upstream_sha": "f".repeat(40),
                "patch_queue_sha256": "f".repeat(64),
                "skippy_abi": "99.0.0",
            })),
        ] {
            if let Some(recipe) = recipe {
                json["native_recipe"] = recipe;
            }
            let roster = serde_json::from_value(json.clone()).unwrap();
            let package = package(&"f".repeat(64));
            assert_eq!(
                require_with_roster(Ok(&roster), &package, "inkling", false).unwrap(),
                SplitCertificationAdmission::Certified
            );
            assert!(require_with_roster(Ok(&roster), &package, "unknown", false).is_err());
        }
    }

    #[test]
    fn rejects_uncertified_architecture_with_actionable_override() {
        let error =
            require_split_certification(&package(&"f".repeat(64)), "unknown", false).unwrap_err();
        assert!(
            error
                .to_string()
                .contains("plan-split --allow-uncertified-split")
        );
    }

    #[test]
    fn explicit_override_admits_uncertified_architecture() {
        assert_eq!(
            require_split_certification(&package(&"f".repeat(64)), "unknown", true).unwrap(),
            SplitCertificationAdmission::UncertifiedOverride
        );
    }

    #[test]
    fn package_v2_uses_the_same_architecture_admission() {
        let mut package = package(&"f".repeat(64));
        package.package_ref = "hf://example/package".to_string();
        let roster = fixture_roster();
        assert_eq!(
            require_with_roster(Ok(&roster), &package, "inkling", false).unwrap(),
            SplitCertificationAdmission::Certified
        );
    }

    #[test]
    fn unsupported_schema_never_certifies_a_listed_architecture() {
        let mut roster = fixture_roster();
        roster.schema_version = 1;
        let package = package(&"f".repeat(64));
        assert!(require_with_roster(Ok(&roster), &package, "inkling", false).is_err());
        assert_eq!(
            require_with_roster(Ok(&roster), &package, "inkling", true).unwrap(),
            SplitCertificationAdmission::UncertifiedOverride
        );
    }

    #[test]
    fn unavailable_roster_requires_an_explicit_override() {
        let package = package(&"f".repeat(64));
        assert!(
            require_with_roster(
                Err(anyhow::anyhow!("unavailable")),
                &package,
                "inkling",
                false
            )
            .is_err()
        );
        assert_eq!(
            require_with_roster(
                Err(anyhow::anyhow!("unavailable")),
                &package,
                "inkling",
                true
            )
            .unwrap(),
            SplitCertificationAdmission::UncertifiedOverride
        );
    }
}
