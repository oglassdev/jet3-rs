//! Canonical protocol outcomes for successful reads and rejected openings.

use jet3::{DatabaseFormatError, DatabaseOpenError};

use crate::{Producer, ScenarioId, SemanticProtocolError, SemanticSnapshot, Sha256};

/// Closed cross-producer class for a supported rejected-format opening.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RejectedFormatErrorClass {
    /// The complete header identifies a generation other than Jet 3.
    UnsupportedVersion,
    /// The complete Jet 3 header carries an encrypted marker.
    EncryptedDatabase,
    /// The complete Jet 3 header carries a password-protected state.
    PasswordProtected,
}

impl RejectedFormatErrorClass {
    /// Returns the protocol spelling of this normalized class.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::UnsupportedVersion => "unsupported_version",
            Self::EncryptedDatabase => "encrypted_database",
            Self::PasswordProtected => "password_protected",
        }
    }

    /// Normalizes only the three admitted structured format rejections.
    #[must_use]
    pub const fn from_open_error(error: &DatabaseOpenError) -> Option<Self> {
        match error {
            DatabaseOpenError::Format(DatabaseFormatError::UnsupportedVersion { .. }) => {
                Some(Self::UnsupportedVersion)
            }
            DatabaseOpenError::Format(DatabaseFormatError::EncryptedOrUnsupported { .. }) => {
                Some(Self::EncryptedDatabase)
            }
            DatabaseOpenError::Format(DatabaseFormatError::PasswordedOrUnsupported) => {
                Some(Self::PasswordProtected)
            }
            _ => None,
        }
    }
}

/// Canonical opening-failure artifact with no semantic database payload.
#[derive(Clone, Debug, PartialEq)]
pub struct SemanticOpenFailure {
    /// Closed protocol scenario identifier.
    pub scenario_id: ScenarioId,
    /// Producer identity and exact source revision.
    pub producer: Producer,
    /// SHA-256 of the exact immutable staged database bytes.
    pub database_sha256: Sha256,
    /// Exact normalized rejected-format class.
    pub error_class: RejectedFormatErrorClass,
}

/// Canonical protocol outcome emitted in `snapshot.json`.
#[derive(Clone, Debug, PartialEq)]
pub enum SemanticSnapshotOutcome {
    /// A database opened and semantic traversal completed.
    Success(SemanticSnapshot),
    /// Opening stopped at one admitted rejected-format class.
    OpeningFailure(SemanticOpenFailure),
}

impl SemanticSnapshotOutcome {
    /// Returns compact canonical UTF-8 JSON with one trailing newline.
    pub fn to_canonical_json(&self) -> Result<Vec<u8>, SemanticProtocolError> {
        crate::semantic_json::write_outcome(self)
    }

    pub(crate) fn validate(&self) -> Result<(), SemanticProtocolError> {
        match self {
            Self::Success(snapshot) => snapshot.validate(),
            Self::OpeningFailure(_) => Ok(()),
        }
    }

    pub(crate) fn into_success(self) -> Result<SemanticSnapshot, SemanticProtocolError> {
        match self {
            Self::Success(snapshot) => Ok(snapshot),
            Self::OpeningFailure(_) => Err(SemanticProtocolError::InvalidModel {
                path: "$.outcome".to_owned(),
                reason: "semantic traversal cannot return an opening failure",
            }),
        }
    }
}

/// Outcome-specific evidence carried by a Rust coverage receipt.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum CoverageReceiptOutcome {
    /// Semantic traversal completed and hashed its admitted allocated sets.
    Success {
        /// SHA-256 binding the traversed allocated-page sets.
        allocated_set_sha256: Sha256,
    },
    /// Opening stopped before allocation traversal became applicable.
    OpeningFailure {
        /// Exact normalized rejected-format class.
        error_class: RejectedFormatErrorClass,
    },
}

#[cfg(test)]
mod tests {
    use jet3::{DatabaseFormatError, DatabaseOpenError, JetFileKind};

    use super::RejectedFormatErrorClass;

    #[test]
    fn shared_rejected_format_vectors_match_exact_open_error_variants()
    -> Result<(), Box<dyn std::error::Error>> {
        let fixture = include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../oracle/windows-dao/protocol/v1_2/fixtures/",
            "rejected-format-normalization-vectors.tsv"
        ));
        let errors = [
            DatabaseOpenError::Format(DatabaseFormatError::UnsupportedVersion { observed: 1 }),
            DatabaseOpenError::Format(DatabaseFormatError::EncryptedOrUnsupported { observed: 0 }),
            DatabaseOpenError::Format(DatabaseFormatError::PasswordedOrUnsupported),
        ];
        let mut lines = fixture.lines().filter(|line| !line.starts_with('#'));
        for error in &errors {
            let line = lines.next().ok_or("fixture lacks an expected vector")?;
            let fields = line.split('\t').collect::<Vec<_>>();
            assert_eq!(fields.len(), 4);
            let normalized = RejectedFormatErrorClass::from_open_error(error)
                .ok_or("fixture variant is not admitted")?;
            assert_eq!(normalized.as_str(), fields[3]);
        }
        assert!(lines.next().is_none());

        let unrelated = DatabaseOpenError::SignatureChanged {
            initial: JetFileKind::Standard,
            header: JetFileKind::System,
        };
        assert_eq!(RejectedFormatErrorClass::from_open_error(&unrelated), None);
        Ok(())
    }
}
