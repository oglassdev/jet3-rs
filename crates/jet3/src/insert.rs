//! Existing-row insertion composed from EXP-0060/0061 encoding and EXP-0162 slots.
use crate::{
    AllocationMap, ColumnPhysicalType, ColumnStorageClass, DatabaseReader, FileSource,
    InlineAllocationMap, MapRowLocator, PAGE_BYTES, PublishStage, ResourceBudget, RowColumnLayout,
    RowLocator, RowValue, UpdateError,
};
use std::convert::Infallible;
use std::error::Error as StdError;
use std::path::Path;

/// Appends an encoded row to an existing populated, owned and available data page.
///
/// Values use the existing checked scalar/Text/Binary row encoder, including null
/// and Boolean fields. AutoIncrement, long values, indexes and relationships are
/// refused. Owned/available maps must be inline. No slot reuse, compaction, page
/// allocation or empty-table insertion is implemented. The selected page must
/// retain capacity for one more equal-sized row and representable directory slot;
/// this is a candidate restriction, not a DAO free-space threshold.
///
/// Only the new row, appended slot, page free/count fields and table row count
/// change. All other bytes, including page zero and maps, remain exact. This
/// construction requires separate DAO validation and makes no compatibility claim.
/// Callers must exclude external writers throughout this Unix-only operation.
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

fn inline_map<'a>(
    database: &mut DatabaseReader<FileSource>,
    locator: MapRowLocator,
    bytes: &'a mut [u8; PAGE_BYTES],
    budget: &mut ResourceBudget,
) -> Result<InlineAllocationMap<'a>, UpdateError> {
    let page = database
        .read_classified_page(locator.page(), bytes, budget)
        .map_err(crate::TableDefinitionError::Page)?;
    let record = crate::locate_usage_map(page, locator, budget).map_err(UpdateError::UsageMap)?;
    match crate::decode_allocation_map(record.raw(), budget).map_err(UpdateError::Allocation)? {
        AllocationMap::Inline(map) => Ok(map),
        _ => Err(UpdateError::Unsupported("indirect insertion map")),
    }
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
    let definition = crate::update::writable_table(&mut database, table, budget)?;
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
            "AutoIncrement or long-value table",
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
    let length =
        crate::encode_row(&layout[..columns.len()], values, &mut encoded, budget)?.get() as usize;
    let mut observed_rows = 0_u32;
    {
        let mut rows = database.rows(&definition, budget)?;
        while let Some(row) = rows.next_row()? {
            if row.locator() != row.storage_locator() {
                return Err(UpdateError::Unsupported("overflow row"));
            }
            observed_rows = observed_rows
                .checked_add(1)
                .ok_or(UpdateError::Mismatch("row count overflow"))?;
        }
    }
    if observed_rows == 0 {
        return Err(UpdateError::Unsupported("empty-table insertion"));
    }
    let mut source_definition = [0; PAGE_BYTES];
    database.read_raw_page(definition.root(), &mut source_definition, budget)?;
    let patched_definition =
        crate::row_insert_page::increment_count(&source_definition, observed_rows, budget)?;
    let mut owned_bytes = [0; PAGE_BYTES];
    let owned = inline_map(
        &mut database,
        definition.maps().owned(),
        &mut owned_bytes,
        budget,
    )?;
    let mut available_bytes = [0; PAGE_BYTES];
    let available = inline_map(
        &mut database,
        definition.maps().available(),
        &mut available_bytes,
        budget,
    )?;
    let geometry = database.geometry();
    let mut candidates = available.allocated_pages(geometry);
    let mut source_page = [0; PAGE_BYTES];
    let selected = loop {
        let Some(page) = candidates
            .next_page(budget)
            .map_err(UpdateError::Allocation)?
        else {
            return Err(UpdateError::Unsupported(
                "no populated page with retained row capacity",
            ));
        };
        let mut owned_pages = owned.allocated_pages(geometry);
        let mut member = false;
        while let Some(owner_page) = owned_pages
            .next_page(budget)
            .map_err(UpdateError::Allocation)?
        {
            member |= owner_page == page;
        }
        if !member {
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
            break (page, patched, slot);
        }
    };
    crate::update_pages::publish_changes(
        path,
        database.into_source(),
        &[
            crate::update_pages::PageChange {
                page: selected.0,
                before: &source_page,
                after: selected.1.as_bytes(),
            },
            crate::update_pages::PageChange {
                page: definition.root(),
                before: &source_definition,
                after: patched_definition.as_bytes(),
            },
        ],
        budget,
        hook,
    )?;
    Ok(RowLocator::new(selected.0, selected.2))
}

#[cfg(all(test, unix))]
#[path = "insert_tests.rs"]
mod tests;
