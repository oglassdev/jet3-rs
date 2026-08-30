//! Encoders for Jet 3 usage (allocation) maps.
//!
//! These invert the crate's `SRC-0020` / `EXP-0057` decoders: an inline
//! type-0 record, an indirect type-1 reference row, and a complete type-`05`
//! extended bitmap page. Bits are manipulated directly; the meaning of a set
//! bit is the caller's concern (`SRC-0020` table maps: set means allocated;
//! `EXP-0051` global map: set means not in use). Nothing here chooses pages.

use std::fmt;

use crate::allocation::EXTENDED_BITMAP_BITS as CRATE_EXTENDED_BITMAP_BITS;
use crate::{
    BinaryWriter, ByteCount, Error, PageImage, PageKind, PageNumber, PageOffset, ResourceBudget,
};

/// Pages represented by one complete type-`05` bitmap page (`SRC-0020`).
pub const EXTENDED_BITMAP_BITS: u64 = CRATE_EXTENDED_BITMAP_BITS;

// SRC-0020: record type bytes and fixed field lengths.
const INLINE_RECORD_TYPE: u8 = 0x00;
const INDIRECT_RECORD_TYPE: u8 = 0x01;
const INLINE_HEADER_LEN: u64 = 5;
const REFERENCE_LEN: u64 = 4;
// SRC-0020: a Jet 3 type-05 page is `05 01 00 00` followed by 2,044 bitmap
// bytes, low-order bit first.
const EXTENDED_HEADER: [u8; 4] = [0x05, 0x01, 0x00, 0x00];
const EXTENDED_BITMAP_OFFSET: usize = 4;

/// A structured failure while building or encoding a usage map.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum UsageMapWriteError {
    /// The page is not represented by this map's bit range.
    PageOutOfMap {
        /// Rejected page.
        page: PageNumber,
        /// First page represented by bit zero.
        first: PageNumber,
        /// Number of pages the map represents.
        page_count: u64,
    },
    /// A page does not fit a four-byte little-endian field.
    PageNotRepresentable {
        /// Rejected page.
        page: PageNumber,
    },
    /// Checked arithmetic, budget, or output-capacity validation failed.
    Encoding(Error),
}

impl fmt::Display for UsageMapWriteError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::PageOutOfMap {
                page,
                first,
                page_count,
            } => write!(
                formatter,
                "page {} is outside the map covering {} pages from {}",
                page.get(),
                page_count,
                first.get()
            ),
            Self::PageNotRepresentable { page } => write!(
                formatter,
                "page {} does not fit a four-byte reference",
                page.get()
            ),
            Self::Encoding(source) => write!(formatter, "usage-map encoding failed: {source}"),
        }
    }
}

impl std::error::Error for UsageMapWriteError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Encoding(source) => Some(source),
            Self::PageOutOfMap { .. } | Self::PageNotRepresentable { .. } => None,
        }
    }
}

impl From<Error> for UsageMapWriteError {
    fn from(source: Error) -> Self {
        Self::Encoding(source)
    }
}

/// Resolves a map-relative bit into its byte index and low-bit-first mask.
fn bit_location(
    page: PageNumber,
    first: PageNumber,
    page_count: u64,
) -> Result<(usize, u8), UsageMapWriteError> {
    let relative = page
        .get()
        .checked_sub(first.get())
        .filter(|relative| *relative < page_count)
        .ok_or(UsageMapWriteError::PageOutOfMap {
            page,
            first,
            page_count,
        })?;
    let byte = usize::try_from(relative / 8).map_err(|_| Error::IntegerConversion {
        value: u128::from(relative / 8),
        target: "usize",
    })?;
    // SRC-0020: low-order bit first within each bitmap byte.
    let mask = 1_u8 << (relative % 8);
    Ok((byte, mask))
}

/// Builds a type-0 record: tag, four-byte starting page, inline bitmap.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InlineUsageMapEncoder {
    start_page: u32,
    bitmap: Vec<u8>,
}

