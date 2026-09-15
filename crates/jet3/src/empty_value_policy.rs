//! Enforce EXP-0266 empty-value options without changing catalog properties.
use crate::{
    ByteCount, ColumnPhysicalType, ColumnStorageClass, DatabaseReader, InlineLongValue, LongValue,
    LongValueChunkValue, ReadAt, ResourceBudget, RowValue, RowWriteError, TableDefinition,
    TextCodePage, UpdateError, ValueKind,
};

pub(crate) fn check<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    table: &TableDefinition,
    values: &[RowValue<'_>],
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let needs_option = |column: &crate::ColumnDefinition, value: &RowValue<'_>| {
        matches!(
            (column.physical_type(), column.storage(), value),
            (
                ColumnPhysicalType::Text,
                ColumnStorageClass::Variable { .. },
                RowValue::Text([])
            ) | (ColumnPhysicalType::Memo, _, RowValue::Memo([]))
        )
    };
    if !table
        .columns()
        .iter()
        .zip(values)
        .any(|(column, value)| needs_option(column, value))
    {
        return Ok(());
    }
    let payload = load(database, table, budget)?;
    let options = if let Some(payload) = payload {
        crate::column_property_reader::decode(&payload, table.columns(), budget)?
    } else {
        [false; 255]
    };
    for (ordinal, (column, value)) in table.columns().iter().zip(values).enumerate() {
        if needs_option(column, value) && !options.get(ordinal).copied().unwrap_or(false) {
            return Err(RowWriteError::ZeroLengthNotAllowed {
                ordinal: column.ordinal().get(),
                physical_type: column.physical_type(),
            }
            .into());
        }
    }
    Ok(())
}

fn buffer(length: usize, budget: &mut ResourceBudget) -> Result<Vec<u8>, UpdateError> {
    let count = ByteCount::from_usize(length)?;
    budget.check_decoded_value(count)?;
    budget.charge_allocation(count)?;
    let mut bytes = Vec::new();
    bytes
        .try_reserve_exact(length)
        .map_err(|_| crate::Error::Io {
            operation: "reserve stored column properties",
            kind: std::io::ErrorKind::OutOfMemory,
        })?;
    Ok(bytes)
}

fn load<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    table: &TableDefinition,
    budget: &mut ResourceBudget,
) -> Result<Option<Vec<u8>>, UpdateError> {
    let root = database.catalog(budget)?.root();
    let catalog = database.table_definition(root, budget)?;
    let column = |name: &[u8]| {
        catalog
            .columns()
            .iter()
            .find(|column| column.name().raw_bytes() == name)
            .map(|column| column.ordinal())
            .ok_or(UpdateError::Mismatch("catalog property column"))
    };
    let id = column(b"Id")?;
    let property = column(b"LvProp")?;
    let owned = crate::column_property_ownership::load(database, &catalog, property, budget)?;
    let mut rows = database.rows(&catalog, budget)?;
    while let Some(mut row) = rows.next_row()? {
        let matches = matches!(row.value(id, TextCodePage::Windows1252)?.ok_or(UpdateError::Mismatch("catalog object Id"))?.kind(),
            ValueKind::Long(id) if u64::from(*id as u32) == table.root().get());
        if !matches {
            continue;
        }
        let value = row
            .value(property, TextCodePage::Windows1252)?
            .ok_or(UpdateError::Mismatch("catalog LvProp"))?;
        match value.kind() {
            ValueKind::Null => return Ok(None),
            ValueKind::LongValue(LongValue::Inline { value, .. }) => {
                let source = match value {
                    InlineLongValue::Text(text) => text.raw_bytes(),
                    InlineLongValue::Binary(bytes) => bytes,
                };
                let mut saved = [0; crate::PAGE_BYTES];
                let length = source.len();
                saved
                    .get_mut(..length)
                    .ok_or(UpdateError::Mismatch("inline property capacity"))?
                    .copy_from_slice(source);
                let mut bytes = buffer(length, rows.owned.budget_mut())?;
                bytes.extend_from_slice(&saved[..length]);
                return Ok(Some(bytes));
            }
            ValueKind::LongValue(LongValue::External(reference)) => {
                let reference = *reference;
                let mut stream = rows.long_value(reference)?;
                let mut bytes = buffer(reference.length() as usize, stream.budget_mut())?;
                while let Some(chunk) = stream.next_chunk()? {
                    if !owned.contains(chunk.locator().page())? {
                        return Err(UpdateError::Mismatch(
                            "property reference outside column map",
                        ));
                    }
                    let source = match chunk.value() {
                        LongValueChunkValue::Text(text) => text.raw_bytes(),
                        LongValueChunkValue::Binary(bytes) => bytes,
                    };
                    if source.len() > reference.length() as usize - bytes.len() {
                        return Err(UpdateError::Mismatch("property declared length"));
                    }
                    bytes.extend_from_slice(source);
                }
                return Ok(Some(bytes));
            }
            _ => return Err(UpdateError::Mismatch("catalog LvProp type")),
        }
    }
    Err(UpdateError::NotFound("table column properties"))
}
