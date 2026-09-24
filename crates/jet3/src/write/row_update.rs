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
/// The row stays in its slot when it fits, otherwise it moves to hidden
/// overflow storage. Indexes, long-value storage, allocation maps and enforced
/// relationships (including cascades) are updated and checked, and every
/// affected row publishes in one atomic replacement; all other bytes are
/// preserved. Callers must exclude other writers for the whole operation.
/// `budget` bounds planning, copying and verification.
///
/// # Errors
///
/// Returns [`UpdateError`] when the file or request is outside the supported
/// scope, a key or relationship constraint refuses the change, the table
/// stores a validation rule (rules are not evaluated), or `budget` is
/// exhausted; the original file is then unchanged. Publication failures
/// identify their stage. See `docs/plans/V1_SCOPE.md` for the supported scope.
pub fn update_row(
    path: impl AsRef<Path>,
    request: RowUpdate<'_>,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    update_with_hook(path.as_ref(), request, budget, |_| Ok::<(), Infallible>(()))
}

pub(super) fn update_with_hook<H, HE>(
    path: &Path,
    request: RowUpdate<'_>,
    budget: &mut ResourceBudget,
    hook: H,
) -> Result<(), UpdateError>
where
    H: FnMut(PublishStage) -> Result<(), HE>,
    HE: StdError + Send + Sync + 'static,
{
    let change = crate::relationship::mutation::Change::Replace(request.row, request.values);
    super::driver::apply(path, request.table, change, budget, hook).map(drop)
}

pub(crate) fn plan(
    database: &mut DatabaseReader<crate::FileSource>,
    definition: &crate::TableDefinition,
    request: RowUpdate<'_>,
    check_relationships: bool,
    budget: &mut ResourceBudget,
) -> Result<crate::write::page_edits::PageEdits, UpdateError> {
    let graph = crate::row::mutation_graph::RowGraph::load(
        database,
        definition,
        Some(request.row),
        budget,
    )?;
    let auto = crate::write::auto_number::AutoNumber::load(definition)?;
    let mut lowered = [RowValue::Null; u8::MAX as usize];
    if let Some(auto) = auto {
        auto.copy_values(request.values, &mut lowered, budget)?;
    }
    let mut index = if definition.physical_indexes().is_empty() {
        None
    } else {
        Some(crate::index::mutation::load(database, definition, budget)?)
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
        let mut rows = database.rows(definition, budget)?;
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
    crate::properties::value_policy::check(database, definition, values, budget)?;
    let mut long_values = crate::long_value::mutation::LongValues::load(
        database,
        definition,
        Some(request.row),
        budget,
    )?;
    long_values.remove_selected(budget)?;
    let mut encoded = [0; PAGE_BYTES];
    let length = long_values.encode_row(&layout[..columns.len()], values, &mut encoded, budget)?;
    if check_relationships {
        crate::relationship::mutation::check(
            database,
            definition,
            request.table,
            crate::relationship::mutation::Change::Replace(request.row, values),
            budget,
        )?;
    }
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
    budget.charge_work_units(4)?;
    crate::row::data_page::check_table_rows(&count_page, observed)?;
    let mut edits = crate::write::page_edits::PageEdits::new(database.geometry().page_count());
    long_values.stage(database, &mut edits, budget)?;
    crate::row::mutation_place::replace(
        database,
        definition,
        &graph.selected,
        &encoded[..length],
        &minimum[..minimum_length],
        &mut edits,
        budget,
    )?;
    if let Some(index) = &mut index {
        index.replace(request.row, values, budget)?;
        index.stage(database, definition, &mut edits, budget)?;
    }
    Ok(edits)
}
