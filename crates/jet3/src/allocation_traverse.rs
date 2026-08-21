//! Bounded, iterative traversal over the detached allocation-map decoders.
//!
//! This module supplies the safety boundary that database traversal requires:
//! checked page references, a visited set charged before it is allocated,
//! a chain-depth ceiling, structured cycle detection, and a page-visit charge
//! for every reference including repeats. It follows only what `SRC-0020`
//! documents. The steps that source does not establish — locating a map
//! record, interpreting a zero reference, and deriving the database page that
//! an extended bitmap bit represents — return [`UnsupportedTraversalStep`]
//! instead of guessing.

use crate::{
    ByteCount, ClassifiedPage, DatabasePageError, DatabaseReader, Error, ExtendedAllocationBits,
    IndirectAllocationMap, JET3_PAGE_SIZE, MapPageReferences, PageGeometry, PageKind, PageNumber,
    ReadAt, ResourceBudget, allocation::AllocationMapError, classify_page,
    extended_allocation_bits,
};
use std::fmt;

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;

/// A traversal step whose rule is not established by any recorded source.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[non_exhaustive]
pub enum UnsupportedTraversalStep {
    /// Locating the global or per-table allocation-map record in a database.
    MapLocation,
    /// Deciding whether a zero map-page reference is a null slot or page zero.
    ZeroReference,
    /// Deriving the absolute database page represented by an extended bit.
    ExtendedPageBase,
}

impl fmt::Display for UnsupportedTraversalStep {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::MapLocation => "locating an allocation-map record",
            Self::ZeroReference => "interpreting a zero map-page reference",
            Self::ExtendedPageBase => "deriving the page base of an extended bitmap",
        })
    }
}

/// A structured failure while following allocation-map page references.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum AllocationTraversalError {
    /// The step is not established by recorded provenance.
    Unsupported(UnsupportedTraversalStep),
    /// Decoding or iterating the detached record failed.
    Map(AllocationMapError),
    /// Reading or classifying a referenced page failed.
    Page(DatabasePageError),
    /// A raw reference is outside the captured page range.
    InvalidReference {
        /// Zero-based position of the reference in the record.
        index: usize,
        /// The rejected raw reference.
        reference: u32,
        /// The geometry check that rejected it.
        source: Error,
    },
    /// A map page was referenced more than once in one traversal.
    RepeatedMapPage {
        /// The repeated page.
        page: PageNumber,
        /// Zero-based position of the repeated reference.
        index: usize,
    },
    /// A referenced page is not an extended usage bitmap.
    ExpectedExtendedUsageBitmap {
        /// The referenced page.
        page: PageNumber,
        /// Its lossless classification.
        actual: PageKind,
    },
    /// Resource policy rejected the step, including depth and visited-set
    /// charges.
    Resource(Error),
}

impl fmt::Display for AllocationTraversalError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unsupported(step) => {
                write!(formatter, "{step} is blocked on recorded evidence")
            }
            Self::Map(source) => write!(formatter, "allocation-map traversal failed: {source}"),
            Self::Page(source) => write!(formatter, "map page access failed: {source}"),
            Self::InvalidReference {
                index,
                reference,
                source,
            } => write!(
                formatter,
                "map-page reference {index} ({reference}) is invalid: {source}"
            ),
            Self::RepeatedMapPage { page, index } => write!(
                formatter,
                "map-page reference {index} repeats page {}",
                page.get()
            ),
            Self::ExpectedExtendedUsageBitmap { page, actual } => write!(
                formatter,
                "expected page {} to be an extended usage bitmap, found {actual:?}",
                page.get()
            ),
            Self::Resource(source) => {
                write!(formatter, "allocation-map traversal rejected: {source}")
            }
        }
    }
}

impl std::error::Error for AllocationTraversalError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Map(source) => Some(source),
            Self::Page(source) => Some(source),
            Self::InvalidReference { source, .. } | Self::Resource(source) => Some(source),
            Self::Unsupported(_)
            | Self::RepeatedMapPage { .. }
            | Self::ExpectedExtendedUsageBitmap { .. } => None,
        }
    }
}

impl From<AllocationMapError> for AllocationTraversalError {
    fn from(source: AllocationMapError) -> Self {
        match source {
            AllocationMapError::Resource(error) => Self::Resource(error),
            other => Self::Map(other),
        }
    }
}

/// Locates an allocation-map record inside a database.
///
/// `SRC-0020` does not establish where the global or per-table map records
/// live, so this always returns
/// [`UnsupportedTraversalStep::MapLocation`]. It exists so the traversal
/// entry point is fixed before the evidence that fills it in is recorded.
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

/// One-bit-per-page visited set whose storage is charged before allocation.
#[derive(Debug)]
pub struct VisitedPages {
    bits: Vec<u8>,
    page_count: u64,
}

impl VisitedPages {
    /// Reserves one bit per page in `geometry`, charging the byte count to the
    /// allocation budget before any memory is requested.
    pub fn new(geometry: PageGeometry, budget: &mut ResourceBudget) -> Result<Self, Error> {
        let page_count = geometry.page_count();
        let byte_len = page_count.div_ceil(8);
        budget.charge_allocation(ByteCount::new(byte_len))?;
        let capacity = usize::try_from(byte_len).map_err(|_| Error::IntegerConversion {
            value: u128::from(byte_len),
            target: "usize",
        })?;
        Ok(Self {
            bits: vec![0; capacity],
            page_count,
        })
    }

