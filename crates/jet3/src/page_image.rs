//! Owned, checked builders for complete Jet 3 page images.
//!
//! A [`PageImage`] is a zero-filled 2,048-byte page with its `SRC-0020`
//! byte-zero tag. [`DataPageBuilder`] appends rows to a tag-`01` page using
//! the reverse-packed directory grammar of `SRC-0020` and `EXP-0060`, the
//! exact inverse of the crate's row-directory decoder. Neither type performs
//! I/O or chooses page numbers.

use std::fmt;

use crate::data_page_directory::LONG_VALUE_OWNER;
use crate::{
    BinaryWriter, ByteCount, ByteOffset, Error, JET3_PAGE_SIZE, PageKind, PageNumber, PageOffset,
    ResourceBudget,
};

/// Byte length of one complete Jet 3 page (`SRC-0020`).
pub const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;

// SRC-0020: byte-zero page tags.
const DATABASE_DEFINITION_TAG: u8 = 0x00;
const DATA_TAG: u8 = 0x01;
const TABLE_DEFINITION_TAG: u8 = 0x02;
const INTERMEDIATE_INDEX_TAG: u8 = 0x03;
const LEAF_INDEX_TAG: u8 = 0x04;
const EXTENDED_USAGE_BITMAP_TAG: u8 = 0x05;

// EXP-0060: a data page stores its table-definition root as u32 at [4,8).
const OWNER_OFFSET: u64 = 4;
// SRC-0020: little-endian u16 row count at [8,10), two-byte entries from 10.
const ROW_COUNT_OFFSET: u64 = 8;
const DIRECTORY_OFFSET: usize = 10;
const ENTRY_LEN: usize = 2;
// EXP-0060: the low 13 bits of a directory entry select the row start; the
// remaining bits are flags, which this builder never sets.
const OFFSET_MASK: u16 = 0x1fff;
// Row slots are addressed publicly by `u8` (`RowLocator`), so a builder page
// holds at most 256 rows.
const MAX_BUILT_ROWS: u16 = 256;

/// Returns the `SRC-0020` byte-zero tag for a page classification.
#[must_use]
pub const fn page_tag(kind: PageKind) -> u8 {
    match kind {
        PageKind::DatabaseDefinition => DATABASE_DEFINITION_TAG,
        PageKind::Data => DATA_TAG,
        PageKind::TableDefinition => TABLE_DEFINITION_TAG,
        PageKind::IntermediateIndex => INTERMEDIATE_INDEX_TAG,
        PageKind::LeafIndex => LEAF_INDEX_TAG,
        PageKind::ExtendedUsageBitmap => EXTENDED_USAGE_BITMAP_TAG,
        PageKind::Unknown(tag) => tag,
    }
}

/// An owned, complete Jet 3 page under construction.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PageImage {
    bytes: [u8; PAGE_BYTES],
}

impl PageImage {
    /// Creates a zero-filled page carrying the tag for `kind` at byte zero.
    #[must_use]
    pub const fn new(kind: PageKind) -> Self {
        let mut bytes = [0; PAGE_BYTES];
        bytes[0] = page_tag(kind);
        Self { bytes }
    }

    /// Wraps an existing complete page without inspecting it.
    #[must_use]
    pub const fn from_bytes(bytes: [u8; PAGE_BYTES]) -> Self {
        Self { bytes }
    }

    /// Returns the byte-zero tag.
    #[must_use]
    pub const fn tag(&self) -> u8 {
        self.bytes[0]
    }

    /// Writes `bytes` at a page-local offset after bounds and budget checks.
    ///
    /// The write is all-or-nothing: no byte changes when the range or the
    /// encoded-byte budget is rejected.
    pub fn write_at(
        &mut self,
        offset: PageOffset,
        bytes: &[u8],
        budget: &mut ResourceBudget,
    ) -> Result<(), Error> {
        let mut writer = BinaryWriter::new(&mut self.bytes, budget)?;
        writer.seek(ByteOffset::new(offset.get()))?;
        writer.write_exact(bytes)
    }

    /// Returns the complete page bytes.
    #[must_use]
    pub const fn as_bytes(&self) -> &[u8; PAGE_BYTES] {
        &self.bytes
    }

    /// Consumes the builder and returns the complete page bytes.
    #[must_use]
    pub const fn into_bytes(self) -> [u8; PAGE_BYTES] {
        self.bytes
    }
}

/// A structured failure while appending rows to a data page image.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum PageImageError {
    /// The owner page does not fit the four-byte owner field.
    OwnerNotRepresentable {
        /// Rejected table-definition root.
        owner: PageNumber,
    },
    /// A zero-length primary row cannot be distinguished from a deleted slot.
    EmptyRow,
    /// The row plus its directory entry do not fit the remaining free space.
    PageFull {
        /// Bytes needed for the row and its directory entry.
        needed: ByteCount,
        /// Free bytes between the directory end and the lowest row start.
        available: ByteCount,
    },
    /// All addressable row slots are in use.
    RowSlotsExhausted {
        /// Maximum rows one built page may hold.
        maximum: u16,
    },
    /// Checked encoding into the page image failed.
    Encoding(Error),
}

