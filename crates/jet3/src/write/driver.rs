//! Shared open, guard, cascade, plan and publish sequence for row writes.
use crate::{
    ColumnDefinition, ColumnPhysicalType, ColumnStorageClass, DatabaseReader, FieldUpdate,
    PublishStage, ResourceBudget, RowColumnLayout, RowDelete, RowLocator, RowUpdate, WriteError,
    relationship::mutation::Change,
};
use std::{error::Error as StdError, path::Path};

/// Applies one row change and returns the inserted row's locator for inserts.
pub(super) fn apply<H, HE>(
    path: &Path,
    table: &[u8],
    change: Change<'_>,
    budget: &mut ResourceBudget,
    hook: H,
) -> Result<Option<RowLocator>, WriteError>
where
    H: FnMut(PublishStage) -> Result<(), HE>,
    HE: StdError + Send + Sync + 'static,
{
    let mut database = DatabaseReader::open(path, budget)?;
    super::update::require_writable_sort_order(&database)?;
    let definition = super::update::indexed_writable_table(&mut database, table, budget)?;
    if matches!(change, Change::Replace(..) | Change::Field(..)) {
        let options = crate::properties::value_policy::options(&mut database, &definition, budget)?;
        crate::properties::value_policy::refuse_rules(&options, &definition)?;
    }
    if let Some(cascade) =
        crate::relationship::cascade::prepare(&mut database, &definition, table, change, budget)?
    {
        cascade.publish(path, database, budget, hook)?;
        return Ok(None);
    }
    let (edits, inserted) = match change {
        Change::Insert(values) => {
            let (edits, row) =
                super::insert::plan(&mut database, &definition, table, values, true, budget)?;
            (edits, Some(row))
        }
        Change::Replace(row, values) => {
            let request = RowUpdate { table, row, values };
            let edits = super::row_update::plan(&mut database, &definition, request, true, budget)?;
            (edits, None)
        }
        Change::Field(row, column, value) => {
            let request = FieldUpdate {
                table,
                row,
                column,
                value,
            };
            let edits = super::update::plan(&mut database, &definition, request, true, budget)?;
            (edits, None)
        }
        Change::Delete(row) => {
            let request = RowDelete { table, row };
            let edits = super::delete::plan(&mut database, &definition, request, true, budget)?;
            (edits, None)
        }
    };
    edits.publish(path, database, budget, hook)?;
    Ok(inserted)
}

/// The table's row layout in column order; refuses gaps in column ordinals.
pub(super) fn row_layout(
    columns: &[ColumnDefinition],
    budget: &mut ResourceBudget,
) -> Result<[RowColumnLayout; u8::MAX as usize], WriteError> {
    if columns.len() > usize::from(u8::MAX) {
        return Err(WriteError::Unsupported("row column count"));
    }
    let mut layout = [RowColumnLayout::new(
        ColumnPhysicalType::Long,
        ColumnStorageClass::Fixed { offset: 0 },
        4,
    ); u8::MAX as usize];
    budget.charge_items(columns.len() as u64)?;
    for (ordinal, (target, column)) in layout.iter_mut().zip(columns).enumerate() {
        if usize::from(column.ordinal().get()) != ordinal {
            return Err(WriteError::Unsupported("noncontiguous column ordinals"));
        }
        *target = column.into();
    }
    Ok(layout)
}
