//! Appending physical slots from EXP-0162, using EXP-0060 directory/row layout.
use crate::row_directory::RowDirectory;
use crate::{PAGE_BYTES, PageImage, PageNumber, PageOffset, ResourceBudget, UpdateError};

const FREE_BYTES: usize = 2;
const SLOT_COUNT: usize = 8;
const DIRECTORY: usize = 10;
const ENTRY_BYTES: usize = 2;
const TABLE_COUNT: usize = 12;

pub(crate) fn append(
    page: PageNumber,
    owner: PageNumber,
    source: &[u8; PAGE_BYTES],
    row: &[u8],
    budget: &mut ResourceBudget,
) -> Result<Option<(PageImage, u8)>, UpdateError> {
    let directory = RowDirectory::validate(page, owner, source, budget)?;
    let count = directory.row_count();
    if count == 0 {
        return Ok(None);
    }
    budget.charge_items(u64::from(count))?;
    let mut live = 0;
    for ordinal in 0..count {
        let entry = directory.entry(source, ordinal as u8)?;
        if entry.range().is_empty() && entry.hidden() && entry.overflow() {
            continue;
        }
        if entry.hidden() || entry.overflow() || entry.range().is_empty() {
            return Err(UpdateError::Unsupported(
                "page contains nonordinary row slots",
            ));
        }
        live += 1;
    }
    if live == 0 {
        return Ok(None);
    }
    let packed_start = directory.entry(source, (count - 1) as u8)?.range().start;
    let directory_end = DIRECTORY + ENTRY_BYTES * usize::from(count);
    let free = usize::from(u16::from_le_bytes([
        source[FREE_BYTES],
        source[FREE_BYTES + 1],
    ]));
    if free != packed_start - directory_end {
        return Err(UpdateError::Mismatch("data page free-byte count"));
    }
    // Candidate scope retains capacity for another equal-sized row and slot;
    // this is not a DAO available-map threshold or allocation policy.
    let needed = row
        .len()
        .checked_add(ENTRY_BYTES)
        .ok_or(UpdateError::Mismatch("row width"))?;
    if count >= u16::from(u8::MAX)
        || free
            < needed
                .checked_mul(2)
                .ok_or(UpdateError::Mismatch("row capacity"))?
    {
        return Ok(None);
    }
    let start = packed_start
        .checked_sub(row.len())
        .ok_or(UpdateError::Mismatch("row start"))?;
    let new_free = u16::try_from(free - needed).map_err(|_| UpdateError::Mismatch("free bytes"))?;
    let word = u16::try_from(start).map_err(|_| UpdateError::Mismatch("row offset"))?;
    let mut patched = PageImage::from_bytes(*source);
    patched.write_at(PageOffset::new(start as u64), row, budget)?;
    patched.write_at(
        PageOffset::new(directory_end as u64),
        &word.to_le_bytes(),
        budget,
    )?;
    patched.write_at(
        PageOffset::new(FREE_BYTES as u64),
        &new_free.to_le_bytes(),
        budget,
    )?;
    patched.write_at(
        PageOffset::new(SLOT_COUNT as u64),
        &(count + 1).to_le_bytes(),
        budget,
    )?;
    Ok(Some((patched, count as u8)))
}

pub(crate) fn increment_count(
    source: &[u8; PAGE_BYTES],
    observed_rows: u32,
    budget: &mut ResourceBudget,
) -> Result<PageImage, UpdateError> {
    if source[TABLE_COUNT..TABLE_COUNT + 4] != observed_rows.to_le_bytes() {
        return Err(UpdateError::Mismatch("table row count"));
    }
    let count = observed_rows
        .checked_add(1)
        .ok_or(UpdateError::Mismatch("table row count overflow"))?;
    let mut patched = PageImage::from_bytes(*source);
    patched.write_at(
        PageOffset::new(TABLE_COUNT as u64),
        &count.to_le_bytes(),
        budget,
    )?;
    Ok(patched)
}
