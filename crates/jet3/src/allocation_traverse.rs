//! Bounded, iterative Jet 3 owned-page traversal.
//!
//! Format-specific locator, pointer, and extended-base rules come from
//! `EXP-0057`; detached map and bitmap shapes remain grounded in `SRC-0020`.

use crate::{
    AllocationMap, ByteCount, ClassifiedPage, DatabasePageError, DatabaseReader, Error,
    ExtendedAllocationBits, JET3_PAGE_SIZE, MapLocationError, PageGeometry, PageKind, PageNumber,
    ReadAt, ResourceBudget, UsageMapError, allocation::AllocationMapError, decode_allocation_map,
    extended_allocation_bits, locate_table_maps, locate_usage_map,
};
use std::fmt;
use std::ops::Range;

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const EXTENDED_BITMAP_OFFSET: usize = 4;
// SRC-0020 and EXP-0057: 2,044 bytes, one bit per page.
const EXTENDED_BITMAP_BITS: u64 = 16_352;
const INLINE_VISITED_BYTES: usize = 32;

/// A structured failure while locating or traversing an allocation map.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum AllocationTraversalError {
    /// Decoding the table-definition map locators failed.
    MapLocation(MapLocationError),
    /// Locating the caller-delimited map row failed.
    UsageMap(UsageMapError),
    /// Decoding a map record or extended bitmap failed.
    AllocationMap(AllocationMapError),
    /// Reading or classifying a followed page failed.
    Page(DatabasePageError),
    /// A page number is outside the captured page range.
    InvalidReference {
        /// The rejected page number.
        page: PageNumber,
        /// The geometry check that rejected it.
        source: Error,
    },
    /// A type-1 map page points back to the data page holding its record.
    SelfReference {
        /// Page holding the usage-map record.
        record_page: PageNumber,
    },
    /// A nonzero type-1 slot appears after the first zero slot.
    NonzeroAfterNullSlot {
        /// Zero-based slot ordinal.
        slot: u64,
        /// Nonzero page found after the null slot.
        page: PageNumber,
    },
    /// A page was followed more than once in one chain.
    RepeatedPage {
        /// The repeated page.
        page: PageNumber,
    },
    /// A followed page does not have the required classification.
    UnexpectedPageKind {
        /// The followed page.
        page: PageNumber,
        /// The classification the caller required.
        expected: PageKind,
        /// The lossless classification found.
        actual: PageKind,
    },
    /// A relative extended bit is outside the complete Jet 3 bitmap.
    RelativeBitOutOfRange {
        /// Zero-based type-1 slot ordinal.
        slot: u64,
        /// Rejected bitmap-relative bit.
        bit_index: u64,
    },
    /// Inline start-page arithmetic overflowed.
    InlinePageOverflow {
        /// Inline map's absolute starting page.
        start_page: PageNumber,
        /// Bitmap-relative bit being converted.
        bit_index: u64,
    },
    /// Extended slot-base arithmetic overflowed.
    ExtendedPageOverflow {
        /// Zero-based type-1 slot ordinal.
        slot: u64,
        /// Bitmap-relative bit being converted.
        bit_index: u64,
    },
    /// Resource policy rejected the step.
    Resource(Error),
}

impl fmt::Display for AllocationTraversalError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::MapLocation(source) => write!(formatter, "map location failed: {source}"),
            Self::UsageMap(source) => write!(formatter, "usage-map row failed: {source}"),
            Self::AllocationMap(source) => write!(formatter, "allocation map failed: {source}"),
            Self::Page(source) => write!(formatter, "followed page access failed: {source}"),
            Self::InvalidReference { page, source } => {
                write!(
                    formatter,
                    "page reference {} is invalid: {source}",
                    page.get()
                )
            }
            Self::SelfReference { record_page } => write!(
                formatter,
                "usage-map record page {} refers to itself",
                record_page.get()
            ),
            Self::NonzeroAfterNullSlot { slot, page } => write!(
                formatter,
                "type-1 slot {slot} names page {} after a zero slot",
                page.get()
            ),
            Self::RepeatedPage { page } => {
                write!(formatter, "page {} was already followed", page.get())
            }
            Self::UnexpectedPageKind {
                page,
                expected,
                actual,
            } => write!(
                formatter,
                "expected page {} to be {expected:?}, found {actual:?}",
                page.get()
            ),
            Self::RelativeBitOutOfRange { slot, bit_index } => write!(
                formatter,
                "extended slot {slot} bit {bit_index} is outside its {EXTENDED_BITMAP_BITS} bits"
            ),
            Self::InlinePageOverflow {
                start_page,
                bit_index,
            } => write!(
                formatter,
                "inline page arithmetic overflowed for start page {} and bit {bit_index}",
                start_page.get()
            ),
            Self::ExtendedPageOverflow { slot, bit_index } => write!(
                formatter,
                "extended page arithmetic overflowed for slot {slot} bit {bit_index}"
            ),
            Self::Resource(source) => write!(formatter, "allocation traversal rejected: {source}"),
        }
    }
}

