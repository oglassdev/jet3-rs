//! Named Boolean column constraints using the EXP-0266/0283 property grammar.
use crate::{
    ColumnPhysicalType, ColumnPropertyError, ColumnStorageClass, DatabaseReader, ReadAt,
    ResourceBudget, RowValue, RowWriteError, TableDefinition, UpdateError,
};

use crate::column_property_reader::ColumnOptions;

fn stores_null(kind: ColumnPhysicalType, value: RowValue<'_>) -> bool {
    kind != ColumnPhysicalType::Boolean
        && (matches!(value, RowValue::Null)
            || matches!(
                (kind, value),
                (ColumnPhysicalType::Binary, RowValue::Binary([]))
                    | (ColumnPhysicalType::LongBinary, RowValue::LongBinary([]))
            ))
}

fn empty_string(
    kind: ColumnPhysicalType,
    storage: ColumnStorageClass,
    value: RowValue<'_>,
) -> bool {
    matches!(
        (kind, storage, value),
        (
            ColumnPhysicalType::Text,
            ColumnStorageClass::Variable { .. },
            RowValue::Text([])
        ) | (ColumnPhysicalType::Memo, _, RowValue::Memo([]))
    )
}

pub(crate) fn check_value(
    ordinal: u16,
    kind: ColumnPhysicalType,
    storage: ColumnStorageClass,
    options: ColumnOptions,
    value: RowValue<'_>,
) -> Result<(), RowWriteError> {
    if options.required && stores_null(kind, value) {
        return Err(RowWriteError::RequiredValueMissing {
            ordinal,
            physical_type: kind,
        });
    }
    if !options.allow_zero_length && empty_string(kind, storage, value) {
        return Err(RowWriteError::ZeroLengthNotAllowed {
            ordinal,
            physical_type: kind,
        });
    }
    Ok(())
}

pub(crate) fn options<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    table: &TableDefinition,
    budget: &mut ResourceBudget,
) -> Result<[ColumnOptions; 255], ColumnPropertyError> {
    let root = database.catalog(budget)?.root();
    crate::column_property_values::Properties::load(database, root, &[table.root()], budget)?
        .options(database, table, budget)
}

pub(crate) fn check<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    table: &TableDefinition,
    values: &[RowValue<'_>],
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    if !table.columns().iter().zip(values).any(|(column, value)| {
        stores_null(column.physical_type(), *value)
            || empty_string(column.physical_type(), column.storage(), *value)
    }) {
        return Ok(());
    }
    let options = options(database, table, budget)?;
    for ((column, value), option) in table.columns().iter().zip(values).zip(options) {
        check_value(
            column.ordinal().get(),
            column.physical_type(),
            column.storage(),
            option,
            *value,
        )?;
    }
    Ok(())
}
