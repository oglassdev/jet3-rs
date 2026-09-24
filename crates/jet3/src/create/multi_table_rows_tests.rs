use super::initial_rows_tests::*;
use crate::WriteError;
use crate::testkit::create;
use crate::testkit::table;
use crate::{
    ColumnSpec, ColumnType, ComposeError, DatabaseReader, DatabaseSpec, IndexDirection, IndexKind,
    IndexSpec, PageNumber, ResourceBudget, ResourceLimits, RowValue, RowWriteError, TableRows,
    TableSpec, create::api_tests::*, create_database,
};
use std::fs;

#[test]
fn mixed_tables_assign_later_roots_maps_indexes_and_payloads() -> TestResult {
    let directory = TempDir::new("create")?;
    let numbers = (-254..=254)
        .map(|id| [RowValue::Long(id)])
        .collect::<Vec<_>>();
    let first_rows = numbers.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    let indexes = [IndexSpec {
        name: b"ById",
        fields: &[field(0, IndexDirection::Ascending)],
        kind: IndexKind::Primary,
    }];
    let payload = [b'M'; 512];
    let requests = [
        TableRows {
            table: table(b"Numbers", &[ID], &[]),
            rows: &first_rows,
        },
        TableRows {
            table: table(b"Keys", &[ID], &indexes),
            rows: &[
                &[RowValue::Long(3)],
                &[RowValue::Long(-1)],
                &[RowValue::Long(2)],
            ],
        },
        TableRows {
            table: table(b"Notes", &[NOTE], &[]),
            rows: &[&[RowValue::Memo(&payload)], &[RowValue::Null]],
        },
        TableRows {
            table: table(b"Empty", &[ID], &[]),
            rows: &[],
        },
    ];
    create(directory.target(), &requests)?;
    let bytes = fs::read(directory.target())?;
    assert_eq!(bytes.len(), 37 * crate::PAGE_BYTES);
    for page in 23..26 {
        assert!(map_bit(&bytes, 21, 0, page)?);
    }
    assert!(map_bit(&bytes, 27, 2, 28)?);
    assert!(map_bit(&bytes, 27, 0, 29)?);
    assert!(map_bit(&bytes, 31, 2, 33)?);
    assert!(map_bit(&bytes, 31, 3, 33)?);
    assert!(!map_bit(&bytes, 31, 0, 33)?);
    assert!(map_bit(&bytes, 31, 0, 34)?);
    let mut operation = budget();
    let mut database = DatabaseReader::open(directory.target(), &mut operation)?;
    let definition = database.table_definition(PageNumber::new(26), &mut operation)?;
    let tree = database.index_tree(&definition, 0, &mut operation)?;
    assert_eq!(
        tree.entries()
            .iter()
            .map(|entry| entry.row())
            .collect::<Vec<_>>(),
        [
            crate::RowLocator::new(PageNumber::new(29), 1),
            crate::RowLocator::new(PageNumber::new(29), 2),
            crate::RowLocator::new(PageNumber::new(29), 0),
        ]
    );
    drop(database);
    let tables = requests.map(|request| request.table);
    // Corruption in each later populated table must fail the aggregate check.
    for offset in [
        28 * crate::PAGE_BYTES + 4,
        29 * crate::PAGE_BYTES + 4,
        33 * crate::PAGE_BYTES + 1536,
    ] {
        let mut changed = bytes.clone();
        changed[offset] ^= 1;
        fs::write(directory.target(), changed)?;
        assert!(
            super::check::check_initial_tables(
                &directory.target(),
                &tables,
                &requests,
                &mut budget()
            )
            .is_err()
        );
    }
    Ok(())
}

