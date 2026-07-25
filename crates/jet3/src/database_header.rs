//! Typed access to the documented portions of a Jet 3-sized page zero.
//!
//! This view composes only three independently documented observations:
//! the generic Jet signature at offset `0x4` (`SRC-0004`), the 2 KiB page
//! geometry (`SRC-0005`), and the raw commit slots in `[0x600, 0x800)`
//! (`SRC-0013`). It deliberately assigns no meaning to any other byte and
//! makes no claim about version, encryption, page type, validity, or
//! compatibility.

use std::fmt;

use crate::commit_state::commit_region_from_database_header_page;
use crate::header::{JET3_PAGE_BYTES, classify_database_header_signature};
use crate::{CommitRegion, Error, HeaderError, JetFileKind, PageNumber};

/// Physical page number of the documented database-header page.
///
/// This position alone does not identify a Jet version, encryption state,
/// valid database, or compatible file.
pub const DATABASE_HEADER_PAGE_NUMBER: PageNumber = PageNumber::new(0);

/// A complete 2 KiB page-zero snapshot with only documented fields exposed.
///
/// All bytes are retained exactly. The signature classification is generic
/// Jet identification, while commit-slot contents are volatile and contextual.
/// This type does not identify a Jet version, encryption state, page type, or
/// valid database.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct DatabaseHeaderPage {
    raw: [u8; JET3_PAGE_BYTES],
    signature_kind: JetFileKind,
    commit_region: CommitRegion,
}

impl DatabaseHeaderPage {
    /// Builds a typed view over one exact 2 KiB page-zero snapshot.
    ///
    /// Construction recognizes only the documented generic signature. Every
    /// other byte remains uninterpreted except for preserving the documented
    /// raw commit-region snapshot.
    pub fn from_raw_bytes(raw: [u8; JET3_PAGE_BYTES]) -> Result<Self, HeaderError> {
        let signature_kind = classify_database_header_signature(&raw)?;
        let commit_region = commit_region_from_database_header_page(&raw);
        Ok(Self {
            raw,
            signature_kind,
            commit_region,
        })
    }

    /// Returns all page-zero bytes exactly as supplied.
    #[must_use]
    pub const fn raw_bytes(&self) -> &[u8; JET3_PAGE_BYTES] {
        &self.raw
    }

    /// Returns the documented generic Jet signature classification.
    #[must_use]
    pub const fn signature_kind(&self) -> JetFileKind {
        self.signature_kind
    }

    /// Returns the exact raw commit-region snapshot.
    ///
    /// Its slots remain volatile and contextual and require contemporaneous
    /// `.ldb` locking evidence for meaningful diagnosis.
    #[must_use]
    pub const fn commit_region(&self) -> &CommitRegion {
        &self.commit_region
    }
}

/// A structured failure while reading a typed database-header-page view.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum DatabaseHeaderPageError {
    /// Reading the complete 2 KiB page zero failed.
    Read(Error),
    /// The page did not contain a documented generic Jet signature.
    Signature(HeaderError),
}

impl fmt::Display for DatabaseHeaderPageError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Read(source) => {
                write!(formatter, "failed to read database header page: {source}")
            }
            Self::Signature(source) => {
                write!(formatter, "database header signature failed: {source}")
            }
        }
    }
}

impl std::error::Error for DatabaseHeaderPageError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Read(source) => Some(source),
            Self::Signature(source) => Some(source),
        }
    }
}

#[cfg(test)]
#[path = "database_header_tests.rs"]
mod tests;
