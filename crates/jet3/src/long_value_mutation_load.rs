//! Validate EXP-0061 references against EXP-0077/0234 per-column ownership.
//! EXP-0234 supplies release/reuse forms; EXP-0235 permits deleted siblings.
use super::{LongValues, OWNER, PayloadPage, map::Bitmap, reserve};
use crate::{
    ColumnOrdinal, DatabaseReader, FileSource, LongValue, LongValueReference, MapRowLocator,
    PAGE_BYTES, PageImage, PageNumber, ResourceBudget, RowLocator, TableDefinition, TextCodePage,
    UpdateError, ValueKind,
};

pub(super) fn load(
    database: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    selected: Option<(RowLocator, Option<&[ColumnOrdinal]>)>,
    budget: &mut ResourceBudget,
) -> Result<LongValues, UpdateError> {
    let mut result = LongValues {
        maps: Vec::new(),
        pages: Vec::new(),
        first_append: database.geometry().page_count(),
        append_count: 0,
    };
    if table.long_value_maps().is_empty() {
        return Ok(result);
    }
    reserve(&mut result.maps, table.long_value_maps().len(), budget)?;
    result.maps.extend_from_slice(table.long_value_maps());
    let global_locator = MapRowLocator::new(PageNumber::new(1), 0);
    let global = Bitmap::load(database, global_locator, budget)?;
    let mut map_locators = Vec::new();
    reserve(&mut map_locators, result.maps.len() * 2, budget)?;
    for (column, map) in result.maps.iter().enumerate() {
        for locator in [map.owned(), map.available()] {
            budget.charge_work_units(map_locators.len() as u64)?;
            if locator == global_locator || map_locators.contains(&locator) {
                return Err(UpdateError::Mismatch("aliased long-value allocation map"));
            }
            map_locators.push(locator);
        }
        let owned = Bitmap::load(database, map.owned(), budget)?;
        let available = Bitmap::load(database, map.available(), budget)?;
        let owned_pages = owned.existing_pages(result.first_append, false, budget)?;
        for page in available.existing_pages(result.first_append, false, budget)? {
            if !owned.contains(page)? {
                return Err(UpdateError::Mismatch("available long-value page not owned"));
            }
        }
        for page in owned_pages {
            if global.contains(page)? {
                return Err(UpdateError::Mismatch(
                    "owned long-value page is globally free",
                ));
            }
            let mut image = [0; PAGE_BYTES];
            database.read_raw_page(page, &mut image, budget)?;
            live_slots(
                page,
                &image,
                true,
                reserved_property(table, map.column()),
                budget,
            )?;
            reserve(&mut result.pages, 1, budget)?;
            result.pages.push(PayloadPage {
                page,
                original_column: Some(column),
                column: Some(column),
                original_available: available.contains(page)?,
                image: PageImage::from_bytes(image),
                storage: None,
                seen: [0; 4],
                removed: [0; 4],
                changed: false,
            });
        }
    }
    for page in global.existing_pages(result.first_append, true, budget)? {
        let mut bytes = [0; PAGE_BYTES];
        database.read_raw_page(page, &mut bytes, budget)?;
        if !matches!(bytes[0], 1 | 9) || bytes[1] != 1 || bytes[4..8] != *b"LVAL" {
            continue;
        }
        live_slots(page, &bytes, false, false, budget)?;
        reserve(&mut result.pages, 1, budget)?;
        result.pages.push(PayloadPage {
            page,
            original_column: None,
            column: None,
            original_available: false,
            image: PageImage::from_bytes(bytes),
            storage: None,
            seen: [0; 4],
            removed: [0; 4],
            changed: false,
        });
    }
    budget.charge_work_units(
        (result.pages.len() as u64).saturating_mul((result.pages.len().max(1).ilog2() + 1) as u64),
    )?;
    result.pages.sort_unstable_by_key(|p| p.page);
    if result
        .pages
        .windows(2)
        .any(|pair| pair[0].page == pair[1].page)
    {
        return Err(UpdateError::Mismatch(
            "overlapping long-value page ownership",
        ));
    }
    for locator in &map_locators {
        budget.charge_work_units((result.pages.len().max(1).ilog2() + 1) as u64)?;
        if result
            .pages
            .binary_search_by_key(&locator.page(), |p| p.page)
            .is_ok()
        {
            return Err(UpdateError::Mismatch(
                "long-value map page contains payload fragments",
            ));
        }
    }
    exclude_other_ownership(database, table, &map_locators, &result.pages, budget)?;
    references(database, table, selected, &mut result, budget)?;
    Ok(result)
}

fn live_slots(
    page: PageNumber,
    image: &[u8; PAGE_BYTES],
    owned: bool,
    reserved_empty: bool,
    budget: &mut ResourceBudget,
) -> Result<[u64; 4], UpdateError> {
    if image[1] != 1 || (owned && image[0] != 1) {
        return Err(UpdateError::Mismatch("owned long-value page kind"));
    }
    let directory = crate::row_directory::RowDirectory::validate(page, OWNER, image, budget)?;
    let count = directory.row_count();
    if count == 0 {
        // EXP-0091 retains an empty allocated MSysObjects.LvProp bootstrap page.
        if reserved_empty && image[2..4] == ((PAGE_BYTES - 10) as u16).to_le_bytes() {
            return Ok([0; 4]);
        }
        return Err(UpdateError::Mismatch("empty long-value directory"));
    }
    let lowest = directory.entry(image, (count - 1) as u8)?.range().start;
    if usize::from(u16::from_le_bytes([image[2], image[3]])) != lowest - 10 - 2 * usize::from(count)
    {
        return Err(UpdateError::Mismatch("long-value free-byte count"));
    }
    let mut live = [0; 4];
    budget.charge_items(u64::from(count))?;
    for slot in 0..count {
        let entry = directory.entry(image, slot as u8)?;
        if entry.hidden() && entry.overflow() && entry.range().is_empty() {
            continue;
        }
        if entry.hidden() || entry.overflow() || entry.range().is_empty() || image[0] != 1 {
            return Err(UpdateError::Unsupported("long-value directory flags"));
        }
        live[usize::from(slot) / 64] |= 1 << (slot % 64);
    }
    if owned && live == [0; 4] {
        return Err(UpdateError::Mismatch(
            "owned long-value page has no live fragments",
        ));
    }
    Ok(live)
}