impl InlineUsageMapEncoder {
    /// Starts an all-clear map of `bitmap_len` bytes beginning at `start_page`.
    ///
    /// The bitmap allocation is charged to `budget` before it is made.
    pub fn new(
        start_page: PageNumber,
        bitmap_len: ByteCount,
        budget: &mut ResourceBudget,
    ) -> Result<Self, UsageMapWriteError> {
        let start = u32::try_from(start_page.get())
            .map_err(|_| UsageMapWriteError::PageNotRepresentable { page: start_page })?;
        budget.charge_allocation(bitmap_len)?;
        let len = bitmap_len.to_usize()?;
        let mut bitmap = Vec::new();
        bitmap.try_reserve_exact(len).map_err(|_| Error::Io {
            operation: "reserve inline usage-map bitmap",
            kind: std::io::ErrorKind::OutOfMemory,
        })?;
        bitmap.resize(len, 0);
        Ok(Self {
            start_page: start,
            bitmap,
        })
    }

    /// Returns the page represented by bit zero.
    #[must_use]
    pub const fn start_page(&self) -> PageNumber {
        PageNumber::new(self.start_page as u64)
    }

    /// Returns the number of pages the bitmap represents.
    #[must_use]
    pub fn page_count(&self) -> u64 {
        (self.bitmap.len() as u64).saturating_mul(8)
    }

    /// Returns the complete encoded record length.
    #[must_use]
    pub fn encoded_len(&self) -> ByteCount {
        ByteCount::new(INLINE_HEADER_LEN.saturating_add(self.bitmap.len() as u64))
    }

    /// Sets the bit for `page`.
    pub fn set_page(&mut self, page: PageNumber) -> Result<(), UsageMapWriteError> {
        self.update(page, true)
    }

    /// Clears the bit for `page`.
    pub fn clear_page(&mut self, page: PageNumber) -> Result<(), UsageMapWriteError> {
        self.update(page, false)
    }

    /// Returns whether the bit for `page` is set.
    pub fn is_set(&self, page: PageNumber) -> Result<bool, UsageMapWriteError> {
        let (byte, mask) = self.locate(page)?;
        Ok(self.bitmap.get(byte).is_some_and(|value| value & mask != 0))
    }

    /// Encodes the record into the front of `output`, returning its length.
    pub fn encode_into(
        &self,
        output: &mut [u8],
        budget: &mut ResourceBudget,
    ) -> Result<ByteCount, UsageMapWriteError> {
        let mut writer = BinaryWriter::new(output, budget)?;
        writer.write_u8(INLINE_RECORD_TYPE)?;
        writer.write_u32_le(self.start_page)?;
        writer.write_exact(&self.bitmap)?;
        Ok(self.encoded_len())
    }

    fn locate(&self, page: PageNumber) -> Result<(usize, u8), UsageMapWriteError> {
        bit_location(page, self.start_page(), self.page_count())
    }

    fn update(&mut self, page: PageNumber, set: bool) -> Result<(), UsageMapWriteError> {
        let (byte, mask) = self.locate(page)?;
        let target = self.bitmap.get_mut(byte).ok_or(Error::Arithmetic {
            operation: "locate inline usage-map bitmap byte",
        })?;
        write_bit(target, mask, set);
        Ok(())
    }
}

/// Builds a complete type-`05` extended bitmap page for one type-1 slot.
///
/// The page represents absolute pages `slot * 16_352 + bit` (`EXP-0057`),
/// regardless of which physical page eventually stores it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExtendedUsageMapEncoder {
    slot: u64,
    first: PageNumber,
    image: PageImage,
}

impl ExtendedUsageMapEncoder {
    /// Starts an all-clear bitmap page for the zero-based type-1 `slot`.
    pub fn new(slot: u64, budget: &mut ResourceBudget) -> Result<Self, UsageMapWriteError> {
        let first = slot
            .checked_mul(EXTENDED_BITMAP_BITS)
            .map(PageNumber::new)
            .ok_or(Error::Arithmetic {
                operation: "extended usage-map slot to first page multiplication",
            })?;
        let mut image = PageImage::new(PageKind::ExtendedUsageBitmap);
        image.write_at(PageOffset::new(0), &EXTENDED_HEADER, budget)?;
        Ok(Self { slot, first, image })
    }

