//! Detached, allocation-free decoders for documented Jet 3 allocation maps.
//!
//! These primitives decode only the record and page layouts documented by
//! `SRC-0020`: caller-delimited records and already classified extended bitmap
//! pages. They do not locate records, follow map page references, or infer the
//! database page represented by an extended bitmap bit.

use crate::{ClassifiedPage, Error, PageGeometry, PageKind, PageNumber, ResourceBudget};
use std::fmt;

const INLINE_RECORD_TYPE: u8 = 0x00;
const INDIRECT_RECORD_TYPE: u8 = 0x01;
const INLINE_HEADER_LEN: usize = 5;
const INDIRECT_REFERENCE_LEN: usize = 4;
const EXTENDED_BITMAP_OFFSET: usize = 4;

/// A decoded, caller-delimited allocation-map record.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[non_exhaustive]
pub enum AllocationMap<'a> {
    /// A starting page followed by an inline least-significant-bit-first
    /// bitmap.
    Inline(InlineAllocationMap<'a>),
    /// A sequence of raw map-page references.
    Indirect(IndirectAllocationMap<'a>),
}

/// A type-0 allocation map that borrows its inline bitmap.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct InlineAllocationMap<'a> {
    start_page: PageNumber,
    bitmap: &'a [u8],
}

impl<'a> InlineAllocationMap<'a> {
    /// Returns the first page represented by bitmap bit zero.
    #[must_use]
    pub const fn start_page(&self) -> PageNumber {
        self.start_page
    }

    /// Iterates the set bits as geometry-validated page numbers.
    ///
    /// Every inspected bit charges one item, which also charges one aggregate
    /// work unit. An unset bit is charged even though it produces no item.
    pub fn allocated_pages(&self, geometry: PageGeometry) -> InlineAllocatedPages<'a> {
        InlineAllocatedPages {
            start_page: self.start_page,
            bits: SetBitIndices::new(self.bitmap),
            geometry,
            done: false,
        }
    }
}

/// A type-1 allocation map that borrows its raw little-endian references.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct IndirectAllocationMap<'a> {
    references: &'a [u8],
}

impl<'a> IndirectAllocationMap<'a> {
    /// Returns the number of complete raw references in this record.
    #[must_use]
    pub const fn reference_count(&self) -> usize {
        self.references.len() / INDIRECT_REFERENCE_LEN
    }

    /// Iterates raw little-endian map-page references without interpreting or
    /// following them.
    ///
    /// Every inspected reference charges one item, which also charges one
    /// aggregate work unit. In particular, zero remains a yielded raw value.
    pub fn map_page_references(&self) -> MapPageReferences<'a> {
        MapPageReferences {
            references: self.references,
            offset: 0,
        }
    }
}

/// A structured failure while decoding or iterating a detached allocation map.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum AllocationMapError {
    /// The caller-delimited record has no type byte.
    EmptyRecord,
    /// A type-0 record does not contain its complete five-byte header.
    InlineRecordTooShort {
        /// Actual caller-delimited record length.
        actual_len: usize,
    },
    /// Bytes after a type-1 record tag do not form complete four-byte entries.
    IndirectPayloadMisaligned {
        /// Number of bytes after the type byte.
        payload_len: usize,
    },
    /// The record type is outside the documented detached forms.
    UnsupportedRecordType {
        /// Unrecognized byte-zero record type.
        record_type: u8,
    },
    /// The supplied classified page is not an extended usage bitmap page.
    ExpectedExtendedUsageBitmap {
        /// Physical page number supplied by the classifier.
        page: PageNumber,
        /// Actual lossless page classification.
        actual: PageKind,
    },
    /// Adding an inline bit index to its starting page overflowed.
    PageNumberOverflow {
        /// First page represented by bit zero.
        start_page: u64,
        /// Set bit whose page number could not be represented.
        bit_index: u64,
    },
    /// Checked cursor arithmetic could not represent the next bit position.
    Arithmetic(Error),
    /// A set inline bit refers outside the caller-supplied page geometry.
    PageReference(Error),
    /// Resource policy rejected inspection of a bit or reference.
    Resource(Error),
}

impl fmt::Display for AllocationMapError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::EmptyRecord => write!(formatter, "allocation-map record is empty"),
            Self::InlineRecordTooShort { actual_len } => write!(
                formatter,
                "inline allocation-map record is {actual_len} bytes; at least {INLINE_HEADER_LEN} are required"
            ),
            Self::IndirectPayloadMisaligned { payload_len } => write!(
                formatter,
                "indirect allocation-map payload of {payload_len} bytes is not divisible by {INDIRECT_REFERENCE_LEN}"
            ),
            Self::UnsupportedRecordType { record_type } => write!(
                formatter,
                "unsupported allocation-map record type 0x{record_type:02x}"
            ),
            Self::ExpectedExtendedUsageBitmap { page, actual } => write!(
                formatter,
                "expected page {} to be an extended usage bitmap, found {actual:?}",
                page.get()
            ),
            Self::PageNumberOverflow {
                start_page,
                bit_index,
            } => write!(
                formatter,
                "inline allocation page overflow for start page {start_page} and bit {bit_index}"
            ),
            Self::Arithmetic(source) => {
                write!(
                    formatter,
                    "allocation-map cursor arithmetic failed: {source}"
                )
            }
            Self::PageReference(source) => {
                write!(formatter, "inline allocation page is invalid: {source}")
            }
            Self::Resource(source) => {
                write!(formatter, "allocation-map inspection rejected: {source}")
            }
        }
    }
}

