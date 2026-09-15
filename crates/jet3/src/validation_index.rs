//! EXP-0062 logical index locators; EXP-0148 null policies; existing scalar key encoders.
use super::{TableValidationError, ValidationReport, add, reserve};
use crate::numeric_index_entry::{EntryError, NumericIndexEntry, NumericIndexField, sort_cost};
use crate::numeric_index_key::NumericKeyType;
use crate::{
    ColumnPhysicalType, ColumnStorageClass, DatabaseReader, IndexNullPolicy, IndexTree, ReadAt,
    ResourceBudget, RowLocator, TableDefinition, UpdateError,
};

pub(super) fn key(row: RowLocator) -> (u64, u8) {
    (row.page().get(), row.slot())
}

fn failure(index: u16, detail: &'static str) -> TableValidationError {
    TableValidationError::IndexContents { index, detail }
}

fn source_error(index: u16, error: UpdateError) -> TableValidationError {
    match error {
        UpdateError::Resource(error)
        | UpdateError::Value(
            crate::ValueError::Resource(error)
            | crate::ValueError::Text(crate::TextError::Resource(error)),
        ) => TableValidationError::Resource(error),
        UpdateError::Index(source) => TableValidationError::Index { index, source },
        UpdateError::Definition(source) => TableValidationError::Definition(source),
        UpdateError::Mismatch(detail) | UpdateError::Unsupported(detail) => failure(index, detail),
        _ => failure(index, "index value cannot be represented by its schema"),
    }
}

pub(super) fn validate<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    table: &TableDefinition,
    index: u16,
    tree: &IndexTree,
    rows: &[RowLocator],
    budget: &mut ResourceBudget,
    report: &mut ValidationReport,
) -> Result<(), TableValidationError> {
    let mut seen = Vec::new();
    reserve(&mut seen, rows.len(), budget).map_err(TableValidationError::Resource)?;
    budget
        .charge_work_units(rows.len() as u64)
        .map_err(TableValidationError::Resource)?;
    seen.resize(rows.len(), false);
    for entry in tree.entries() {
        budget
            .charge_work_units(u64::from(rows.len().max(1).ilog2()) + 1)
            .map_err(TableValidationError::Resource)?;
        let position = rows
            .binary_search_by_key(&key(entry.row()), |row| key(*row))
            .map_err(|_| failure(index, "index references a row outside the live table"))?;
        if std::mem::replace(&mut seen[position], true) {
            return Err(failure(
                index,
                "index references a logical row more than once",
            ));
        }
    }
    let Some(fields) = fields(table, index, budget)? else {
        add(&mut report.uninterpreted_indexes, 1).map_err(TableValidationError::Resource)?;
        add(
            &mut report.uninterpreted_index_entries,
            tree.entries().len() as u64,
        )
        .map_err(TableValidationError::Resource)?;
        return Ok(());
    };
    let physical = &table.physical_indexes()[usize::from(index)];
    let null_policy = if physical.required() {
        IndexNullPolicy::Required
    } else if physical.raw_flags() & 2 != 0 {
        IndexNullPolicy::IgnoreAllNull
    } else {
        IndexNullPolicy::Include
    };
    let mut selected = [false; u8::MAX as usize];
    for field in &fields {
        selected[field.column] = true;
    }
    let mut expected = Vec::new();
    let mut cursor = database
        .rows(table, budget)
        .map_err(|source| TableValidationError::Rows {
            completed_rows: 0,
            source,
        })?;
    let mut completed_rows = 0;
    while let Some(mut row) = cursor
        .next_row()
        .map_err(|source| TableValidationError::Rows {
            completed_rows,
            source,
        })?
    {
        row.budget_mut()
            .charge_items(u8::MAX as u64)
            .map_err(TableValidationError::Resource)?;
        let locator = row.locator();
        let values = crate::numeric_row_values::read(&mut row, &selected)
            .map_err(|error| source_error(index, error))?;
        let record =
            NumericIndexEntry::encode(&fields, &values, null_policy, locator, row.budget_mut())
                .map_err(|error| match error {
                    EntryError::Encoding(error) => TableValidationError::Resource(error),
                    EntryError::NullRequired => failure(index, "null field in a required index"),
                    _ => failure(index, "index value cannot be represented by its schema"),
                })?;
        if let Some(record) = record {
            reserve(&mut expected, 1, row.budget_mut()).map_err(TableValidationError::Resource)?;
            expected.push(record);
        }
        add(&mut completed_rows, 1).map_err(TableValidationError::Resource)?;
    }
    drop(cursor);
    budget
        .charge_work_units(
            (expected.len() as u64)
                .saturating_mul(u64::from(expected.len().max(1).ilog2()) + 1)
                .saturating_mul(sort_cost(&fields)),
        )
        .map_err(TableValidationError::Resource)?;
    expected.sort_unstable_by(|a, b| a.record().cmp(b.record()));
    if physical.unique()
        && expected
            .windows(2)
            .any(|pair| !pair[0].has_null() && pair[0].key() == pair[1].key())
    {
        return Err(failure(index, "duplicate non-null key in a unique index"));
    }
    crate::index_mutation_structure::validate(database, table, tree, &fields, null_policy, budget)
        .map_err(|error| source_error(index, error))?;
    budget
        .charge_work_units(
            (expected.len() as u64)
                .saturating_mul(crate::numeric_index_entry::record_capacity(&fields) as u64),
        )
        .map_err(TableValidationError::Resource)?;
    if tree.entries().len() != expected.len()
        || tree
            .entries()
            .iter()
            .zip(&expected)
            .any(|(actual, wanted)| {
                actual.row() != wanted.locator() || actual.key().raw_bytes() != wanted.key()
            })
    {
        return Err(failure(
            index,
            "index keys and logical row values do not match",
        ));
    }
    add(&mut report.indexes_with_verified_keys, 1).map_err(TableValidationError::Resource)
}

