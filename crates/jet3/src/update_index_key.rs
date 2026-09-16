//! Indexed field updates share the complete numeric tree mutation planner.
use crate::{
    DatabaseReader, FieldUpdate, FileSource, ResourceBudget, TableDefinition, UpdateError,
};

pub(crate) fn plan(
    database: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    request: FieldUpdate<'_>,
    budget: &mut ResourceBudget,
) -> Result<Option<crate::index_mutation::Indexes>, UpdateError> {
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
    let mut index = crate::index_mutation::load(database, table, budget)?;
    index.replace_field(database, table, request, budget)?;
    Ok(Some(index))
}
