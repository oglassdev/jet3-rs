//! EXP-0077/0266: catalog properties have exclusive per-column LVAL ownership.
use crate::{
    ColumnOrdinal, DatabaseReader, MapRowLocator, ReadAt, ResourceBudget, TableDefinition,
    WriteError, alloc::mutation_map::MapBits, write::page_edits::reserve,
};

pub(crate) fn load<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    catalog: &TableDefinition,
    column: ColumnOrdinal,
    budget: &mut ResourceBudget,
) -> Result<MapBits, WriteError> {
    let maps = catalog
        .long_value_maps()
        .iter()
        .find(|map| map.column() == column)
        .ok_or(WriteError::Mismatch("catalog property allocation maps"))?;
    let owned = MapBits::load(database, maps.owned(), budget)?;
    let available = MapBits::load(database, maps.available(), budget)?;
    let global = MapBits::load(
        database,
        crate::alloc::mutation_map::global_locator(),
        budget,
    )?;
    if owned.overlaps(&available, budget)?
        || owned.overlaps(&global, budget)?
        || available.overlaps(&global, budget)?
    {
        return Err(WriteError::Mismatch("aliased property allocation maps"));
    }
    let pages = owned.existing_pages(database.geometry().page_count(), false, budget)?;
    for page in available.existing_pages(database.geometry().page_count(), false, budget)? {
        if !owned.contains(page)? {
            return Err(WriteError::Mismatch("available property page not owned"));
        }
    }
    for &page in &pages {
        if global.contains(page)? {
            return Err(WriteError::Mismatch("owned property page globally free"));
        }
    }
    for map in [&owned, &available] {
        metadata(map, &owned, &global, budget)?;
    }
    let mut roots = Vec::new();
    {
        let mut records = database.catalog(budget)?;
        while let Some(record) = records.next_record()? {
            if let Some(root) = record.table_definition() {
                reserve(&mut roots, 1, records.budget_mut())?;
                roots.push(root);
            }
        }
    }
    for root in roots {
        let table = database.table_definition(root, budget)?;
        let mut locators = Vec::new();
        reserve(
            &mut locators,
            2 + table.physical_indexes().len() + table.long_value_maps().len() * 2,
            budget,
        )?;
        locators.extend([table.maps().owned(), table.maps().available()]);
        for index in table.physical_indexes() {
            locators.push(MapRowLocator::new(
                index.usage_map().page(),
                index.usage_map().row(),
            ));
        }
        for map in table.long_value_maps() {
            if root != catalog.root() || map.column() != column {
                locators.extend([map.owned(), map.available()]);
            }
        }
        for locator in locators {
            let map = MapBits::load(database, locator, budget)?;
            if owned.overlaps(&map, budget)? || available.overlaps(&map, budget)? {
                return Err(WriteError::Mismatch(
                    "property map aliases another object map",
                ));
            }
            metadata(&map, &owned, &global, budget)?;
            budget.charge_work_units(pages.len() as u64)?;
            for &page in &pages {
                if map.contains(page)? {
                    return Err(WriteError::Mismatch(
                        "property page belongs to another object",
                    ));
                }
            }
        }
    }
    Ok(owned)
}

fn metadata(
    map: &MapBits,
    owned: &MapBits,
    global: &MapBits,
    budget: &mut ResourceBudget,
) -> Result<(), WriteError> {
    for page in std::iter::once(map.locator.page()).chain(map.spans.iter().map(|span| span.page)) {
        budget.charge_work_units(1)?;
        if owned.contains(page)? || global.contains(page)? {
            return Err(WriteError::Mismatch(
                "property map metadata used as payload or globally free",
            ));
        }
    }
    Ok(())
}
