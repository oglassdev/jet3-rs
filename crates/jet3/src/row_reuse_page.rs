//! EXP-0162/0227: reuse a released data page with its first physical row slot.
use crate::{
    DatabaseReader, FileSource, MapRowLocator, PAGE_BYTES, PageNumber, ResourceBudget,
    TableDefinition, UpdateError,
};

pub(crate) fn find(
    database: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    budget: &mut ResourceBudget,
) -> Result<Option<(PageNumber, [u8; PAGE_BYTES])>, UpdateError> {
    let mut bytes = [0; PAGE_BYTES];
    // EXP-0051: global free-page map, whose future bits may extend beyond EOF.
    let locator = MapRowLocator::new(PageNumber::new(1), 0);
    let page = database
        .read_classified_page(locator.page(), &mut bytes, budget)
        .map_err(crate::TableDefinitionError::Page)?;
    let row = crate::locate_usage_map(page, locator, budget).map_err(UpdateError::UsageMap)?;
    let crate::allocation::AllocationMapLayout::Inline { start_page, bitmap } =
        crate::allocation::decode_allocation_map_layout(row.raw(), budget)
            .map_err(UpdateError::Allocation)?
    else {
        return Err(UpdateError::Unsupported("indirect global allocation map"));
    };
    let bits = &row.raw()[bitmap];
    let existing = database
        .geometry()
        .page_count()
        .saturating_sub(start_page.get());
    let count = existing.min(bits.len() as u64 * 8);
    let owner = u32::try_from(table.root().get())
        .map_err(|_| UpdateError::Mismatch("data page owner width"))?;
    let mut candidate = [0; PAGE_BYTES];
    for bit in 0..count {
        budget.charge_work_units(1)?;
        if bits[(bit / 8) as usize] & (1 << (bit % 8)) == 0 {
            continue;
        }
        let number = PageNumber::new(start_page.get() + bit);
        database.read_raw_page(number, &mut candidate, budget)?;
        if candidate[0] != 9 || candidate[1] != 1 || candidate[4..8] != owner.to_le_bytes() {
            continue;
        }
        let directory =
            crate::row_directory::RowDirectory::validate(number, table.root(), &candidate, budget)?;
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
