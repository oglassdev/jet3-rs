use super::*;
use crate::{
    ColumnOrdinal, ColumnPropertyError, RowUpdate, RowValue, RowWriteError, TableValidationError,
    TextCodePage, UpdateError, ValidationError, ValueKind, create_database_with_rows, insert_row,
    update_row,
};

fn first_row(path: &std::path::Path) -> Result<crate::RowLocator, Box<dyn std::error::Error>> {
    let mut work = budget();
    let mut db = DatabaseReader::open(path, &mut work)?;
    let table = crate::update::indexed_writable_table(&mut db, b"Rows", &mut work)?;
    let mut rows = db.rows(&table, &mut work)?;
    Ok(rows.next_row()?.ok_or("row")?.locator())
}

#[test]
fn required_columns_enforce_nulls_without_indexes_and_keep_scalar_exceptions() -> TestResult {
    let directory = TestDirectory::create()?;
    let path = directory.target();
    let columns = [
        ColumnSpec::new(b"Key", ColumnType::Long).with_required(),
        ColumnSpec::new(b"Optional", ColumnType::Long),
        ColumnSpec::new(b"Flag", ColumnType::Boolean).with_required(),
        ColumnSpec::new(b"Sequence", ColumnType::AutoIncrement).with_required(),
    ];
    assert!(!columns[3].required());
    let table = TableSpec {
        name: b"Rows",
        columns: &columns,
        indexes: &[],
    };
    let invalid = [
        RowValue::Null,
        RowValue::Null,
        RowValue::Null,
        RowValue::AutoIncrement,
    ];
    let error = create_database_with_rows(&path, &table, &[&invalid], &mut budget())
        .err()
        .ok_or("null creation accepted")?;
    assert!(
        matches!(
            error,
            CreateDatabaseError::Compose(ComposeError::Row(RowWriteError::RequiredValueMissing {
                ordinal: 0,
                ..
            }))
        ),
        "{error:?}"
    );
    assert!(!path.exists());
    let values = [
        RowValue::Long(7),
        RowValue::Null,
        RowValue::Null,
        RowValue::AutoIncrement,
    ];
    create_database_with_rows(&path, &table, &[&values], &mut budget())?;
    let original = fs::read(&path)?;
    let row = first_row(&path)?;
    for result in [
        insert_row(&path, b"Rows", &invalid, &mut budget()).map(|_| ()),
        update_row(
            &path,
            RowUpdate {
                table: b"Rows",
                row,
                values: &[
                    RowValue::Null,
                    RowValue::Null,
                    RowValue::Null,
                    RowValue::Long(1),
                ],
            },
            &mut budget(),
        ),
    ] {
        assert!(matches!(
            result,
            Err(UpdateError::Encoding(RowWriteError::RequiredValueMissing {
                ordinal: 0,
                ..
            }))
        ));
        assert_eq!(fs::read(&path)?, original);
    }
    insert_row(
        &path,
        b"Rows",
        &[
            RowValue::Long(0),
            RowValue::Null,
            RowValue::Null,
            RowValue::AutoIncrement,
        ],
        &mut budget(),
    )?;
    let mut work = budget();
    let mut db = DatabaseReader::open(&path, &mut work)?;
    db.validate(TextCodePage::Windows1252, &mut work)?;
    let definition = crate::update::indexed_writable_table(&mut db, b"Rows", &mut work)?;
    let options = crate::column_value_policy::options(&mut db, &definition, &mut work)?;
    assert!(options[0].required && options[2].required);
    assert!(!options[1].required && !options[3].required);
    let mut rows = db.rows(&definition, &mut work)?;
    let mut ids = Vec::new();
    while let Some(mut row) = rows.next_row()? {
        assert!(matches!(
            row.value(ColumnOrdinal::new(2), TextCodePage::Windows1252)?
                .ok_or("flag")?
                .kind(),
            ValueKind::Boolean(false)
        ));
        let value = row
            .value(ColumnOrdinal::new(3), TextCodePage::Windows1252)?
            .ok_or("sequence")?;
        let ValueKind::Long(id) = value.kind() else {
            return Err("sequence type".into());
        };
        ids.push(*id);
    }
    assert_eq!(ids, [1, 2]);
    Ok(())
}