impl fmt::Display for PageImageError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::OwnerNotRepresentable { owner } => write!(
                formatter,
                "data-page owner {} does not fit a four-byte field",
                owner.get()
            ),
            Self::EmptyRow => write!(formatter, "data-page rows must not be empty"),
            Self::PageFull { needed, available } => write!(
                formatter,
                "data page is full: {} bytes needed, {} available",
                needed.get(),
                available.get()
            ),
            Self::RowSlotsExhausted { maximum } => {
                write!(formatter, "data page already holds {maximum} rows")
            }
            Self::Encoding(source) => write!(formatter, "data-page encoding failed: {source}"),
        }
    }
}

impl std::error::Error for PageImageError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Encoding(source) => Some(source),
            Self::OwnerNotRepresentable { .. }
            | Self::EmptyRow
            | Self::PageFull { .. }
            | Self::RowSlotsExhausted { .. } => None,
        }
    }
}

/// Appends primary rows to a tag-`01` data page image.
///
/// Rows are packed downward from the page end; directory entries grow upward
/// from byte 10, so slot `n` is the `n`th row appended. No flag bits are ever
/// written. Header bytes without a provenance-established meaning stay zero.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DataPageBuilder {
    image: PageImage,
    row_count: u16,
    free_end: usize,
}

impl DataPageBuilder {
    /// Starts an empty data page owned by the table-definition root `owner`.
    pub fn new(owner: PageNumber, budget: &mut ResourceBudget) -> Result<Self, PageImageError> {
        let owner_value = u32::try_from(owner.get())
            .map_err(|_| PageImageError::OwnerNotRepresentable { owner })?;
        let mut image = PageImage::new(PageKind::Data);
        image
            .write_at(
                PageOffset::new(OWNER_OFFSET),
                &owner_value.to_le_bytes(),
                budget,
            )
            .map_err(PageImageError::Encoding)?;
        Ok(Self {
            image,
            row_count: 0,
            free_end: PAGE_BYTES,
        })
    }

    /// Starts an empty long-value page, owned by the ASCII `LVAL` marker
    /// `EXP-0061` observed on every external payload page.
    pub fn new_long_value(budget: &mut ResourceBudget) -> Result<Self, PageImageError> {
        let mut image = PageImage::new(PageKind::Data);
        image
            .write_at(PageOffset::new(OWNER_OFFSET), &LONG_VALUE_OWNER, budget)
            .map_err(PageImageError::Encoding)?;
        Ok(Self {
            image,
            row_count: 0,
            free_end: PAGE_BYTES,
        })
    }

    /// Returns the number of rows appended so far.
    #[must_use]
    pub const fn row_count(&self) -> u16 {
        self.row_count
    }

    /// Returns the bytes available for one more row and its directory entry.
    #[must_use]
    pub fn free_bytes(&self) -> ByteCount {
        let directory_end = DIRECTORY_OFFSET + ENTRY_LEN * usize::from(self.row_count);
        ByteCount::new(self.free_end.saturating_sub(directory_end) as u64)
    }

    /// Appends one primary row and returns its zero-based slot.
    ///
    /// Nothing changes when the row does not fit or the slot space is
    /// exhausted. A budget rejection may leave row bytes in free space, but
    /// the declared row count never advances on failure.
    pub fn append_row(
        &mut self,
        row: &[u8],
        budget: &mut ResourceBudget,
    ) -> Result<u8, PageImageError> {
        if row.is_empty() {
            return Err(PageImageError::EmptyRow);
        }
        if self.row_count >= MAX_BUILT_ROWS {
            return Err(PageImageError::RowSlotsExhausted {
                maximum: MAX_BUILT_ROWS,
            });
        }
        let slot = u8::try_from(self.row_count).map_err(|_| PageImageError::RowSlotsExhausted {
            maximum: MAX_BUILT_ROWS,
        })?;
        let needed = ByteCount::from_usize(row.len().saturating_add(ENTRY_LEN))
            .map_err(PageImageError::Encoding)?;
        let available = self.free_bytes();
        if needed.get() > available.get() {
            return Err(PageImageError::PageFull { needed, available });
        }
        let start = self.free_end - row.len();
        let entry_offset = DIRECTORY_OFFSET + ENTRY_LEN * usize::from(self.row_count);
        let raw_offset = u16::try_from(start)
            .ok()
            .filter(|value| value & !OFFSET_MASK == 0)
            .ok_or(PageImageError::Encoding(Error::Arithmetic {
                operation: "encode data-page row offset",
            }))?;

        // The row count is written last, so a budget rejection part-way
        // leaves the declared directory unchanged and the page decodable.
        let mut writer =
            BinaryWriter::new(&mut self.image.bytes, budget).map_err(PageImageError::Encoding)?;
        let write = |writer: &mut BinaryWriter<'_, '_>| -> Result<(), Error> {
            writer.seek(ByteOffset::new(start as u64))?;
            writer.write_exact(row)?;
            writer.seek(ByteOffset::new(entry_offset as u64))?;
            writer.write_u16_le(raw_offset)?;
            writer.seek(ByteOffset::new(ROW_COUNT_OFFSET))?;
            writer.write_u16_le(self.row_count + 1)
        };
        write(&mut writer).map_err(PageImageError::Encoding)?;
        self.row_count += 1;
        self.free_end = start;
        Ok(slot)
    }

    /// Returns the page built so far.
    #[must_use]
    pub const fn image(&self) -> &PageImage {
        &self.image
    }

    /// Consumes the builder and returns the complete page image.
    #[must_use]
    pub fn finish(self) -> PageImage {
        self.image
    }
}

#[cfg(test)]
#[path = "page_image_tests.rs"]
mod tests;
