use super::*;
use crate::{TableRows, create_database_with_table_rows};

const AUTO: ColumnSpec<'static> = ColumnSpec::new(b"Id", ColumnType::AutoIncrement);

const TAG: ColumnSpec<'static> = ColumnSpec::new(b"Tag", ColumnType::Long);

fn auto_table() -> TableSpec<'static> {
    TableSpec {
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
        crate::creation::api::check_initial_rows(&directory.target(), &table, &rows, &mut budget()),
        Err(CandidateCheckError::Mismatch {
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
        crate::creation::api::check_initial_rows(
            &directory.target(),
            &table,
            &changed_rows,
            &mut budget()
        ),
        Err(CandidateCheckError::Mismatch {
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
    for value in [RowValue::Null, RowValue::Long(1), RowValue::Text(b"1")] {
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
                name: b"First",
                columns: &[AUTO],
                indexes: &indexes,
            },
            rows: &[&[RowValue::AutoIncrement], &[RowValue::AutoIncrement]],
        },
        TableRows {
            table: TableSpec {
                name: b"Second",
                columns: &[AUTO],
                indexes: &[],
            },
            rows: &[&[RowValue::AutoIncrement]],
        },
        TableRows {
            table: TableSpec {
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
    let roots =
        crate::creation::api::candidate_table_roots(&mut database, &tables, &mut operation)?;
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
