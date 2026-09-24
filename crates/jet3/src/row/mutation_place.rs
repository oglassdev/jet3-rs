//! EXP-0262 one-link growth, source collapse and direct hidden-target relocation.
use crate::{
    DatabaseReader, FileSource, PAGE_BYTES, ResourceBudget, RowLocator, TableDefinition,
    WriteError,
    row::{directory::RowSlot, mutation_pages::RowPages},
    write::page_edits::PageEdits,
};

pub(crate) fn replace(
    database: &mut DatabaseReader<FileSource>,
    definition: &TableDefinition,
    chain: &[RowLocator],
    encoded: &[u8],
    minimum: &[u8],
    edits: &mut PageEdits,
    budget: &mut ResourceBudget,
) -> Result<(), WriteError> {
    let logical = *chain.first().ok_or(WriteError::NotFound("row"))?;
    if chain.len() > 2 {
        return Err(WriteError::Unsupported(
            "mutation of multi-hop overflow chain",
        ));
    }
    let mut pages = RowPages::new();
    if pages.replace(
        database,
        definition.root(),
        logical,
        encoded,
        RowSlot::Ordinary,
        budget,
    )? {
        if let Some(&storage) = chain.get(1) {
            pages.remove(database, definition.root(), storage, budget)?;
        }
    } else if let Some(&storage) = chain.get(1)
        && pages.replace(
            database,
            definition.root(),
            storage,
            encoded,
            RowSlot::Storage,
            budget,
        )?
    {
        // The existing logical pointer already names this storage slot.
    } else {
        let target = allocate(
            database,
            definition,
            chain,
            (encoded, minimum),
            &mut pages,
            edits,
            budget,
        )?;
        let pointer = crate::row::directory::overflow_pointer(target)?;
        if !pages.replace(
            database,
            definition.root(),
            logical,
            &pointer,
            RowSlot::Link,
            budget,
        )? {
            return Err(WriteError::Unsupported(
                "logical slot cannot hold overflow pointer",
            ));
        }
        if let Some(&storage) = chain.get(1) {
            pages.remove(database, definition.root(), storage, budget)?;
        }
    }
    pages.stage(database, definition, edits, budget)
}

fn allocate(
    database: &mut DatabaseReader<FileSource>,
    definition: &TableDefinition,
    chain: &[RowLocator],
    (encoded, minimum): (&[u8], &[u8]),
    pages: &mut RowPages,
    edits: &mut PageEdits,
    budget: &mut ResourceBudget,
) -> Result<RowLocator, WriteError> {
    let owned =
        crate::alloc::mutation_map::MapBits::load(database, definition.maps().owned(), budget)?;
    let available =
        crate::alloc::mutation_map::MapBits::load(database, definition.maps().available(), budget)?;
    if owned.overlaps(&available, budget)? {
        return Err(WriteError::Mismatch("aliased table maps"));
    }
    let owned_pages = owned.existing_pages(database.geometry().page_count(), false, budget)?;
    let mut source = [0; PAGE_BYTES];
    for page in available.existing_pages(database.geometry().page_count(), false, budget)? {
        budget.charge_work_units(
            (owned_pages.len().max(1).ilog2() + 1) as u64 + chain.len() as u64,
        )?;
        if owned_pages.binary_search(&page).is_err() {
            return Err(WriteError::Mismatch("available page not owned"));
        }
        if chain.iter().any(|row| row.page() == page) {
            continue;
        }
        crate::row::directory::overflow_pointer(RowLocator::new(page, 0))?;
        database.read_raw_page(page, &mut source, budget)?;
        if let Some((after, slot)) =
            crate::row::data_page::DataPageEditor::open(page, definition.root(), &source, budget)?
                .append(encoded, Some(RowSlot::Storage), budget)?
        {
            pages.appended(page, source, after, budget)?;
            return Ok(RowLocator::new(page, slot));
        }
    }
    let mut plan = crate::row::insert_page::plan_eof_insert(
        database,
        definition,
        encoded,
        minimum,
        edits.next_append_page()?,
        budget,
    )?;
    crate::row::directory::hide_first(&mut plan.image, budget)?;
    plan.maps.stage(database, edits, budget)?;
    if plan.page.get() < database.geometry().page_count() {
        edits.set_image(database, plan.page, plan.image, budget)?;
    } else if edits.append(plan.image, budget)? != plan.page {
        return Err(WriteError::Mismatch("overflow EOF placement"));
    }
    Ok(RowLocator::new(plan.page, 0))
}
