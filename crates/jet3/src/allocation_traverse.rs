//! Bounded, iterative page-chain following for future allocation traversal.
//!
//! [`PageChainWalker`] is format-neutral safety machinery: given page numbers
//! the caller already holds, it follows them one at a time under a chain-depth
//! ceiling, a visited set charged before it is allocated, repeat detection,
//! a geometry check, one page visit per followed page, and a required
//! classification. It interprets no database bytes.
//!
//! Every format-specific step that `SRC-0020` does not establish — locating a
//! map record, turning a raw type-1 reference into a page number, and
//! deriving the database page an extended bitmap bit represents — returns
//! [`UnsupportedTraversalStep`] instead of guessing. When provenance for one
//! of those steps is recorded, only that function changes.

use crate::{
    ByteCount, ClassifiedPage, DatabasePageError, DatabaseReader, Error, ExtendedAllocationBits,
    JET3_PAGE_SIZE, PageGeometry, PageKind, PageNumber, ReadAt, ResourceBudget,
    allocation::AllocationMapError, classify_page, extended_allocation_bits,
};
use std::fmt;

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;

/// A traversal step whose rule is not established by any recorded source.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[non_exhaustive]
pub enum UnsupportedTraversalStep {
    /// Locating the global or per-table allocation-map record in a database.
    MapLocation,
    /// Turning a raw type-1 map-page reference into a database page number,
    /// including whether zero is a null slot.
    PointerFollowing,
    /// Deriving the absolute database page represented by an extended bit.
    ExtendedPageBase,
}

impl fmt::Display for UnsupportedTraversalStep {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::MapLocation => "locating an allocation-map record",
            Self::PointerFollowing => "following a raw map-page reference",
            Self::ExtendedPageBase => "deriving the page base of an extended bitmap",
        })
    }
}

/// A structured failure while following a page chain.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum AllocationTraversalError {
    /// The step is not established by recorded provenance.
    Unsupported(UnsupportedTraversalStep),
    /// Reading or classifying a followed page failed.
    Page(DatabasePageError),
    /// A page number is outside the captured page range.
    InvalidReference {
        /// The rejected page number.
        page: PageNumber,
        /// The geometry check that rejected it.
        source: Error,
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
    /// Resource policy rejected the step. The walker is unchanged and the
    /// same step may be retried with more budget.
    Resource(Error),
}

impl fmt::Display for AllocationTraversalError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unsupported(step) => {
                write!(formatter, "{step} is blocked on recorded evidence")
            }
            Self::Page(source) => write!(formatter, "followed page access failed: {source}"),
            Self::InvalidReference { page, source } => {
                write!(
                    formatter,
                    "page reference {} is invalid: {source}",
                    page.get()
                )
            }
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
            Self::Resource(source) => write!(formatter, "page chain step rejected: {source}"),
        }
    }
}

impl std::error::Error for AllocationTraversalError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Page(source) => Some(source),
            Self::InvalidReference { source, .. } | Self::Resource(source) => Some(source),
            Self::Unsupported(_) | Self::RepeatedPage { .. } | Self::UnexpectedPageKind { .. } => {
                None
            }
        }
    }
}

/// Locates an allocation-map record inside a database.
///
/// `SRC-0020` does not establish where map records live, so this always
/// returns [`UnsupportedTraversalStep::MapLocation`] after one work unit.
pub fn locate_allocation_map<S: ReadAt>(
    _database: &DatabaseReader<S>,
    budget: &mut ResourceBudget,
) -> Result<core::convert::Infallible, AllocationTraversalError> {
    budget
        .charge_work_units(1)
        .map_err(AllocationTraversalError::Resource)?;
    Err(AllocationTraversalError::Unsupported(
        UnsupportedTraversalStep::MapLocation,
    ))
}

/// Turns a raw type-1 map-page reference into a page number to follow.
///
/// `SRC-0020` describes the references but does not authorize following
/// them or interpreting zero, so this always returns
/// [`UnsupportedTraversalStep::PointerFollowing`] after one work unit.
pub fn follow_map_page_reference(
    _reference: u32,
    budget: &mut ResourceBudget,
) -> Result<PageNumber, AllocationTraversalError> {
    budget
        .charge_work_units(1)
        .map_err(AllocationTraversalError::Resource)?;
    Err(AllocationTraversalError::Unsupported(
        UnsupportedTraversalStep::PointerFollowing,
    ))
}

