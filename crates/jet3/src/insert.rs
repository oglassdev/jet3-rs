//! Existing-row insertion composed from EXP-0060/0061 encoding and EXP-0162 slots.
use crate::{
    ColumnPhysicalType, ColumnStorageClass, DatabaseReader, PAGE_BYTES, PublishStage,
    ResourceBudget, RowColumnLayout, RowLocator, RowValue, UpdateError,
};
use std::convert::Infallible;
use std::error::Error as StdError;
use std::path::Path;

/// Inserts a row on an available page, a released target-table page, or one EOF page.
///
/// Values use the existing checked scalar/Text/Binary row encoder, including null
/// and Boolean fields. Up to 32 indexes with one to ten supported scalar
/// fields admit primary, unique, nonunique, descending and nullable keys. Each
/// complete tree and row/key correspondence must validate. Changed trees retain
/// their roots, reuse reserved index pages and append nodes as needed. Other
/// key types and relationships are refused. Memo/OLE payloads
/// use independent column maps; raw caller-supplied headers are refused.
/// If no populated page fits, a released global-free page belonging to this table
/// is reused, or one EOF page is appended. Inline maps convert to indirect storage
/// and missing bitmap slots are allocated within the existing reference row.
/// Live-page slot reuse and compaction are not implemented. A selected page must fit
/// the requested row and its directory slot; availability afterward reflects
/// whether another minimum-length row and slot fit.
///
/// External payload pages are validated against every live field reference and
/// disjoint column ownership, then reused or appended. Single and chained
/// storage use separate pools. Data, payloads, indexes and their allocation bits
/// publish together.
///
/// One AutoNumber column accepts `RowValue::AutoIncrement` or an explicit Long.
/// Successful insertion advances its persisted state, with unsigned explicit-ID
/// resets and wrapping generation established by EXP-0237. Rejected requests
/// preserve the file, including the counter; DAO can consume a number on failure.
///
/// The new row, appended slot, page free/count fields, availability and table count
/// change on unindexed existing-page insertion. Indexed insertion additionally
/// updates index nodes/maps and increments each retained counter only for a new
/// included key. EOF insertion clears its global free
/// bit and sets owned/available bits, marking available when a minimum encoded
/// row still fits. AutoNumber insertion also updates its allocation state.
/// All other bytes, including page zero, remain exact. EXP-0232/0238/0239 record
/// the finite numeric, long-value and AutoNumber DAO comparisons.
/// Callers must exclude external writers throughout this operation on Unix or Windows.
/// A pre-publication failure preserves the original; publication errors identify
/// their stage. One resource budget covers planning, copying and full verification.
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

fn insert_with_hook<H, HE>(
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
    let definition = crate::update::indexed_writable_table(&mut database, table, budget)?;
    let mut auto = crate::auto_number_mutation::AutoNumber::load(&definition)?;
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
        Some(crate::index_mutation::load(
            &mut database,
            &definition,
            budget,
        )?)
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
    let mut long_values =
        crate::long_value_mutation::LongValues::load(&mut database, &definition, None, budget)?;
    let mut encoded = [0; PAGE_BYTES];
    let length = long_values.encode_row(&layout[..columns.len()], values, &mut encoded, budget)?;
    let mut edits = crate::page_edits::PageEdits::new(database.geometry().page_count());
    long_values.stage(&mut database, &mut edits, budget)?;
    let mut observed_rows = 0_u32;
    {
        let mut rows = database.rows(&definition, budget)?;
        while let Some(mut row) = rows.next_row()? {
            if row.locator() != row.storage_locator() {
                return Err(UpdateError::Unsupported("overflow row"));
            }
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
        crate::row_insert_page::increment_count(&source_definition, observed_rows, budget)?;
    if let Some(auto) = auto {
        auto.write(&mut patched_definition, budget)?;
    }
    let owned =
        crate::mutation_map::MapBits::load(&mut database, definition.maps().owned(), budget)?;
    let available =
        crate::mutation_map::MapBits::load(&mut database, definition.maps().available(), budget)?;
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
        if let Some((patched, slot)) = crate::row_insert_page::append(
            page,
            definition.root(),
            &source_page,
            &encoded[..length],
            budget,
        )? {
            break Some((page, patched, slot));
        }
    };
    let row = if let Some((page, patched, slot)) = selected {
        edits.replace(
            crate::update_pages::PageChange {
                page,
                before: &source_page,
                after: patched.as_bytes(),
            },
            budget,
        )?;
        let minimum = crate::row_insert_page::minimum_length(columns, budget)?;
        let maps = crate::allocation_patch::plan(
            &mut database,
            &definition,
            page,
            crate::allocation_patch::AllocationChange::Retain {
                before: true,
                available: crate::row_insert_page::has_capacity(patched.as_bytes(), minimum),
            },
            budget,
        )?;
        maps.stage(&mut database, &mut edits, budget)?;
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
        let plan = crate::row_insert_eof::plan(
            &mut database,
            &definition,
            &encoded[..length],
            &minimum[..minimum_length],
            edits.next_append_page()?,
            budget,
        )?;
        plan.maps.stage(&mut database, &mut edits, budget)?;
        if plan.page.get() < database.geometry().page_count() {
            edits.set_image(&mut database, plan.page, plan.image, budget)?;
        } else if edits.append(plan.image, budget)? != plan.page {
            return Err(UpdateError::Mismatch("EOF placement"));
        }
        RowLocator::new(plan.page, 0)
    };
    if let Some(index) = &mut index {
        index.insert(values, row, budget)?;
        index.stage(&mut database, &definition, &mut edits, budget)?;
    }
    edits.replace(
        crate::update_pages::PageChange {
            page: definition.root(),
            before: &source_definition,
            after: patched_definition.as_bytes(),
        },
        budget,
    )?;
    edits.publish(path, database, budget, hook)?;
    Ok(row)
}

#[cfg(all(test, any(unix, windows)))]
#[path = "insert_tests.rs"]
mod tests;

#[cfg(all(test, any(unix, windows)))]
#[path = "indexed_row_tests.rs"]
mod indexed_tests;
