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

    pub(super) fn cli_args(self) -> &'static [&'static str] {
        match self {
            Self::Disabled => &[
                "--native-mtp",
                "false",
                "--speculative-strategy",
                "disabled",
            ],
            Self::SingleDraft => &[
                "--native-mtp",
                "true",
                "--speculative-strategy",
                "native-mtp",
                "--mtp-max-tokens",
                "1",
            ],
            Self::DefaultDraft => &[
                "--native-mtp",
                "true",
                "--speculative-strategy",
                "native-mtp",
            ],
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
            MtpCaseMode::Disabled.cli_args(),
            [
                "--native-mtp",
                "false",
                "--speculative-strategy",
                "disabled"
            ]
        );
        assert!(MtpCaseMode::SingleDraft.enabled());
        assert!(
            MtpCaseMode::SingleDraft
                .cli_args()
                .windows(2)
                .any(|args| args == ["--mtp-max-tokens", "1"])
        );
        assert!(MtpCaseMode::DefaultDraft.enabled());
        assert!(
            !MtpCaseMode::DefaultDraft
                .cli_args()
                .contains(&"--mtp-max-tokens")
        );
    }
}
