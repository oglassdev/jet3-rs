//! Full scalar-row replacement using the checked row encoder and exact publication.
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
    /// Complete replacement row, using the checked scalar/Text/Binary encoder.
    pub values: &'a [RowValue<'a>],
}

/// Replaces a complete ordinary row on its current page without changing its slot.
///
/// Supports scalar/null/Boolean/Text/Binary values in unindexed, relationship-free
/// non-AutoIncrement/non-long-value tables. The page must be inline-owned and
/// available, with consistent metadata and ordinary live rows or known empty
/// `c000` tombstones. No overflow, page allocation or map transition is performed.
/// Existing checked row-encoding limits apply, including variable-offset widths.
/// The resulting page must retain room for a minimum encoded row and directory
/// slot; this is a candidate scope constraint, not a DAO availability threshold.
///
/// Later row bytes and offsets shift as needed, preserving their slots and values.
/// Shrinking leaves newly vacated slack unchanged. Only the replacement, shifted
/// bytes/offsets and page free-byte count change; table/slot counts, maps, page zero
/// and unrelated objects remain exact. This construction requires separate DAO
/// validation and makes no compatibility claim.
///
/// Callers must exclude external writers throughout this Unix-only operation.
/// One resource budget covers planning, copying and complete private verification.
/// Pre-publication failure preserves the original; errors identify publish stages.
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
    let definition = crate::update::writable_table(&mut database, request.table, budget)?;
    if !definition.long_value_maps().is_empty()
        || definition.columns().iter().any(|c| {
            c.auto_increment()
                || matches!(
                    c.physical_type(),
                    ColumnPhysicalType::Memo | ColumnPhysicalType::LongBinary
                )
        })
    {
        return Err(UpdateError::Unsupported(
            "AutoIncrement or long-value row replacement",
        ));
    }
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
    let mut encoded = [0; PAGE_BYTES];
    let length = crate::encode_row(
        &layout[..columns.len()],
        request.values,
        &mut encoded,
        budget,
    )?
    .get() as usize;
    let mut minimum = [0; PAGE_BYTES];
    let nulls = [RowValue::Null; u8::MAX as usize];
    let minimum_length = crate::encode_row(
        &layout[..columns.len()],
        &nulls[..columns.len()],
        &mut minimum,
        budget,
    )?
    .get() as usize;
    let mut observed = 0_u32;
    let mut found = false;
    {
        let mut rows = database.rows(&definition, budget)?;
        while let Some(row) = rows.next_row()? {
            if row.locator() != row.storage_locator() {
                return Err(UpdateError::Unsupported("overflow row"));
            }
            observed = observed
                .checked_add(1)
                .ok_or(UpdateError::Mismatch("row count overflow"))?;
            found |= row.locator() == request.row;
        }
    }
    if !found {
        return Err(UpdateError::NotFound("row"));
    }
    let mut count_page = [0; PAGE_BYTES];
    database.read_raw_page(definition.root(), &mut count_page, budget)?;
    crate::row_update_page::check_count(&count_page, observed, budget)?;
    for locator in [definition.maps().owned(), definition.maps().available()] {
        let mut bytes = [0; PAGE_BYTES];
        let page = database
            .read_classified_page(locator.page(), &mut bytes, budget)
            .map_err(crate::TableDefinitionError::Page)?;
        let record =
            crate::locate_usage_map(page, locator, budget).map_err(UpdateError::UsageMap)?;
        let crate::AllocationMap::Inline(map) =
            crate::decode_allocation_map(record.raw(), budget).map_err(UpdateError::Allocation)?
        else {
            return Err(UpdateError::Unsupported("indirect row replacement map"));
        };
        let mut pages = map.allocated_pages(database.geometry());
        let mut listed = false;
        while let Some(page) = pages.next_page(budget).map_err(UpdateError::Allocation)? {
            listed |= page == request.row.page();
        }
        if !listed {
            return Err(UpdateError::Unsupported(
                "row replacement page not owned and available",
            ));
        }
    }
    let mut before = [0; PAGE_BYTES];
    database.read_raw_page(request.row.page(), &mut before, budget)?;
    let after = crate::row_update_page::replace(
        request.row.page(),
        definition.root(),
        &before,
        request.row.slot(),
        &encoded[..length],
        minimum_length,
        budget,
    )?;
    crate::update_pages::publish_changes(
        path,
        database.into_source(),
        &[crate::update_pages::PageChange {
            page: request.row.page(),
            before: &before,
            after: after.as_bytes(),
        }],
        budget,
        hook,
    )
}

#[cfg(all(test, unix))]
#[path = "row_update_tests.rs"]
mod tests;
