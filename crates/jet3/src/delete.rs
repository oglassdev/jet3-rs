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

/// Deletes one ordinary row, compacting its page or releasing an emptied page.
///
/// Supports relationship-free tables, retaining any AutoNumber state. Memo/OLE fragments
/// are removed from their independent column storage after complete reference
/// and ownership validation; emptied payload pages become globally free.
/// Up to three indexes with one or two supported numeric fields admit deletion,
/// including duplicate and nullable keys. Each matching entry is removed by row
/// locator and changed trees retain their roots. Surplus index pages remain
/// reserved for reuse; retained index counters are unchanged.
/// Slots must be ordinary live rows or known empty `c000` tombstones;
/// inline maps must consistently identify the page as owned and allocated. Later rows move
/// upward without changing their physical slot numbers or stored values. The
/// deleted slot becomes an empty tombstone; existing tombstone flags are retained.
/// A page containing one live row is released through its existing
/// inline global/owned/available maps. Its physical slot count is retained and
/// all slots become empty tombstones. Inconsistent free/count metadata is refused.
/// On retained unindexed pages, only shifted row bytes, affected directory offsets,
/// free-byte count, table row count and available membership change. Availability
/// records whether a minimum row and slot fit. Vacated slack, page zero and unrelated
/// objects remain exact for retained pages. Released pages change their tag, directory
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
    let auto = crate::auto_number_mutation::AutoNumber::load(&definition)?;
    let mut index = if definition.indexes().is_empty() && definition.physical_indexes().is_empty() {
        None
    } else {
        Some(crate::index_mutation::load(
            &mut database,
            &definition,
            budget,
        )?)
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
        while let Some(mut row) = rows.next_row()? {
            if let Some(auto) = auto {
                auto.read(&mut row)?;
            }
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
    let available =
        crate::allocation_patch::available(&mut database, &definition, request.row.page(), budget)?;
    let mut source_definition = [0; PAGE_BYTES];
    database.read_raw_page(definition.root(), &mut source_definition, budget)?;
    let patched_definition =
        crate::row_delete_page::decrement_count(&source_definition, observed_rows, budget)?;
    let mut long_values = crate::long_value_mutation::LongValues::load(
        &mut database,
        &definition,
        Some(request.row),
        budget,
    )?;
    long_values.remove_selected(budget)?;
    let mut edits = crate::page_edits::PageEdits::new(database.geometry().page_count());
    long_values.stage(&mut database, &mut edits, budget)?;
    edits.replace(
        crate::update_pages::PageChange {
            page: request.row.page(),
            before: &source_page,
            after: patched_page.image().as_bytes(),
        },
        budget,
    )?;
    edits.replace(
        crate::update_pages::PageChange {
            page: definition.root(),
            before: &source_definition,
            after: patched_definition.as_bytes(),
        },
        budget,
    )?;
    let allocation = if matches!(patched_page, crate::row_delete_page::Deletion::Released(_)) {
        crate::allocation_patch::AllocationChange::Release { available }
    } else {
        let minimum = crate::row_insert_page::minimum_length(definition.columns(), budget)?;
        crate::allocation_patch::AllocationChange::Retain {
            before: available,
            available: crate::row_insert_page::has_capacity(
                patched_page.image().as_bytes(),
                minimum,
            ),
        }
    };
    let maps = crate::allocation_patch::plan(
        &mut database,
        &definition,
        request.row.page(),
        allocation,
        budget,
    )?;
    maps.stage(&mut database, &mut edits, budget)?;
    if let Some(index) = &mut index {
        index.remove(request.row, budget)?;
        index.stage(&mut database, &definition, &mut edits, budget)?;
    }
    edits.publish(path, database.into_source(), budget, hook)
}

#[cfg(all(test, unix))]
#[path = "delete_tests.rs"]
mod tests;
