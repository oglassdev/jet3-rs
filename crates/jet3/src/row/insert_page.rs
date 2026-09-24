//! EXP-0162 appends within the EXP-0305 slot limit and EXP-0060 directory layout,
//! and new-page placement when no existing data page can hold the row.
use crate::{
    DatabaseReader, FileSource, PAGE_BYTES, PageImage, PageImageError, PageNumber, PageOffset,
    ResourceBudget, TableDefinition, UpdateError,
    alloc::patch::{AllocationChange, MapPatches},
    format::{
        data_page_directory::{ENTRY_LEN, ROW_COUNT_OFFSET},
        page_image::MAX_BUILT_ROWS,
    },
    row::{
        data_page::{DataPageEditor, write_free, write_slot},
        directory::RowSlot,
    },
};

impl DataPageEditor<'_> {
    /// EXP-0162 append within the EXP-0305 slot limit; `None` when the row does not fit.
    pub(crate) fn append(
        &self,
        row: &[u8],
        physical: Option<RowSlot>,
        budget: &mut ResourceBudget,
    ) -> Result<Option<(PageImage, u8)>, UpdateError> {
        let count = self.directory.row_count();
        if count == 0 {
            return Ok(None);
        }
        budget.charge_items(u64::from(count))?;
        if self.live_rows(physical.is_some(), "page contains nonordinary row slots")? == 0 {
            return Ok(None);
        }
        let packed = self.packed("data page free-byte count")?;
        let state = physical.unwrap_or(RowSlot::Ordinary);
        state.check_length(row.len())?;
        if state == RowSlot::Deleted {
            return Err(UpdateError::Mismatch("appending a deleted row"));
        }
        let needed = row
            .len()
            .checked_add(ENTRY_LEN)
            .ok_or(UpdateError::Mismatch("row width"))?;
        if count >= MAX_BUILT_ROWS || packed.free < needed {
            return Ok(None);
        }
        let start = packed
            .lowest
            .checked_sub(row.len())
            .ok_or(UpdateError::Mismatch("row start"))?;
        let new_free =
            u16::try_from(packed.free - needed).map_err(|_| UpdateError::Mismatch("free bytes"))?;
        let word =
            u16::try_from(start).map_err(|_| UpdateError::Mismatch("row offset"))? | state.flags();
        let mut patched = PageImage::from_bytes(*self.source);
        patched.write_at(PageOffset::new(start as u64), row, budget)?;
        write_slot(&mut patched, count, word, budget)?;
        write_free(&mut patched, new_free, budget)?;
        patched.write_at(
            PageOffset::new(ROW_COUNT_OFFSET as u64),
            &(count + 1).to_le_bytes(),
            budget,
        )?;
        Ok(Some((patched, count as u8)))
    }
}

pub(crate) fn minimum_length(
    columns: &[crate::ColumnDefinition],
    budget: &mut ResourceBudget,
) -> Result<usize, UpdateError> {
    use crate::{ColumnPhysicalType, ColumnStorageClass, RowColumnLayout};
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
    let mut encoded = [0; PAGE_BYTES];
    minimum_row(&layout[..columns.len()], &mut encoded, budget)
}

/// Encodes the all-null row, the smallest row `layout` can store.
pub(crate) fn minimum_row(
    layout: &[crate::RowColumnLayout],
    output: &mut [u8; PAGE_BYTES],
    budget: &mut ResourceBudget,
) -> Result<usize, UpdateError> {
    let nulls = [crate::RowValue::Null; u8::MAX as usize];
    let nulls = nulls.get(..layout.len()).unwrap_or(&nulls);
    Ok(crate::encode_row(layout, nulls, output, budget)?.get() as usize)
}

// Released-page reuse (EXP-0227) or single EOF allocation: SRC-0020/EXP-0057 map framing and EXP-0051 free bits;
// EXP-0065 Q2 clears the new EOF bit. Row packing uses EXP-0060/EXP-0116.
pub(crate) struct EofInsert {
    pub page: PageNumber,
    pub image: PageImage,
    pub maps: MapPatches,
}

