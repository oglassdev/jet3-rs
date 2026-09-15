//! Indexed fixed-field updates share the complete Long tree mutation planner.
use crate::{
    DatabaseReader, FieldUpdate, FileSource, ResourceBudget, RowValue, TableDefinition, UpdateError,
};

pub(crate) fn plan(
    database: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    request: FieldUpdate<'_>,
    budget: &mut ResourceBudget,
) -> Result<Option<crate::unique_index::UniqueIndex>, UpdateError> {
    let mut indexed = false;
    for index in table.physical_indexes() {
        for key in index.fields() {
            budget.charge_items(1)?;
            indexed |= key.column() == request.column;
        }
    }
    if !indexed {
        return Ok(None);
    }
    let mut index = crate::unique_index::load(database, table, budget)?;
    let RowValue::Long(value) = request.value else {
        return Err(UpdateError::Unsupported("key update requires present Long"));
    };
    index.replace(request.row, value, budget)?;
    Ok(Some(index))
}