impl std::error::Error for AllocationTraversalError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::MapLocation(source) => Some(source),
            Self::UsageMap(source) => Some(source),
            Self::AllocationMap(source) => Some(source),
            Self::Page(source) => Some(source),
            Self::InvalidReference { source, .. } | Self::Resource(source) => Some(source),
            Self::SelfReference { .. }
            | Self::NonzeroAfterNullSlot { .. }
            | Self::RepeatedPage { .. }
            | Self::UnexpectedPageKind { .. }
            | Self::RelativeBitOutOfRange { .. }
            | Self::InlinePageOverflow { .. }
            | Self::ExtendedPageOverflow { .. } => None,
        }
    }
}

/// Interprets one type-1 slot as an optional direct physical page reference.
pub fn follow_map_page_reference(
    reference: u32,
    budget: &mut ResourceBudget,
) -> Result<Option<PageNumber>, AllocationTraversalError> {
    budget
        .charge_work_units(1)
        .map_err(AllocationTraversalError::Resource)?;
    Ok((reference != 0).then(|| PageNumber::new(u64::from(reference))))
}

/// One-bit-per-page visited set whose storage is charged before initialization.
#[derive(Debug)]
pub struct VisitedPages {
    bits: VisitedStorage,
    page_count: u64,
}

#[derive(Debug)]
enum VisitedStorage {
    Inline([u8; INLINE_VISITED_BYTES]),
    Heap(Vec<u8>),
}

impl VisitedStorage {
    fn as_slice(&self) -> &[u8] {
        match self {
            Self::Inline(bits) => bits,
            Self::Heap(bits) => bits,
        }
    }

    fn as_mut_slice(&mut self) -> &mut [u8] {
        match self {
            Self::Inline(bits) => bits,
            Self::Heap(bits) => bits,
        }
    }
}

impl VisitedPages {
    /// Reserves one bit per page, charging before initializing inline or heap storage.
    pub fn new(geometry: PageGeometry, budget: &mut ResourceBudget) -> Result<Self, Error> {
        let page_count = geometry.page_count();
        let byte_len = page_count.div_ceil(8);
        budget.charge_allocation(ByteCount::new(byte_len))?;
        let capacity = usize::try_from(byte_len).map_err(|_| Error::IntegerConversion {
            value: u128::from(byte_len),
            target: "usize",
        })?;
        let bits = if capacity <= INLINE_VISITED_BYTES {
            VisitedStorage::Inline([0; INLINE_VISITED_BYTES])
        } else {
            let mut bits = Vec::new();
            bits.try_reserve_exact(capacity).map_err(|_| Error::Io {
                operation: "reserve page visited set",
                kind: std::io::ErrorKind::OutOfMemory,
            })?;
            bits.resize(capacity, 0);
            VisitedStorage::Heap(bits)
        };
        Ok(Self { bits, page_count })
    }

    /// Returns whether `page` was marked, without changing state.
    #[must_use]
    pub fn contains(&self, page: PageNumber) -> bool {
        self.slot(page)
            .and_then(|(byte, mask)| self.bits.as_slice().get(byte).map(|value| (value, mask)))
            .is_some_and(|(value, mask)| value & mask != 0)
    }

    /// Marks `page` and reports whether it was already marked.
    pub fn insert(&mut self, page: PageNumber) -> Result<bool, Error> {
        let (byte, mask) = self.slot(page).ok_or(Error::PageOutOfBounds {
            page: page.get(),
            page_count: self.page_count,
        })?;
        let value = self
            .bits
            .as_mut_slice()
            .get_mut(byte)
            .ok_or(Error::Arithmetic {
                operation: "access page visited bit",
            })?;
        let seen = *value & mask != 0;
        *value |= mask;
        Ok(seen)
    }

    fn slot(&self, page: PageNumber) -> Option<(usize, u8)> {
        if page.get() >= self.page_count {
            return None;
        }
        let byte = usize::try_from(page.get() / 8).ok()?;
        let mask = 1_u8.checked_shl(u32::try_from(page.get() % 8).ok()?)?;
        (byte < self.bits.as_slice().len()).then_some((byte, mask))
    }
}

