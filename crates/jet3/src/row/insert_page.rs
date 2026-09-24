//! EXP-0162 appends within the EXP-0305 slot limit and EXP-0060 directory layout.
use crate::{
    PAGE_BYTES, PageImage, PageNumber, PageOffset, ResourceBudget, UpdateError,
    definition::header::ROW_COUNT as TABLE_ROW_COUNT,
    format::{
        data_page_directory::{DIRECTORY_OFFSET, ENTRY_LEN, FREE_SPACE_OFFSET, ROW_COUNT_OFFSET},
        page_image::MAX_BUILT_ROWS,
    },
    row::{directory::RowDirectory, slot::RowSlot},
};

pub(crate) fn append(
    page: PageNumber,
    owner: PageNumber,
    source: &[u8; PAGE_BYTES],
    row: &[u8],
    budget: &mut ResourceBudget,
) -> Result<Option<(PageImage, u8)>, UpdateError> {
    append_inner(page, owner, source, row, None, budget)
}

pub(crate) fn append_physical(
    page: PageNumber,
    owner: PageNumber,
    source: &[u8; PAGE_BYTES],
    row: &[u8],
    state: RowSlot,
    budget: &mut ResourceBudget,
) -> Result<Option<(PageImage, u8)>, UpdateError> {
    append_inner(page, owner, source, row, Some(state), budget)
}

fn append_inner(
    page: PageNumber,
    owner: PageNumber,
    source: &[u8; PAGE_BYTES],
    row: &[u8],
    state: Option<RowSlot>,
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
        if state.is_some() {
            if RowSlot::read(&entry)? != RowSlot::Deleted {
                live += 1;
            }
            continue;
        }
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
    let directory_end = DIRECTORY_OFFSET + ENTRY_LEN * usize::from(count);
    let free = usize::from(u16::from_le_bytes([
        source[FREE_SPACE_OFFSET],
        source[FREE_SPACE_OFFSET + 1],
    ]));
    if free != packed_start - directory_end {
        return Err(UpdateError::Mismatch("data page free-byte count"));
    }
    let state = state.unwrap_or(RowSlot::Ordinary);
    state.check_length(row.len())?;
    if state == RowSlot::Deleted {
        return Err(UpdateError::Mismatch("appending a deleted row"));
    }
    let needed = row
        .len()
        .checked_add(ENTRY_LEN)
        .ok_or(UpdateError::Mismatch("row width"))?;
    if count >= MAX_BUILT_ROWS || free < needed {
        return Ok(None);
    }
    let start = packed_start
        .checked_sub(row.len())
        .ok_or(UpdateError::Mismatch("row start"))?;
    let new_free = u16::try_from(free - needed).map_err(|_| UpdateError::Mismatch("free bytes"))?;
    let word =
        u16::try_from(start).map_err(|_| UpdateError::Mismatch("row offset"))? | state.flags();
    let mut patched = PageImage::from_bytes(*source);
    patched.write_at(PageOffset::new(start as u64), row, budget)?;
    patched.write_at(
        PageOffset::new(directory_end as u64),
        &word.to_le_bytes(),
        budget,
    )?;
    patched.write_at(
        PageOffset::new(FREE_SPACE_OFFSET as u64),
        &new_free.to_le_bytes(),
        budget,
    )?;
    patched.write_at(
        PageOffset::new(ROW_COUNT_OFFSET as u64),
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
    if source[TABLE_ROW_COUNT..TABLE_ROW_COUNT + 4] != observed_rows.to_le_bytes() {
        return Err(UpdateError::Mismatch("table row count"));
    }
    let count = observed_rows
        .checked_add(1)
        .ok_or(UpdateError::Mismatch("table row count overflow"))?;
    let mut patched = PageImage::from_bytes(*source);
    patched.write_at(
        PageOffset::new(TABLE_ROW_COUNT as u64),
        &count.to_le_bytes(),
        budget,
    )?;
    Ok(patched)
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

pub(crate) fn minimum_length(
    columns: &[crate::ColumnDefinition],
    budget: &mut ResourceBudget,
) -> Result<usize, UpdateError> {
    use crate::{ColumnPhysicalType, ColumnStorageClass, RowColumnLayout, RowValue};
    if columns.len() > u8::MAX as usize {
        return Err(UpdateError::Unsupported("row column count"));
    }
    let mut layout = [RowColumnLayout::new(
        ColumnPhysicalType::Long,
        ColumnStorageClass::Fixed { offset: 0 },
        4,
    ); u8::MAX as usize];
    budget.charge_items(columns.len() as u64)?;
    for (entry, column) in layout.iter_mut().zip(columns) {
        *entry = column.into();
    }
    let nulls = [RowValue::Null; u8::MAX as usize];
    let mut encoded = [0; PAGE_BYTES];
    Ok(crate::encode_row(
        &layout[..columns.len()],
        &nulls[..columns.len()],
        &mut encoded,
        budget,
    )?
    .get() as usize)
}
