//! Existing-row insertion composed from EXP-0060/0061 encoding and EXP-0162 slots.
use crate::{
    AllocationMap, ColumnPhysicalType, ColumnStorageClass, DatabaseReader, FileSource,
    InlineAllocationMap, MapRowLocator, PAGE_BYTES, PublishStage, ResourceBudget, RowColumnLayout,
    RowLocator, RowValue, UpdateError,
};
use std::convert::Infallible;
use std::error::Error as StdError;
use std::path::Path;

/// Inserts an encoded row on a populated available page or one new EOF page.
///
/// Values use the existing checked scalar/Text/Binary row encoder, including null
/// and Boolean fields. One unique/primary present Long index is supported when
/// its complete tree is an uncompressed root leaf with capacity for another key.
/// Indexed insertion requires an existing populated data page; its leaf records,
/// boundary bitmap, free count and physical distinct-key count are updated too.
/// Other indexes, AutoIncrement, long values and relationships are refused.
/// If no populated page fits, one EOF page is appended
/// only within existing inline global/owned/available maps. No reuse,
/// map growth or compaction is implemented. An existing selected page must
/// retain capacity for one more equal-sized row and representable directory slot;
/// this is a candidate restriction, not a DAO free-space threshold.
///
/// Only the new row, appended slot, page free/count fields and table row count
/// change on unindexed existing-page insertion. Indexed insertion additionally
/// updates the leaf and physical distinct count. EOF insertion clears its global free
/// bit and sets owned/available bits, marking available when a minimum encoded
/// row still fits. All other bytes, including page zero, remain exact. This
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
    let definition = crate::update::indexed_writable_table(&mut database, table, budget)?;
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
    let mut index = if definition.indexes().is_empty() && definition.physical_indexes().is_empty() {
        None
    } else {
        let leaf = crate::unique_leaf::validate(&mut database, &definition, budget)?;
        leaf.check_map(&mut database, &definition, budget)?;
        Some(leaf)
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
    let mut source_definition = [0; PAGE_BYTES];
    database.read_raw_page(definition.root(), &mut source_definition, budget)?;
    let mut patched_definition =
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
            break None;
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
            break Some((page, patched, slot));
        }
    };
    let Some(selected) = selected else {
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
            budget,
        )?;
        let leaf_image = if let Some(leaf) = &mut index {
            let Some(RowValue::Long(value)) = values.get(usize::from(leaf.column.get())) else {
                return Err(UpdateError::Unsupported("insert requires present Long key"));
            };
            let image = leaf.insert(*value, RowLocator::new(plan.page, 0), budget)?;
            crate::index_key_page::set_distinct_count(&mut patched_definition, leaf.count, budget)?;
            Some(image)
        } else {
            None
        };
        let (planned, count) = plan.changes(crate::update_pages::PageChange {
            page: definition.root(),
            before: &source_definition,
            after: patched_definition.as_bytes(),
        });
        let mut changes = [planned[0]; 5];
        changes[..count].copy_from_slice(&planned[..count]);
        let count = if let (Some(leaf), Some(image)) = (&index, &leaf_image) {
            changes[count] = crate::update_pages::PageChange {
                page: leaf.page,
                before: &leaf.before,
                after: image.as_bytes(),
            };
            count + 1
        } else {
            count
        };
        crate::update_pages::publish_changes_with_append(
            path,
            database.into_source(),
            &changes[..count],
            Some(plan.image.as_bytes()),
            budget,
            hook,
        )?;
        return Ok(RowLocator::new(plan.page, 0));
    };
    if let Some(leaf) = &mut index {
        let Some(RowValue::Long(value)) = values.get(usize::from(leaf.column.get())) else {
            return Err(UpdateError::Unsupported("insert requires present Long key"));
        };
        let after = leaf.insert(*value, RowLocator::new(selected.0, selected.2), budget)?;
        crate::index_key_page::set_distinct_count(&mut patched_definition, leaf.count, budget)?;
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
                crate::update_pages::PageChange {
                    page: leaf.page,
                    before: &leaf.before,
                    after: after.as_bytes(),
                },
            ],
            budget,
            hook,
        )?;
        return Ok(RowLocator::new(selected.0, selected.2));
    }
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

#[cfg(all(test, unix))]
#[path = "indexed_row_tests.rs"]
mod indexed_tests;
