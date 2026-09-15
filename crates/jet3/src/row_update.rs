//! Full scalar and long-value row replacement using the checked row encoder and exact publication.
use crate::{
    ColumnPhysicalType, ColumnStorageClass, DatabaseReader, PAGE_BYTES, PublishStage,
    ResourceBudget, RowColumnLayout, RowLocator, RowValue, UpdateError,
};
use std::{convert::Infallible, error::Error as StdError, path::Path};

/// Replacement values for an existing row, in its schema's column order.
#[derive(Debug, Clone, Copy)]
pub struct RowUpdate<'a> {
    /// Exact database-encoded user table name.
    pub table: &'a [u8],
    /// Existing physical/logical locator obtained while the source is unchanged.
    pub row: RowLocator,
    /// Complete replacement row, including raw Memo/OLE payload values.
    pub values: &'a [RowValue<'a>],
}

/// Replaces a complete ordinary row on its current page without changing its slot.
///
/// Supports scalar/null/Boolean/Text/Binary values in relationship-free
/// tables, including independent Memo/OLE columns. The page must be inline-owned and
/// allocated, with consistent metadata and ordinary live rows or known empty
/// `c000` tombstones. The data page and row locator remain fixed. Up to three
/// indexes with one or two supported numeric fields admit key and null changes,
/// with uniqueness enforced for fully present keys.
/// Existing checked row-encoding limits apply, including variable-offset widths.
/// The replacement must fit the existing contiguous space. Available membership
/// records whether a minimum encoded row and directory slot still fit; this is
/// a candidate policy, not a model of DAO's availability threshold.
///
/// External payloads are validated against live references and column ownership.
/// Replaced fragments are released or reused, with single and chained storage
/// in separate pools. All payload, data, index and allocation changes publish
/// together. Caller-supplied raw long-value headers are refused.
///
/// Later row bytes and offsets shift as needed, preserving their slots and values.
/// Shrinking leaves newly vacated slack unchanged. Only the replacement, shifted
/// bytes/offsets and page free-byte count change in the data page. A changed key
/// rebuilds index nodes and may allocate them within inline maps. The page's
/// available bit reflects remaining capacity. Table/slot counts, page zero and
/// unrelated objects remain exact. This construction requires separate DAO
/// validation and makes no compatibility claim.
///
/// Callers must exclude external writers throughout this Unix-only operation.
/// One resource budget covers planning, copying and complete private verification.
/// Pre-publication failure preserves the original; errors identify publish stages.
/// An AutoNumber field accepts its unchanged Long value or `RowValue::AutoIncrement`
/// to retain its value. Changing that field is refused and its counter is retained.
pub fn update_row(
    path: impl AsRef<Path>,
    request: RowUpdate<'_>,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    update_with_hook(path.as_ref(), request, budget, |_| Ok::<(), Infallible>(()))
}

fn update_with_hook<H, HE>(
    path: &Path,
    request: RowUpdate<'_>,
    budget: &mut ResourceBudget,
    hook: H,
) -> Result<(), UpdateError>
where
    H: FnMut(PublishStage) -> Result<(), HE>,
    HE: StdError + Send + Sync + 'static,
{
    let mut database = DatabaseReader::open(path, budget)?;
    let definition = crate::update::indexed_writable_table(&mut database, request.table, budget)?;
    let auto = crate::auto_number_mutation::AutoNumber::load(&definition)?;
    let mut lowered = [RowValue::Null; u8::MAX as usize];
    if let Some(auto) = auto {
        auto.copy_values(request.values, &mut lowered, budget)?;
    }
    let mut index = if definition.physical_indexes().is_empty() {
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
    let mut observed = 0_u32;
    let mut found = false;
    {
        let mut rows = database.rows(&definition, budget)?;
        while let Some(mut row) = rows.next_row()? {
            if row.locator() != row.storage_locator() {
                return Err(UpdateError::Unsupported("overflow row"));
            }
            observed = observed
                .checked_add(1)
                .ok_or(UpdateError::Mismatch("row count overflow"))?;
            if let Some(auto) = auto {
                let value = auto.read(&mut row)?;
                if row.locator() == request.row {
                    auto.retain(&mut lowered[..request.values.len()], value)?;
                }
            }
            found |= row.locator() == request.row;
        }
    }
    if !found {
        return Err(UpdateError::NotFound("row"));
    }
    let values = if auto.is_some() {
        &lowered[..request.values.len()]
    } else {
        request.values
    };
    let mut long_values = crate::long_value_mutation::LongValues::load(
        &mut database,
        &definition,
        Some(request.row),
        budget,
    )?;
    long_values.remove_selected(budget)?;
    let mut encoded = [0; PAGE_BYTES];
    let length = long_values.encode_row(&layout[..columns.len()], values, &mut encoded, budget)?;
    let mut minimum = [0; PAGE_BYTES];
    let nulls = [RowValue::Null; u8::MAX as usize];
    let minimum_length = crate::encode_row(
        &layout[..columns.len()],
        &nulls[..columns.len()],
        &mut minimum,
        budget,
    )?
    .get() as usize;
    let mut count_page = [0; PAGE_BYTES];
    database.read_raw_page(definition.root(), &mut count_page, budget)?;
    crate::row_update_page::check_count(&count_page, observed, budget)?;
    let available =
        crate::allocation_patch::available(&mut database, &definition, request.row.page(), budget)?;
    let mut before = [0; PAGE_BYTES];
    database.read_raw_page(request.row.page(), &mut before, budget)?;
    let after = crate::row_update_page::replace(
        request.row.page(),
        definition.root(),
        &before,
        request.row.slot(),
        &encoded[..length],
        budget,
    )?;
    let mut edits = crate::page_edits::PageEdits::new(database.geometry().page_count());
    long_values.stage(&mut database, &mut edits, budget)?;
    edits.replace(
        crate::update_pages::PageChange {
            page: request.row.page(),
            before: &before,
            after: after.as_bytes(),
        },
        budget,
    )?;
    let maps = crate::allocation_patch::plan(
        &mut database,
        &definition,
        request.row.page(),
        crate::allocation_patch::AllocationChange::Retain {
            before: available,
            available: crate::row_insert_page::has_capacity(after.as_bytes(), minimum_length),
        },
        budget,
    )?;
    maps.stage(&mut database, &mut edits, budget)?;
    if let Some(index) = &mut index {
        index.replace(request.row, values, budget)?;
        index.stage(&mut database, &definition, &mut edits, budget)?;
    }
    edits.publish(path, database.into_source(), budget, hook)
}

#[cfg(all(test, unix))]
#[path = "row_update_tests.rs"]
mod tests;
