//! EXP-0051/0057/0062: index ownership and global allocated membership.
use crate::{
    DatabaseReader, FileSource, MapRowLocator, PAGE_BYTES, PageNumber, ResourceBudget, UpdateError,
};

pub(crate) fn load(
    database: &mut DatabaseReader<FileSource>,
    root: PageNumber,
    location: MapRowLocator,
    budget: &mut ResourceBudget,
) -> Result<Vec<PageNumber>, UpdateError> {
    let map = crate::mutation_map::MapBits::load(database, location, budget)?;
    let mapped = map.existing_pages(database.geometry().page_count(), false, budget)?;
    let global = crate::mutation_map::MapBits::load(
        database,
        crate::mutation_map_write::global_locator(),
        budget,
    )?;
    if map.overlaps(&global, budget)? {
        return Err(UpdateError::Mismatch("aliased index and global maps"));
    }
    let mut bytes = [0; PAGE_BYTES];
    let owner =
        u32::try_from(root.get()).map_err(|_| UpdateError::Mismatch("index owner width"))?;
    for page in &mapped {
        budget.charge_items(1)?;
        if global.contains(*page)? {
            return Err(UpdateError::Mismatch("mapped index page is globally free"));
        }
        database.read_raw_page(*page, &mut bytes, budget)?;
        if !matches!(bytes[0], 3 | 4) || bytes[1] != 1 || bytes[4..8] != owner.to_le_bytes() {
            return Err(UpdateError::Mismatch("mapped index page kind or owner"));
        }
    }
    Ok(mapped)
}