fn fields(
    table: &TableDefinition,
    index: u16,
    budget: &mut ResourceBudget,
) -> Result<Option<Vec<NumericIndexField>>, TableValidationError> {
    let physical = &table.physical_indexes()[usize::from(index)];
    if !(1..=crate::numeric_index_entry::MAX_FIELDS).contains(&physical.fields().len())
        || table.columns().len() > u8::MAX as usize
    {
        return Ok(None);
    }
    budget
        .charge_items(physical.fields().len() as u64)
        .map_err(TableValidationError::Resource)?;
    let mut fields = Vec::new();
    reserve(&mut fields, physical.fields().len(), budget)
        .map_err(TableValidationError::Resource)?;
    for field in physical.fields() {
        let ordinal = usize::from(field.column().get());
        let column = table
            .columns()
            .get(ordinal)
            .ok_or_else(|| failure(index, "missing index column"))?;
        let kind = match column.physical_type() {
            ColumnPhysicalType::Boolean => NumericKeyType::Boolean,
            ColumnPhysicalType::Byte => NumericKeyType::Byte,
            ColumnPhysicalType::Integer => NumericKeyType::Integer,
            ColumnPhysicalType::Long => NumericKeyType::Long,
            ColumnPhysicalType::Currency => NumericKeyType::Currency,
            ColumnPhysicalType::Single => NumericKeyType::Single,
            ColumnPhysicalType::Double => NumericKeyType::Double,
            ColumnPhysicalType::DateTime => NumericKeyType::DateTime,
            ColumnPhysicalType::Guid => NumericKeyType::Guid,
            ColumnPhysicalType::Binary | ColumnPhysicalType::Text => {
                let Ok(max_len @ 1..=255) = u8::try_from(column.size()) else {
                    return Ok(None);
                };
                if column.physical_type() == ColumnPhysicalType::Binary {
                    NumericKeyType::Binary { max_len }
                } else if matches!(column.storage(), ColumnStorageClass::Variable { .. })
                    && column.raw_encoding_context() == &crate::text_index_key::ENCODING_CONTEXT
                {
                    NumericKeyType::Text { max_len }
                } else {
                    return Ok(None);
                }
            }
            _ => return Ok(None),
        };
        fields.push(NumericIndexField {
            column: ordinal,
            direction: field.direction(),
            kind,
        });
    }
    Ok(Some(fields))
}
