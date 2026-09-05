//! Bounded key/row correlation; only fixed-size isolated-leaf replacement.
use crate::index_key_page::RECORD_BYTES;
use crate::{
    DatabaseReader, FieldUpdate, FileSource, PAGE_BYTES, PageImage, PageNumber, ResourceBudget,
    RowValue, TableDefinition, UpdateError,
};

pub(crate) struct KeyChange {
    pub(crate) page: PageNumber,
    pub(crate) before: [u8; PAGE_BYTES],
    pub(crate) after: PageImage,
}

pub(crate) fn plan(
    database: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    request: FieldUpdate<'_>,
    budget: &mut ResourceBudget,
) -> Result<Option<KeyChange>, UpdateError> {
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
    let mut leaf = crate::unique_leaf::validate(database, table, budget)?;
    let RowValue::Long(value) = request.value else {
        return Err(UpdateError::Unsupported("key update requires present Long"));
    };
    let replacement = crate::long_index_key::encode(value, leaf.direction);
    budget.charge_work_units((leaf.count * RECORD_BYTES) as u64)?;
    let mut selected = None;
    for (ordinal, record) in leaf.records[..leaf.count].iter().enumerate() {
        if crate::index_key_page::locator(record) == Some(request.row) {
            selected = Some(ordinal);
        } else if record[..replacement.len()] == replacement {
            return Err(UpdateError::Unsupported("duplicate unique key"));
        }
    }
    let selected = selected.ok_or(UpdateError::NotFound("indexed row"))?;
    leaf.records[selected][..replacement.len()].copy_from_slice(&replacement);
    budget.charge_work_units((leaf.count * leaf.count) as u64)?;
    leaf.records[..leaf.count].sort_unstable();
    let after = crate::index_key_page::replace(&leaf.before, &leaf.records[..leaf.count], budget)?;
    Ok(Some(KeyChange {
        page: leaf.page,
        before: leaf.before,
        after,
    }))
}