#[test]
fn required_payloads_distinguish_empty_strings_from_storage_nulls() -> TestResult {
    let directory = TestDirectory::create()?;
    let path = directory.target();
    let columns = [
        ColumnSpec::new(b"Text", ColumnType::Text { max_len: nz(8) })
            .with_required()
            .with_allow_zero_length(),
        ColumnSpec::new(b"Memo", ColumnType::Memo).with_required(),
        ColumnSpec::new(b"Binary", ColumnType::Binary { max_len: nz(8) }).with_required(),
        ColumnSpec::new(b"Ole", ColumnType::LongBinary).with_required(),
        ColumnSpec::new(b"Fixed", ColumnType::FixedText { len: nz(4) }).with_required(),
    ];
    let values = [
        RowValue::Text(b""),
        RowValue::Memo(b"value"),
        RowValue::Binary(b"x"),
        RowValue::LongBinary(b"x"),
        RowValue::Text(b"    "),
    ];
    create_database_with_rows(
        &path,
        &TableSpec {
            name: b"Rows",
            columns: &columns,
            indexes: &[],
        },
        &[&values],
        &mut budget(),
    )?;
    let original = fs::read(&path)?;
    for (column, value, zero_length) in [
        (0, RowValue::Null, false),
        (1, RowValue::Null, false),
        (1, RowValue::Memo(b""), true),
        (2, RowValue::Binary(b""), false),
        (3, RowValue::LongBinary(b""), false),
        (4, RowValue::Null, false),
    ] {
        let mut invalid = values;
        invalid[column] = value;
        let error = insert_row(&path, b"Rows", &invalid, &mut budget())
            .err()
            .ok_or("invalid value accepted")?;
        let expected = if zero_length {
            RowWriteError::ZeroLengthNotAllowed {
                ordinal: column as u16,
                physical_type: columns[column].physical_type(),
            }
        } else {
            RowWriteError::RequiredValueMissing {
                ordinal: column as u16,
                physical_type: columns[column].physical_type(),
            }
        };
        assert!(matches!(error, UpdateError::Encoding(actual) if actual == expected));
        assert_eq!(fs::read(&path)?, original);
    }
    insert_row(&path, b"Rows", &values, &mut budget())?;
    let mut work = budget();
    DatabaseReader::open(&path, &mut work)?.validate(TextCodePage::Windows1252, &mut work)?;
    Ok(())
}

#[test]
fn required_property_corruption_and_stored_nulls_are_reported() -> TestResult {
    let directory = TestDirectory::create()?;
    let path = directory.target();
    let columns = [ColumnSpec::new(b"Key", ColumnType::Long).with_required()];
    let table = TableSpec {
        name: b"Rows",
        columns: &columns,
        indexes: &[],
    };
    create_database_with_rows(&path, &table, &[&[RowValue::Long(1)]], &mut budget())?;
    let property = crate::column_properties::ColumnProperties::new(&columns).ok_or("properties")?;
    let mut encoded = vec![0; property.len()];
    property.encode(&mut encoded, &mut budget())?;
    let original = fs::read(&path)?;
    let start = original
        .windows(encoded.len())
        .position(|bytes| bytes == encoded)
        .ok_or("property payload")?;
    let boolean = start + encoded.len() - 1;
    let mut changed = original.clone();
    changed[boolean] = 1;
    fs::write(&path, &changed)?;
    assert!(matches!(
        insert_row(&path, b"Rows", &[RowValue::Null], &mut budget()),
        Err(UpdateError::ColumnProperties(ColumnPropertyError::Invalid(
            "named Boolean property record"
        )))
    ));
    assert_eq!(fs::read(&path)?, changed);
    let mut work = budget();
    assert!(matches!(
        DatabaseReader::open(&path, &mut work)?.validate(TextCodePage::Windows1252, &mut work),
        Err(ValidationError::Table {
            source: TableValidationError::ColumnProperties(ColumnPropertyError::Invalid(_)),
            ..
        })
    ));

    changed[boolean] = 0;
    fs::write(&path, &changed)?;
    let null_row = insert_row(&path, b"Rows", &[RowValue::Null], &mut budget())?;
    let mut changed = fs::read(&path)?;
    changed[boolean] = 0xff;
    fs::write(&path, &changed)?;
    let mut work = budget();
    assert!(
        matches!(DatabaseReader::open(&path, &mut work)?.validate(TextCodePage::Windows1252, &mut work),
        Err(ValidationError::Table { source: TableValidationError::RequiredValue { row, column }, .. })
            if row == null_row && column == ColumnOrdinal::new(0))
    );
    assert_eq!(fs::read(&path)?, changed);

    let mut db = DatabaseReader::open(&path, &mut budget())?;
    let definition = crate::update::indexed_writable_table(&mut db, b"Rows", &mut budget())?;
    let field_block = 4 + u32::from_le_bytes(encoded[4..8].try_into()?) as usize;
    let block_len = u32::from_le_bytes(encoded[field_block..field_block + 4].try_into()?);
    let record = encoded[encoded.len() - 9..].to_vec();
    encoded.extend_from_slice(&record);
    encoded[field_block..field_block + 4].copy_from_slice(&(block_len + 9).to_le_bytes());
    assert!(matches!(
        crate::column_property_reader::decode(&encoded, definition.columns(), &mut budget()),
        Err(ColumnPropertyError::Invalid(
            "named Boolean property record"
        ))
    ));
    Ok(())
}