impl std::error::Error for AllocationMapError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Arithmetic(source) | Self::PageReference(source) | Self::Resource(source) => {
                Some(source)
            }
            Self::EmptyRecord
            | Self::InlineRecordTooShort { .. }
            | Self::IndirectPayloadMisaligned { .. }
            | Self::UnsupportedRecordType { .. }
            | Self::ExpectedExtendedUsageBitmap { .. }
            | Self::PageNumberOverflow { .. } => None,
        }
    }
}

/// Decodes one complete, caller-delimited allocation-map record.
///
/// The function borrows the payload and allocates nothing. It charges exactly
/// one explicit work unit before inspecting the record. A type-0 record is the
/// tag, a four-byte little-endian starting page, and all remaining bytes as its
/// bitmap. A type-1 record is the tag followed by zero or more complete
/// four-byte little-endian raw references.
pub fn decode_allocation_map<'record>(
    record: &'record [u8],
    budget: &mut ResourceBudget,
) -> Result<AllocationMap<'record>, AllocationMapError> {
    budget
        .charge_work_units(1)
        .map_err(AllocationMapError::Resource)?;
    let Some((&record_type, payload)) = record.split_first() else {
        return Err(AllocationMapError::EmptyRecord);
    };

    match record_type {
        INLINE_RECORD_TYPE => {
            if record.len() < INLINE_HEADER_LEN {
                return Err(AllocationMapError::InlineRecordTooShort {
                    actual_len: record.len(),
                });
            }
            let start_page = u32::from_le_bytes([payload[0], payload[1], payload[2], payload[3]]);
            Ok(AllocationMap::Inline(InlineAllocationMap {
                start_page: PageNumber::new(u64::from(start_page)),
                bitmap: &payload[INDIRECT_REFERENCE_LEN..],
            }))
        }
        INDIRECT_RECORD_TYPE => {
            if payload.len() % INDIRECT_REFERENCE_LEN != 0 {
                return Err(AllocationMapError::IndirectPayloadMisaligned {
                    payload_len: payload.len(),
                });
            }
            Ok(AllocationMap::Indirect(IndirectAllocationMap {
                references: payload,
            }))
        }
        _ => Err(AllocationMapError::UnsupportedRecordType { record_type }),
    }
}

/// Iterates set bits in the bitmap portion of a classified `0x05` page.
///
/// The four-byte page header is deliberately ignored after classification.
/// Returned values are bitmap-relative bit indices, not database page
/// numbers. Every inspected bit charges one item and one aggregate work unit.
pub fn extended_allocation_bits<'page>(
    page: ClassifiedPage<'page>,
) -> Result<ExtendedAllocationBits<'page>, AllocationMapError> {
    if page.kind() != PageKind::ExtendedUsageBitmap {
        return Err(AllocationMapError::ExpectedExtendedUsageBitmap {
            page: page.number(),
            actual: page.kind(),
        });
    }

    Ok(ExtendedAllocationBits {
        bits: SetBitIndices::new(&page.raw_bytes()[EXTENDED_BITMAP_OFFSET..]),
    })
}

/// Allocation-free cursor over set pages in a type-0 inline bitmap.
#[derive(Debug)]
pub struct InlineAllocatedPages<'map> {
    start_page: PageNumber,
    bits: SetBitIndices<'map>,
    geometry: PageGeometry,
    done: bool,
}

impl InlineAllocatedPages<'_> {
    /// Returns the next set page, charging all bits inspected to find it.
    ///
    /// A resource rejection leaves the rejected bit pending so a caller can
    /// retry without skipping input. Geometry and arithmetic failures exhaust
    /// the cursor. Passing the budget to each call leaves it available for
    /// operations on a yielded page before scanning resumes.
    pub fn next_page(
        &mut self,
        budget: &mut ResourceBudget,
    ) -> Result<Option<PageNumber>, AllocationMapError> {
        if self.done {
            return Ok(None);
        }

        let bit_index = match self.bits.next_set_bit(budget) {
            Ok(Some(bit_index)) => bit_index,
            Ok(None) => return Ok(None),
            Err(source) => return Err(source.into()),
        };
        let Some(page) = self.start_page.get().checked_add(bit_index) else {
            self.done = true;
            return Err(AllocationMapError::PageNumberOverflow {
                start_page: self.start_page.get(),
                bit_index,
            });
        };
        let page = PageNumber::new(page);
        if let Err(source) = self.geometry.validate_reference(page) {
            self.done = true;
            return Err(AllocationMapError::PageReference(source));
        }
        Ok(Some(page))
    }
}

