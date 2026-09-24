//! Length limits for one minimum `MSysObjects` catalog row (`EXP-0058`).

use std::fmt;

use crate::Error;

/// `EXP-0058`: the name starts at byte 31.
pub(super) const NAME_START: usize = 31;
/// `EXP-0058`: six-byte reverse trailer: name end, name start 31, fixed
/// boundary 11, marker `0xff`, then two bytes with no established meaning.
const TRAILER_LEN: usize = 6;
/// The one-byte name-end offset bounds the name length.
pub(super) const MAX_NAME_LEN: usize = u8::MAX as usize - NAME_START;

/// Structured failure while encoding a catalog row.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum CatalogRecordWriteError {
    /// An `Unknown` kind aliases a discriminant with a known interpretation.
    NonCanonicalObjectKind {
        /// Rejected physical kind value.
        raw: u16,
    },
    /// A table cannot use database-definition page zero as its TDEF root.
    NullTableDefinition,
    /// The name is empty.
    EmptyName,
    /// The name does not fit the one-byte name-end offset.
    NameTooLong {
        /// Requested length.
        length: usize,
        /// Maximum length.
        maximum: usize,
    },
    /// The output slice cannot hold the complete row.
    OutputTooSmall {
        /// Required length.
        needed: usize,
        /// Provided length.
        available: usize,
    },
    /// Resource policy or checked arithmetic rejected the encoding.
    Resource(Error),
}

impl fmt::Display for CatalogRecordWriteError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "catalog record encoding failed: {self:?}")
    }
}

impl std::error::Error for CatalogRecordWriteError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Resource(source) => Some(source),
            _ => None,
        }
    }
}

/// Returns the exact encoded row length for a name of `name_len` bytes.
pub fn catalog_record_len(name_len: usize) -> Result<usize, CatalogRecordWriteError> {
    if name_len == 0 {
        return Err(CatalogRecordWriteError::EmptyName);
    }
    if name_len > MAX_NAME_LEN {
        return Err(CatalogRecordWriteError::NameTooLong {
            length: name_len,
            maximum: MAX_NAME_LEN,
        });
    }
    Ok(NAME_START + name_len + TRAILER_LEN)
}
