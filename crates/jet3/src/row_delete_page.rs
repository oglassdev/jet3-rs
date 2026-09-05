//! Preservation-aware tail tombstones and counts from EXP-0162 (EXP-0059/0060 layout).
use crate::row_directory::RowDirectory;
use crate::{PAGE_BYTES, PageImage, PageNumber, PageOffset, ResourceBudget, UpdateError};
const FREE_BYTES: usize = 2;
const DIRECTORY: usize = 10;
const ENTRY_BYTES: usize = 2;
const TOMBSTONE: u16 = 0xc000;
const TABLE_COUNT: usize = 12;

pub(crate) fn tail(
    page: PageNumber,
    owner: PageNumber,
    source: &[u8; PAGE_BYTES],
    slot: u8,
    budget: &mut ResourceBudget,
) -> Result<PageImage, UpdateError> {
    let directory = RowDirectory::validate(page, owner, source, budget)?;
    if directory.row_count() < 2 {
        return Err(UpdateError::Unsupported("sole-row page release"));
    }
    if u16::from(slot) + 1 != directory.row_count() {
        return Err(UpdateError::Unsupported("non-tail row compaction"));
    }
    for ordinal in 0..directory.row_count() {
        let entry = directory.entry(source, ordinal as u8)?;
        if entry.hidden() || entry.overflow() || entry.range().is_empty() {
            return Err(UpdateError::Unsupported(
                "page contains hidden, overflow or deleted slots",
            ));
        }
    }
    let range = directory.entry(source, slot)?.range();
    let directory_end = DIRECTORY + ENTRY_BYTES * usize::from(directory.row_count());
    let free = usize::from(u16::from_le_bytes([
        source[FREE_BYTES],
        source[FREE_BYTES + 1],
    ]));
    if free != range.start - directory_end {
        return Err(UpdateError::Mismatch("data page free-byte count"));
    }
    let new_free = u16::try_from(range.end - directory_end)
        .map_err(|_| UpdateError::Mismatch("free-byte range"))?;
    let word = u16::try_from(range.end).map_err(|_| UpdateError::Mismatch("tombstone offset"))?
        | TOMBSTONE;
    let mut patched = PageImage::from_bytes(*source);
    patched.write_at(
        PageOffset::new(FREE_BYTES as u64),
        &new_free.to_le_bytes(),
        budget,
    )?;
    patched.write_at(
        PageOffset::new((DIRECTORY + ENTRY_BYTES * usize::from(slot)) as u64),
        &word.to_le_bytes(),
        budget,
    )?;
    Ok(patched)
}

pub(crate) fn decrement_count(
    source: &[u8; PAGE_BYTES],
    observed_rows: u32,
    budget: &mut ResourceBudget,
) -> Result<PageImage, UpdateError> {
    let expected = observed_rows.to_le_bytes();
    if source[TABLE_COUNT..TABLE_COUNT + 4] != expected {
        return Err(UpdateError::Mismatch("table row count"));
    }
    let count = observed_rows
        .checked_sub(1)
        .ok_or(UpdateError::Mismatch("empty table"))?;
    let mut patched = PageImage::from_bytes(*source);
    patched.write_at(
        PageOffset::new(TABLE_COUNT as u64),
        &count.to_le_bytes(),
        budget,
    )?;
    Ok(patched)
}