    /// Returns the zero-based type-1 slot ordinal.
    #[must_use]
    pub const fn slot(&self) -> u64 {
        self.slot
    }

    /// Returns the page represented by bit zero.
    #[must_use]
    pub const fn first_page(&self) -> PageNumber {
        self.first
    }

    /// Sets the bit for `page`, charging the one-byte image update to `budget`.
    pub fn set_page(
        &mut self,
        page: PageNumber,
        budget: &mut ResourceBudget,
    ) -> Result<(), UsageMapWriteError> {
        self.update(page, true, budget)
    }

    /// Clears the bit for `page`, charging the one-byte image update to `budget`.
    pub fn clear_page(
        &mut self,
        page: PageNumber,
        budget: &mut ResourceBudget,
    ) -> Result<(), UsageMapWriteError> {
        self.update(page, false, budget)
    }

    /// Returns whether the bit for `page` is set.
    pub fn is_set(&self, page: PageNumber) -> Result<bool, UsageMapWriteError> {
        let index = self.locate(page)?;
        Ok(self
            .image
            .as_bytes()
            .get(index.0)
            .is_some_and(|value| value & index.1 != 0))
    }

    /// Returns the complete page image.
    #[must_use]
    pub const fn image(&self) -> &PageImage {
        &self.image
    }

    /// Consumes the encoder and returns the complete page image.
    #[must_use]
    pub fn into_image(self) -> PageImage {
        self.image
    }

    fn locate(&self, page: PageNumber) -> Result<(usize, u8), UsageMapWriteError> {
        let (byte, mask) = bit_location(page, self.first, EXTENDED_BITMAP_BITS)?;
        let index = byte
            .checked_add(EXTENDED_BITMAP_OFFSET)
            .ok_or(Error::Arithmetic {
                operation: "extended usage-map bitmap byte offset addition",
            })?;
        Ok((index, mask))
    }

    fn update(
        &mut self,
        page: PageNumber,
        set: bool,
        budget: &mut ResourceBudget,
    ) -> Result<(), UsageMapWriteError> {
        let (index, mask) = self.locate(page)?;
        let current = *self.image.as_bytes().get(index).ok_or(Error::Arithmetic {
            operation: "locate extended usage-map bitmap byte",
        })?;
        let mut updated = current;
        write_bit(&mut updated, mask, set);
        self.image
            .write_at(PageOffset::from_usize(index)?, &[updated], budget)?;
        Ok(())
    }
}

/// Returns the encoded length of a type-1 row holding `reference_count` slots.
pub fn indirect_record_len(reference_count: u64) -> Result<ByteCount, Error> {
    reference_count
        .checked_mul(REFERENCE_LEN)
        .and_then(|payload| payload.checked_add(1))
        .map(ByteCount::new)
        .ok_or(Error::Arithmetic {
            operation: "indirect usage-map record length",
        })
}

/// Encodes a type-1 row: tag `01` then each reference as little-endian `u32`.
///
/// A zero reference is an unused slot (`EXP-0057`). Returns the encoded
/// length; the output must hold the complete row.
pub fn encode_indirect_references(
    references: &[PageNumber],
    output: &mut [u8],
    budget: &mut ResourceBudget,
) -> Result<ByteCount, UsageMapWriteError> {
    let mut writer = BinaryWriter::new(output, budget)?;
    writer.write_u8(INDIRECT_RECORD_TYPE)?;
    for &page in references {
        let value = u32::try_from(page.get())
            .map_err(|_| UsageMapWriteError::PageNotRepresentable { page })?;
        writer.write_u32_le(value)?;
    }
    Ok(indirect_record_len(references.len() as u64)?)
}

fn write_bit(target: &mut u8, mask: u8, set: bool) {
    if set {
        *target |= mask;
    } else {
        *target &= !mask;
    }
}

#[cfg(test)]
#[path = "usage_map_writer_tests.rs"]
mod tests;