fn exclude_other_ownership(
    database: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    target_maps: &[MapRowLocator],
    payloads: &[PayloadPage],
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
    for root in roots {
        let definition = database.table_definition(root, budget)?;
        let mut maps = Vec::new();
        reserve(
            &mut maps,
            2 + definition.physical_indexes().len() + definition.long_value_maps().len() * 2,
            budget,
        )?;
        maps.extend([definition.maps().owned(), definition.maps().available()]);
        for index in definition.physical_indexes() {
            maps.push(MapRowLocator::new(
                index.usage_map().page(),
                index.usage_map().row(),
            ));
        }
        if root != table.root() {
            for map in definition.long_value_maps() {
                maps.extend([map.owned(), map.available()]);
            }
        }
        for locator in maps {
            budget.charge_work_units(
                target_maps.len() as u64 + (payloads.len().max(1).ilog2() + 1) as u64,
            )?;
            if target_maps.contains(&locator) {
                return Err(UpdateError::Mismatch(
                    "long-value map aliases another object map",
                ));
            }
            if payloads
                .binary_search_by_key(&locator.page(), |p| p.page)
                .is_ok()
            {
                return Err(UpdateError::Mismatch(
                    "object map page contains long-value fragments",
                ));
            }
            let map = Bitmap::load(database, locator, budget)?;
            for page in map.existing_pages(database.geometry().page_count(), false, budget)? {
                budget.charge_work_units((payloads.len().max(1).ilog2() + 1) as u64)?;
                if payloads.binary_search_by_key(&page, |p| p.page).is_ok() {
                    return Err(UpdateError::Mismatch(
                        "long-value page belongs to another object",
                    ));
                }
            }
        }
    }
    Ok(())
}

fn references(
    database: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    selected: Option<(RowLocator, Option<&[ColumnOrdinal]>)>,
    result: &mut LongValues,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let mut pending: Vec<(usize, LongValueReference)> = Vec::new();
    reserve(&mut pending, result.maps.len(), budget)?;
    let mut cursor = database.rows(table, budget)?;
    let mut count = 0_u32;
    loop {
        cursor
            .owned
            .budget_mut()
            .charge_items(result.maps.len() as u64)?;
        let Some(mut row) = cursor.next_row()? else {
            break;
        };
        let remove_row = selected.is_some_and(|(locator, _)| locator == row.locator());
        pending.clear();
        for (column, map) in result.maps.iter().enumerate() {
            let value = row
                .value(map.column(), TextCodePage::Windows1252)?
                .ok_or(UpdateError::NotFound("long-value field"))?;
            if let ValueKind::LongValue(LongValue::External(reference)) = value.kind() {
                pending.push((column, *reference));
            }
        }
        for &(column, reference) in &pending {
            let mut stream = cursor.long_value(reference)?;
            loop {
                stream
                    .budget_mut()
                    .charge_work_units((result.pages.len().max(1).ilog2() + 1) as u64)?;
                let Some(chunk) = stream.next_chunk()? else {
                    break;
                };
                let locator = chunk.locator();
                let index = result
                    .pages
                    .binary_search_by_key(&locator.page(), |p| p.page)
                    .map_err(|_| {
                        UpdateError::Mismatch("long-value reference outside column map")
                    })?;
                let page = &mut result.pages[index];
                if page.column != Some(column) {
                    return Err(UpdateError::Mismatch(
                        "long-value reference has wrong column owner",
                    ));
                }
                if page
                    .storage
                    .is_some_and(|storage| storage != reference.storage())
                {
                    return Err(UpdateError::Unsupported(
                        "mixed single and chained fragment page",
                    ));
                }
                page.storage = Some(reference.storage());
                let word = usize::from(locator.slot()) / 64;
                let mask = 1 << (locator.slot() % 64);
                if page.seen[word] & mask != 0 {
                    return Err(UpdateError::Mismatch("aliased long-value fragment"));
                }
                page.seen[word] |= mask;
                if remove_row
                    && selected.is_some_and(|(_, selected_column)| {
                        selected_column
                            .is_none_or(|values| values.contains(&result.maps[column].column()))
                    })
                {
                    page.removed[word] |= mask;
                }
            }
        }
        count = count
            .checked_add(1)
            .ok_or(UpdateError::Mismatch("table row count overflow"))?;
    }
    drop(cursor);
    if count != table.row_count() {
        return Err(UpdateError::Mismatch("table row count"));
    }
    for page in &result.pages {
        if page.column.is_some()
            && page.seen
                != live_slots(
                    page.page,
                    page.image.as_bytes(),
                    true,
                    page.column.is_some_and(|column| {
                        reserved_property(table, result.maps[column].column())
                    }),
                    budget,
                )?
        {
            return Err(UpdateError::Mismatch(
                "unreferenced live long-value fragment",
            ));
        }
    }
    Ok(())
}

fn reserved_property(table: &TableDefinition, column: ColumnOrdinal) -> bool {
    table.kind() == crate::TableDefinitionKind::System
        && table
            .columns()
            .get(usize::from(column.get()))
            .is_some_and(|column| column.name().raw_bytes() == b"LvProp")
}