/// Follows caller-supplied pages under geometry, depth, visit, and cycle bounds.
#[derive(Debug)]
pub struct PageChainWalker {
    geometry: PageGeometry,
    visited: VisitedPages,
    depth: u64,
}

impl PageChainWalker {
    /// Starts a chain, charging and reserving the visited set first.
    pub fn new(
        geometry: PageGeometry,
        budget: &mut ResourceBudget,
    ) -> Result<Self, AllocationTraversalError> {
        let visited =
            VisitedPages::new(geometry, budget).map_err(AllocationTraversalError::Resource)?;
        Ok(Self {
            geometry,
            visited,
            depth: 0,
        })
    }

    /// Returns the number of pages followed so far.
    #[must_use]
    pub const fn depth(&self) -> u64 {
        self.depth
    }

    /// Returns whether `page` was already followed in this chain.
    #[must_use]
    pub fn followed(&self, page: PageNumber) -> bool {
        self.visited.contains(page)
    }

    /// Follows `page`, requiring it to classify as `expected`.
    pub fn follow<'page, S: ReadAt>(
        &mut self,
        page: PageNumber,
        expected: PageKind,
        database: &mut DatabaseReader<S>,
        destination: &'page mut [u8; PAGE_BYTES],
        budget: &mut ResourceBudget,
    ) -> Result<ClassifiedPage<'page>, AllocationTraversalError> {
        budget
            .charge_work_units(1)
            .map_err(AllocationTraversalError::Resource)?;
        let next_depth = self
            .depth
            .checked_add(1)
            .ok_or(AllocationTraversalError::Resource(Error::Arithmetic {
                operation: "advance page chain depth",
            }))?;
        budget
            .check_chain_depth(next_depth)
            .map_err(AllocationTraversalError::Resource)?;
        self.geometry
            .validate_reference(page)
            .map_err(|source| AllocationTraversalError::InvalidReference { page, source })?;
        database
            .read_raw_page(page, destination, budget)
            .map_err(|source| AllocationTraversalError::Page(DatabasePageError::Read(source)))?;
        let classified = crate::classify_page(page, destination, budget).map_err(|source| {
            AllocationTraversalError::Page(DatabasePageError::Classification(source))
        })?;
        if classified.kind() != expected {
            return Err(AllocationTraversalError::UnexpectedPageKind {
                page,
                expected,
                actual: classified.kind(),
            });
        }
        if self
            .visited
            .insert(page)
            .map_err(AllocationTraversalError::Resource)?
        {
            return Err(AllocationTraversalError::RepeatedPage { page });
        }
        self.depth = next_depth;
        Ok(classified)
    }
}

/// An extended usage-bitmap page paired with its type-1 slot ordinal.
#[derive(Debug)]
pub struct ReachedMapPage<'page> {
    page: PageNumber,
    slot: u64,
    bits: ExtendedAllocationBits<'page>,
}

impl<'page> ReachedMapPage<'page> {
    /// Wraps a page that already classified as an extended usage bitmap.
    pub fn new(slot: u64, page: ClassifiedPage<'page>) -> Result<Self, AllocationMapError> {
        let number = page.number();
        Ok(Self {
            page: number,
            slot,
            bits: extended_allocation_bits(page)?,
        })
    }

    /// Returns the physical page number that stores this bitmap.
    #[must_use]
    pub const fn page(&self) -> PageNumber {
        self.page
    }

    /// Returns the zero-based type-1 slot ordinal.
    #[must_use]
    pub const fn slot(&self) -> u64 {
        self.slot
    }

    /// Returns the set relative bit indices of the page's bitmap.
    pub fn relative_bits(&mut self) -> &mut ExtendedAllocationBits<'page> {
        &mut self.bits
    }

    /// Converts a relative bit into an absolute database page.
    pub fn absolute_page(&self, relative_bit: u64) -> Result<PageNumber, AllocationTraversalError> {
        absolute_extended_page(self.slot, relative_bit)
    }
}

#[derive(Debug)]
struct BitCursor {
    next: u64,
    end: u64,
}

impl BitCursor {
    const fn new(end: u64) -> Self {
        Self { next: 0, end }
    }

