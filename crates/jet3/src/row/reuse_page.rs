//! EXP-0162/0227: reuse a released data page with its first physical row slot.
use crate::{
    DatabaseReader, FileSource, PAGE_BYTES, PageNumber, ResourceBudget, TableDefinition,
    UpdateError,
};

pub(crate) fn find(
    database: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    budget: &mut ResourceBudget,
) -> Result<Option<(PageNumber, [u8; PAGE_BYTES])>, UpdateError> {
    let global = crate::alloc::mutation_map::MapBits::load(
        database,
        crate::alloc::mutation_map_write::global_locator(),
        budget,
    )?;
    let free = global.existing_pages(database.geometry().page_count(), true, budget)?;
    let owner = u32::try_from(table.root().get())
        .map_err(|_| UpdateError::Mismatch("data page owner width"))?;
    let mut candidate = [0; PAGE_BYTES];
    for number in free {
        budget.charge_work_units(1)?;
        database.read_raw_page(number, &mut candidate, budget)?;
        if candidate[0] != 9 || candidate[1] != 1 || candidate[4..8] != owner.to_le_bytes() {
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
