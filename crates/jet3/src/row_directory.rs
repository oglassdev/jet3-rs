//! Validated Jet 3 data-page row directories from `EXP-0060`.

use std::fmt;
use std::ops::Range;

use crate::{Error, JET3_PAGE_SIZE, PageNumber, ResourceBudget};

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const OWNER_OFFSET: usize = 4;
const ROW_COUNT_OFFSET: usize = 8;
const DIRECTORY_OFFSET: usize = 10;
const ENTRY_LEN: usize = 2;
const OFFSET_MASK: u16 = 0x1fff;
const UNKNOWN_FLAG: u16 = 0x2000;
const OVERFLOW_FLAG: u16 = 0x4000;
const HIDDEN_FLAG: u16 = 0x8000;
const MAX_ROW_COUNT: usize = (PAGE_BYTES - DIRECTORY_OFFSET) / ENTRY_LEN;
const OVERFLOW_POINTER_LEN: usize = 4;

/// A physical row slot on one data page.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct RowLocator {
    page: PageNumber,
    slot: u8,
}

impl RowLocator {
    pub(crate) const fn new(page: PageNumber, slot: u8) -> Self {
        Self { page, slot }
    }

    #[must_use]
    pub const fn page(self) -> PageNumber {
        self.page
    }

    #[must_use]
    pub const fn slot(self) -> u8 {
        self.slot
    }
}

/// A malformed or unsupported row-directory condition.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum RowDirectoryError {
    UnexpectedOwner {
        expected: PageNumber,
        actual: PageNumber,
    },
    RowCountTooLarge {
        row_count: u16,
        maximum: usize,
    },
    RowSlotNotRepresentable {
        row_count: u16,
    },
    UnknownFlag {
        row: u8,
        raw_offset: u16,
    },
    OffsetOutOfPage {
        row: u8,
        raw_offset: u16,
    },
    InvalidBounds {
        row: u8,
        start: usize,
        end: usize,
        directory_end: usize,
    },
    InvalidOverflowPointerLength {
        row: u8,
        length: usize,
    },
    MissingRow {
        row: u8,
        row_count: u16,
    },
    DirectoryChanged {
        page: PageNumber,
        previous_row_count: u16,
        current_row_count: u16,
    },
    Resource(Error),
}

impl fmt::Display for RowDirectoryError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "row directory failed: {self:?}")
    }
}

impl std::error::Error for RowDirectoryError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Resource(source) => Some(source),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct RowDirectory {
    page: PageNumber,
    row_count: u16,
    next_row: u16,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct RowEntry {
    locator: RowLocator,
    range: Range<usize>,
    overflow: bool,
    hidden: bool,
}

impl RowEntry {
    pub(crate) const fn locator(&self) -> RowLocator {
        self.locator
    }

    pub(crate) fn range(&self) -> Range<usize> {
        self.range.clone()
    }

    pub(crate) const fn overflow(&self) -> bool {
        self.overflow
    }

    pub(crate) const fn hidden(&self) -> bool {
        self.hidden
    }
}

impl RowDirectory {
    pub(crate) fn validate_owner(
        expected_owner: PageNumber,
        page: &[u8; PAGE_BYTES],
    ) -> Result<(), RowDirectoryError> {
        let actual_owner = PageNumber::new(u64::from(u32_at(page, OWNER_OFFSET)));
        if actual_owner != expected_owner {
            return Err(RowDirectoryError::UnexpectedOwner {
                expected: expected_owner,
                actual: actual_owner,
            });
        }
        Ok(())
    }

    pub(crate) fn validate(
        page_number: PageNumber,
        expected_owner: PageNumber,
        page: &[u8; PAGE_BYTES],
        budget: &mut ResourceBudget,
    ) -> Result<Self, RowDirectoryError> {
        Self::validate_owner(expected_owner, page)?;
        let row_count = u16_at(page, ROW_COUNT_OFFSET);
        if usize::from(row_count) > MAX_ROW_COUNT {
            return Err(RowDirectoryError::RowCountTooLarge {
                row_count,
                maximum: MAX_ROW_COUNT,
            });
        }
        if row_count > u16::from(u8::MAX) + 1 {
            return Err(RowDirectoryError::RowSlotNotRepresentable { row_count });
        }
        budget
            .charge_items(u64::from(row_count))
            .map_err(RowDirectoryError::Resource)?;
        let directory_end = DIRECTORY_OFFSET + ENTRY_LEN * usize::from(row_count);
        let mut end = PAGE_BYTES;
        for row in 0..row_count {
            let slot = u8::try_from(row)
                .map_err(|_| RowDirectoryError::RowSlotNotRepresentable { row_count })?;
            let raw_offset = raw_offset(page, row);
            let start = validate_entry(slot, raw_offset, end, directory_end)?;
            end = start;
        }
        Ok(Self {
            page: page_number,
            row_count,
            next_row: 0,
        })
    }