    /// Returns whether `page` was marked, without changing state.
    #[must_use]
    pub fn contains(&self, page: PageNumber) -> bool {
        self.slot(page)
            .is_some_and(|(byte, mask)| self.bits[byte] & mask != 0)
    }

    /// Marks `page` and reports whether it was already marked.
    ///
    /// A page outside the reserved range is reported as a structured
    /// arithmetic failure rather than grown into.
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

/// An extended usage-bitmap page reached by following one map reference.
#[derive(Debug)]
pub struct ReachedMapPage<'page> {
    page: PageNumber,
    index: usize,
    bits: ExtendedAllocationBits<'page>,
}

impl<'page> ReachedMapPage<'page> {
    /// Returns the physical page number that was followed.
    #[must_use]
    pub const fn page(&self) -> PageNumber {
        self.page
    }

    /// Returns the zero-based position of the reference that reached it.
    #[must_use]
    pub const fn reference_index(&self) -> usize {
        self.index
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

/// Iteratively follows the references of one caller-delimited type-1 record.
///
/// Each call to [`Self::next_map_page`] performs exactly one reference step
/// under the full safety boundary, so the caller drives the chain and no
/// recursion or unbounded state exists.
#[derive(Debug)]
pub struct MapPageWalker<'map> {
    references: MapPageReferences<'map>,
    geometry: PageGeometry,
    visited: VisitedPages,
    index: usize,
    depth: u64,
    done: bool,
}

impl<'map> MapPageWalker<'map> {
    /// Starts a walk over `map`, charging and reserving the visited set first.
    pub fn new(
        map: IndirectAllocationMap<'map>,
        geometry: PageGeometry,
        budget: &mut ResourceBudget,
    ) -> Result<Self, AllocationTraversalError> {
        let visited =
            VisitedPages::new(geometry, budget).map_err(AllocationTraversalError::Resource)?;
        Ok(Self {
            references: map.map_page_references(),
            geometry,
            visited,
            index: 0,
            depth: 0,
            done: false,
        })
    }

    /// Returns the number of extended bitmap pages reached so far.
    #[must_use]
    pub const fn depth(&self) -> u64 {
        self.depth
    }

    /// Follows the next reference and returns its extended bitmap page.
    ///
    /// Order of checks per reference: one explicit work unit, the raw
    /// reference item, the chain-depth ceiling, the geometry reference check,
    /// the page read (which charges one page visit for every reference,
    /// including a repeated one), the visited set, then classification. Every
    /// failure other than a retryable resource rejection exhausts the walker.
    pub fn next_map_page<'page, S: ReadAt>(
        &mut self,
        database: &mut DatabaseReader<S>,
        destination: &'page mut [u8; PAGE_BYTES],
        budget: &mut ResourceBudget,
    ) -> Result<Option<ReachedMapPage<'page>>, AllocationTraversalError> {
        if self.done {
            return Ok(None);
        }
        budget
            .charge_work_units(1)
            .map_err(AllocationTraversalError::Resource)?;
        let Some(reference) = self.references.next_reference(budget)? else {
            self.done = true;
            return Ok(None);
        };
        self.fallible_step(reference, database, destination, budget)
            .inspect_err(|_| self.done = true)
    }

    fn fallible_step<'page, S: ReadAt>(
        &mut self,
        reference: u32,
        database: &mut DatabaseReader<S>,
        destination: &'page mut [u8; PAGE_BYTES],
        budget: &mut ResourceBudget,
    ) -> Result<Option<ReachedMapPage<'page>>, AllocationTraversalError> {
        let index = self.index;
        self.index = index
            .checked_add(1)
            .ok_or(AllocationTraversalError::Resource(Error::Arithmetic {
                operation: "advance map-page reference index",
            }))?;
        let next_depth = self
            .depth
            .checked_add(1)
            .ok_or(AllocationTraversalError::Resource(Error::Arithmetic {
                operation: "advance map-page chain depth",
            }))?;
        budget
            .check_chain_depth(next_depth)
            .map_err(AllocationTraversalError::Resource)?;
        if reference == 0 {
            return Err(AllocationTraversalError::Unsupported(
                UnsupportedTraversalStep::ZeroReference,
            ));
        }
        let page = PageNumber::new(u64::from(reference));
        self.geometry.validate_reference(page).map_err(|source| {
            AllocationTraversalError::InvalidReference {
                index,
                reference,
                source,
            }
        })?;
        database
            .read_raw_page(page, destination, budget)
            .map_err(|source| AllocationTraversalError::Page(DatabasePageError::Read(source)))?;
        if self
            .visited
            .insert(page)
            .map_err(AllocationTraversalError::Resource)?
        {
            return Err(AllocationTraversalError::RepeatedMapPage { page, index });
        }
        let classified = classify_page(page, destination, budget).map_err(|source| {
            AllocationTraversalError::Page(DatabasePageError::Classification(source))
        })?;
        let bits = extended_bits(classified)?;
        self.depth = next_depth;
        Ok(Some(ReachedMapPage { page, index, bits }))
    }
}

fn extended_bits(
    page: ClassifiedPage<'_>,
) -> Result<ExtendedAllocationBits<'_>, AllocationTraversalError> {
    let number = page.number();
    let kind = page.kind();
    extended_allocation_bits(page).map_err(|source| match source {
        AllocationMapError::ExpectedExtendedUsageBitmap { .. } => {
            AllocationTraversalError::ExpectedExtendedUsageBitmap {
                page: number,
                actual: kind,
            }
        }
        other => AllocationTraversalError::Map(other),
    })
}

#[cfg(test)]
#[path = "allocation_traverse_tests.rs"]
mod tests;
