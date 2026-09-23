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

/// Deletes one logical row and its hidden storage, compacting or releasing pages.
///
/// Retains any AutoNumber state. Enforced relationships with one to ten ordered
/// scalar fields delete matching children when cascade deletion is enabled;
/// otherwise referencing children block deletion. Memo/OLE fragments
/// are removed from their independent column storage after complete reference
/// and ownership validation; emptied payload pages become globally free.
/// Up to 32 indexes with one to ten supported scalar fields admit deletion,
/// including duplicate and nullable keys. Each matching entry is removed by row
/// locator and changed trees retain their roots. Surplus index pages remain
/// reserved for reuse. Ordinary index counters are unchanged; foreign index
/// deletion updates the two-word retained state recorded by EXP-0268.
/// Ordinary rows and one-link overflow rows are supported. Hidden storage must
/// be uniquely reachable from a logical row; selected multi-hop chains are refused.
/// EXP-0262 establishes deletion of both the logical link and hidden target.
/// Allocation maps must consistently identify affected pages as owned and allocated. Later rows move
/// upward without changing their physical slot numbers or stored values. The
/// deleted slot becomes an empty tombstone; existing tombstone flags are retained.
/// A page containing one remaining physical record is released through its existing
/// global/owned/available maps. Its physical slot count is retained and
/// all slots become empty tombstones. Inconsistent free/count metadata is refused.
/// On retained unindexed pages, only shifted row bytes, affected directory offsets,
/// free-byte count, table row count and available membership change. Availability
/// records whether a minimum row and slot fit. Vacated slack, page zero and unrelated
/// objects remain exact for retained pages. Released pages change their tag, directory
/// word and free count, and their three map bits; payload/slack and file length
/// remain exact.
/// EXP-0232/0238/0239 record finite numeric, long-value and AutoNumber DAO
/// comparisons with these preservation guarantees.
///
/// Callers must exclude external writers throughout this operation on Unix or Windows.
/// The same resource budget covers planning, private copying and full-file
/// verification. Any pre-publication failure preserves the original; publication
/// errors identify their stage, including post-publication sync failures.
/// Every affected enforced relationship is checked, including
/// multiple relationships and self-references. Every child key with a non-null
/// component must occur in its parent table. Cascade selection includes exact
/// partial-null and all-null tuples. All recursively affected rows, indexes and
/// payload storage publish together; failure preserves the complete original.
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
    crate::update::require_general_sort_order(&database)?;
    let definition = crate::update::indexed_writable_table(&mut database, request.table, budget)?;
    if let Some(cascade) = crate::cascade::prepare(
        &mut database,
        &definition,
        request.table,
        crate::relationship_mutation::Change::Delete(request.row),
        budget,
    )? {
        return cascade.publish(path, database, budget, hook);
    }
    let edits = plan(&mut database, &definition, request, true, budget)?;
    edits.publish(path, database, budget, hook)
}

pub(crate) fn plan(
    database: &mut DatabaseReader<crate::FileSource>,
    definition: &crate::TableDefinition,
    request: RowDelete<'_>,
    check_relationships: bool,
    budget: &mut ResourceBudget,
) -> Result<crate::page_edits::PageEdits, UpdateError> {
    let graph =
        crate::row_mutation_graph::RowGraph::load(database, definition, Some(request.row), budget)?;
    if graph.selected.len() > 2 {
        return Err(UpdateError::Unsupported(
            "mutation of multi-hop overflow chain",
        ));
    }
    if check_relationships {
        crate::relationship_mutation::check(
            database,
            definition,
            request.table,
            crate::relationship_mutation::Change::Delete(request.row),
            budget,
        )?;
    }
    let auto = crate::auto_number_mutation::AutoNumber::load(definition)?;
    let mut index = if definition.indexes().is_empty() && definition.physical_indexes().is_empty() {
        None
    } else {
        Some(crate::index_mutation::load(database, definition, budget)?)
    };
    let mut observed_rows = 0_u32;
    let mut found = false;
    {
        let mut rows = database.rows(definition, budget)?;
        while let Some(mut row) = rows.next_row()? {
            if let Some(auto) = auto {
                auto.read(&mut row)?;
            }
            observed_rows = observed_rows
                .checked_add(1)
                .ok_or(UpdateError::Mismatch("row count overflow"))?;
            if row.locator() == request.row {
                found = true;
            }
        }
    }
    if !found {
        return Err(UpdateError::NotFound("row"));
    }
    let mut source_definition = [0; PAGE_BYTES];
    database.read_raw_page(definition.root(), &mut source_definition, budget)?;
    let patched_definition =
        crate::row_delete_page::decrement_count(&source_definition, observed_rows, budget)?;
    let mut long_values = crate::long_value_mutation::LongValues::load(
        database,
        definition,
        Some(request.row),
        budget,
    )?;
    long_values.remove_selected(budget)?;
    let mut edits = crate::page_edits::PageEdits::new(database.geometry().page_count());
    long_values.stage(database, &mut edits, budget)?;
    let mut pages = crate::row_mutation_pages::RowPages::new();
    for row in graph.selected.iter().rev() {
        pages.remove(database, definition.root(), *row, budget)?;
    }
    pages.stage(database, definition, &mut edits, budget)?;
    edits.replace(
        crate::update_pages::PageChange {
            page: definition.root(),
            before: &source_definition,
            after: patched_definition.as_bytes(),
        },
        budget,
    )?;
    if let Some(index) = &mut index {
        index.remove(request.row, budget)?;
        index.stage(database, definition, &mut edits, budget)?;
    }
    Ok(edits)
}

#[cfg(all(test, any(unix, windows)))]
#[path = "delete_tests.rs"]
mod tests;