/// One-bit-per-page visited set whose storage is charged before allocation.
#[derive(Debug)]
pub struct VisitedPages {
    bits: Vec<u8>,
    page_count: u64,
}

impl VisitedPages {
    /// Reserves one bit per page in `geometry`, charging the byte count to the
    /// allocation budget before any memory is requested. A refused
    /// reservation is a structured error, not an abort.
    pub fn new(geometry: PageGeometry, budget: &mut ResourceBudget) -> Result<Self, Error> {
        let page_count = geometry.page_count();
        let byte_len = page_count.div_ceil(8);
        budget.charge_allocation(ByteCount::new(byte_len))?;
        let capacity = usize::try_from(byte_len).map_err(|_| Error::IntegerConversion {
            value: u128::from(byte_len),
            target: "usize",
        })?;
        let mut bits = Vec::new();
        bits.try_reserve_exact(capacity).map_err(|_| Error::Io {
            operation: "reserve page visited set",
            kind: std::io::ErrorKind::OutOfMemory,
        })?;
        bits.resize(capacity, 0);
        Ok(Self { bits, page_count })
    }

    /// Returns whether `page` was marked, without changing state.
    #[must_use]
    pub fn contains(&self, page: PageNumber) -> bool {
        self.slot(page)
            .is_some_and(|(byte, mask)| self.bits[byte] & mask != 0)
    }

    /// Marks `page` and reports whether it was already marked.
    ///
    /// A page outside the reserved range is rejected rather than grown into.
    pub fn insert(&mut self, page: PageNumber) -> Result<bool, Error> {
        let (byte, mask) = self.slot(page).ok_or(Error::PageOutOfBounds {
            page: page.get(),
            page_count: self.page_count,
        })?;
        let seen = self.bits[byte] & mask != 0;
        self.bits[byte] |= mask;
        Ok(seen)
    }

    fn slot(&self, page: PageNumber) -> Option<(usize, u8)> {
        if page.get() >= self.page_count {
            return None;
        }
        let byte = usize::try_from(page.get() / 8).ok()?;
        let mask = 1_u8.checked_shl(u32::try_from(page.get() % 8).ok()?)?;
        (byte < self.bits.len()).then_some((byte, mask))
    }
}

/// Follows caller-supplied page numbers one at a time under the full
/// traversal safety boundary.
///
/// The walker never decides which page comes next; it only makes following
/// a page safe. No recursion or unbounded state exists, and a rejected step
/// leaves the walker exactly as it was.
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
    ///
    /// Order of checks: one explicit work unit, the chain-depth ceiling, the
    /// geometry reference check, the page read (one page visit, charged even
    /// when the page turns out to be a repeat), classification, the required
    /// kind, then the visited set. Depth and the visited set change only
    /// after every check passes, so any failure leaves the walker unchanged.
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
        let classified = classify_page(page, destination, budget).map_err(|source| {
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

/// An extended usage-bitmap page reached through a [`PageChainWalker`].
#[derive(Debug)]
pub struct ReachedMapPage<'page> {
    page: PageNumber,
    bits: ExtendedAllocationBits<'page>,
}

impl<'page> ReachedMapPage<'page> {
    /// Wraps a page that already classified as an extended usage bitmap.
    pub fn new(page: ClassifiedPage<'page>) -> Result<Self, AllocationMapError> {
        let number = page.number();
        Ok(Self {
            page: number,
            bits: extended_allocation_bits(page)?,
        })
    }

    /// Returns the physical page number that was followed.
    #[must_use]
    pub const fn page(&self) -> PageNumber {
        self.page
    }

    /// Returns the set relative bit indices of the page's bitmap.
    pub fn relative_bits(&mut self) -> &mut ExtendedAllocationBits<'page> {
        &mut self.bits
    }

    /// Converts a relative bit into an absolute database page.
    ///
    /// `SRC-0020` does not establish the page base of an extended map, so this
    /// always returns [`UnsupportedTraversalStep::ExtendedPageBase`].
    pub fn absolute_page(
        &self,
        _relative_bit: u64,
    ) -> Result<PageNumber, AllocationTraversalError> {
        Err(AllocationTraversalError::Unsupported(
            UnsupportedTraversalStep::ExtendedPageBase,
        ))
    }
}

#[cfg(test)]
#[path = "allocation_traverse_tests.rs"]
mod tests;
