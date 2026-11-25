#[derive(Clone, Copy)]
pub(super) enum MtpCaseMode {
    Disabled,
    SingleDraft,
    DefaultDraft,
}

impl MtpCaseMode {
    pub(super) fn enabled(self) -> bool {
        !matches!(self, Self::Disabled)
    }

    pub(super) fn frontend_cli_args(self) -> &'static [&'static str] {
        match self {
            Self::Disabled => &["--native-mtp=false", "--speculative-strategy", "disabled"],
            Self::SingleDraft => &[
                "--native-mtp=true",
                "--speculative-strategy",
                "native-mtp",
                "--mtp-max-tokens",
                "1",
            ],
            Self::DefaultDraft => &["--native-mtp=true", "--speculative-strategy", "native-mtp"],
        }
    }

    pub(super) fn worker_cli_args(self) -> &'static [&'static str] {
        if self.enabled() {
            &["--native-mtp=true"]
        } else {
            &["--native-mtp=false"]
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cases_select_current_runtime_controls() {
        assert!(!MtpCaseMode::Disabled.enabled());
        assert_eq!(
            MtpCaseMode::Disabled.frontend_cli_args(),
            ["--native-mtp=false", "--speculative-strategy", "disabled"]
        );
        assert!(MtpCaseMode::SingleDraft.enabled());
        assert!(
            MtpCaseMode::SingleDraft
                .frontend_cli_args()
                .windows(2)
                .any(|args| args == ["--mtp-max-tokens", "1"])
        );
        assert!(MtpCaseMode::DefaultDraft.enabled());
        assert!(
            !MtpCaseMode::DefaultDraft
                .frontend_cli_args()
                .contains(&"--mtp-max-tokens")
        );
        for mode in [
            MtpCaseMode::Disabled,
            MtpCaseMode::SingleDraft,
            MtpCaseMode::DefaultDraft,
        ] {
            assert_eq!(mode.worker_cli_args().len(), 1);
            assert!(mode.worker_cli_args()[0].starts_with("--native-mtp="));
        }
    }
}
