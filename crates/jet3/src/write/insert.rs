//! Existing-row insertion composed from EXP-0060/0061 encoding and EXP-0162 slots.
use crate::{
    ColumnPhysicalType, ColumnStorageClass, DatabaseReader, PAGE_BYTES, PublishStage,
    ResourceBudget, RowColumnLayout, RowLocator, RowValue, UpdateError,
};
use std::convert::Infallible;
use std::error::Error as StdError;
use std::path::Path;

/// Inserts one row into an existing user table.
///
/// The row is placed on a page with room, a released page of the table, or one
/// appended page. Indexes, long-value storage, allocation maps, AutoNumber
/// state and enforced relationships are updated and checked, and every change
/// publishes in one atomic replacement; all other bytes are preserved. Callers
/// must exclude other writers for the whole operation. `budget` bounds
/// planning, copying and verification.
///
/// # Errors
///
/// Returns [`UpdateError`] when the file or request is outside the supported
/// scope, a key or relationship constraint refuses the change, the table
/// stores a validation rule (rules are not evaluated), or `budget` is
/// exhausted; the original file is then unchanged. Publication failures
/// identify their stage. See `docs/plans/V1_SCOPE.md` for the supported scope.
pub fn insert_row(
    path: impl AsRef<Path>,
    table: &[u8],
    values: &[RowValue<'_>],
    budget: &mut ResourceBudget,
) -> Result<RowLocator, UpdateError> {
    insert_with_hook(path.as_ref(), table, values, budget, |_| {
        Ok::<(), Infallible>(())
    })
}

pub(super) fn insert_with_hook<H, HE>(
    path: &Path,
    table: &[u8],
    values: &[RowValue<'_>],
    budget: &mut ResourceBudget,
    hook: H,
) -> Result<RowLocator, UpdateError>
where
    H: FnMut(PublishStage) -> Result<(), HE>,
    HE: StdError + Send + Sync + 'static,
{
    let mut database = DatabaseReader::open(path, budget)?;
    crate::write::update::require_writable_sort_order(&database)?;
    let definition = crate::write::update::indexed_writable_table(&mut database, table, budget)?;
    let (edits, row) = plan(&mut database, &definition, table, values, true, budget)?;
    edits.publish(path, database, budget, hook)?;
    Ok(row)
}

pub(crate) fn plan(
    database: &mut DatabaseReader<crate::FileSource>,
    definition: &crate::TableDefinition,
    table: &[u8],
    values: &[RowValue<'_>],
    check_relationships: bool,
    budget: &mut ResourceBudget,
) -> Result<(crate::write::page_edits::PageEdits, RowLocator), UpdateError> {
    crate::row::mutation_graph::RowGraph::load(database, definition, None, budget)?;
    let mut auto = crate::write::auto_number::AutoNumber::load(definition)?;
    let mut lowered = [RowValue::Null; u8::MAX as usize];
    let values = if let Some(state) = auto {
        state.copy_values(values, &mut lowered, budget)?;
        auto = Some(state.insert(&mut lowered[..values.len()])?);
        &lowered[..values.len()]
    } else {
        values
    };
    let mut index = if definition.indexes().is_empty() && definition.physical_indexes().is_empty() {
        None
    } else {
        Some(crate::index::mutation::load(database, definition, budget)?)
    };
    let columns = definition.columns();
    if columns.len() > usize::from(u8::MAX) {
        return Err(UpdateError::Unsupported("row column count"));
    }
    let mut layout = [RowColumnLayout::new(
        ColumnPhysicalType::Long,
        ColumnStorageClass::Fixed { offset: 0 },
        4,
    ); u8::MAX as usize];
    budget.charge_items(columns.len() as u64)?;
    for (ordinal, (target, column)) in layout.iter_mut().zip(columns).enumerate() {
        if usize::from(column.ordinal().get()) != ordinal {
            return Err(UpdateError::Unsupported("noncontiguous column ordinals"));
        }
        *target = column.into();
    }
    crate::properties::value_policy::check(database, definition, values, budget)?;
    let mut long_values =
        crate::long_value::mutation::LongValues::load(database, definition, None, budget)?;
    let mut encoded = [0; PAGE_BYTES];
    let length = long_values.encode_row(&layout[..columns.len()], values, &mut encoded, budget)?;
    if check_relationships {
        crate::relationship::mutation::check(
            database,
            definition,
            table,
            crate::relationship::mutation::Change::Insert(values),
            budget,
        )?;
    }
    let mut edits = crate::write::page_edits::PageEdits::new(database.geometry().page_count());
    long_values.stage(database, &mut edits, budget)?;
    let mut observed_rows = 0_u32;
    {
        let mut rows = database.rows(definition, budget)?;
        while let Some(mut row) = rows.next_row()? {
            if let Some(auto) = auto {
                auto.read(&mut row)?;
            }
            observed_rows = observed_rows
                .checked_add(1)
                .ok_or(UpdateError::Mismatch("row count overflow"))?;
        }
    }
    let mut source_definition = [0; PAGE_BYTES];
    database.read_raw_page(definition.root(), &mut source_definition, budget)?;
    let mut patched_definition =
        crate::row::insert_page::increment_count(&source_definition, observed_rows, budget)?;
    if let Some(auto) = auto {
        auto.write(&mut patched_definition, budget)?;
    }
    let owned =
        crate::alloc::mutation_map::MapBits::load(database, definition.maps().owned(), budget)?;
    let available =
        crate::alloc::mutation_map::MapBits::load(database, definition.maps().available(), budget)?;
    if owned.overlaps(&available, budget)? {
        return Err(UpdateError::Mismatch("aliased table maps"));
    }
    let owned_pages = owned.existing_pages(database.geometry().page_count(), false, budget)?;
    let mut candidates = available
        .existing_pages(database.geometry().page_count(), false, budget)?
        .into_iter();
    let mut source_page = [0; PAGE_BYTES];
    let selected = loop {
        let Some(page) = candidates.next() else {
            break None;
        };
        budget.charge_work_units((owned_pages.len().max(1).ilog2() + 1) as u64)?;
        if owned_pages.binary_search(&page).is_err() {
            return Err(UpdateError::Mismatch("available page not owned"));
        }
        database.read_raw_page(page, &mut source_page, budget)?;
        if let Some((patched, slot)) = crate::row::insert_page::append_physical(
            page,
            definition.root(),
            &source_page,
            &encoded[..length],
            crate::row::directory::RowSlot::Ordinary,
            budget,
        )? {
            break Some((page, patched, slot));
        }
    };
    let row = if let Some((page, patched, slot)) = selected {
        edits.replace(
            crate::write::update_pages::PageChange {
                page,
                before: &source_page,
                after: patched.as_bytes(),
            },
            budget,
        )?;
        let minimum = crate::row::insert_page::minimum_length(columns, budget)?;
        let maps = crate::alloc::patch::plan(
            database,
            definition,
            page,
            crate::alloc::patch::AllocationChange::Retain {
                before: true,
                available: crate::row::insert_page::has_capacity(patched.as_bytes(), minimum),
            },
            budget,
        )?;
        maps.stage(database, &mut edits, budget)?;
        RowLocator::new(page, slot)
    } else {
        let mut minimum = [0; PAGE_BYTES];
        let nulls = [RowValue::Null; u8::MAX as usize];
        let minimum_length = crate::encode_row(
            &layout[..columns.len()],
            &nulls[..columns.len()],
            &mut minimum,
            budget,
        )?
        .get() as usize;
        let plan = crate::row::insert_page::plan_eof_insert(
            database,
            definition,
            &encoded[..length],
            &minimum[..minimum_length],
            edits.next_append_page()?,
            budget,
        )?;
        plan.maps.stage(database, &mut edits, budget)?;
        if plan.page.get() < database.geometry().page_count() {
            edits.set_image(database, plan.page, plan.image, budget)?;
        } else if edits.append(plan.image, budget)? != plan.page {
            return Err(UpdateError::Mismatch("EOF placement"));
        }
        RowLocator::new(plan.page, 0)
    };
    if let Some(index) = &mut index {
        index.insert(values, row, budget)?;
        index.stage(database, definition, &mut edits, budget)?;
    }
    edits.replace(
        crate::write::update_pages::PageChange {
            page: definition.root(),
            before: &source_definition,
            after: patched_definition.as_bytes(),
        },
        budget,
    )?;
    Ok((edits, row))
}
