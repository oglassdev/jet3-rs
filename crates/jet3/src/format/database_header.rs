//! Typed access to the supported portions of a Jet 3 database page zero.
//!
//! This view composes the independently documented generic observations:
//! the generic Jet signature at offset `0x4` (`SRC-0004`), the 2 KiB page
//! geometry (`SRC-0005`), and the raw commit slots in `[0x600, 0x800)`
//! (`SRC-0013`). `EXP-0056` additionally supports the narrow, fail-closed
//! opening discriminator implemented here, and `EXP-0299` the raw
//! sort-order marker. No other page-zero byte is interpreted.
use crate::{
    CommitRegion, Error, HeaderError, JetFileKind, PAGE_BYTES, PageNumber,
    format::{
        commit_state::commit_region_from_database_header_page,
        header::classify_database_header_signature,
    },
};

use std::fmt;

const VERSION_OFFSET: usize = 0x14;
const JET3_VERSION_MARKER: u8 = 0x00;
const ENCRYPTION_OFFSET: usize = 0x41;
const UNENCRYPTED_MARKER: u8 = 0x4e;
const PASSWORD_STATE_START: usize = 0x42;
const PASSWORD_STATE_END: usize = 0x50;
const JET3_NO_PASSWORD_STATE: [u8; PASSWORD_STATE_END - PASSWORD_STATE_START] = [
    0x86, 0xfb, 0xec, 0x37, 0x5d, 0x44, 0x9c, 0xfa, 0xc6, 0x5e, 0x28, 0xe6, 0x13, 0xb6,
];

const SORT_ORDER_START: usize = 0x3a;
/// EXP-0299/0309: page-zero bytes `0x3a..0x3e` identify the database sort order.
const GENERAL_SORT_ORDER: [u8; 4] = [0xed, 0xc7, 0x9f, 0x46];

/// Database sort order recorded on page zero (EXP-0299).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum SortOrder {
    /// General (English-US, code page 1252), the default for new databases.
    General,
    /// Nordic (Swedish/Finnish), code page 1252.
    Nordic,
    /// Traditional Spanish, code page 1252.
    Spanish,
    /// Dutch, code page 1252.
    Dutch,
    /// Cyrillic, code page 1251.
    Cyrillic,
    /// Greek, code page 1253.
    Greek,
    /// Any other raw page-zero marker. Reading is supported; writing is refused.
    Other {
        /// Raw page-zero bytes `0x3a..0x3e`.
        raw: [u8; 4],
    },
}

impl SortOrder {
    pub(crate) const fn known() -> [Self; 6] {
        [
            Self::General,
            Self::Nordic,
            Self::Spanish,
            Self::Dutch,
            Self::Cyrillic,
            Self::Greek,
        ]
    }

    /// Returns the code page paired with a recognized native sort order.
    #[must_use]
    pub const fn code_page(self) -> Option<crate::TextCodePage> {
        Some(match self {
            Self::General | Self::Nordic | Self::Spanish | Self::Dutch => {
                crate::TextCodePage::Windows1252
            }
            Self::Cyrillic => crate::TextCodePage::Windows1251,
            Self::Greek => crate::TextCodePage::Windows1253,
            Self::Other { .. } => return None,
        })
    }

    // EXP-0299/0309: LCID followed by code page, both little-endian.
    pub(crate) const fn encoding_context(self) -> Option<[u8; 4]> {
        Some(match self {
            Self::General => [0x09, 0x04, 0xe4, 0x04],
            Self::Nordic => [0x1d, 0x04, 0xe4, 0x04],
            Self::Spanish => [0x0a, 0x04, 0xe4, 0x04],
            Self::Dutch => [0x13, 0x04, 0xe4, 0x04],
            Self::Cyrillic => [0x19, 0x04, 0xe3, 0x04],
            Self::Greek => [0x08, 0x04, 0xe5, 0x04],
            Self::Other { .. } => return None,
        })
    }

    pub(crate) fn from_encoding_context(context: &[u8; 4]) -> Option<Self> {
        Self::known()
            .into_iter()
            .find(|order| order.encoding_context().as_ref() == Some(context))
    }

    pub(crate) const fn raw_marker(self) -> [u8; 4] {
        match self {
            Self::General => GENERAL_SORT_ORDER,
            Self::Nordic => [0xf9, 0xc7, 0x9f, 0x46],
            Self::Spanish => [0xee, 0xc7, 0x9f, 0x46],
            Self::Dutch => [0xf7, 0xc7, 0x9f, 0x46],
            Self::Cyrillic => [0xfd, 0xc7, 0x98, 0x46],
            Self::Greek => [0xec, 0xc7, 0x9e, 0x46],
            Self::Other { raw } => raw,
        }
    }
}

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
    raw: [u8; PAGE_BYTES],
    signature_kind: JetFileKind,
    commit_region: CommitRegion,
}

impl DatabaseHeaderPage {
    /// Builds a typed view over one exact 2 KiB page-zero snapshot.
    ///
    /// Construction recognizes only the documented generic signature. Every
    /// other byte remains uninterpreted except for preserving the documented
    /// raw commit-region snapshot.
    pub fn from_raw_bytes(raw: [u8; PAGE_BYTES]) -> Result<Self, HeaderError> {
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
    pub const fn raw_bytes(&self) -> &[u8; PAGE_BYTES] {
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

    /// Returns the database sort order from its raw page-zero marker.
    #[must_use]
    pub fn sort_order(&self) -> SortOrder {
        let mut raw = [0; 4];
        raw.copy_from_slice(&self.raw[SORT_ORDER_START..SORT_ORDER_START + 4]);
        SortOrder::known()
            .into_iter()
            .find(|order| order.raw_marker() == raw)
            .unwrap_or(SortOrder::Other { raw })
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
