//! Typed access to the supported portions of a Jet 3 database page zero.
//!
//! This view composes the independently documented generic observations:
//! the generic Jet signature at offset `0x4` (`SRC-0004`), the 2 KiB page
//! geometry (`SRC-0005`), and the raw commit slots in `[0x600, 0x800)`
//! (`SRC-0013`). `EXP-0056` additionally supports the narrow, fail-closed
//! opening discriminator implemented here. No other page-zero byte is
//! interpreted.

use std::fmt;

use crate::commit_state::commit_region_from_database_header_page;
use crate::header::{JET3_PAGE_BYTES, classify_database_header_signature};
use crate::{CommitRegion, Error, HeaderError, JetFileKind, PageNumber};

const VERSION_OFFSET: usize = 0x14;
const JET3_VERSION_MARKER: u8 = 0x00;
const ENCRYPTION_OFFSET: usize = 0x41;
const UNENCRYPTED_MARKER: u8 = 0x4e;
const PASSWORD_STATE_START: usize = 0x42;
const PASSWORD_STATE_END: usize = 0x50;
const JET3_NO_PASSWORD_STATE: [u8; PASSWORD_STATE_END - PASSWORD_STATE_START] = [
    0x86, 0xfb, 0xec, 0x37, 0x5d, 0x44, 0x9c, 0xfa, 0xc6, 0x5e, 0x28, 0xe6, 0x13, 0xb6,
];

/// Physical page number of the documented database-header page.
///
/// This position alone does not identify a Jet version, encryption state,
/// valid database, or compatible file.
pub const DATABASE_HEADER_PAGE_NUMBER: PageNumber = PageNumber::new(0);

/// Database generation admitted by the supported opening boundary.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum DatabaseVersion {
    /// Microsoft Jet 3.0/3.5 format.
    Jet3,
}

/// Protection state admitted by the supported opening boundary.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum DatabaseProtection {
    /// The database is unencrypted and has no database password.
    UnencryptedWithoutPassword,
}

/// Narrow format identity established while opening a database.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct SupportedDatabaseFormat {
    version: DatabaseVersion,
    protection: DatabaseProtection,
}

impl SupportedDatabaseFormat {
    /// Returns the admitted database generation.
    #[must_use]
    pub const fn version(self) -> DatabaseVersion {
        self.version
    }

    /// Returns the admitted protection state.
    #[must_use]
    pub const fn protection(self) -> DatabaseProtection {
        self.protection
    }
}

/// A structured rejection of an unsupported page-zero format state.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum DatabaseFormatError {
    /// The observed generation marker is not the Jet 3 marker.
    UnsupportedVersion {
        /// Exact bounded marker observed at the version discriminator.
        observed: u8,
    },
    /// The observed protection marker is not the unencrypted marker.
    EncryptedOrUnsupported {
        /// Exact bounded marker observed at the encryption discriminator.
        observed: u8,
    },
    /// The page does not carry the observed Jet 3 no-password state.
    PasswordedOrUnsupported,
}

impl fmt::Display for DatabaseFormatError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::UnsupportedVersion { observed } => {
                write!(
                    formatter,
                    "unsupported database version marker {observed:#04x}"
                )
            }
            Self::EncryptedOrUnsupported { observed } => write!(
                formatter,
                "encrypted or unsupported database protection marker {observed:#04x}"
            ),
            Self::PasswordedOrUnsupported => {
                formatter.write_str("passworded or unsupported database header state")
            }
        }
    }
}

impl std::error::Error for DatabaseFormatError {}

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

    /// Validates the supported Jet 3, unencrypted, no-password opening state.
    pub fn supported_format(&self) -> Result<SupportedDatabaseFormat, DatabaseFormatError> {
        let version = self.raw[VERSION_OFFSET];
        if version != JET3_VERSION_MARKER {
            return Err(DatabaseFormatError::UnsupportedVersion { observed: version });
        }
        let encryption = self.raw[ENCRYPTION_OFFSET];
        if encryption != UNENCRYPTED_MARKER {
            return Err(DatabaseFormatError::EncryptedOrUnsupported {
                observed: encryption,
            });
        }
        if self.raw[PASSWORD_STATE_START..PASSWORD_STATE_END] != JET3_NO_PASSWORD_STATE {
            return Err(DatabaseFormatError::PasswordedOrUnsupported);
        }
        Ok(SupportedDatabaseFormat {
            version: DatabaseVersion::Jet3,
            protection: DatabaseProtection::UnencryptedWithoutPassword,
        })
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
