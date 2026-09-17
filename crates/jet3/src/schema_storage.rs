//! Releasing selected table storage while retaining shared map pages (EXP-0057/0077/0297).
use crate::mutation_map::MapBits;
use crate::page_edits::{PageEdits, reserve};
use crate::{
    DatabaseReader, FileSource, MapRowLocator, PageNumber, ResourceBudget, TableDefinition,
    UpdateError,
};

pub(crate) fn locators(
    table: &TableDefinition,
    budget: &mut ResourceBudget,
) -> Result<Vec<MapRowLocator>, UpdateError> {
    let mut values = Vec::new();
    reserve(
        &mut values,
        2 + table.physical_indexes().len() + 2 * table.long_value_maps().len(),
        budget,
    )?;
    values.extend([table.maps().owned(), table.maps().available()]);
    values.extend(
        table
            .physical_indexes()
            .iter()
            .map(|index| MapRowLocator::new(index.usage_map().page(), index.usage_map().row())),
    );
    values.extend(
        table
            .long_value_maps()
            .iter()
            .flat_map(|map| [map.owned(), map.available()]),
    );
    Ok(values)
}

pub(crate) fn release_table(
    database: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    budget: &mut ResourceBudget,
) -> Result<PageEdits, UpdateError> {
    crate::row_mutation_graph::RowGraph::load(database, table, None, budget)?;
    crate::long_value_mutation::LongValues::load(database, table, None, budget)?;
    if !table.physical_indexes().is_empty() {
        crate::index_mutation::load(database, table, budget)?;
    }
    let mut metadata = Vec::new();
    let mut content = Vec::new();
    let mut maps: Vec<MapBits> = Vec::new();
    let global = MapBits::load(
        database,
        crate::mutation_map_write::global_locator(),
        budget,
    )?;
    let selected = locators(table, budget)?;
    reserve(&mut maps, selected.len(), budget)?;
    for locator in selected {
        let map = MapBits::load(database, locator, budget)?;
        if locator == global.locator || map.overlaps(&global, budget)? {
            return Err(UpdateError::Mismatch(
                "schema map aliases global allocation",
            ));
        }
        for previous in &maps {
            if map.locator == previous.locator || map.overlaps(previous, budget)? {
                return Err(UpdateError::Mismatch("schema map aliases another role"));
            }
        }
        push(&mut metadata, locator.page(), budget)?;
        for span in &map.spans {
            push(&mut metadata, span.page, budget)?;
        }
        for member in map.existing_pages(database.geometry().page_count(), false, budget)? {
            push(&mut content, member, budget)?;
        }
        maps.push(map);
    }
    for &page in table.pages() {
        push(&mut content, page, budget)?;
    }
    let mut roots = Vec::new();
    {
        let mut catalog = database.catalog(budget)?;
        while let Some(record) = catalog.next_record()? {
            if let Some(root) = record.table_definition()
                && root != table.root()
            {
                reserve(&mut roots, 1, catalog.budget_mut())?;
                roots.push(root);
            }
        }
    }
    for root in roots {
        let other = database.table_definition(root, budget)?;
        for page in other.pages() {
            budget.charge_items(content.len() as u64 + metadata.len() as u64)?;
            if content.contains(page) || metadata.contains(page) {
                return Err(UpdateError::Mismatch(
                    "schema storage aliases another definition",
                ));
            }
        }
        for locator in locators(&other, budget)? {
            let other = MapBits::load(database, locator, budget)?;
            budget.charge_items(content.len() as u64)?;
            if content.contains(&locator.page()) {
                return Err(UpdateError::Mismatch("schema content aliases another map"));
            }
            for map in &maps {
                if map.locator == other.locator || map.overlaps(&other, budget)? {
                    return Err(UpdateError::Mismatch("schema maps shared between objects"));
                }
            }
            for page in other.existing_pages(database.geometry().page_count(), false, budget)? {
                budget.charge_items(content.len() as u64 + metadata.len() as u64)?;
                if content.contains(&page) || metadata.contains(&page) {
                    return Err(UpdateError::Mismatch(
                        "schema storage claimed by another object",
                    ));
                }
            }
        }
    }
    let mut edits = PageEdits::new(database.geometry().page_count());
    for map in maps {
        for page in map.existing_pages(database.geometry().page_count(), false, budget)? {
            edits.map_bit(database, map.locator, page, true, false, budget)?;
        }
    }
    for page in content {
        edits.map_bit(
            database,
            crate::mutation_map_write::global_locator(),
            page,
            false,
            true,
            budget,
        )?;
    }
    Ok(edits)
}

fn push(
    values: &mut Vec<PageNumber>,
    page: PageNumber,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    budget.charge_items(values.len() as u64)?;
    if !values.contains(&page) {
        reserve(values, 1, budget)?;
        values.push(page);
    }
    Ok(())
}
