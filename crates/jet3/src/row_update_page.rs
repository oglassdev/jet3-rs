//! Same-page row replacement from EXP-0060/0061 encoding and EXP-0162 movement.
use crate::row_directory::RowDirectory;
use crate::{PAGE_BYTES, PageImage, PageNumber, PageOffset, ResourceBudget, UpdateError};
const FREE_BYTES: usize = 2;
const DIRECTORY: usize = 10;
const ENTRY_BYTES: usize = 2;
const TOMBSTONE: u16 = 0xc000;
const TABLE_COUNT: usize = 12;

pub(crate) fn replace(
    page: PageNumber,
    owner: PageNumber,
    source: &[u8; PAGE_BYTES],
    slot: u8,
    encoded: &[u8],
    minimum_length: usize,
    budget: &mut ResourceBudget,
) -> Result<PageImage, UpdateError> {
    let directory = RowDirectory::validate(page, owner, source, budget)?;
    let count = directory.row_count();
    let target = directory.entry(source, slot)?;
    if target.hidden() || target.overflow() || target.range().is_empty() {
        return Err(UpdateError::Unsupported(
            "replacement target is not an ordinary live row",
        ));
    }
    budget.charge_work_units(2 * u64::from(count))?;
    for ordinal in 0..count {
        let entry = directory.entry(source, ordinal as u8)?;
        if entry.hidden() && entry.overflow() && entry.range().is_empty() {
            continue;
        }
        if entry.hidden() || entry.overflow() || entry.range().is_empty() {
            return Err(UpdateError::Unsupported(
                "replacement page contains an unsupported row slot",
            ));
        }
    }
    let old = target.range();
    let lowest = directory.entry(source, (count - 1) as u8)?.range().start;
    let directory_end = DIRECTORY + ENTRY_BYTES * usize::from(count);
    let free = usize::from(u16::from_le_bytes([
        source[FREE_BYTES],
        source[FREE_BYTES + 1],
    ]));
    if free != lowest - directory_end {
        return Err(UpdateError::Mismatch("replacement free-byte count"));
    }
    let new_free = free
        .checked_add(old.len())
        .and_then(|space| space.checked_sub(encoded.len()))
        .ok_or(UpdateError::Unsupported(
            "replacement exceeds contiguous page space",
        ))?;
    let retained = minimum_length
        .checked_add(ENTRY_BYTES)
        .ok_or(UpdateError::Mismatch("minimum row width"))?;
    // Candidate policy preserves physical capacity for another minimum row and slot.
    if encoded.is_empty() || new_free < retained || count > u16::from(u8::MAX) {
        return Err(UpdateError::Unsupported(
            "replacement lacks retained row capacity",
        ));
    }
    let new_start = old
        .end
        .checked_sub(encoded.len())
        .ok_or(UpdateError::Mismatch("replacement row boundary"))?;
    let mut image = PageImage::from_bytes(*source);
    budget.charge_work_units((old.start - lowest) as u64)?;
    image.write_at(
        PageOffset::new((directory_end + new_free) as u64),
        &source[lowest..old.start],
        budget,
    )?;
    image.write_at(PageOffset::new(new_start as u64), encoded, budget)?;
    for ordinal in u16::from(slot)..count {
        let entry = directory.entry(source, ordinal as u8)?;
        let offset = entry
            .range()
            .start
            .checked_add(old.len())
            .and_then(|v| v.checked_sub(encoded.len()))
            .filter(|v| *v <= PAGE_BYTES)
            .ok_or(UpdateError::Mismatch("replacement slot offset"))?;
        let word = u16::try_from(offset)
            .map_err(|_| UpdateError::Mismatch("replacement slot width"))?
            | if entry.hidden() { TOMBSTONE } else { 0 };
        image.write_at(
            PageOffset::new((DIRECTORY + ENTRY_BYTES * usize::from(ordinal)) as u64),
            &word.to_le_bytes(),
            budget,
        )?;
    }
    let free = u16::try_from(new_free)
        .map_err(|_| UpdateError::Mismatch("replacement free-byte width"))?;
    image.write_at(
        PageOffset::new(FREE_BYTES as u64),
        &free.to_le_bytes(),
        budget,
    )?;
    Ok(image)
}

pub(crate) fn check_count(
    source: &[u8; PAGE_BYTES],
    observed: u32,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    budget.charge_work_units(4)?;
    if source[TABLE_COUNT..TABLE_COUNT + 4] != observed.to_le_bytes() {
        return Err(UpdateError::Mismatch("table row count"));
    }
    Ok(())
}
