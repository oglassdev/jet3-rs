//! Generic Jet file identification from the documented header signature.
//!
//! This module recognizes only the signature literals documented by
//! provenance source `SRC-0004`. Recognition does not establish a Jet version,
//! encryption state, page size, database validity, or application
//! compatibility.

use std::fmt;

use crate::{ByteCount, ByteOffset, Error, PageGeometry, ReadAt, ReadBudget};

const SIGNATURE_OFFSET: ByteOffset = ByteOffset::new(4);
const SIGNATURE_LENGTH: usize = 15;
const STANDARD_SIGNATURE: &[u8; SIGNATURE_LENGTH] = b"Standard Jet DB";
const SYSTEM_SIGNATURE_PREFIX: &[u8] = b"Jet System DB ";
const TEMPORARY_SIGNATURE_PREFIX: &[u8] = b"Temp Jet DB ";

/// The documented 2 KiB page size for Jet 3 databases.
///
/// Provenance source `SRC-0005` documents that Jet 4 changed the earlier
/// 2 KiB page size to 4 KiB. This constant alone does not identify a file's
/// version or validate its contents.
pub const JET3_PAGE_SIZE: ByteCount = ByteCount::new(2_048);

/// The generic Jet file kind named by a documented header signature.
///
/// This classification does not identify a Jet version or establish that the
/// remainder of the input is a valid database.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum JetFileKind {
    /// A file carrying the documented standard-database signature.
    Standard,
    /// A file carrying the documented system-database signature.
    System,
    /// A file carrying the documented temporary-database signature.
    Temporary,
}

/// A failure while reading or recognizing a generic Jet header signature.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum HeaderError {
    /// A bounded random-access read failed.
    Read(Error),
    /// The 15-byte signature window did not contain a documented signature.
    UnknownSignature {
        /// The complete signature window observed at byte offset 4.
        observed: [u8; SIGNATURE_LENGTH],
    },
}

impl fmt::Display for HeaderError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Read(source) => write!(formatter, "failed to read Jet signature: {source}"),
            Self::UnknownSignature { observed } => {
                write!(formatter, "unknown Jet signature bytes: {observed:?}")
            }
        }
    }
}

impl std::error::Error for HeaderError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Read(source) => Some(source),
            Self::UnknownSignature { .. } => None,
        }
    }
}

impl From<Error> for HeaderError {
    fn from(source: Error) -> Self {
        Self::Read(source)
    }
}

/// Reads the documented 15-byte signature window at byte offset 4.
///
/// `SRC-0004` gives 15 bytes for the Standard literal, but its System and
/// Temporary literals are shorter. Their remaining bytes in this 15-byte
/// window are therefore not interpreted: those kinds are recognized from the
/// documented literal prefix. This is generic Jet identification only; it
/// does not identify Jet 3 or any other version, inspect encryption or page
/// size, validate a database, or demonstrate compatibility.
pub fn read_jet_signature(
    source: &mut (impl ReadAt + ?Sized),
    budget: &mut ReadBudget,
) -> Result<JetFileKind, HeaderError> {
    let mut observed = [0_u8; SIGNATURE_LENGTH];
    source.read_exact_at(SIGNATURE_OFFSET, &mut observed, budget)?;

    if observed == *STANDARD_SIGNATURE {
        Ok(JetFileKind::Standard)
    } else if observed.starts_with(SYSTEM_SIGNATURE_PREFIX) {
        Ok(JetFileKind::System)
    } else if observed.starts_with(TEMPORARY_SIGNATURE_PREFIX) {
        Ok(JetFileKind::Temporary)
    } else {
        Err(HeaderError::UnknownSignature { observed })
    }
}

/// Derives 2 KiB page geometry from a source's captured length.
///
/// This helper performs no reads. A length divisible by [`JET3_PAGE_SIZE`]
/// establishes only arithmetic geometry; it does not identify the source as
/// Jet 3 or validate any page or database content.
pub fn jet3_page_geometry<S>(source: &S) -> Result<PageGeometry, Error>
where
    S: ReadAt + ?Sized,
{
    PageGeometry::for_source(source, JET3_PAGE_SIZE)
}

#[cfg(test)]
#[path = "header_tests.rs"]
mod tests;
