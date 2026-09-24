//! Named Boolean column constraints using the EXP-0266/0283 grammar and EXP-0285 presence semantics.
//! Stored EXP-0299 validation rules are not evaluated, so writes to their tables are refused.
use crate::{
    ColumnPhysicalType, ColumnPropertyError, ColumnStorageClass, DatabaseReader, ReadAt,
    ResourceBudget, RowValue, RowWriteError, TableDefinition, WriteError,
    properties::reader::{ColumnOptions, PropertyOptions},
};

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
    if options.allow_zero_length == Some(false) && empty_string(kind, storage, value) {
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
) -> Result<PropertyOptions, ColumnPropertyError> {
    let root = database.catalog(budget)?.root();
    crate::properties::values::Properties::load(database, root, &[table.root()], budget)?
        .options(database, table, budget)
}

pub(crate) fn check<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    table: &TableDefinition,
    values: &[RowValue<'_>],
    budget: &mut ResourceBudget,
) -> Result<(), WriteError> {
    let options = options(database, table, budget)?;
    refuse_rules(&options, table)?;
    for ((column, value), option) in table.columns().iter().zip(values).zip(options.columns) {
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

/// Refuses a write when the table or one of its columns stores a nonempty rule.
pub(crate) fn refuse_rules(
    options: &PropertyOptions,
    table: &TableDefinition,
) -> Result<(), WriteError> {
    match options.validation_rule(table.columns()) {
        Some(column) => Err(WriteError::ValidationRule {
            column: column.map(crate::ColumnOrdinal::new),
        }),
        None => Ok(()),
    }
}