    fn next_set(
        &mut self,
        bytes: &[u8],
        budget: &mut ResourceBudget,
    ) -> Result<Option<u64>, AllocationTraversalError> {
        while self.next < self.end {
            budget
                .charge_items(1)
                .map_err(AllocationTraversalError::Resource)?;
            let bit = self.next;
            self.next = self
                .next
                .checked_add(1)
                .ok_or(AllocationTraversalError::Resource(Error::Arithmetic {
                    operation: "advance owned-page bitmap cursor",
                }))?;
            let byte = usize::try_from(bit / 8).map_err(|_| {
                AllocationTraversalError::Resource(Error::IntegerConversion {
                    value: u128::from(bit / 8),
                    target: "usize",
                })
            })?;
            let mask = 1_u8 << (bit % 8);
            let value =
                bytes
                    .get(byte)
                    .ok_or(AllocationTraversalError::Resource(Error::Arithmetic {
                        operation: "access owned-page bitmap byte",
                    }))?;
            if value & mask != 0 {
                return Ok(Some(bit));
            }
        }
        Ok(None)
    }
}

#[derive(Debug)]
enum OwnedMapState {
    Inline {
        start_page: PageNumber,
        bitmap: Range<usize>,
        bits: BitCursor,
    },
    Indirect {
        references: Range<usize>,
        next_slot: u64,
        slot_count: u64,
        zero_seen: bool,
        active: Option<(u64, BitCursor)>,
    },
}

/// Fixed-memory iterator over pages owned by one table root.
#[derive(Debug)]
pub struct OwnedPages<'operation, S> {
    database: &'operation mut DatabaseReader<S>,
    budget: &'operation mut ResourceBudget,
    geometry: PageGeometry,
    record_page: PageNumber,
    usage_page: [u8; PAGE_BYTES],
    extended_page: [u8; PAGE_BYTES],
    walker: Option<PageChainWalker>,
    state: OwnedMapState,
}

impl<'operation, S: ReadAt> OwnedPages<'operation, S> {
    pub(crate) fn new(
        database: &'operation mut DatabaseReader<S>,
        table_root: PageNumber,
        budget: &'operation mut ResourceBudget,
    ) -> Result<Self, AllocationTraversalError> {
        let geometry = database.geometry();
        let mut table_page = [0_u8; PAGE_BYTES];
        let classified = database
            .read_classified_page(table_root, &mut table_page, budget)
            .map_err(AllocationTraversalError::Page)?;
        let locations = locate_table_maps(classified, geometry, budget)
            .map_err(AllocationTraversalError::MapLocation)?;
        let locator = locations.owned();
        let mut usage_page = [0_u8; PAGE_BYTES];
        let classified = database
            .read_classified_page(locator.page(), &mut usage_page, budget)
            .map_err(AllocationTraversalError::Page)?;
        let record = locate_usage_map(classified, locator, budget)
            .map_err(AllocationTraversalError::UsageMap)?;
        let range = record.range();
        let decoded = decode_allocation_map(record.raw(), budget)
            .map_err(AllocationTraversalError::AllocationMap)?;
        let (state, walker) = match decoded {
            AllocationMap::Inline(map) => {
                let bitmap_start =
                    range
                        .start
                        .checked_add(5)
                        .ok_or(AllocationTraversalError::Resource(Error::Arithmetic {
                            operation: "locate inline usage-map bitmap",
                        }))?;
                let bitmap_bytes = range.end.checked_sub(bitmap_start).ok_or(
                    AllocationTraversalError::Resource(Error::Arithmetic {
                        operation: "measure inline usage-map bitmap",
                    }),
                )?;
                let bit_count = u64::try_from(bitmap_bytes)
                    .ok()
                    .and_then(|value| value.checked_mul(8))
                    .ok_or(AllocationTraversalError::Resource(Error::Arithmetic {
                        operation: "measure inline usage-map bits",
                    }))?;
                (
                    OwnedMapState::Inline {
                        start_page: map.start_page(),
                        bitmap: bitmap_start..range.end,
                        bits: BitCursor::new(bit_count),
                    },
                    None,
                )
            }
            AllocationMap::Indirect(map) => {
                let references_start =
                    range
                        .start
                        .checked_add(1)
                        .ok_or(AllocationTraversalError::Resource(Error::Arithmetic {
                            operation: "locate indirect usage-map references",
                        }))?;
                let slot_count = u64::try_from(map.reference_count()).map_err(|_| {
                    AllocationTraversalError::Resource(Error::IntegerConversion {
                        value: map.reference_count() as u128,
                        target: "u64",
                    })
                })?;
                (
                    OwnedMapState::Indirect {
                        references: references_start..range.end,
                        next_slot: 0,
                        slot_count,
                        zero_seen: false,
                        active: None,
                    },
                    Some(PageChainWalker::new(geometry, budget)?),
                )
            }
        };
        Ok(Self {
            database,
            budget,
            geometry,
            record_page: locator.page(),
            usage_page,
            extended_page: [0_u8; PAGE_BYTES],
            walker,
            state,
        })
    }

