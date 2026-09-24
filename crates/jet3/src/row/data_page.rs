//! Checked data-page primitives in the EXP-0059/0060 directory layout shared by
//! the append, replace and remove operations in the sibling `*_page` modules.
use crate::{
    PAGE_BYTES, PageImage, PageNumber, PageOffset, ResourceBudget, WriteError,
    definition::header::ROW_COUNT as TABLE_ROW_COUNT,
    format::{
        data_page_directory::{DIRECTORY_OFFSET, ENTRY_LEN, FREE_SPACE_OFFSET, ROW_COUNT_OFFSET},
        page_image::MAX_BUILT_ROWS,
    },
    row::directory::{RowDirectory, RowEntry, RowSlot},
};

/// A data page whose directory was validated for `owner`; edits return patched copies.
///
/// Ordinary edits (`physical == false`) refuse pages holding hidden or overflow
/// rows other than deleted slots; physical edits accept every checked [`RowSlot`].
pub(crate) struct DataPageEditor<'a> {
    pub(super) source: &'a [u8; PAGE_BYTES],
    pub(super) directory: RowDirectory,
}

pub(super) struct Packed {
    pub(super) lowest: usize,
    pub(super) directory_end: usize,
    pub(super) free: usize,
}

impl<'a> DataPageEditor<'a> {
    pub(crate) fn open(
        page: PageNumber,
        owner: PageNumber,
        source: &'a [u8; PAGE_BYTES],
        budget: &mut ResourceBudget,
    ) -> Result<Self, WriteError> {
        let directory = RowDirectory::validate(page, owner, source, budget)?;
        Ok(Self { source, directory })
    }

    pub(super) fn entry(&self, slot: u8) -> Result<RowEntry, WriteError> {
        Ok(self.directory.entry(self.source, slot)?)
    }

    pub(super) fn live_rows(
        &self,
        physical: bool,
        unsupported: &'static str,
    ) -> Result<u16, WriteError> {
        let mut live = 0;
        for ordinal in 0..self.directory.row_count() {
            let entry = self.entry(ordinal as u8)?;
            if physical {
                if RowSlot::read(&entry)? != RowSlot::Deleted {
                    live += 1;
                }
                continue;
            }
            if entry.hidden() && entry.overflow() && entry.range().is_empty() {
                continue;
            }
            if entry.hidden() || entry.overflow() || entry.range().is_empty() {
                return Err(WriteError::Unsupported(unsupported));
            }
            live += 1;
        }
        Ok(live)
    }

    /// Requires the stored free-byte count to equal the gap below the lowest row.
    pub(super) fn packed(&self, mismatch: &'static str) -> Result<Packed, WriteError> {
        let count = self.directory.row_count();
        let last = count.checked_sub(1).ok_or(WriteError::Mismatch(mismatch))?;
        let lowest = self.entry(last as u8)?.range().start;
        let directory_end = DIRECTORY_OFFSET + ENTRY_LEN * usize::from(count);
        let free = usize::from(u16::from_le_bytes([
            self.source[FREE_SPACE_OFFSET],
            self.source[FREE_SPACE_OFFSET + 1],
        ]));
        if free != lowest - directory_end {
            return Err(WriteError::Mismatch(mismatch));
        }
        Ok(Packed {
            lowest,
            directory_end,
            free,
        })
    }
}

pub(super) fn write_slot(
    image: &mut PageImage,
    ordinal: u16,
    word: u16,
    budget: &mut ResourceBudget,
) -> Result<(), WriteError> {
    let offset = DIRECTORY_OFFSET + ENTRY_LEN * usize::from(ordinal);
    image.write_at(PageOffset::new(offset as u64), &word.to_le_bytes(), budget)?;
    Ok(())
}

pub(super) fn write_free(
    image: &mut PageImage,
    free: u16,
    budget: &mut ResourceBudget,
) -> Result<(), WriteError> {
    image.write_at(
        PageOffset::new(FREE_SPACE_OFFSET as u64),
        &free.to_le_bytes(),
        budget,
    )?;
    Ok(())
}

/// Candidate availability policy: an appended minimum row and directory slot fit.
/// EXP-0060 supplies the physical slots; this does not model DAO's allocation policy.
pub(crate) fn has_capacity(page: &[u8; PAGE_BYTES], minimum: usize) -> bool {
    let count = u16::from_le_bytes([page[ROW_COUNT_OFFSET], page[ROW_COUNT_OFFSET + 1]]);
    let free = usize::from(u16::from_le_bytes([
        page[FREE_SPACE_OFFSET],
        page[FREE_SPACE_OFFSET + 1],
    ]));
    count < MAX_BUILT_ROWS && minimum.checked_add(ENTRY_LEN).is_some_and(|n| free >= n)
}

/// Requires the table definition's stored row count to equal the rows read.
pub(crate) fn check_table_rows(source: &[u8; PAGE_BYTES], observed: u32) -> Result<(), WriteError> {
    if source[TABLE_ROW_COUNT..TABLE_ROW_COUNT + 4] != observed.to_le_bytes() {
        return Err(WriteError::Mismatch("table row count"));
    }
    Ok(())
}

/// Checks the stored row count, then writes it one higher (`inserted`) or lower.
pub(crate) fn count_table_row(
    source: &[u8; PAGE_BYTES],
    observed: u32,
    inserted: bool,
    budget: &mut ResourceBudget,
) -> Result<PageImage, WriteError> {
    check_table_rows(source, observed)?;
    let count = if inserted {
        observed
            .checked_add(1)
            .ok_or(WriteError::Mismatch("table row count overflow"))?
    } else {
        observed
            .checked_sub(1)
            .ok_or(WriteError::Mismatch("empty table"))?
    };
    let mut patched = PageImage::from_bytes(*source);
    patched.write_at(
        PageOffset::new(TABLE_ROW_COUNT as u64),
        &count.to_le_bytes(),
        budget,
    )?;
    Ok(patched)
}
