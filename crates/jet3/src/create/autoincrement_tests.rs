use super::initial_rows_tests::*;
use crate::{
    ColumnSpec, ColumnType, ComposeError, DatabaseReader, IndexDirection, IndexKind, IndexSpec,
    ResourceBudget, ResourceLimits, RowValue, TableRows, TableSpec,
    create::{
        api::{CreateDatabaseError, ImageCheckError},
        api_tests::*,
    },
    create_database_with_rows, create_database_with_table_rows,
};
use std::fs;

const AUTO: ColumnSpec<'static> = ColumnSpec::new(b"Id", ColumnType::AutoIncrement);

const TAG: ColumnSpec<'static> = ColumnSpec::new(b"Tag", ColumnType::Long);

fn auto_table() -> TableSpec<'static> {
    TableSpec {
        validation: crate::TableValidation::NONE,
        name: b"Generated",
        columns: &[AUTO, TAG],
        indexes: &[],
    }
}

#[test]
fn autoincrement_generates_rows_and_detects_state_or_row_corruption() -> TestResult {
    let directory = TestDirectory::create()?;
    let values = (1..=256)
        .map(|tag| [RowValue::AutoIncrement, RowValue::Long(tag)])
        .collect::<Vec<_>>();
    let rows = values.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    let table = auto_table();
    create_database_with_rows(directory.target(), &table, &rows, &mut budget())?;
    let original = fs::read(directory.target())?;
    assert_eq!(
        &original[20 * crate::PAGE_BYTES + 16..20 * crate::PAGE_BYTES + 20],
        &256_i32.to_le_bytes()
    );
    let mut changed = original.clone();
    changed[20 * crate::PAGE_BYTES + 16] = 1;
    fs::write(directory.target(), changed)?;
    assert!(matches!(
        crate::create::api::check_initial_rows(&directory.target(), &table, &rows, &mut budget()),
        Err(ImageCheckError::Mismatch {
            detail: "initial AutoIncrement state"
        })
    ));
    fs::write(directory.target(), original)?;
    let mut changed_values = values.clone();
    changed_values[0][1] = RowValue::Long(-1);
    let changed_rows = changed_values
        .iter()
        .map(|row| row.as_slice())
        .collect::<Vec<_>>();
    assert!(matches!(
        crate::create::api::check_initial_rows(
            &directory.target(),
            &table,
            &changed_rows,
            &mut budget()
        ),
        Err(ImageCheckError::Mismatch {
            detail: "initial row value"
        })
    ));
    Ok(())
}

#[test]
fn autoincrement_invalid_values_and_types_leave_no_file() -> TestResult {
    let directory = TestDirectory::create()?;
    let table = TableSpec {
        columns: &[AUTO],
        ..auto_table()
    };
    for value in [
        RowValue::Null,
        RowValue::Boolean(true),
        RowValue::Text(b"1"),
    ] {
        assert!(matches!(
            create_database_with_rows(directory.target(), &table, &[&[value]], &mut budget()),
            Err(CreateDatabaseError::Compose(
                ComposeError::InitialAutoIncrement { .. }
            ))
        ));
    }
    assert!(
        create_database_with_rows(
            directory.target(),
            &scalar_table(),
            &[&[RowValue::AutoIncrement, RowValue::Null]],
            &mut budget()
        )
        .is_err()
    );
    assert!(create_database_with_rows(directory.target(), &table, &[&[]], &mut budget()).is_err());
    let multiple = TableSpec {
        columns: &[AUTO, ColumnSpec::new(b"Other", ColumnType::AutoIncrement)],
        ..auto_table()
    };
    assert!(create_database_with_rows(directory.target(), &multiple, &[], &mut budget()).is_err());
    assert!(directory.entries()?.is_empty());
    Ok(())
}

#[test]
fn autoincrement_multi_table_indexed_and_empty_counters_are_independent() -> TestResult {
    let directory = TestDirectory::create()?;
    let indexes = [IndexSpec {
        name: b"PrimaryKey",
        fields: &[field(0, IndexDirection::Ascending)],
        kind: IndexKind::Primary,
    }];
    let requests = [
        TableRows {
            table: TableSpec {
                validation: crate::TableValidation::NONE,
                name: b"First",
                columns: &[AUTO],
                indexes: &indexes,
            },
            rows: &[&[RowValue::AutoIncrement], &[RowValue::AutoIncrement]],
        },
        TableRows {
            table: TableSpec {
                validation: crate::TableValidation::NONE,
                name: b"Second",
                columns: &[AUTO],
                indexes: &[],
            },
            rows: &[&[RowValue::AutoIncrement]],
        },
        TableRows {
            table: TableSpec {
                validation: crate::TableValidation::NONE,
                name: b"Empty",
                columns: &[AUTO],
                indexes: &[],
            },
            rows: &[],
        },
    ];
    create_database_with_table_rows(directory.target(), &requests, &mut budget())?;
    let mut operation = budget();
    let mut database = DatabaseReader::open(directory.target(), &mut operation)?;
    let tables = requests.map(|r| r.table);
    let roots = crate::create::api::image_table_roots(&mut database, &tables, &mut operation)?;
    for (root, count) in roots.into_iter().zip([2_i32, 1, 0]) {
        let mut bytes = [0_u8; crate::PAGE_BYTES];
        database.read_raw_page(root.ok_or("missing root")?, &mut bytes, &mut operation)?;
        assert_eq!(&bytes[16..20], &count.to_le_bytes());
    }
    Ok(())
}

