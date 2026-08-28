//! Build-bound producer identity for semantic artifacts.

use jet3_testkit::{Producer, ProducerKind};

const EMBEDDED_IDENTITY: &str = env!("JET3_BUILD_IDENTITY");

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum BuildIdentity<'a> {
    Clean(&'a str),
    Dirty(&'a str),
    Diagnostic(&'a str),
}

impl<'a> BuildIdentity<'a> {
    fn parse(value: &'a str) -> Self {
        if is_exact_revision(value) {
            Self::Clean(value)
        } else if let Some(revision) = value.strip_suffix("-dirty")
            && is_exact_revision(revision)
        {
            Self::Dirty(value)
        } else {
            Self::Diagnostic(value)
        }
    }
}

pub(crate) fn snapshot_producer() -> Result<Producer, &'static str> {
    producer_for(BuildIdentity::parse(EMBEDDED_IDENTITY))
}

fn producer_for(identity: BuildIdentity<'_>) -> Result<Producer, &'static str> {
    match identity {
        BuildIdentity::Clean(revision) => {
            Producer::new(ProducerKind::Rust, revision).map_err(|_| "invalid_build_identity")
        }
        BuildIdentity::Dirty(_) => Err("dirty_build_identity"),
        BuildIdentity::Diagnostic(_) => Err("unavailable_build_identity"),
    }
}

fn is_exact_revision(value: &str) -> bool {
    value.len() == 40
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

#[cfg(test)]
mod tests {
    use super::{BuildIdentity, producer_for};

    const REVISION: &str = "0123456789abcdef0123456789abcdef01234567";

    #[test]
    fn exact_clean_revision_constructs_the_producer() -> Result<(), Box<dyn std::error::Error>> {
        let producer = producer_for(BuildIdentity::parse(REVISION))?;
        assert_eq!(producer.source_revision(), REVISION);
        Ok(())
    }

    #[test]
    fn dirty_and_diagnostic_builds_cannot_produce_exact_commit_evidence() {
        assert_eq!(
            producer_for(BuildIdentity::parse(&format!("{REVISION}-dirty"))).err(),
            Some("dirty_build_identity")
        );
        assert_eq!(
            producer_for(BuildIdentity::parse("diagnostic-non-git-build")).err(),
            Some("unavailable_build_identity")
        );
    }

    #[test]
    fn uppercase_short_and_decorated_revisions_are_diagnostic() {
        for value in [
            "0123456789ABCDEF0123456789ABCDEF01234567",
            "01234567",
            "g123456789abcdef0123456789abcdef01234567",
        ] {
            assert!(matches!(
                BuildIdentity::parse(value),
                BuildIdentity::Diagnostic(_)
            ));
        }
    }
}
