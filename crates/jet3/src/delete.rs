//! Bounded existing-row deletion using the tail transition observed in EXP-0162.
use crate::{DatabaseReader, PAGE_BYTES, PublishStage, ResourceBudget, RowLocator, UpdateError};
use std::convert::Infallible;
use std::error::Error as StdError;
use std::path::Path;

/// One existing user row, addressed by the reader's stable logical locator.
#[derive(Debug, Clone, Copy)]
pub struct RowDelete<'a> {
    /// Exact database-encoded user table name.
    pub table: &'a [u8],
    /// Locator obtained while the source is unchanged.
    pub row: RowLocator,
}

/// Deletes the final physical row slot from a page containing at least two rows.
///
/// Supports unindexed, relationship-free tables without AutoIncrement or long
/// values. All slots on the affected page must be ordinary live rows, and the
/// page must already appear in its inline available map. Non-tail compaction,
/// tombstone reuse, page release and inconsistent free/count metadata are refused.
/// The removed payload remains in unused space; every byte except the directory
/// word, free-byte count and table row count is preserved, including page zero.
/// Keeping page zero unchanged is a candidate construction awaiting DAO validation.
/// This operation makes no DAO compatibility claim.
///
/// Callers must exclude external writers throughout this Unix-only operation.
/// The same resource budget covers planning, private copying and full-file
/// verification. Any pre-publication failure preserves the original; publication
/// errors identify their stage, including post-publication sync failures.
pub fn delete_row(
    path: impl AsRef<Path>,
    request: RowDelete<'_>,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    delete_with_hook(path.as_ref(), request, budget, |_| Ok::<(), Infallible>(()))
}

fn delete_with_hook<H, HE>(
    path: &Path,
    request: RowDelete<'_>,
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
        || definition.columns().iter().any(|c| c.auto_increment())
    {
        return Err(UpdateError::Unsupported(
            "AutoIncrement or long-value table",
        ));
    }
    let mut source_page = [0; PAGE_BYTES];
    database.read_raw_page(request.row.page(), &mut source_page, budget)?;
    let patched_page = crate::row_delete_page::tail(
        request.row.page(),
        definition.root(),
        &source_page,
        request.row.slot(),
        budget,
    )?;
    let mut observed_rows = 0_u32;
    let mut found = false;
    {
        let mut rows = database.rows(&definition, budget)?;
        while let Some(row) = rows.next_row()? {
            observed_rows = observed_rows
                .checked_add(1)
                .ok_or(UpdateError::Mismatch("row count overflow"))?;
            if row.locator() == request.row {
                if row.storage_locator() != request.row {
                    return Err(UpdateError::Unsupported("overflow row"));
                }
                found = true;
            }
        }
    }
    if !found {
        return Err(UpdateError::NotFound("row"));
    }
    let locator = definition.maps().available();
    let mut map_page = [0; PAGE_BYTES];
    let classified = database
        .read_classified_page(locator.page(), &mut map_page, budget)
        .map_err(crate::TableDefinitionError::Page)?;
    let map =
        crate::locate_usage_map(classified, locator, budget).map_err(UpdateError::UsageMap)?;
    let crate::AllocationMap::Inline(map) =
        crate::decode_allocation_map(map.raw(), budget).map_err(UpdateError::Allocation)?
    else {
        return Err(UpdateError::Unsupported("indirect available map"));
    };
    let mut available = map.allocated_pages(database.geometry());
    let mut listed = false;
    while let Some(page) = available
        .next_page(budget)
        .map_err(UpdateError::Allocation)?
    {
        listed |= page == request.row.page();
    }
    if !listed {
        return Err(UpdateError::Unsupported("page absent from available map"));
    }
    let mut source_definition = [0; PAGE_BYTES];
    database.read_raw_page(definition.root(), &mut source_definition, budget)?;
    let patched_definition =
        crate::row_delete_page::decrement_count(&source_definition, observed_rows, budget)?;
    crate::update_pages::publish_changes(
        path,
        database.into_source(),
        &[
            crate::update_pages::PageChange {
                page: request.row.page(),
                before: &source_page,
                after: patched_page.as_bytes(),
            },
            crate::update_pages::PageChange {
                page: definition.root(),
                before: &source_definition,
                after: patched_definition.as_bytes(),
            },
        ],
        budget,
        hook,
    )
}

#[cfg(all(test, unix))]
#[path = "delete_tests.rs"]
mod tests;
