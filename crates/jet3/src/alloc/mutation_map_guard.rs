//! Reject shared bitmap storage before changing any allocation role (EXP-0057/0077).
use crate::{
    DatabaseReader, FileSource, MapRowLocator, ResourceBudget, UpdateError,
    alloc::mutation_map::{MapBits, PendingMap, global_locator},
    write::page_edits::reserve,
};

pub(crate) fn validate(
    database: &mut DatabaseReader<FileSource>,
    maps: &[PendingMap],
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let mut roots = Vec::new();
    {
        let mut catalog = database.catalog(budget)?;
        while let Some(record) = catalog.next_record()? {
            if let Some(root) = record.table_definition() {
                reserve(&mut roots, 1, catalog.budget_mut())?;
                roots.push(root);
            }
        }
    }
    let mut seen = Vec::new();
    reserve(&mut seen, maps.len(), budget)?;
    seen.resize(maps.len(), false);
    let global = MapBits::load(database, global_locator(), budget)?;
    for map in maps {
        for page in std::iter::once(map.bits.locator.page())
            .chain(map.bits.spans.iter().map(|span| span.page))
        {
            budget.charge_items(1)?;
            if global.contains(page)? {
                return Err(UpdateError::Mismatch(
                    "allocation metadata is globally free",
                ));
            }
        }
    }
    inspect(&global, maps, &mut seen, true, budget)?;
    for root in roots {
        let table = database.table_definition(root, budget)?;
        let mut locators = Vec::new();
        reserve(
            &mut locators,
            2 + table.physical_indexes().len() + 2 * table.long_value_maps().len(),
            budget,
        )?;
        locators.extend([table.maps().owned(), table.maps().available()]);
        locators.extend(
            table
                .physical_indexes()
                .iter()
                .map(|index| MapRowLocator::new(index.usage_map().page(), index.usage_map().row())),
        );
        for map in table.long_value_maps() {
            locators.extend([map.owned(), map.available()]);
        }
        for locator in locators {
            let bits = MapBits::load(database, locator, budget)?;
            inspect(&bits, maps, &mut seen, false, budget)?;
        }
    }
    if seen.iter().any(|seen| !seen) {
        return Err(UpdateError::Mismatch("unreferenced mutation map"));
    }
    Ok(())
}

fn inspect(
    bits: &MapBits,
    maps: &[PendingMap],
    seen: &mut [bool],
    global: bool,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    for (position, map) in maps.iter().enumerate() {
        if bits.locator == map.bits.locator {
            if seen[position] {
                return Err(UpdateError::Mismatch("allocation map shared between roles"));
            }
            seen[position] = true;
        } else if bits.overlaps(&map.bits, budget)? {
            return Err(UpdateError::Mismatch(
                "allocation bitmap shared between roles",
            ));
        }
        if !global {
            for page in std::iter::once(map.bits.locator.page())
                .chain(map.bits.spans.iter().map(|span| span.page))
            {
                budget.charge_items(1)?;
                if bits.contains(page)? {
                    return Err(UpdateError::Mismatch(
                        "allocation metadata claimed as content",
                    ));
                }
            }
        }
    }
    Ok(())
}