/// Allocation-free cursor over raw type-1 map-page references.
#[derive(Debug)]
pub struct MapPageReferences<'map> {
    references: &'map [u8],
    offset: usize,
}

impl MapPageReferences<'_> {
    /// Returns the next raw reference without following or interpreting it.
    ///
    /// A resource rejection leaves the pending reference unadvanced so the
    /// caller can retry with another operation budget.
    pub fn next_reference(
        &mut self,
        budget: &mut ResourceBudget,
    ) -> Result<Option<u32>, AllocationMapError> {
        if self.offset == self.references.len() {
            return Ok(None);
        }
        if let Err(source) = budget.charge_items(1) {
            return Err(AllocationMapError::Resource(source));
        }

        let end = self.offset.checked_add(INDIRECT_REFERENCE_LEN).ok_or(
            AllocationMapError::Arithmetic(Error::Arithmetic {
                operation: "advance indirect allocation-map reference cursor",
            }),
        )?;
        let entry = &self.references[self.offset..end];
        self.offset = end;
        Ok(Some(u32::from_le_bytes([
            entry[0], entry[1], entry[2], entry[3],
        ])))
    }

    /// Returns the number of raw references not yet inspected.
    #[must_use]
    pub fn remaining_references(&self) -> usize {
        (self.references.len() - self.offset) / INDIRECT_REFERENCE_LEN
    }
}

/// Allocation-free cursor over set bit indices in an extended usage bitmap.
#[derive(Debug)]
pub struct ExtendedAllocationBits<'page> {
    bits: SetBitIndices<'page>,
}

impl ExtendedAllocationBits<'_> {
    /// Returns the next set relative bit index, charging every inspected bit.
    ///
    /// A resource rejection leaves the pending bit unadvanced so the caller
    /// can retry with another operation budget.
    pub fn next_bit(
        &mut self,
        budget: &mut ResourceBudget,
    ) -> Result<Option<u64>, AllocationMapError> {
        self.bits
            .next_set_bit(budget)
            .map_err(AllocationMapError::from)
    }
}

#[derive(Debug)]
struct SetBitIndices<'bytes> {
    bytes: &'bytes [u8],
    byte_index: usize,
    bit_in_byte: u8,
    bit_index: u64,
    done: bool,
}

impl<'bytes> SetBitIndices<'bytes> {
    const fn new(bytes: &'bytes [u8]) -> Self {
        Self {
            bytes,
            byte_index: 0,
            bit_in_byte: 0,
            bit_index: 0,
            done: false,
        }
    }

    fn next_set_bit(&mut self, budget: &mut ResourceBudget) -> Result<Option<u64>, SetBitError> {
        while !self.done && self.byte_index < self.bytes.len() {
            budget.charge_items(1).map_err(SetBitError::Resource)?;

            let bit_index = self.bit_index;
            let is_set = self.bytes[self.byte_index] & (1 << self.bit_in_byte) != 0;
            self.advance()?;
            if is_set {
                return Ok(Some(bit_index));
            }
        }
        Ok(None)
    }

    fn advance(&mut self) -> Result<(), SetBitError> {
        self.bit_index = self.bit_index.checked_add(1).ok_or_else(|| {
            self.done = true;
            SetBitError::Arithmetic(Error::Arithmetic {
                operation: "advance allocation-map bit index",
            })
        })?;
        self.bit_in_byte = self.bit_in_byte.checked_add(1).ok_or_else(|| {
            self.done = true;
            SetBitError::Arithmetic(Error::Arithmetic {
                operation: "advance allocation-map bit within byte",
            })
        })?;
        if self.bit_in_byte == 8 {
            self.bit_in_byte = 0;
            self.byte_index = self.byte_index.checked_add(1).ok_or_else(|| {
                self.done = true;
                SetBitError::Arithmetic(Error::Arithmetic {
                    operation: "advance allocation-map bitmap byte",
                })
            })?;
        }
        if self.byte_index == self.bytes.len() {
            self.done = true;
        }
        Ok(())
    }
}

#[derive(Debug)]
enum SetBitError {
    Resource(Error),
    Arithmetic(Error),
}

impl From<SetBitError> for AllocationMapError {
    fn from(source: SetBitError) -> Self {
        match source {
            SetBitError::Resource(source) => Self::Resource(source),
            SetBitError::Arithmetic(source) => Self::Arithmetic(source),
        }
    }
}

#[cfg(test)]
#[path = "allocation_tests.rs"]
mod tests;
