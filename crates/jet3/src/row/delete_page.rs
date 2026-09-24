//! Slot-preserving compaction and tombstones from EXP-0162 (EXP-0059/0060 layout).
use crate::{
    PAGE_BYTES, PageImage, PageNumber, PageOffset, ResourceBudget, UpdateError,
    definition::header::ROW_COUNT as TABLE_ROW_COUNT,
    format::data_page_directory::{DIRECTORY_OFFSET, ENTRY_LEN, FREE_SPACE_OFFSET},
    row::{directory::RowDirectory, slot::RowSlot},
};
const TOMBSTONE: u16 = 0xc000;
// EXP-0162/0224 last-row deletion changes tag, free bytes and directory words.
const RELEASED_PAGE_TAG: u8 = 0x09;

pub(crate) enum Deletion {
    Retained(PageImage),
    Released(PageImage),
}

#[cfg(test)]
impl Deletion {
    pub fn image(&self) -> &PageImage {
        match self {
            Self::Retained(image) | Self::Released(image) => image,
        }
    }
}

pub(crate) fn remove(
    page: PageNumber,
    owner: PageNumber,
    source: &[u8; PAGE_BYTES],
    slot: u8,
    budget: &mut ResourceBudget,
) -> Result<Deletion, UpdateError> {
    remove_inner(page, owner, source, slot, false, budget)
}

pub(crate) fn remove_physical(
    page: PageNumber,
    owner: PageNumber,
    source: &[u8; PAGE_BYTES],
    slot: u8,
    budget: &mut ResourceBudget,
) -> Result<Deletion, UpdateError> {
    remove_inner(page, owner, source, slot, true, budget)
}

fn remove_inner(
    page: PageNumber,
    owner: PageNumber,
    source: &[u8; PAGE_BYTES],
    slot: u8,
    physical: bool,
    budget: &mut ResourceBudget,
) -> Result<Deletion, UpdateError> {
    let directory = RowDirectory::validate(page, owner, source, budget)?;
    let range = directory.entry(source, slot)?.range();
    let mut live = 0;
    budget.charge_work_units(2 * u64::from(directory.row_count()))?;
    for ordinal in 0..directory.row_count() {
        let entry = directory.entry(source, ordinal as u8)?;
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
            return Err(UpdateError::Unsupported(
                "page contains an unsupported row slot",
            ));
        }
        live += 1;
    }
    if range.is_empty() {
        return Err(UpdateError::NotFound("live row slot"));
    }
    let lowest = directory
        .entry(source, (directory.row_count() - 1) as u8)?
        .range()
        .start;
    let directory_end = DIRECTORY_OFFSET + ENTRY_LEN * usize::from(directory.row_count());
    let free = usize::from(u16::from_le_bytes([
        source[FREE_SPACE_OFFSET],
        source[FREE_SPACE_OFFSET + 1],
    ]));
    if free != lowest - directory_end {
        return Err(UpdateError::Mismatch("data page free-byte count"));
    }
    let removed = range.len();
    let new_free =
        u16::try_from(free + removed).map_err(|_| UpdateError::Mismatch("free-byte range"))?;
    let mut patched = PageImage::from_bytes(*source);
    if live == 1 {
        patched.write_at(PageOffset::new(0), &[RELEASED_PAGE_TAG], budget)?;
        for ordinal in 0..directory.row_count() {
            patched.write_at(
                PageOffset::new((DIRECTORY_OFFSET + ENTRY_LEN * usize::from(ordinal)) as u64),
                &(TOMBSTONE | PAGE_BYTES as u16).to_le_bytes(),
                budget,
            )?;
        }
        patched.write_at(
            PageOffset::new(FREE_SPACE_OFFSET as u64),
            &new_free.to_le_bytes(),
            budget,
        )?;
        return Ok(Deletion::Released(patched));
    }
    // EXP-0162 moves later row bytes upward and leaves the vacated slack intact.
    budget.charge_work_units((range.start - lowest) as u64)?;
    patched.write_at(
        PageOffset::new((lowest + removed) as u64),
        &source[lowest..range.start],
        budget,
    )?;
    for ordinal in u16::from(slot)..directory.row_count() {
        let entry = directory.entry(source, ordinal as u8)?;
        let start = if ordinal == u16::from(slot) {
            range.end
        } else {
            entry
                .range()
                .start
                .checked_add(removed)
                .filter(|v| *v <= PAGE_BYTES)
                .ok_or(UpdateError::Mismatch("compacted row offset"))?
        };
        let flags = if ordinal == u16::from(slot) {
            TOMBSTONE
        } else {
            RowSlot::read(&entry)?.flags()
        };
        let word =
            u16::try_from(start).map_err(|_| UpdateError::Mismatch("tombstone offset"))? | flags;
        patched.write_at(
            PageOffset::new((DIRECTORY_OFFSET + ENTRY_LEN * usize::from(ordinal)) as u64),
            &word.to_le_bytes(),
            budget,
        )?;
    }
    patched.write_at(
        PageOffset::new(FREE_SPACE_OFFSET as u64),
        &new_free.to_le_bytes(),
        budget,
    )?;
    Ok(Deletion::Retained(patched))
}

pub(crate) fn decrement_count(
    source: &[u8; PAGE_BYTES],
    observed_rows: u32,
    budget: &mut ResourceBudget,
) -> Result<PageImage, UpdateError> {
    let expected = observed_rows.to_le_bytes();
    if source[TABLE_ROW_COUNT..TABLE_ROW_COUNT + 4] != expected {
        return Err(UpdateError::Mismatch("table row count"));
    }
    let count = observed_rows
        .checked_sub(1)
        .ok_or(UpdateError::Mismatch("empty table"))?;
    let mut patched = PageImage::from_bytes(*source);
    patched.write_at(
        PageOffset::new(TABLE_ROW_COUNT as u64),
        &count.to_le_bytes(),
        budget,
    )?;
    Ok(patched)
}
