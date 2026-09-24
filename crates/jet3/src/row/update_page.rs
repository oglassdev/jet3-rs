//! Same-page row replacement from EXP-0060/0061 encoding and EXP-0162 movement.
use crate::{
    PAGE_BYTES, PageImage, PageNumber, PageOffset, ResourceBudget, UpdateError,
    row::{directory::RowDirectory, slot::RowSlot},
};
const FREE_BYTES: usize = 2;
const DIRECTORY: usize = 10;
const ENTRY_BYTES: usize = 2;
const TABLE_COUNT: usize = 12;

pub(crate) fn replace(
    page: PageNumber,
    owner: PageNumber,
    source: &[u8; PAGE_BYTES],
    slot: u8,
    encoded: &[u8],
    budget: &mut ResourceBudget,
) -> Result<PageImage, UpdateError> {
    replace_inner(page, owner, source, slot, encoded, None, budget)?.ok_or(
        UpdateError::Unsupported("replacement exceeds contiguous page space"),
    )
}

pub(crate) fn replace_physical(
    page: PageNumber,
    owner: PageNumber,
    source: &[u8; PAGE_BYTES],
    slot: u8,
    encoded: &[u8],
    state: RowSlot,
    budget: &mut ResourceBudget,
) -> Result<Option<PageImage>, UpdateError> {
    replace_inner(page, owner, source, slot, encoded, Some(state), budget)
}

fn replace_inner(
    page: PageNumber,
    owner: PageNumber,
    source: &[u8; PAGE_BYTES],
    slot: u8,
    encoded: &[u8],
    state: Option<RowSlot>,
    budget: &mut ResourceBudget,
) -> Result<Option<PageImage>, UpdateError> {
    let directory = RowDirectory::validate(page, owner, source, budget)?;
    let count = directory.row_count();
    let target = directory.entry(source, slot)?;
    let previous = RowSlot::read(&target)?;
    if previous == RowSlot::Deleted || (state.is_none() && previous != RowSlot::Ordinary) {
        return Err(UpdateError::Unsupported(
            "replacement target is not an ordinary live row",
        ));
    }
    budget.charge_work_units(2 * u64::from(count))?;
    for ordinal in 0..count {
        let entry = directory.entry(source, ordinal as u8)?;
        if state.is_some() {
            RowSlot::read(&entry)?;
            continue;
        }
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
    let Some(new_free) = free
        .checked_add(old.len())
        .and_then(|space| space.checked_sub(encoded.len()))
    else {
        return Ok(None);
    };
    let state = state.unwrap_or(RowSlot::Ordinary);
    state.check_length(encoded.len())?;
    if encoded.is_empty() {
        return Err(UpdateError::Unsupported("empty replacement row"));
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
        let flags = if ordinal == u16::from(slot) {
            state.flags()
        } else {
            RowSlot::read(&entry)?.flags()
        };
        let word = u16::try_from(offset)
            .map_err(|_| UpdateError::Mismatch("replacement slot width"))?
            | flags;
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
    Ok(Some(image))
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
