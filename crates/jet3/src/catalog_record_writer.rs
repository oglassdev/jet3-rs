//! Checked encoder for one minimum `MSysObjects` catalog row, the inverse of
//! `catalog_record.rs` (`EXP-0058`).

use std::fmt;

use crate::{
    BinaryWriter, ByteCount, CatalogObjectClass, CatalogObjectKind, Error, ResourceBudget,
};

/// `EXP-0058`: every catalog record begins with column count 17.
const CATALOG_COLUMN_COUNT: u8 = 17;
/// `EXP-0058`: identifier at `[1,5)`, kind at `[9,11)`, flags at `[27,31)`.
const OBJECT_ID_OFFSET: usize = 1;
const OBJECT_KIND_OFFSET: usize = 9;
const OBJECT_FLAGS_OFFSET: usize = 27;
/// `EXP-0058`: the name starts at byte 31.
const NAME_START: usize = 31;
/// `EXP-0058`: six-byte reverse trailer: name end, name start 31, fixed
/// boundary 11, marker `0xff`, then two bytes with no established meaning.
const TRAILER_LEN: usize = 6;
const FIXED_BOUNDARY: u8 = 11;
const TRAILER_MARKER: u8 = 0xff;
/// `EXP-0058`: user objects have flags 0, system objects `0x80000000`.
const USER_FLAGS: u32 = 0;
const SYSTEM_FLAGS: u32 = 0x8000_0000;
/// The one-byte name-end offset bounds the name length.
const MAX_NAME_LEN: usize = u8::MAX as usize - NAME_START;

/// One catalog object row to encode.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CatalogRecordSpec<'a> {
    /// Object identifier; for tables, also the table-definition root page.
    pub id: u32,
    /// Object kind.
    pub kind: CatalogObjectKind,
    /// User or system classification.
    pub class: CatalogObjectClass,
    /// Raw database-code-page name bytes.
    pub name: &'a [u8],
}

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

/// Encodes one catalog row into `output`, returning the encoded length.
///
/// Bytes with no `EXP-0058` meaning are written as zero.
pub fn encode_catalog_record(
    spec: &CatalogRecordSpec<'_>,
    output: &mut [u8],
    budget: &mut ResourceBudget,
) -> Result<ByteCount, CatalogRecordWriteError> {
    validate_spec(spec)?;
    let length = catalog_record_len(spec.name.len())?;
    if output.len() < length {
        return Err(CatalogRecordWriteError::OutputTooSmall {
            needed: length,
            available: output.len(),
        });
    }
    let name_end = u8::try_from(NAME_START + spec.name.len()).map_err(|_| {
        CatalogRecordWriteError::NameTooLong {
            length: spec.name.len(),
            maximum: MAX_NAME_LEN,
        }
    })?;
    let flags = match spec.class {
        CatalogObjectClass::User => USER_FLAGS,
        CatalogObjectClass::System => SYSTEM_FLAGS,
    };
    let mut writer =
        BinaryWriter::new(output, budget).map_err(CatalogRecordWriteError::Resource)?;
    write_row(&mut writer, spec, name_end, flags).map_err(CatalogRecordWriteError::Resource)?;
    Ok(ByteCount::new(writer.position().get()))
}

fn validate_spec(spec: &CatalogRecordSpec<'_>) -> Result<(), CatalogRecordWriteError> {
    match spec.kind {
        CatalogObjectKind::Table if spec.id == 0 => {
            Err(CatalogRecordWriteError::NullTableDefinition)
        }
        CatalogObjectKind::Unknown(raw) if raw == CatalogObjectKind::Table.raw() => {
            Err(CatalogRecordWriteError::NonCanonicalObjectKind { raw })
        }
        CatalogObjectKind::Table | CatalogObjectKind::Unknown(_) => Ok(()),
    }
}

fn write_row(
    writer: &mut BinaryWriter<'_, '_>,
    spec: &CatalogRecordSpec<'_>,
    name_end: u8,
    flags: u32,
) -> Result<(), Error> {
    writer.write_u8(CATALOG_COLUMN_COUNT)?;
    writer.write_u32_le(spec.id)?;
    writer.write_exact(&[0; OBJECT_KIND_OFFSET - OBJECT_ID_OFFSET - 4])?;
    writer.write_u16_le(spec.kind.raw())?;
    writer.write_exact(&[0; OBJECT_FLAGS_OFFSET - OBJECT_KIND_OFFSET - 2])?;
    writer.write_u32_le(flags)?;
    writer.write_exact(spec.name)?;
    writer.write_u8(name_end)?;
    writer.write_u8(NAME_START as u8)?;
    writer.write_u8(FIXED_BOUNDARY)?;
    writer.write_u8(TRAILER_MARKER)?;
    writer.write_exact(&[0; 2])
}

#[cfg(test)]
#[path = "catalog_record_writer_tests.rs"]
mod tests;
