//! Minimum Jet 3 catalog-page and object-record decoding from `EXP-0058`.
//!
//! Catalog data pages compose the common Jet 3 row-directory grammar from
//! `SRC-0020` through the shared bounded directory decoder.

use std::fmt;

use crate::data_page_directory::{DataPageDirectory, DataPageDirectoryError, PAGE_BYTES};
use crate::{ByteCount, Error, PageNumber, ResourceBudget};

// EXP-0058: minimum catalog record fields and reverse trailer entries.
const CATALOG_COLUMN_COUNT: u8 = 17;
const OBJECT_ID_OFFSET: usize = 1;
const OBJECT_KIND_OFFSET: usize = 9;
const OBJECT_FLAGS_OFFSET: usize = 27;
const NAME_START: usize = 31;
const MINIMUM_RECORD_LEN: usize = NAME_START + 6;
const NAME_END_FROM_END: usize = 6;
const NAME_START_FROM_END: usize = 5;
const FIXED_BOUNDARY_FROM_END: usize = 4;
const TRAILER_MARKER_FROM_END: usize = 3;
const FIXED_BOUNDARY: u8 = 11;
const TRAILER_MARKER: u8 = 0xff;
const TABLE_KIND: u16 = 1;
const USER_FLAGS: u32 = 0;
const SYSTEM_FLAGS: u32 = 0x8000_0000;

/// Stable identifier stored by one catalog object record.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct CatalogObjectId(u32);

impl CatalogObjectId {
    /// Returns the lossless raw identifier.
    #[must_use]
    pub const fn get(self) -> u32 {
        self.0
    }
}

/// Object kind meanings established for the minimum catalog slice.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum CatalogObjectKind {
    /// A table whose identifier is also its table-definition root.
    Table,
    /// A sourced kind not interpreted by this slice.
    Unknown(u16),
}

impl CatalogObjectKind {
    /// Returns the lossless physical kind value.
    #[must_use]
    pub const fn raw(self) -> u16 {
        match self {
            Self::Table => TABLE_KIND,
            Self::Unknown(raw) => raw,
        }
    }
}

/// Whether the observed object flags classify a user or system object.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum CatalogObjectClass {
    /// Exact raw flags zero.
    User,
    /// Exact raw flags `0x80000000`.
    System,
}

/// Context required to interpret catalog-name bytes losslessly later.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum CatalogNameEncoding {
    /// Bytes use the database's ANSI code-page context.
    DatabaseCodePage,
}

/// Owned raw catalog name plus its sourced encoding context.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CatalogName {
    raw: Vec<u8>,
    encoding: CatalogNameEncoding,
}

impl CatalogName {
    /// Returns the name bytes exactly as stored.
    #[must_use]
    pub fn raw_bytes(&self) -> &[u8] {
        &self.raw
    }

    /// Returns the physical encoding context without guessing a code page.
    #[must_use]
    pub const fn encoding(&self) -> CatalogNameEncoding {
        self.encoding
    }

    /// Borrows an ASCII decoded representation when every byte is ASCII.
    #[must_use]
    pub fn decoded_ascii(&self) -> Option<&str> {
        self.raw
            .is_ascii()
            .then(|| std::str::from_utf8(&self.raw).ok())
            .flatten()
    }
}

/// One immutable minimum catalog object record.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CatalogRecord {
    id: CatalogObjectId,
    kind: CatalogObjectKind,
    class: CatalogObjectClass,
    raw_flags: u32,
    name: CatalogName,
    table_definition: Option<PageNumber>,
}

impl CatalogRecord {
    /// Returns the catalog identifier.
    #[must_use]
    pub const fn id(&self) -> CatalogObjectId {
        self.id
    }

    /// Returns the interpreted kind while retaining unknown raw values.
    #[must_use]
    pub const fn kind(&self) -> CatalogObjectKind {
        self.kind
    }

    /// Returns whether exact sourced flags classify the object as user/system.
    #[must_use]
    pub const fn class(&self) -> CatalogObjectClass {
        self.class
    }

    /// Returns the lossless raw object flags.
    #[must_use]
    pub const fn raw_flags(&self) -> u32 {
        self.raw_flags
    }

    /// Returns the lossless owned name.
    #[must_use]
    pub const fn name(&self) -> &CatalogName {
        &self.name
    }

    /// Returns the typed table-definition root for observed table records.
    #[must_use]
    pub const fn table_definition(&self) -> Option<PageNumber> {
        self.table_definition
    }
}

/// One active row borrowed from a validated catalog data page.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct CatalogRecordView<'row> {
    id: CatalogObjectId,
    kind: CatalogObjectKind,
    class: CatalogObjectClass,
    raw_flags: u32,
    raw_name: &'row [u8],
}

impl CatalogRecordView<'_> {
    pub(crate) const fn id(self) -> CatalogObjectId {
        self.id
    }

    pub(crate) const fn kind(self) -> CatalogObjectKind {
        self.kind
    }

    pub(crate) const fn class(self) -> CatalogObjectClass {
        self.class
    }
}