fn page_error(error: PageImageError) -> UpdateError {
    match error {
        PageImageError::Encoding(error) => UpdateError::Resource(error),
        _ => UpdateError::Unsupported("row or owner does not fit new data page"),
    }
}

pub(crate) fn plan_eof_insert(
    database: &mut DatabaseReader<FileSource>,
    definition: &TableDefinition,
    encoded: &[u8],
    minimum: &[u8],
    next_append: PageNumber,
    budget: &mut ResourceBudget,
) -> Result<EofInsert, UpdateError> {
    let reusable = find_released_page(database, definition, budget)?;
    let page = reusable.as_ref().map_or(next_append, |(page, _)| *page);
    // Map references to data pages must remain representable by Jet's u24 locators.
    if page.get() > 0x00ff_ffff {
        return Err(UpdateError::Unsupported("EOF page reference width"));
    }
    let mut builder = crate::DataPageBuilder::new(definition.root(), budget).map_err(page_error)?;
    builder.append_row(encoded, budget).map_err(page_error)?;
    let free = u16::try_from(builder.free_bytes().get())
        .map_err(|_| UpdateError::Mismatch("new data page free bytes"))?;
    // The same candidate policy as initial creation: physically fit a minimum row.
    let available = match builder.clone().append_row(minimum, budget) {
        Ok(_) => true,
        Err(PageImageError::PageFull { .. } | PageImageError::RowSlotsExhausted { .. }) => false,
        Err(error) => return Err(page_error(error)),
    };
    let mut image = builder.finish();
    let [lo, hi] = free.to_le_bytes();
    image.write_at(PageOffset::new(1), &[1, lo, hi], budget)?;
    if let Some((_, before)) = reusable {
        let mut retained = PageImage::from_bytes(before);
        // EXP-0227 resets the physical directory to slot zero while retaining slack.
        retained.write_at(PageOffset::new(0), &image.as_bytes()[..12], budget)?;
        retained.write_at(
            PageOffset::new((crate::PAGE_BYTES - encoded.len()) as u64),
            encoded,
            budget,
        )?;
        image = retained;
    }
    let maps = crate::alloc::patch::plan(
        database,
        definition,
        page,
        AllocationChange::Allocate { available },
        budget,
    )?;
    Ok(EofInsert { page, image, maps })
}

// EXP-0162/0227: reuse a released data page with its first physical row slot.
fn find_released_page(
    database: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    budget: &mut ResourceBudget,
) -> Result<Option<(PageNumber, [u8; PAGE_BYTES])>, UpdateError> {
    let global = crate::alloc::mutation_map::MapBits::load(
        database,
        crate::alloc::mutation_map::global_locator(),
        budget,
    )?;
    let free = global.existing_pages(database.geometry().page_count(), true, budget)?;
    let owner = u32::try_from(table.root().get())
        .map_err(|_| UpdateError::Mismatch("data page owner width"))?;
    let mut candidate = [0; PAGE_BYTES];
    for number in free {
        budget.charge_work_units(1)?;
        database.read_raw_page(number, &mut candidate, budget)?;
        if !crate::alloc::mutation_map::owned_page(&candidate, &[9], owner.to_le_bytes()) {
            continue;
        }
        let directory = crate::row::directory::RowDirectory::validate(
            number,
            table.root(),
            &candidate,
            budget,
        )?;
        let count = directory.row_count();
        if count == 0 {
            continue;
        }
        budget.charge_work_units(u64::from(count))?;
        for ordinal in 0..count {
            let entry = directory.entry(&candidate, ordinal as u8)?;
            if !entry.hidden() || !entry.overflow() || entry.range() != (PAGE_BYTES..PAGE_BYTES) {
                return Err(UpdateError::Mismatch("released page contains a row"));
            }
        }
        let free_bytes = u16::from_le_bytes([candidate[2], candidate[3]]);
        if usize::from(free_bytes) != PAGE_BYTES - 10 - 2 * usize::from(count) {
            return Err(UpdateError::Mismatch("released page free bytes"));
        }
        return Ok(Some((number, candidate)));
    }
    Ok(None)
}
