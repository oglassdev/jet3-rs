//! Validated Jet 3 data-page row directories from `EXP-0060`.

use std::fmt;
use std::ops::Range;

use crate::data_page_directory::{
    DataPageDirectory, DataPageDirectoryError, MAX_ROW_COUNT, PAGE_BYTES,
};
use crate::{Error, PageNumber, ResourceBudget};

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
    inner: DataPageDirectory,
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
        let actual_owner = PageNumber::new(u64::from(u32::from_le_bytes(
            DataPageDirectory::owner(page),
        )));
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
        let row_count = DataPageDirectory::declared_row_count(page);
        if usize::from(row_count) > MAX_ROW_COUNT {
            return Err(RowDirectoryError::RowCountTooLarge {
                row_count,
                maximum: MAX_ROW_COUNT,
            });
        }
        if row_count > u16::from(u8::MAX) + 1 {
            return Err(RowDirectoryError::RowSlotNotRepresentable { row_count });
        }
        let inner = DataPageDirectory::validate(page, budget).map_err(map_directory_error)?;
        let mut validation = inner.clone();
        while let Some(entry) = validation.next_entry(page) {
            if entry.overflow() && !entry.hidden() && entry.range().len() != OVERFLOW_POINTER_LEN {
                let row = u8::try_from(entry.row())
                    .map_err(|_| RowDirectoryError::RowSlotNotRepresentable { row_count })?;
                return Err(RowDirectoryError::InvalidOverflowPointerLength {
                    row,
                    length: entry.range().len(),
                });
            }
        }
        Ok(Self {
            page: page_number,
            inner,
        })
    }

    pub(crate) fn next_primary(
        &mut self,
        page: &[u8; PAGE_BYTES],
    ) -> Result<Option<RowEntry>, RowDirectoryError> {
        while let Some(common) = self.inner.next_entry(page) {
            let slot = u8::try_from(common.row()).map_err(|_| {
                RowDirectoryError::RowSlotNotRepresentable {
                    row_count: self.inner.row_count(),
                }
            })?;
            let entry = RowEntry {
                locator: RowLocator::new(self.page, slot),
                range: common.range(),
                overflow: common.overflow(),
                hidden: common.hidden(),
            };
            if !entry.hidden() {
                return Ok(Some(entry));
            }
        }
        Ok(None)
    }

    pub(crate) fn resume_after(mut self, previous: &Self) -> Result<Self, RowDirectoryError> {
        if self.page != previous.page || self.inner.row_count() != previous.inner.row_count() {
            return Err(RowDirectoryError::DirectoryChanged {
                page: self.page,
                previous_row_count: previous.inner.row_count(),
                current_row_count: self.inner.row_count(),
            });
        }
        self.inner.resume_after(&previous.inner);
        Ok(self)
    }

    pub(crate) fn entry(
        &self,
        page: &[u8; PAGE_BYTES],
        row: u8,
    ) -> Result<RowEntry, RowDirectoryError> {
        let Some(common) = self.inner.entry(page, u16::from(row)) else {
            return Err(RowDirectoryError::MissingRow {
                row,
                row_count: self.inner.row_count(),
            });
        };
        Ok(RowEntry {
            locator: RowLocator::new(self.page, row),
            range: common.range(),
            overflow: common.overflow(),
            hidden: common.hidden(),
        })
    }
}

fn map_directory_error(error: DataPageDirectoryError) -> RowDirectoryError {
    match error {
        DataPageDirectoryError::RowCountTooLarge { row_count, maximum } => {
            RowDirectoryError::RowCountTooLarge { row_count, maximum }
        }
        DataPageDirectoryError::UnknownFlag { row, raw_offset } => RowDirectoryError::UnknownFlag {
            row: row as u8,
            raw_offset,
        },
        DataPageDirectoryError::OffsetOutOfPage { row, raw_offset } => {
            RowDirectoryError::OffsetOutOfPage {
                row: row as u8,
                raw_offset,
            }
        }
        DataPageDirectoryError::InvalidBounds {
            row,
            start,
            end,
            directory_end,
        } => RowDirectoryError::InvalidBounds {
            row: row as u8,
            start,
            end,
            directory_end,
        },
        DataPageDirectoryError::Resource(source) => RowDirectoryError::Resource(source),
    }
}

#[cfg(test)]
#[path = "row_directory_tests.rs"]
mod tests;
