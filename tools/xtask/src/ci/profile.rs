use crate::command::DynResult;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Profile {
    Pr,
    Full,
}

impl Profile {
    pub fn from_environment() -> DynResult<Self> {
        Self::parse(&std::env::var("SKIPPY_CI_PROFILE").unwrap_or_else(|_| "full".into()))
    }

    pub fn parse(value: &str) -> DynResult<Self> {
        match value {
            "pr" => Ok(Self::Pr),
            "full" => Ok(Self::Full),
            _ => Err("CI profile must be pr or full".into()),
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Self::Pr => "pr",
            Self::Full => "full",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_declared_profiles_are_admitted() {
        assert_eq!(Profile::parse("pr").unwrap(), Profile::Pr);
        assert_eq!(Profile::parse("full").unwrap(), Profile::Full);
        for value in ["", "skip", "release", "PR"] {
            assert!(Profile::parse(value).is_err());
        }
    }
}