impl<'row> CatalogRecordView<'row> {
    pub(crate) const fn name_bytes(self) -> &'row [u8] {
        self.raw_name
    }

    pub(crate) fn into_owned(
        self,
        table_definition: Option<PageNumber>,
        budget: &mut ResourceBudget,
    ) -> Result<CatalogRecord, Error> {
        let length = ByteCount::from_usize(self.raw_name.len())?;
        budget.charge_allocation(length)?;
        let mut raw = Vec::new();
        raw.try_reserve_exact(self.raw_name.len())
            .map_err(|_| Error::Io {
                operation: "reserve raw catalog name",
                kind: std::io::ErrorKind::OutOfMemory,
            })?;
        raw.extend_from_slice(self.raw_name);
        Ok(CatalogRecord {
            id: self.id,
            kind: self.kind,
            class: self.class,
            raw_flags: self.raw_flags,
            name: CatalogName {
                raw,
                encoding: CatalogNameEncoding::DatabaseCodePage,
            },
            table_definition,
        })
    }
}

/// Structured catalog directory or record corruption.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum CatalogRecordError {
    /// A row count cannot fit a complete directory.
    RowCountTooLarge {
        /// Declared row count.
        row_count: u16,
        /// Maximum count that can fit in the page directory.
        maximum: usize,
    },
    /// A directory entry contains an unobserved flag bit.
    UnknownDirectoryFlag {
        /// Zero-based directory entry.
        row: u16,
        /// Sourced offset and flag word.
        raw_offset: u16,
    },
    /// A masked row offset is outside the page.
    RowOffsetOutOfPage {
        /// Zero-based directory entry.
        row: u16,
        /// Sourced offset and flag word.
        raw_offset: u16,
    },
    /// Row bounds overlap the directory or reverse incorrectly.
    InvalidRowBounds {
        /// Zero-based directory entry.
        row: u16,
        /// Inclusive row start.
        start: usize,
        /// Exclusive row end.
        end: usize,
        /// First byte occupied by the reverse directory.
        directory_end: usize,
    },
    /// An active catalog row has the overflow flag.
    ActiveOverflowRow {
        /// Zero-based directory entry.
        row: u16,
    },
    /// A catalog record is shorter than its minimum fields and trailer.
    RecordTooShort {
        /// Observed record length.
        length: usize,
        /// Minimum required length.
        minimum: usize,
    },
    /// A selected catalog row has an unexpected column count.
    UnexpectedColumnCount {
        /// Sourced catalog column count.
        observed: u8,
    },
    /// The minimum reverse trailer does not match the observed layout.
    InvalidNameTrailer {
        /// Decoded inclusive name start.
        name_start: usize,
        /// Decoded exclusive name end.
        name_end: usize,
        /// Sourced fixed-field boundary marker.
        fixed_boundary: u8,
        /// Sourced trailer marker.
        marker: u8,
        /// Complete record length.
        record_length: usize,
    },
    /// Object flags are outside the two observed exact classifications.
    UnsupportedObjectFlags {
        /// Sourced object flags.
        raw: u32,
    },
    /// Resource policy rejected directory or record work.
    Resource(Error),
}

impl fmt::Display for CatalogRecordError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::RowCountTooLarge { row_count, maximum } => {
                write!(
                    formatter,
                    "catalog page declares {row_count} rows; at most {maximum} fit"
                )
            }
            Self::UnknownDirectoryFlag { row, raw_offset } => write!(
                formatter,
                "catalog row {row} has unknown directory flags in 0x{raw_offset:04x}"
            ),
            Self::RowOffsetOutOfPage { row, raw_offset } => write!(
                formatter,
                "catalog row {row} has out-of-page offset 0x{raw_offset:04x}"
            ),
            Self::InvalidRowBounds {
                row,
                start,
                end,
                directory_end,
            } => write!(
                formatter,
                "catalog row {row} has invalid bounds [{start}, {end}) with directory ending at {directory_end}"
            ),
            Self::ActiveOverflowRow { row } => {
                write!(formatter, "active catalog row {row} uses the overflow flag")
            }
            Self::RecordTooShort { length, minimum } => write!(
                formatter,
                "catalog record length {length} is below minimum {minimum}"
            ),
            Self::UnexpectedColumnCount { observed } => {
                write!(formatter, "catalog record declares {observed} columns")
            }
            Self::InvalidNameTrailer {
                name_start,
                name_end,
                fixed_boundary,
                marker,
                record_length,
            } => write!(
                formatter,
                "catalog name trailer has range [{name_start}, {name_end}), fixed boundary {fixed_boundary}, marker 0x{marker:02x}, and record length {record_length}"
            ),
            Self::UnsupportedObjectFlags { raw } => {
                write!(
                    formatter,
                    "catalog object flags 0x{raw:08x} are unsupported"
                )
            }
            Self::Resource(source) => write!(formatter, "catalog record rejected: {source}"),
        }
    }
}

impl std::error::Error for CatalogRecordError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Resource(source) => Some(source),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct CatalogPageDirectory {
    inner: DataPageDirectory,
}