#[test]
fn autoincrement_budget_and_existing_destination_are_preserved() -> TestResult {
    let directory = TestDirectory::create()?;
    let table = TableSpec {
        columns: &[AUTO],
        ..auto_table()
    };
    let rows: &[&[RowValue<'_>]] = &[&[RowValue::AutoIncrement]];
    let mut limited = ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(0));
    assert!(create_database_with_rows(directory.target(), &table, rows, &mut limited).is_err());
    assert!(directory.entries()?.is_empty());
    fs::write(directory.target(), b"original")?;
    assert!(matches!(
        create_database_with_rows(directory.target(), &table, rows, &mut budget()),
        Err(CreateDatabaseError::Publish(_))
    ));
    assert_eq!(fs::read(directory.target())?, b"original");
    Ok(())
}

#[test]
fn autoincrement_positive_counts_are_not_limited_to_the_observed_sample() -> TestResult {
    let directory = TestDirectory::create()?;
    let table = TableSpec {
        columns: &[AUTO],
        ..auto_table()
    };
    let row = [RowValue::AutoIncrement];
    let rows = vec![row.as_slice(); 257];
    create_database_with_rows(directory.target(), &table, &rows, &mut budget())?;
    let bytes = fs::read(directory.target())?;
    assert_eq!(
        &bytes[20 * crate::PAGE_BYTES + 16..20 * crate::PAGE_BYTES + 20],
        &257_i32.to_le_bytes()
    );
    Ok(())
}

#[test]
fn autoincrement_explicit_ids_wrap_with_independent_indexed_tables() -> TestResult {
    let directory = TestDirectory::create()?;
    let indexes = [
        IndexSpec {
            name: b"PrimaryKey",
            fields: &[field(0, IndexDirection::Ascending)],
            kind: IndexKind::Primary,
        },
        IndexSpec {
            name: b"ByTag",
            fields: &[field(1, IndexDirection::Descending)],
            kind: IndexKind::Unique,
        },
        IndexSpec {
            name: b"ByPair",
            fields: &[
                field(0, IndexDirection::Descending),
                field(1, IndexDirection::Ascending),
            ],
            kind: IndexKind::Ordinary,
        },
    ];
    let inputs = [
        RowValue::Long(i32::MAX),
        RowValue::Long(1000),
        RowValue::Long(-1),
        RowValue::AutoIncrement,
        RowValue::AutoIncrement,
        RowValue::Long(10),
        RowValue::AutoIncrement,
        RowValue::Long(i32::MIN),
        RowValue::AutoIncrement,
    ];
    let expected = [i32::MAX, 1000, -1, 0, 1, 10, 11, i32::MIN, i32::MIN + 1];
    let values: Vec<_> = inputs
        .into_iter()
        .enumerate()
        .map(|(tag, id)| [id, RowValue::Long(tag as i32)])
        .collect();
    let rows: Vec<_> = values.iter().map(|row| row.as_slice()).collect();
    let requests = [
        TableRows {
            table: TableSpec {
                name: b"Plain",
                ..auto_table()
            },
            rows: &rows,
        },
        TableRows {
            table: TableSpec {
                name: b"Indexed",
                indexes: &indexes,
                ..auto_table()
            },
            rows: &rows,
        },
    ];
    create_database_with_table_rows(directory.target(), &requests, &mut budget())?;
    let mut operation = budget();
    let mut database = DatabaseReader::open(directory.target(), &mut operation)?;
    let roots = crate::create::api::image_table_roots(
        &mut database,
        &requests.map(|request| request.table),
        &mut operation,
    )?;
    for root in roots {
        let table = database.table_definition(root.ok_or("missing root")?, &mut operation)?;
        assert_eq!(&table.raw_header()[16..20], &(i32::MIN + 1).to_le_bytes());
        let mut ids = Vec::new();
        {
            let mut cursor = database.rows(&table, &mut operation)?;
            while let Some(row) = cursor.next_row()? {
                let id = row
                    .field(crate::ColumnOrdinal::new(0))
                    .and_then(|v| v.raw_bytes())
                    .ok_or("missing ID")?;
                ids.push(i32::from_le_bytes(id.try_into()?));
            }
        }
        assert_eq!(ids, expected);
        if !table.indexes().is_empty() {
            crate::index::mutation::load(&mut database, &table, &mut operation)?;
        }
    }
    Ok(())
}

#[test]
fn autoincrement_explicit_duplicate_unique_ids_leave_no_file() -> TestResult {
    let directory = TestDirectory::create()?;
    let indexes = [IndexSpec {
        name: b"PrimaryKey",
        fields: &[field(0, IndexDirection::Ascending)],
        kind: IndexKind::Primary,
    }];
    let table = TableSpec {
        indexes: &indexes,
        ..auto_table()
    };
    let rows: &[&[RowValue<'_>]] = &[
        &[RowValue::Long(-1), RowValue::Long(1)],
        &[RowValue::Long(-1), RowValue::Long(2)],
    ];
    assert!(create_database_with_rows(directory.target(), &table, rows, &mut budget()).is_err());
    assert!(directory.entries()?.is_empty());
    Ok(())
}