    pub(crate) fn next_primary(
        &mut self,
        page: &[u8; PAGE_BYTES],
    ) -> Result<Option<RowEntry>, RowDirectoryError> {
        while self.next_row < self.row_count {
            let row = self.next_row;
            self.next_row += 1;
            let slot =
                u8::try_from(row).map_err(|_| RowDirectoryError::RowSlotNotRepresentable {
                    row_count: self.row_count,
                })?;
            let entry = self.entry(page, slot)?;
            if !entry.hidden() {
                return Ok(Some(entry));
            }
        }
        Ok(None)
    }

    pub(crate) fn resume_after(mut self, previous: &Self) -> Result<Self, RowDirectoryError> {
        if self.page != previous.page || self.row_count != previous.row_count {
            return Err(RowDirectoryError::DirectoryChanged {
                page: self.page,
                previous_row_count: previous.row_count,
                current_row_count: self.row_count,
            });
        }
        self.next_row = previous.next_row;
        Ok(self)
    }

    pub(crate) fn entry(
        &self,
        page: &[u8; PAGE_BYTES],
        row: u8,
    ) -> Result<RowEntry, RowDirectoryError> {
        if u16::from(row) >= self.row_count {
            return Err(RowDirectoryError::MissingRow {
                row,
                row_count: self.row_count,
            });
        }
        let raw = raw_offset(page, u16::from(row));
        let start = usize::from(raw & OFFSET_MASK);
        let end = if row == 0 {
            PAGE_BYTES
        } else {
            usize::from(raw_offset(page, u16::from(row) - 1) & OFFSET_MASK)
        };
        Ok(RowEntry {
            locator: RowLocator::new(self.page, row),
            range: start..end,
            overflow: raw & OVERFLOW_FLAG != 0,
            hidden: raw & HIDDEN_FLAG != 0,
        })
    }
}

fn validate_entry(
    row: u8,
    raw_offset: u16,
    end: usize,
    directory_end: usize,
) -> Result<usize, RowDirectoryError> {
    if raw_offset & UNKNOWN_FLAG != 0 {
        return Err(RowDirectoryError::UnknownFlag { row, raw_offset });
    }
    let start = usize::from(raw_offset & OFFSET_MASK);
    if start >= PAGE_BYTES {
        return Err(RowDirectoryError::OffsetOutOfPage { row, raw_offset });
    }
    let hidden = raw_offset & HIDDEN_FLAG != 0;
    if start < directory_end || start > end || (!hidden && start == end) {
        return Err(RowDirectoryError::InvalidBounds {
            row,
            start,
            end,
            directory_end,
        });
    }
    if raw_offset & OVERFLOW_FLAG != 0 && !hidden && end - start != OVERFLOW_POINTER_LEN {
        return Err(RowDirectoryError::InvalidOverflowPointerLength {
            row,
            length: end - start,
        });
    }
    Ok(start)
}

fn raw_offset(page: &[u8; PAGE_BYTES], row: u16) -> u16 {
    let offset = DIRECTORY_OFFSET + ENTRY_LEN * usize::from(row);
    u16::from_le_bytes([page[offset], page[offset + 1]])
}

fn u32_at(bytes: &[u8], offset: usize) -> u32 {
    u32::from_le_bytes([
        bytes[offset],
        bytes[offset + 1],
        bytes[offset + 2],
        bytes[offset + 3],
    ])
}

fn u16_at(bytes: &[u8], offset: usize) -> u16 {
    u16::from_le_bytes([bytes[offset], bytes[offset + 1]])
}

#[cfg(test)]
#[path = "row_directory_tests.rs"]
mod tests;
