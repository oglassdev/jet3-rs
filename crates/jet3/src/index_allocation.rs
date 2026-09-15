//! EXP-0051/0057/0062: inline index ownership and global allocated membership.
use crate::{
    AllocationMap, DatabaseReader, FileSource, MapRowLocator, PAGE_BYTES, PageNumber,
    ResourceBudget, UpdateError,
};

pub(crate) fn load(
    database: &mut DatabaseReader<FileSource>,
    root: PageNumber,
    location: MapRowLocator,
    budget: &mut ResourceBudget,
) -> Result<Vec<PageNumber>, UpdateError> {
    let mut bytes = [0; PAGE_BYTES];
    let page = database
        .read_classified_page(location.page(), &mut bytes, budget)
        .map_err(crate::TableDefinitionError::Page)?;
    let row = crate::locate_usage_map(page, location, budget).map_err(UpdateError::UsageMap)?;
    let AllocationMap::Inline(map) =
        crate::decode_allocation_map(row.raw(), budget).map_err(UpdateError::Allocation)?
    else {
        return Err(UpdateError::Unsupported("indirect index map"));
    };
    let mut allocated = map.allocated_pages(database.geometry());
    let mut mapped = Vec::new();
    while let Some(page) = allocated
        .next_page(budget)
        .map_err(UpdateError::Allocation)?
    {
        crate::page_edits::reserve(&mut mapped, 1, budget)?;
        mapped.push(page);
    }
    let mut global_bytes = [0; PAGE_BYTES];
    let global = MapRowLocator::new(PageNumber::new(1), 0);
    let page = database
        .read_classified_page(global.page(), &mut global_bytes, budget)
        .map_err(crate::TableDefinitionError::Page)?;
    let row = crate::locate_usage_map(page, global, budget).map_err(UpdateError::UsageMap)?;
    let crate::allocation::AllocationMapLayout::Inline { start_page, bitmap } =
        crate::allocation::decode_allocation_map_layout(row.raw(), budget)
            .map_err(UpdateError::Allocation)?
    else {
        return Err(UpdateError::Unsupported("indirect global map"));
    };
    let free = &row.raw()[bitmap];
    let owner =
        u32::try_from(root.get()).map_err(|_| UpdateError::Mismatch("index owner width"))?;
    for page in &mapped {
        budget.charge_items(1)?;
        let bit = page
            .get()
            .checked_sub(start_page.get())
            .filter(|bit| bit / 8 < free.len() as u64)
            .ok_or(UpdateError::Unsupported("index page outside global map"))?;
        if free[(bit / 8) as usize] & (1 << (bit % 8)) != 0 {
            return Err(UpdateError::Mismatch("mapped index page is globally free"));
        }
        database.read_raw_page(*page, &mut bytes, budget)?;
        if !matches!(bytes[0], 3 | 4) || bytes[1] != 1 || bytes[4..8] != owner.to_le_bytes() {
            return Err(UpdateError::Mismatch("mapped index page kind or owner"));
        }
    }
    Ok(mapped)
}