impl CatalogPageDirectory {
    pub(crate) fn validate(
        page: &[u8; PAGE_BYTES],
        budget: &mut ResourceBudget,
    ) -> Result<Self, CatalogRecordError> {
        let inner = DataPageDirectory::validate(page, budget).map_err(map_directory_error)?;
        let mut validation = inner.clone();
        while let Some(entry) = validation.next_entry(page) {
            if !entry.hidden() && entry.overflow() {
                return Err(CatalogRecordError::ActiveOverflowRow { row: entry.row() });
            }
        }
        Ok(Self { inner })
    }

    pub(crate) fn next_active<'page>(
        &mut self,
        page: &'page [u8; PAGE_BYTES],
    ) -> Result<Option<&'page [u8]>, CatalogRecordError> {
        while let Some(entry) = self.inner.next_entry(page) {
            if entry.hidden() {
                continue;
            }
            return Ok(Some(&page[entry.range()]));
        }
        Ok(None)
    }
}

pub(crate) fn decode_catalog_record<'row>(
    row: &'row [u8],
    budget: &mut ResourceBudget,
) -> Result<CatalogRecordView<'row>, CatalogRecordError> {
    budget
        .charge_work_units(1)
        .map_err(CatalogRecordError::Resource)?;
    if row.len() < MINIMUM_RECORD_LEN {
        return Err(CatalogRecordError::RecordTooShort {
            length: row.len(),
            minimum: MINIMUM_RECORD_LEN,
        });
    }
    if row[0] != CATALOG_COLUMN_COUNT {
        return Err(CatalogRecordError::UnexpectedColumnCount { observed: row[0] });
    }
    let name_end = usize::from(row[row.len() - NAME_END_FROM_END]);
    let name_start = usize::from(row[row.len() - NAME_START_FROM_END]);
    let fixed_boundary = row[row.len() - FIXED_BOUNDARY_FROM_END];
    let marker = row[row.len() - TRAILER_MARKER_FROM_END];
    if name_start != NAME_START
        || name_end <= name_start
        || name_end > row.len() - NAME_END_FROM_END
        || fixed_boundary != FIXED_BOUNDARY
        || marker != TRAILER_MARKER
    {
        return Err(CatalogRecordError::InvalidNameTrailer {
            name_start,
            name_end,
            fixed_boundary,
            marker,
            record_length: row.len(),
        });
    }
    let id = CatalogObjectId(u32::from_le_bytes(
        row[OBJECT_ID_OFFSET..OBJECT_ID_OFFSET + 4]
            .try_into()
            .map_err(|_| CatalogRecordError::RecordTooShort {
                length: row.len(),
                minimum: MINIMUM_RECORD_LEN,
            })?,
    ));
    let raw_kind = u16::from_le_bytes(
        row[OBJECT_KIND_OFFSET..OBJECT_KIND_OFFSET + 2]
            .try_into()
            .map_err(|_| CatalogRecordError::RecordTooShort {
                length: row.len(),
                minimum: MINIMUM_RECORD_LEN,
            })?,
    );
    let kind = if raw_kind == TABLE_KIND {
        CatalogObjectKind::Table
    } else {
        CatalogObjectKind::Unknown(raw_kind)
    };
    let raw_flags = u32::from_le_bytes(
        row[OBJECT_FLAGS_OFFSET..OBJECT_FLAGS_OFFSET + 4]
            .try_into()
            .map_err(|_| CatalogRecordError::RecordTooShort {
                length: row.len(),
                minimum: MINIMUM_RECORD_LEN,
            })?,
    );
    let class = match raw_flags {
        USER_FLAGS => CatalogObjectClass::User,
        SYSTEM_FLAGS => CatalogObjectClass::System,
        raw => return Err(CatalogRecordError::UnsupportedObjectFlags { raw }),
    };
    Ok(CatalogRecordView {
        id,
        kind,
        class,
        raw_flags,
        raw_name: &row[name_start..name_end],
    })
}

fn map_directory_error(error: DataPageDirectoryError) -> CatalogRecordError {
    match error {
        DataPageDirectoryError::RowCountTooLarge { row_count, maximum } => {
            CatalogRecordError::RowCountTooLarge { row_count, maximum }
        }
        DataPageDirectoryError::UnknownFlag { row, raw_offset } => {
            CatalogRecordError::UnknownDirectoryFlag { row, raw_offset }
        }
        DataPageDirectoryError::OffsetOutOfPage { row, raw_offset } => {
            CatalogRecordError::RowOffsetOutOfPage { row, raw_offset }
        }
        DataPageDirectoryError::InvalidBounds {
            row,
            start,
            end,
            directory_end,
        } => CatalogRecordError::InvalidRowBounds {
            row,
            start,
            end,
            directory_end,
        },
        DataPageDirectoryError::Resource(source) => CatalogRecordError::Resource(source),
    }
}

#[cfg(test)]
#[path = "catalog_record_tests.rs"]
mod tests;
