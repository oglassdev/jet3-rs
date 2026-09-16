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
    /// Existing logical locator obtained while the source is unchanged.
    pub row: RowLocator,
    /// Complete replacement row, including raw Memo/OLE payload values.
    pub values: &'a [RowValue<'a>],
}

/// Replaces a complete row while retaining its logical locator.
///
/// Supports scalar/null/Boolean/Text/Binary values and independent Memo/OLE
/// columns. Enforced non-cascading relationships with one to ten ordered scalar fields require matching
/// parents and reject changes to parent keys referenced by other rows, including
/// null parents while null child keys remain. Foreign keys and parent keys backed only by
/// hidden relationship indexes update their two-word retained state on assignment
/// (EXP-0268/0286), even when the value is unchanged. A self-reference whose foreign
/// physical index precedes its parent requires the key to exist before replacement.
/// Up to 32 indexes with one to ten
/// supported scalar fields admit key and null changes, with uniqueness enforced
/// for fully present keys. Every hidden storage slot must belong to exactly one
/// logical row. Mutation of a selected multi-hop overflow chain is refused.
///
/// A replacement that fits its logical slot is stored there, releasing any old
/// hidden target. Otherwise it replaces the current hidden target when space
/// permits, or allocates hidden storage and rewrites the original logical link
/// directly. EXP-0262 establishes these native transitions. Whole-row encoding
/// limits still apply; overflow does not allow a row to exceed them.
///
/// External payloads are validated against live references and column ownership.
/// Replaced fragments are released or reused, with single and chained storage
/// in separate pools. All payload, row, index and allocation changes publish
/// together. Caller-supplied raw long-value headers are refused.
///
/// Compaction preserves neighboring slots and values. Empty slots become
/// tombstones; emptied pages are released for reuse. Available membership records
/// whether a minimum encoded row and directory slot fit. Changed indexes retain
/// logical row locators. Table counts, page zero and unrelated objects remain
/// exact; newly vacated row slack is retained.
///
/// Callers must exclude external writers throughout this operation on Unix or Windows.
/// One resource budget covers planning, copying and complete private verification.
/// Pre-publication failure preserves the original; errors identify publish stages.
/// An AutoNumber field accepts its unchanged Long value or `RowValue::AutoIncrement`
/// to retain its value. Changing that field is refused and its counter is retained.
/// Every affected enforced, non-cascading relationship is checked, including
/// multiple relationships and self-references. Every child key with at least one
/// non-null component must occur in its parent table. Other rows referencing the
/// selected parent key block replacement even when that key is unchanged. When
/// its parent tree precedes its foreign tree, a self-reference excludes the
/// selected row from this guard and checks its child key against the resulting
/// parent keys (EXP-0292). Cascades are refused.
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
    let graph = crate::row_mutation_graph::RowGraph::load(
        &mut database,
        &definition,
        Some(request.row),
        budget,
    )?;
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
    crate::column_value_policy::check(&mut database, &definition, values, budget)?;
    let mut long_values = crate::long_value_mutation::LongValues::load(
        &mut database,
        &definition,
        Some(request.row),
        budget,
    )?;
    long_values.remove_selected(budget)?;
    let mut encoded = [0; PAGE_BYTES];
    let length = long_values.encode_row(&layout[..columns.len()], values, &mut encoded, budget)?;
    crate::relationship_mutation::check(
        &mut database,
        &definition,
        request.table,
        crate::relationship_mutation::Change::Replace(request.row, values),
        budget,
    )?;
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
    let mut edits = crate::page_edits::PageEdits::new(database.geometry().page_count());
    long_values.stage(&mut database, &mut edits, budget)?;
    crate::row_mutation_place::replace(
        &mut database,
        &definition,
        &graph.selected,
        &encoded[..length],
        &minimum[..minimum_length],
        &mut edits,
        budget,
    )?;
    if let Some(index) = &mut index {
        index.replace(request.row, values, budget)?;
        index.stage(&mut database, &definition, &mut edits, budget)?;
    }
    edits.publish(path, database, budget, hook)
}

#[cfg(all(test, any(unix, windows)))]
#[path = "row_update_tests.rs"]
mod tests;
