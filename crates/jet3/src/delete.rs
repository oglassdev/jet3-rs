//! Bounded existing-row deletion using the compaction observed in EXP-0162.
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

/// Deletes one ordinary row, compacting its page or releasing a single-slot page.
///
/// Supports relationship-free tables without AutoIncrement or long values.
/// One unique/primary present Long index additionally supports retained-page
/// deletion when its complete tree is an uncompressed root leaf. The matching
/// leaf entry is removed, its boundary/free and physical distinct-key count are
/// updated, and unused leaf bytes remain exact. Other indexed deletions are refused.
/// Slots must be ordinary live rows or known empty `c000` tombstones;
/// the page must already appear in its inline available map. Later rows move
/// upward without changing their physical slot numbers or stored values. The
/// deleted slot becomes an empty tombstone; existing tombstone flags are retained.
/// A page containing exactly one physical row is released through its existing
/// inline global/owned/available maps. A sole live row alongside tombstones and
/// inconsistent free/count metadata are refused.
/// On retained unindexed pages, only shifted row bytes, affected directory offsets,
/// free-byte count and table row count change. Vacated slack, maps, page zero and unrelated objects
/// remain exact for retained pages. Released pages change their tag, directory
/// word and free count, and their three map bits; payload/slack and file length
/// remain exact.
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
    let definition = crate::update::indexed_writable_table(&mut database, request.table, budget)?;
    if !definition.long_value_maps().is_empty()
        || definition.columns().iter().any(|c| c.auto_increment())
    {
        return Err(UpdateError::Unsupported(
            "AutoIncrement or long-value table",
        ));
    }
    let mut index = if definition.indexes().is_empty() && definition.physical_indexes().is_empty() {
        None
    } else {
        if definition.columns().iter().any(|c| {
            matches!(
                c.physical_type(),
                crate::ColumnPhysicalType::Memo | crate::ColumnPhysicalType::LongBinary
            )
        }) {
            return Err(UpdateError::Unsupported("indexed long-value table"));
        }
        let leaf = crate::unique_leaf::validate(&mut database, &definition, budget)?;
        leaf.check_map(&mut database, &definition, budget)?;
        Some(leaf)
    };
    let mut source_page = [0; PAGE_BYTES];
    database.read_raw_page(request.row.page(), &mut source_page, budget)?;
    let patched_page = crate::row_delete_page::remove(
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
    let mut patched_definition =
        crate::row_delete_page::decrement_count(&source_definition, observed_rows, budget)?;
    if let Some(leaf) = &mut index {
        if matches!(patched_page, crate::row_delete_page::Deletion::Released(_)) {
            return Err(UpdateError::Unsupported(
                "indexed deletion requires retained data page",
            ));
        }
        let after = leaf.remove(request.row, budget)?;
        crate::index_key_page::set_distinct_count(&mut patched_definition, leaf.count, budget)?;
        return crate::update_pages::publish_changes(
            path,
            database.into_source(),
            &[
                crate::update_pages::PageChange {
                    page: request.row.page(),
                    before: &source_page,
                    after: patched_page.image().as_bytes(),
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
        );
    }
    if matches!(patched_page, crate::row_delete_page::Deletion::Released(_)) {
        if definition.columns().iter().any(|column| {
            matches!(
                column.physical_type(),
                crate::ColumnPhysicalType::Memo | crate::ColumnPhysicalType::LongBinary
            )
        }) {
            return Err(UpdateError::Unsupported("long-value page release"));
        }
        let maps = crate::allocation_patch::plan(
            &mut database,
            &definition,
            request.row.page(),
            crate::allocation_patch::AllocationChange::Release,
            budget,
        )?;
        let definition_change = crate::update_pages::PageChange {
            page: definition.root(),
            before: &source_definition,
            after: patched_definition.as_bytes(),
        };
        let mut changes = [definition_change; 5];
        changes[0] = crate::update_pages::PageChange {
            page: request.row.page(),
            before: &source_page,
            after: patched_page.image().as_bytes(),
        };
        let map_changes = maps.changes();
        let count = 2 + map_changes.len();
        for (change, map) in changes.iter_mut().skip(2).zip(map_changes) {
            *change = map;
        }
        return crate::update_pages::publish_changes(
            path,
            database.into_source(),
            &changes[..count],
            budget,
            hook,
        );
    }
    crate::update_pages::publish_changes(
        path,
        database.into_source(),
        &[
            crate::update_pages::PageChange {
                page: request.row.page(),
                before: &source_page,
                after: patched_page.image().as_bytes(),
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
