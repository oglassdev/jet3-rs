//! Shared structural decoding for Jet 3 data-page row directories.
//!
//! `EXP-0060` and `EXP-0061` establish the row and long-value constraints
//! composed here.

use std::ops::Range;

use crate::{Error, JET3_PAGE_SIZE, ResourceBudget};

pub(crate) const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
pub(crate) const LONG_VALUE_OWNER: [u8; 4] = *b"LVAL";

const OWNER_OFFSET: usize = 4;
const ROW_COUNT_OFFSET: usize = 8;
const DIRECTORY_OFFSET: usize = 10;
const ENTRY_LEN: usize = 2;
const OFFSET_MASK: u16 = 0x1fff;
const UNKNOWN_FLAG: u16 = 0x2000;
const OVERFLOW_FLAG: u16 = 0x4000;
const HIDDEN_FLAG: u16 = 0x8000;
pub(crate) const MAX_ROW_COUNT: usize = (PAGE_BYTES - DIRECTORY_OFFSET) / ENTRY_LEN;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum DataPageDirectoryError {
    RowCountTooLarge {
        row_count: u16,
        maximum: usize,
    },
    UnknownFlag {
        row: u16,
        raw_offset: u16,
    },
    OffsetOutOfPage {
        row: u16,
        raw_offset: u16,
    },
    InvalidBounds {
        row: u16,
        start: usize,
        end: usize,
        directory_end: usize,
    },
    Resource(Error),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct DataPageDirectory {
    row_count: u16,
    next_row: u16,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct DataPageEntry {
    row: u16,
    range: Range<usize>,
    overflow: bool,
    hidden: bool,
}

impl DataPageEntry {
    pub(crate) const fn row(&self) -> u16 {
        self.row
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

impl DataPageDirectory {
    pub(crate) const fn declared_row_count(page: &[u8; PAGE_BYTES]) -> u16 {
        u16::from_le_bytes([page[ROW_COUNT_OFFSET], page[ROW_COUNT_OFFSET + 1]])
    }

    pub(crate) const fn owner(page: &[u8; PAGE_BYTES]) -> [u8; 4] {
        [
            page[OWNER_OFFSET],
            page[OWNER_OFFSET + 1],
            page[OWNER_OFFSET + 2],
            page[OWNER_OFFSET + 3],
        ]
    }

    pub(crate) fn validate(
        page: &[u8; PAGE_BYTES],
        budget: &mut ResourceBudget,
    ) -> Result<Self, DataPageDirectoryError> {
        let row_count = Self::declared_row_count(page);
        if usize::from(row_count) > MAX_ROW_COUNT {
            return Err(DataPageDirectoryError::RowCountTooLarge {
                row_count,
                maximum: MAX_ROW_COUNT,
            });
        }
        budget
            .charge_items(u64::from(row_count))
            .map_err(DataPageDirectoryError::Resource)?;
        let directory_end = DIRECTORY_OFFSET + ENTRY_LEN * usize::from(row_count);
        let mut end = PAGE_BYTES;
        for row in 0..row_count {
            let raw_offset = raw_offset(page, row);
            end = validate_entry(row, raw_offset, end, directory_end)?;
        }
        Ok(Self {
            row_count,
            next_row: 0,
        })
    }

    pub(crate) const fn row_count(&self) -> u16 {
        self.row_count
    }

    pub(crate) fn next_entry(&mut self, page: &[u8; PAGE_BYTES]) -> Option<DataPageEntry> {
        if self.next_row >= self.row_count {
            return None;
        }
        let row = self.next_row;
        self.next_row += 1;
        self.entry(page, row)
    }

    pub(crate) fn entry(&self, page: &[u8; PAGE_BYTES], row: u16) -> Option<DataPageEntry> {
        if row >= self.row_count {
            return None;
        }
        let raw = raw_offset(page, row);
        let start = usize::from(raw & OFFSET_MASK);
        let end = if row == 0 {
            PAGE_BYTES
        } else {
            usize::from(raw_offset(page, row - 1) & OFFSET_MASK)
        };
        Some(DataPageEntry {
            row,
            range: start..end,
            overflow: raw & OVERFLOW_FLAG != 0,
            hidden: raw & HIDDEN_FLAG != 0,
        })
    }

    pub(crate) fn resume_after(&mut self, previous: &Self) {
        self.next_row = previous.next_row;
    }
}

fn validate_entry(
    row: u16,
    raw_offset: u16,
    end: usize,
    directory_end: usize,
) -> Result<usize, DataPageDirectoryError> {
    if raw_offset & UNKNOWN_FLAG != 0 {
        return Err(DataPageDirectoryError::UnknownFlag { row, raw_offset });
    }
    let start = usize::from(raw_offset & OFFSET_MASK);
    if start >= PAGE_BYTES {
        return Err(DataPageDirectoryError::OffsetOutOfPage { row, raw_offset });
    }
    let hidden = raw_offset & HIDDEN_FLAG != 0;
    if start < directory_end || start > end || (!hidden && start == end) {
        return Err(DataPageDirectoryError::InvalidBounds {
            row,
            start,
            end,
            directory_end,
        });
    }
    Ok(start)
}

fn raw_offset(page: &[u8; PAGE_BYTES], row: u16) -> u16 {
    let offset = DIRECTORY_OFFSET + ENTRY_LEN * usize::from(row);
    u16::from_le_bytes([page[offset], page[offset + 1]])
}