#[test]
fn empty_requests_and_empty_first_table_keep_first_create_placement() -> TestResult {
    let empty = TempDir::new("create")?;
    create(empty.target(), &[])?;
    assert_eq!(
        fs::metadata(empty.target())?.len(),
        20 * crate::PAGE_BYTES as u64
    );
    let directory = TempDir::new("create")?;
    let payload = [7; 2048];
    let requests = [
        TableRows {
            table: table(b"Empty", &[ID], &[]),
            rows: &[],
        },
        TableRows {
            table: TableSpec {
                validation: crate::TableValidation::NONE,
                name: b"Binary",
                columns: &[ColumnSpec::new(b"Payload", ColumnType::LongBinary)],
                indexes: &[],
            },
            rows: &[&[RowValue::LongBinary(&payload)]],
        },
    ];
    create(directory.target(), &requests)?;
    let bytes = fs::read(directory.target())?;
    assert_eq!(bytes.len(), 28 * crate::PAGE_BYTES);
    assert!(map_bit(&bytes, 24, 2, 25)?);
    assert!(map_bit(&bytes, 24, 2, 26)?);
    assert!(map_bit(&bytes, 24, 0, 27)?);
    Ok(())
}

#[test]
fn table_limit_duplicate_names_and_later_failure_preserve_destination() -> TestResult {
    let directory = TempDir::new("create")?;
    let first = TableRows {
        table: table(b"First", &[ID], &[]),
        rows: &[&[RowValue::Long(1)]],
    };
    let second = TableRows {
        table: TableSpec {
            name: b"Second",
            ..first.table
        },
        ..first
    };
    create(directory.target(), &[first, second])?;
    let original = fs::read(directory.target())?;
    assert!(matches!(
        create(directory.target(), &vec![first; 32640]),
        Err(WriteError::Compose(ComposeError::TableCountOverflow {
            count: 32640,
            ..
        }))
    ));
    let duplicate = TableRows {
        table: TableSpec {
            name: b"fIRST",
            ..first.table
        },
        ..first
    };
    assert!(matches!(
        create(directory.target(), &[first, duplicate]),
        Err(WriteError::Compose(ComposeError::DuplicateTableName {
            first: 0,
            second: 1
        }))
    ));
    let wrong = TableRows {
        rows: &[&[RowValue::Text(b"wrong")]],
        ..second
    };
    assert!(matches!(
        create(directory.target(), &[first, wrong]),
        Err(WriteError::Compose(ComposeError::Row(
            RowWriteError::TypeMismatch { .. }
        )))
    ));
    let mut limited = ResourceBudget::new(
        ResourceLimits::default().with_max_allocation_bytes(crate::ByteCount::new(1)),
    );
    assert!(
        create_database(
            directory.target(),
            &DatabaseSpec {
                tables: &[first, second],
                ..DatabaseSpec::default()
            },
            &mut limited
        )
        .is_err()
    );
    assert_eq!(fs::read(directory.target())?, original);
    assert_eq!(directory.entries()?, ["created.mdb"]);
    Ok(())
}

#[test]
fn later_table_pages_share_the_same_inline_allocation_limit() -> TestResult {
    let directory = TempDir::new("create")?;
    let names = (0..70)
        .map(|number| format!("F{number}"))
        .collect::<Vec<_>>();
    let columns = names
        .iter()
        .map(|name| ColumnSpec::new(name.as_bytes(), ColumnType::Double))
        .collect::<Vec<_>>();
    let row = [RowValue::Double(1.0); 70];
    let rows = vec![row.as_slice(); 3000];
    let first = TableRows {
        table: table(b"WideRows", &columns, &[]),
        rows: &rows[..2997],
    };
    let later = TableRows {
        table: table(b"Later", &[ID], &[]),
        rows: &[],
    };
    create(directory.target(), &[first, later])?;
    let original = fs::read(directory.target())?;
    assert_eq!(original.len(), 1024 * crate::PAGE_BYTES);
    let larger = TableRows {
        rows: &rows,
        ..first
    };
    assert!(create(directory.target(), &[larger, later]).is_err());
    assert_eq!(fs::read(directory.target())?, original);
    Ok(())
}