    /// Returns the next geometry-validated owned page.
    pub fn next_page(&mut self) -> Result<Option<PageNumber>, AllocationTraversalError> {
        loop {
            match &mut self.state {
                OwnedMapState::Inline {
                    start_page,
                    bitmap,
                    bits,
                } => {
                    let Some(bit) = bits.next_set(&self.usage_page[bitmap.clone()], self.budget)?
                    else {
                        return Ok(None);
                    };
                    let value = start_page.get().checked_add(bit).ok_or(
                        AllocationTraversalError::InlinePageOverflow {
                            start_page: *start_page,
                            bit_index: bit,
                        },
                    )?;
                    let page = PageNumber::new(value);
                    self.geometry.validate_reference(page).map_err(|source| {
                        AllocationTraversalError::InvalidReference { page, source }
                    })?;
                    return Ok(Some(page));
                }
                OwnedMapState::Indirect {
                    references,
                    next_slot,
                    slot_count,
                    zero_seen,
                    active,
                } => {
                    if let Some((slot, bits)) = active {
                        if let Some(bit) = bits
                            .next_set(&self.extended_page[EXTENDED_BITMAP_OFFSET..], self.budget)?
                        {
                            let page = absolute_extended_page(*slot, bit)?;
                            self.geometry.validate_reference(page).map_err(|source| {
                                AllocationTraversalError::InvalidReference { page, source }
                            })?;
                            return Ok(Some(page));
                        }
                        *active = None;
                        continue;
                    }
                    if *next_slot == *slot_count {
                        return Ok(None);
                    }
                    self.budget
                        .charge_items(1)
                        .map_err(AllocationTraversalError::Resource)?;
                    let slot = *next_slot;
                    let byte_offset = usize::try_from(slot)
                        .ok()
                        .and_then(|value| value.checked_mul(4))
                        .and_then(|value| references.start.checked_add(value))
                        .ok_or(AllocationTraversalError::Resource(Error::Arithmetic {
                            operation: "locate indirect usage-map slot",
                        }))?;
                    let entry_end =
                        byte_offset
                            .checked_add(4)
                            .ok_or(AllocationTraversalError::Resource(Error::Arithmetic {
                                operation: "measure indirect usage-map slot",
                            }))?;
                    let entry = self.usage_page.get(byte_offset..entry_end).ok_or(
                        AllocationTraversalError::Resource(Error::Arithmetic {
                            operation: "access indirect usage-map slot",
                        }),
                    )?;
                    let raw = u32::from_le_bytes([entry[0], entry[1], entry[2], entry[3]]);
                    *next_slot =
                        next_slot
                            .checked_add(1)
                            .ok_or(AllocationTraversalError::Resource(Error::Arithmetic {
                                operation: "advance indirect usage-map slot",
                            }))?;
                    let Some(page) = follow_map_page_reference(raw, self.budget)? else {
                        *zero_seen = true;
                        continue;
                    };
                    if *zero_seen {
                        return Err(AllocationTraversalError::NonzeroAfterNullSlot { slot, page });
                    }
                    if page == self.record_page {
                        return Err(AllocationTraversalError::SelfReference {
                            record_page: self.record_page,
                        });
                    }
                    let walker = self
                        .walker
                        .as_mut()
                        .ok_or(AllocationTraversalError::Resource(Error::Arithmetic {
                            operation: "access indirect usage-map walker",
                        }))?;
                    let classified = walker.follow(
                        page,
                        PageKind::ExtendedUsageBitmap,
                        self.database,
                        &mut self.extended_page,
                        self.budget,
                    )?;
                    ReachedMapPage::new(slot, classified)
                        .map_err(AllocationTraversalError::AllocationMap)?;
                    *active = Some((slot, BitCursor::new(EXTENDED_BITMAP_BITS)));
                }
            }
        }
    }
}

fn absolute_extended_page(
    slot: u64,
    relative_bit: u64,
) -> Result<PageNumber, AllocationTraversalError> {
    if relative_bit >= EXTENDED_BITMAP_BITS {
        return Err(AllocationTraversalError::RelativeBitOutOfRange {
            slot,
            bit_index: relative_bit,
        });
    }
    // EXP-0057: slot ordinal, not referenced bitmap page, selects the base.
    let value = slot
        .checked_mul(EXTENDED_BITMAP_BITS)
        .and_then(|base| base.checked_add(relative_bit))
        .ok_or(AllocationTraversalError::ExtendedPageOverflow {
            slot,
            bit_index: relative_bit,
        })?;
    Ok(PageNumber::new(value))
}

#[cfg(test)]
#[path = "allocation_traverse_tests.rs"]
mod tests;
