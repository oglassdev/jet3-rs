use super::*;
use crate::{TableRows, create_database_with_table_rows};

#[test]
fn mixed_tables_assign_later_roots_maps_indexes_and_payloads() -> TestResult {
    let directory = TestDirectory::create()?;
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
            table: TableSpec {
                name: b"Numbers",
                columns: &[ID],
                indexes: &[],
            },
            rows: &first_rows,
        },
        TableRows {
            table: TableSpec {
                name: b"Keys",
                columns: &[ID],
                indexes: &indexes,
            },
            rows: &[
                &[RowValue::Long(3)],
                &[RowValue::Long(-1)],
                &[RowValue::Long(2)],
            ],
        },
        TableRows {
            table: TableSpec {
                name: b"Notes",
                columns: &[NOTE],
                indexes: &[],
            },
            rows: &[&[RowValue::Memo(&payload)], &[RowValue::Null]],
        },
        TableRows {
            table: TableSpec {
                name: b"Empty",
                columns: &[ID],
                indexes: &[],
            },
            rows: &[],
        },
    ];
    create_database_with_table_rows(directory.target(), &requests, &mut budget())?;
    let bytes = fs::read(directory.target())?;
    assert_eq!(bytes.len(), 36 * crate::PAGE_BYTES);
    for page in 23..26 {
        assert!(map_bit(&bytes, 21, 0, page)?);
    }
    assert!(map_bit(&bytes, 27, 2, 28)?);
    assert!(map_bit(&bytes, 27, 0, 29)?);
    assert!(map_bit(&bytes, 31, 2, 32)?);
    assert!(map_bit(&bytes, 31, 3, 32)?);
    assert!(!map_bit(&bytes, 31, 0, 32)?);
    assert!(map_bit(&bytes, 31, 0, 33)?);
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
        32 * crate::PAGE_BYTES + 1536,
    ] {
        let mut changed = bytes.clone();
        changed[offset] ^= 1;
        fs::write(directory.target(), changed)?;
        assert!(
            super::super::super::check_initial_tables(
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
    let empty = TestDirectory::create()?;
    create_database_with_table_rows(empty.target(), &[], &mut budget())?;
    assert_eq!(
        fs::metadata(empty.target())?.len(),
        20 * crate::PAGE_BYTES as u64
    );
    let directory = TestDirectory::create()?;
    let payload = [7; 2048];
    let requests = [
        TableRows {
            table: TableSpec {
                name: b"Empty",
                columns: &[ID],
                indexes: &[],
            },
            rows: &[],
        },
        TableRows {
            table: TableSpec {
                name: b"Binary",
                columns: &[ColumnSpec::new(b"Payload", ColumnType::LongBinary)],
                indexes: &[],
            },
            rows: &[&[RowValue::LongBinary(&payload)]],
        },
    ];
    create_database_with_table_rows(directory.target(), &requests, &mut budget())?;
    let bytes = fs::read(directory.target())?;
    assert_eq!(bytes.len(), 28 * crate::PAGE_BYTES);
    assert!(map_bit(&bytes, 24, 2, 25)?);
    assert!(map_bit(&bytes, 24, 2, 26)?);
    assert!(map_bit(&bytes, 24, 0, 27)?);
    Ok(())
}

#[test]
fn table_limit_duplicate_names_and_later_failure_preserve_destination() -> TestResult {
    let directory = TestDirectory::create()?;
    let first = TableRows {
        table: TableSpec {
            name: b"First",
            columns: &[ID],
            indexes: &[],
        },
        rows: &[&[RowValue::Long(1)]],
    };
    let second = TableRows {
        table: TableSpec {
            name: b"Second",
            ..first.table
        },
        ..first
    };
    create_database_with_table_rows(directory.target(), &[first, second], &mut budget())?;
    let original = fs::read(directory.target())?;
    assert!(matches!(
        create_database_with_table_rows(directory.target(), &[first; 5], &mut budget()),
        Err(CreateDatabaseError::Compose(
            ComposeError::UnobservedTableCount { count: 5, .. }
        ))
    ));
    let duplicate = TableRows {
        table: TableSpec {
            name: b"fIRST",
            ..first.table
        },
        ..first
    };
    assert!(matches!(
        create_database_with_table_rows(directory.target(), &[first, duplicate], &mut budget()),
        Err(CreateDatabaseError::Compose(
            ComposeError::DuplicateTableName {
                first: 0,
                second: 1
            }
        ))
    ));
    let wrong = TableRows {
        rows: &[&[RowValue::Text(b"wrong")]],
        ..second
    };
    assert!(matches!(
        create_database_with_table_rows(directory.target(), &[first, wrong], &mut budget()),
        Err(CreateDatabaseError::Compose(ComposeError::Row(
            RowWriteError::TypeMismatch { .. }
        )))
    ));
    let mut limited = ResourceBudget::new(
        ResourceLimits::default().with_max_allocation_bytes(crate::ByteCount::new(1)),
    );
    assert!(
        create_database_with_table_rows(directory.target(), &[first, second], &mut limited)
            .is_err()
    );
    assert_eq!(fs::read(directory.target())?, original);
    assert_eq!(directory.entries()?, ["created.mdb"]);
    Ok(())
}

#[test]
fn later_table_pages_share_the_same_inline_allocation_limit() -> TestResult {
    let directory = TestDirectory::create()?;
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
        table: TableSpec {
            name: b"WideRows",
            columns: &columns,
            indexes: &[],
        },
        rows: &rows[..2997],
    };
    let later = TableRows {
        table: TableSpec {
            name: b"Later",
            columns: &[ID],
            indexes: &[],
        },
        rows: &[],
    };
    create_database_with_table_rows(directory.target(), &[first, later], &mut budget())?;
    let original = fs::read(directory.target())?;
    assert_eq!(original.len(), 1024 * crate::PAGE_BYTES);
    let larger = TableRows {
        rows: &rows,
        ..first
    };
    assert!(
        create_database_with_table_rows(directory.target(), &[larger, later], &mut budget())
            .is_err()
    );
    assert_eq!(fs::read(directory.target())?, original);
    Ok(())
}
