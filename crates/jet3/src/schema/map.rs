//! New usage-map rows using EXP-0057/0254 inline and indirect layouts.
use crate::{
    ByteCount, DataPageBuilder, DatabaseReader, ExtendedUsageMapEncoder, FileSource,
    InlineUsageMapEncoder, MapRowLocator, PageNumber, ResourceBudget, UpdateError,
    row::{data_page::DataPageEditor, delete_page::Deletion},
    schema::definition::allocate,
    write::page_edits::PageEdits,
};

pub(crate) fn create(
    database: &mut DatabaseReader<FileSource>,
    edits: &mut PageEdits,
    members: &[PageNumber],
    budget: &mut ResourceBudget,
) -> Result<MapRowLocator, UpdateError> {
    budget.charge_items(members.len() as u64)?;
    let highest = members.iter().map(|page| page.get()).max().unwrap_or(0);
    let mut row = [0; 133];
    if highest < 1024 {
        let mut map = InlineUsageMapEncoder::new(PageNumber::new(0), ByteCount::new(128), budget)?;
        for &page in members {
            map.set_page(page)?;
        }
        map.encode_into(&mut row, budget)?;
    } else {
        let count = highest / crate::EXTENDED_BITMAP_BITS + 1;
        if count > crate::alloc::usage_map_writer::INDIRECT_REFERENCE_SLOTS as u64 {
            return Err(UpdateError::Unsupported(
                "indirect allocation reference capacity",
            ));
        }
        let mut references =
            [PageNumber::new(0); crate::alloc::usage_map_writer::INDIRECT_REFERENCE_SLOTS];
        for slot in 0..count {
            let mut map = ExtendedUsageMapEncoder::new(slot, budget)?;
            budget.charge_items(members.len() as u64)?;
            for &page in members {
                if page.get() / crate::EXTENDED_BITMAP_BITS == slot {
                    map.set_page(page, budget)?;
                }
            }
            references[slot as usize] = allocate(database, edits, map.into_image(), budget)?;
        }
        crate::encode_indirect_references(&references, &mut row, budget)?;
    }
    let mut builder = DataPageBuilder::new(PageNumber::new(0), budget)?;
    builder.append_row(&row, budget)?;
    let free = u16::try_from(builder.free_bytes().get())
        .map_err(|_| UpdateError::Mismatch("usage-map free bytes"))?;
    let mut image = builder.finish();
    image.write_at(crate::PageOffset::new(2), &free.to_le_bytes(), budget)?;
    let page = allocate(database, edits, image, budget)?;
    Ok(MapRowLocator::new(page, 0))
}

// EXP-0297: removed map rows compact to empty tombstones; unshared bitmap pages are freed.
pub(crate) fn retire(
    file: &mut std::fs::File,
    journal: &mut PageEdits,
    locator: MapRowLocator,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    crate::schema::edit::apply(file, journal, budget, |database, budget| {
        let removed = crate::alloc::mutation_map::MapBits::load(database, locator, budget)?;
        if !removed
            .existing_pages(database.geometry().page_count(), false, budget)?
            .is_empty()
        {
            return Err(UpdateError::Mismatch("retired map still owns content"));
        }
        let global = crate::alloc::mutation_map::MapBits::load(
            database,
            crate::alloc::mutation_map::global_locator(),
            budget,
        )?;
        if removed.locator == global.locator || removed.overlaps(&global, budget)? {
            return Err(UpdateError::Mismatch("retired map aliases allocation"));
        }
        let mut roots = Vec::new();
        {
            let mut catalog = database.catalog(budget)?;
            while let Some(record) = catalog.next_record()? {
                if let Some(root) = record.table_definition() {
                    crate::write::page_edits::reserve(&mut roots, 1, catalog.budget_mut())?;
                    roots.push(root);
                }
            }
        }
        for root in roots {
            let table = database.table_definition(root, budget)?;
            for other in crate::schema::storage::locators(&table, budget)? {
                let other = crate::alloc::mutation_map::MapBits::load(database, other, budget)?;
                if other.locator == removed.locator || other.overlaps(&removed, budget)? {
                    return Err(UpdateError::Mismatch("retired map remains referenced"));
                }
            }
        }
        let mut bytes = [0; crate::PAGE_BYTES];
        database.read_raw_page(locator.page(), &mut bytes, budget)?;
        let deletion = DataPageEditor::open(locator.page(), PageNumber::new(0), &bytes, budget)?
            .remove(locator.row(), true, budget)?;
        let mut edits = PageEdits::new(database.geometry().page_count());
        if matches!(deletion, Deletion::Released(_)) {
            edits.map_bit(
                database,
                crate::alloc::mutation_map::global_locator(),
                locator.page(),
                false,
                true,
                budget,
            )?;
        }
        edits.set_image(database, locator.page(), deletion.into_image(), budget)?;
        for span in removed.spans {
            if span.page != locator.page() {
                edits.map_bit(
                    database,
                    crate::alloc::mutation_map::global_locator(),
                    span.page,
                    false,
                    true,
                    budget,
                )?;
            }
        }
        Ok((edits, ()))
    })
}
