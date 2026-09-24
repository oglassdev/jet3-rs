//! Shared open, guard, cascade, plan and publish sequence for row writes.
use crate::{
    DatabaseReader, FieldUpdate, PublishStage, ResourceBudget, RowDelete, RowLocator, RowUpdate,
    UpdateError, relationship::mutation::Change,
};
use std::{error::Error as StdError, path::Path};

/// Applies one row change and returns the inserted row's locator for inserts.
pub(super) fn apply<H, HE>(
    path: &Path,
    table: &[u8],
    change: Change<'_>,
    budget: &mut ResourceBudget,
    hook: H,
) -> Result<Option<RowLocator>, UpdateError>
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
