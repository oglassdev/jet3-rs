use super::insert_indexed_tests::*;
use crate::{
    ColumnSpec, ColumnType, DatabaseReader, IndexColumnSpec, IndexKind, IndexSpec, RowDelete,
    RowValue, TableSpec, UpdateError, write::insert::*,
};
use std::error::Error as StdError;
use std::fs;

fn fixture(kind: ColumnType, values: &[RowValue<'_>]) -> Result<Fixture, Box<dyn StdError>> {
    let f = Fixture::new(0, false, IndexKind::Primary)?;
    fs::remove_file(f.path())?;
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"Value", kind),
    ];
    let indexes = [
        IndexSpec {
            name: b"ById",
            kind: IndexKind::Primary,
            fields: &[IndexColumnSpec::ascending(0)],
        },
        IndexSpec {
            name: b"ByValue",
            kind: IndexKind::Unique,
            fields: &[IndexColumnSpec::descending(1)],
        },
    ];
    let values: Vec<_> = values
        .iter()
        .enumerate()
        .map(|(id, value)| [RowValue::Long(id as i32), *value])
        .collect();
    let rows: Vec<_> = values.iter().map(|row| row.as_slice()).collect();
    crate::create_database(
        f.path(),
        &crate::DatabaseSpec {
            tables: &[crate::TableRows {
                table: TableSpec {
                    validation: crate::TableValidation::NONE,
                    name: b"Rows",
                    columns: &columns,
                    indexes: &indexes,
                },
                rows: &rows,
            }],
            ..crate::DatabaseSpec::default()
        },
        &mut budget(),
    )?;
    Ok(f)
}

#[test]
fn text_uniqueness_uses_collation_and_preserves_file_on_refusal() -> TestResult {
    let f = fixture(
        ColumnType::Text {
            max_len: std::num::NonZeroU8::MAX,
        },
        &[
            RowValue::Text(b"a"),
            RowValue::Text(b"\xe9"),
            RowValue::Text(b"ae"),
            RowValue::Text(b"\xdf"),
            RowValue::Text(b" "),
            RowValue::Null,
        ],
    )?;
    let before = fs::read(f.path())?;
    for value in [
        b"A".as_slice(),
        b"a  ",
        b"\xc9",
        b"\xc6",
        b"ss",
        b"SS",
        b"  ",
    ] {
        let error = insert_row(
            f.path(),
            b"Rows",
            &[RowValue::Long(99), RowValue::Text(value)],
            &mut budget(),
        )
        .err()
        .ok_or("duplicate accepted")?;
        assert!(
            matches!(error, UpdateError::Unsupported("duplicate unique key")),
            "{error:?}"
        );
        assert_eq!(fs::read(f.path())?, before);
    }
    let row = f.rows()?[0].1;
    let error = crate::update_row(
        f.path(),
        crate::RowUpdate {
            table: b"Rows",
            row,
            values: &[RowValue::Long(0), RowValue::Text(b"\xc9")],
        },
        &mut budget(),
    )
    .err()
    .ok_or("duplicate update accepted")?;
    assert!(matches!(
        error,
        UpdateError::Unsupported("duplicate unique key")
    ));
    assert_eq!(fs::read(f.path())?, before);
    for (id, value) in [(10, b"e".as_slice()), (11, b"a\xa0"), (12, b"a\n")] {
        insert_row(
            f.path(),
            b"Rows",
            &[RowValue::Long(id), RowValue::Text(value)],
            &mut budget(),
        )?;
    }
    crate::update_row(
        f.path(),
        crate::RowUpdate {
            table: b"Rows",
            row,
            values: &[RowValue::Long(0), RowValue::Text(b"A  ")],
        },
        &mut budget(),
    )?;
    let mut b = budget();
    let mut db = DatabaseReader::open(f.path(), &mut b)?;
    let table = f.definition()?;
    let mut cursor = db.rows(&table, &mut b)?;
    let saved = cursor.next_row()?.ok_or("row")?;
    assert_eq!(
        saved
            .field(crate::ColumnOrdinal::new(1))
            .and_then(|v| v.raw_bytes()),
        Some(b"A  ".as_slice())
    );
    f.validate()
}

#[test]
fn guid_mutations_preserve_display_order_and_unique_keys() -> TestResult {
    let value = [3, 2, 1, 0, 5, 4, 7, 6, 8, 9, 10, 11, 12, 13, 14, 15];
    let f = fixture(ColumnType::Guid, &[RowValue::Guid(value), RowValue::Null])?;
    let before = fs::read(f.path())?;
    assert!(
        insert_row(
            f.path(),
            b"Rows",
            &[RowValue::Long(2), RowValue::Guid(value)],
            &mut budget()
        )
        .is_err()
    );
    assert_eq!(fs::read(f.path())?, before);
    let inserted = insert_row(
        f.path(),
        b"Rows",
        &[RowValue::Long(2), RowValue::Guid([0xff; 16])],
        &mut budget(),
    )?;
    crate::update_field(
        f.path(),
        crate::FieldUpdate {
            table: b"Rows",
            row: inserted,
            column: crate::ColumnOrdinal::new(1),
            value: RowValue::Guid([0; 16]),
        },
        &mut budget(),
    )?;
    f.validate()?;
    crate::delete_row(
        f.path(),
        RowDelete {
            table: b"Rows",
            row: inserted,
        },
        &mut budget(),
    )?;
    f.validate()
}
