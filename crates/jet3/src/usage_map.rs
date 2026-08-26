//! Bounded row-anchored allocation-map views from `SRC-0020` and `EXP-0057`.

use std::fmt;
use std::ops::Range;

use crate::{ClassifiedPage, Error, MapRowLocator, PageKind, PageNumber, ResourceBudget};

// SRC-0020: Jet 3 data-page row directory fields.
const ROW_COUNT_OFFSET: usize = 8;
const ROW_DIRECTORY_OFFSET: usize = 10;
const ROW_ENTRY_LEN: usize = 2;
const PAGE_BYTES: usize = 2048;
const MAX_ROW_COUNT: usize = (PAGE_BYTES - ROW_DIRECTORY_OFFSET) / ROW_ENTRY_LEN;
const MAX_UNFLAGGED_ROW_OFFSET: u16 = 2047;

/// One complete allocation-map record borrowed from a data-page row.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct UsageMapRecord<'page> {
    location: MapRowLocator,
    raw: &'page [u8],
    start: usize,
    end: usize,
}

impl<'page> UsageMapRecord<'page> {
    /// Returns the physical row locator used to obtain the record.
    #[must_use]
    pub const fn location(self) -> MapRowLocator {
        self.location
    }

    /// Returns the complete caller-delimited map record.
    #[must_use]
    pub const fn raw(self) -> &'page [u8] {
        self.raw
    }

    /// Returns the row's half-open page-local bounds.
    #[must_use]
    pub fn range(self) -> Range<usize> {
        self.start..self.end
    }
}

/// A structured failure while locating a usage-map row.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum UsageMapError {
    /// The supplied page does not match the locator.
    PageMismatch {
        expected: PageNumber,
        actual: PageNumber,
    },
    /// The locator target is not a data page.
    ExpectedDataPage { page: PageNumber, actual: PageKind },
    /// The row count cannot fit a complete Jet 3 directory.
    RowCountTooLarge { row_count: u16, maximum: usize },
    /// The requested row slot is absent.
    RowOutOfBounds { row: u8, row_count: u16 },
    /// A row-directory entry contains flags or an impossible page offset.
    FlaggedOrOutOfPageRow { row: u16, raw_offset: u16 },
    /// A row begins inside the directory or does not precede its end.
    InvalidRowBounds {
        row: u16,
        start: usize,
        end: usize,
        directory_end: usize,
    },
    /// Resource policy rejected bounded directory work.
    Resource(Error),
}

impl fmt::Display for UsageMapError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::PageMismatch { expected, actual } => write!(
                formatter,
                "usage-map locator names page {}, but page {} was supplied",
                expected.get(),
                actual.get()
            ),
            Self::ExpectedDataPage { page, actual } => write!(
                formatter,
                "usage-map page {} must be a data page, found {actual:?}",
                page.get()
            ),
            Self::RowCountTooLarge { row_count, maximum } => write!(
                formatter,
                "usage-map data page declares {row_count} rows; at most {maximum} fit"
            ),
            Self::RowOutOfBounds { row, row_count } => write!(
                formatter,
                "usage-map row {row} is outside the page's {row_count} rows"
            ),
            Self::FlaggedOrOutOfPageRow { row, raw_offset } => write!(
                formatter,
                "usage-map row {row} has flagged or out-of-page offset 0x{raw_offset:04x}"
            ),
            Self::InvalidRowBounds {
                row,
                start,
                end,
                directory_end,
            } => write!(
                formatter,
                "usage-map row {row} has invalid bounds [{start}, {end}) with directory ending at {directory_end}"
            ),
            Self::Resource(source) => write!(formatter, "usage-map row lookup rejected: {source}"),
        }
    }
}

impl std::error::Error for UsageMapError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Resource(source) => Some(source),
            Self::PageMismatch { .. }
            | Self::ExpectedDataPage { .. }
            | Self::RowCountTooLarge { .. }
            | Self::RowOutOfBounds { .. }
            | Self::FlaggedOrOutOfPageRow { .. }
            | Self::InvalidRowBounds { .. } => None,
        }
    }
}

/// Locates one complete allocation-map record using a checked data-page row.
pub fn locate_usage_map<'page>(
    page: ClassifiedPage<'page>,
    locator: MapRowLocator,
    budget: &mut ResourceBudget,
) -> Result<UsageMapRecord<'page>, UsageMapError> {
    budget
        .charge_work_units(1)
        .map_err(UsageMapError::Resource)?;
    if page.number() != locator.page() {
        return Err(UsageMapError::PageMismatch {
            expected: locator.page(),
            actual: page.number(),
        });
    }
    if page.kind() != PageKind::Data {
        return Err(UsageMapError::ExpectedDataPage {
            page: page.number(),
            actual: page.kind(),
        });
    }
    let raw = page.raw_bytes();
    let row_count = u16::from_le_bytes([raw[ROW_COUNT_OFFSET], raw[ROW_COUNT_OFFSET + 1]]);
    if usize::from(row_count) > MAX_ROW_COUNT {
        return Err(UsageMapError::RowCountTooLarge {
            row_count,
            maximum: MAX_ROW_COUNT,
        });
    }
    if u16::from(locator.row()) >= row_count {
        return Err(UsageMapError::RowOutOfBounds {
            row: locator.row(),
            row_count,
        });
    }
    budget
        .charge_items(u64::from(row_count))
        .map_err(UsageMapError::Resource)?;
    let directory_end = ROW_DIRECTORY_OFFSET + ROW_ENTRY_LEN * usize::from(row_count);
    let mut prior_start = PAGE_BYTES;
    let mut selected = None;
    for row in 0..row_count {
        let offset = ROW_DIRECTORY_OFFSET + ROW_ENTRY_LEN * usize::from(row);
        let raw_offset = u16::from_le_bytes([raw[offset], raw[offset + 1]]);
        if raw_offset > MAX_UNFLAGGED_ROW_OFFSET {
            return Err(UsageMapError::FlaggedOrOutOfPageRow { row, raw_offset });
        }
        let start = usize::from(raw_offset);
        let end = prior_start;
        if start < directory_end || start >= end {
            return Err(UsageMapError::InvalidRowBounds {
                row,
                start,
                end,
                directory_end,
            });
        }
        if row == u16::from(locator.row()) {
            selected = Some(start..end);
        }
        prior_start = start;
    }
    let range = selected.ok_or(UsageMapError::RowOutOfBounds {
        row: locator.row(),
        row_count,
    })?;
    Ok(UsageMapRecord {
        location: locator,
        raw: &raw[range.clone()],
        start: range.start,
        end: range.end,
    })
}

#[cfg(test)]
#[path = "usage_map_tests.rs"]
mod tests;
