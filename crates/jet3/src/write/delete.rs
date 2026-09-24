//! Bounded existing-row deletion using the compaction observed in EXP-0162.
use crate::{DatabaseReader, PAGE_BYTES, PublishStage, ResourceBudget, RowLocator, WriteError};
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

/// Deletes one logical row and its hidden storage.
///
/// Later rows on the page move without changing their slots; a page left with
/// no live rows is released. Index entries, long-value fragments, allocation
/// maps and enforced relationships (including cascading deletes) are updated
/// and checked, and every change publishes in one atomic replacement; all
/// other bytes are preserved. Callers must exclude other writers for the whole
/// operation. `budget` bounds planning, copying and verification.
///
/// # Errors
///
/// Returns [`WriteError`] when the file or request is outside the supported
/// scope, a relationship constraint refuses the deletion, or `budget` is
/// exhausted; the original file is then unchanged. Publication failures
/// identify their stage. See `docs/plans/V1_SCOPE.md` for the supported scope.
pub fn delete_row(
    path: impl AsRef<Path>,
    request: RowDelete<'_>,
    budget: &mut ResourceBudget,
) -> Result<(), WriteError> {
    delete_with_hook(path.as_ref(), request, budget, |_| Ok::<(), Infallible>(()))
}

pub(super) fn delete_with_hook<H, HE>(
    path: &Path,
    request: RowDelete<'_>,
    budget: &mut ResourceBudget,
    hook: H,
) -> Result<(), WriteError>
where
    H: FnMut(PublishStage) -> Result<(), HE>,
    HE: StdError + Send + Sync + 'static,
{
    let change = crate::relationship::mutation::Change::Delete(request.row);
    super::driver::apply(path, request.table, change, budget, hook).map(drop)
}

pub(crate) fn plan(
    database: &mut DatabaseReader<crate::FileSource>,
    definition: &crate::TableDefinition,
    request: RowDelete<'_>,
    check_relationships: bool,
    budget: &mut ResourceBudget,
) -> Result<crate::write::page_edits::PageEdits, WriteError> {
    let graph = crate::row::mutation_graph::RowGraph::load(
        database,
        definition,
        Some(request.row),
        budget,
    )?;
    if graph.selected.len() > 2 {
        return Err(WriteError::Unsupported(
            "mutation of multi-hop overflow chain",
        ));
    }
    if check_relationships {
        crate::relationship::mutation::check(
            database,
            definition,
            request.table,
            crate::relationship::mutation::Change::Delete(request.row),
            budget,
        )?;
    }
    let auto = crate::write::auto_number::AutoNumber::load(definition)?;
    let mut index = if definition.indexes().is_empty() && definition.physical_indexes().is_empty() {
        None
    } else {
        Some(crate::index::mutation::load(database, definition, budget)?)
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
                .ok_or(WriteError::Mismatch("row count overflow"))?;
            if row.locator() == request.row {
                found = true;
            }
        }
    }
    if !found {
        return Err(WriteError::NotFound("row"));
    }
    let mut source_definition = [0; PAGE_BYTES];
    database.read_raw_page(definition.root(), &mut source_definition, budget)?;
    let patched_definition =
        crate::row::data_page::count_table_row(&source_definition, observed_rows, false, budget)?;
    let mut long_values = crate::long_value::mutation::LongValues::load(
        database,
        definition,
        Some(request.row),
        budget,
    )?;
    long_values.remove_selected(budget)?;
    let mut edits = crate::write::page_edits::PageEdits::new(database.geometry().page_count());
    long_values.stage(database, &mut edits, budget)?;
    let mut pages = crate::row::mutation_pages::RowPages::new();
    for row in graph.selected.iter().rev() {
        pages.remove(database, definition.root(), *row, budget)?;
    }
    pages.stage(database, definition, &mut edits, budget)?;
    edits.replace(
        crate::write::update_pages::PageChange {
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
