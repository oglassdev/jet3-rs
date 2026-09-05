use super::*;
use crate::IndexNullPolicy;

const TWO: [IndexColumnSpec<'static>; 2] = [
    field(0, IndexDirection::Ascending),
    field(1, IndexDirection::Descending),
];
const COLUMNS: [ColumnSpec<'static>; 2] = [ID, ColumnSpec::new(b"B", ColumnType::Long)];

#[test]
fn nullable_components_uniqueness_omission_and_distinct_counts_match_policy() -> TestResult {
    let values = [
        [RowValue::Null, RowValue::Null],
        [RowValue::Null, RowValue::Null],
        [RowValue::Null, RowValue::Long(1)],
        [RowValue::Null, RowValue::Long(1)],
        [RowValue::Long(1), RowValue::Null],
        [RowValue::Long(1), RowValue::Null],
        [RowValue::Long(1), RowValue::Long(1)],
        [RowValue::Long(2), RowValue::Long(2)],
    ];
    let rows: Vec<_> = values.iter().map(|row| row.as_slice()).collect();
    for (kind, entries, distinct, flags) in [
        (IndexKind::Ordinary, 8, 5, 0),
        (IndexKind::Unique, 8, 5, 1),
        (
            IndexKind::Ordinary.with_null_policy(IndexNullPolicy::IgnoreAllNull),
            6,
            4,
            2,
        ),
        (
            IndexKind::Unique.with_null_policy(IndexNullPolicy::IgnoreAllNull),
            6,
            4,
            3,
        ),
    ] {
        let directory = TestDirectory::create()?;
        let indexes = [IndexSpec {
            name: b"ById",
            kind,
            fields: &TWO,
        }];
        let table = TableSpec {
            name: b"Items",
            columns: &COLUMNS,
            indexes: &indexes,
        };
        create_database_with_rows(directory.target(), &table, &rows, &mut budget())?;
        let index = tree(&directory.target())?;
        assert_eq!(index.entries().len(), entries);
        let keys: Vec<_> = index
            .entries()
            .iter()
            .map(|entry| entry.key().raw_bytes())
            .collect();
        assert!(keys.contains(&[0, 0x80, 0x7f, 0xff, 0xff, 0xfe].as_slice()));
        assert!(keys.contains(&[0x7f, 0x80, 0, 0, 1, 0xff].as_slice()));
        assert_eq!(keys.contains(&[0, 0xff].as_slice()), entries == 8);
        let bytes = fs::read(directory.target())?;
        assert_eq!(
            &bytes[20 * crate::PAGE_BYTES + 47..20 * crate::PAGE_BYTES + 51],
            &(distinct as u32).to_le_bytes()
        );
        let mut db = DatabaseReader::open(directory.target(), &mut budget())?;
        let definition = db.table_definition(PageNumber::new(20), &mut budget())?;
        assert_eq!(definition.physical_indexes()[0].raw_flags(), flags);
        assert_eq!(
            db.rows(&definition, &mut budget())?
                .next_row()?
                .ok_or("row absent")?
                .field(crate::ColumnOrdinal::new(0)),
            Some(crate::RawField::Null)
        );
    }
    Ok(())
}

#[test]
fn single_null_keys_and_empty_ignored_tree_keep_real_row_counts() -> TestResult {
    for direction in [IndexDirection::Ascending, IndexDirection::Descending] {
        for kind in [
            IndexKind::Unique,
            IndexKind::Unique.with_null_policy(IndexNullPolicy::IgnoreAllNull),
        ] {
            let directory = TestDirectory::create()?;
            let fields = [field(0, direction)];
            let indexes = [IndexSpec {
                name: b"ById",
                kind,
                fields: &fields,
            }];
            let table = TableSpec {
                name: b"Items",
                columns: &[ID],
                indexes: &indexes,
            };
            create_database_with_rows(
                directory.target(),
                &table,
                &[&[RowValue::Null], &[RowValue::Null]],
                &mut budget(),
            )?;
            let index = tree(&directory.target())?;
            if kind.null_policy() == IndexNullPolicy::Include {
                assert_eq!(index.entries().len(), 2);
                assert_eq!(
                    index.entries()[0].key().raw_bytes(),
                    &[if direction == IndexDirection::Ascending {
                        0
                    } else {
                        255
                    }]
                );
            } else {
                assert!(index.entries().is_empty());
                assert_eq!(index.nodes().len(), 1);
            }
        }
    }
    Ok(())
}

#[test]
fn required_null_and_present_duplicate_refusals_preserve_destination() -> TestResult {
    let directory = TestDirectory::create()?;
    fs::write(directory.target(), b"preserve")?;
    let rows: &[&[RowValue<'_>]] = &[&[RowValue::Null, RowValue::Long(1)]];
    for kind in [
        IndexKind::Primary,
        IndexKind::Ordinary.with_null_policy(IndexNullPolicy::Required),
    ] {
        let indexes = [IndexSpec {
            name: b"ById",
            kind,
            fields: &TWO,
        }];
        let table = TableSpec {
            name: b"Items",
            columns: &COLUMNS,
            indexes: &indexes,
        };
        assert!(matches!(
            create_database_with_rows(directory.target(), &table, rows, &mut budget()),
            Err(CreateDatabaseError::Compose(
                ComposeError::NullInitialIndexKey { row: 0 }
            ))
        ));
        assert_eq!(fs::read(directory.target())?, b"preserve");
    }
    let indexes = [IndexSpec {
        name: b"ById",
        kind: IndexKind::Unique,
        fields: &TWO,
    }];
    let table = TableSpec {
        name: b"Items",
        columns: &COLUMNS,
        indexes: &indexes,
    };
    let duplicate: &[RowValue<'_>] = &[RowValue::Long(1), RowValue::Long(2)];
    assert!(matches!(
        create_database_with_rows(
            directory.target(),
            &table,
            &[duplicate, duplicate],
            &mut budget()
        ),
        Err(CreateDatabaseError::Compose(
            ComposeError::DuplicateInitialCompositeIndexKey { values: [1, 2] }
        ))
    ));
    assert_eq!(fs::read(directory.target())?, b"preserve");
    let invalid = [IndexSpec {
        kind: IndexKind::Primary.with_null_policy(IndexNullPolicy::IgnoreAllNull),
        ..indexes[0]
    }];
    assert!(
        create_database_with_rows(
            directory.target(),
            &TableSpec {
                indexes: &invalid,
                ..table
            },
            &[],
            &mut budget()
        )
        .is_err()
    );
    assert_eq!(fs::read(directory.target())?, b"preserve");
    Ok(())
}

#[test]
fn variable_width_duplicate_runs_span_three_levels_and_later_table_maps() -> TestResult {
    let directory = TestDirectory::create()?;
    let values: Vec<_> = (0..30_000)
        .map(|n| {
            if n < 1000 {
                [RowValue::Null, RowValue::Null]
            } else if n % 2 == 0 {
                [RowValue::Long(n), RowValue::Null]
            } else {
                [RowValue::Long(n), RowValue::Long(-n)]
            }
        })
        .collect();
    let rows: Vec<_> = values.iter().map(|row| row.as_slice()).collect();
    let indexes = [IndexSpec {
        name: b"ById",
        kind: IndexKind::Unique,
        fields: &TWO,
    }];
    let table = TableSpec {
        name: b"Items",
        columns: &COLUMNS,
        indexes: &indexes,
    };
    let requests = [
        crate::TableRows {
            table: TableSpec {
                name: b"Empty",
                columns: &[ID],
                indexes: &[],
            },
            rows: &[],
        },
        crate::TableRows { table, rows: &rows },
    ];
    crate::create_database_with_table_rows(directory.target(), &requests, &mut budget())?;
    let mut b = budget();
    let mut db = DatabaseReader::open(directory.target(), &mut b)?;
    let root = {
        let mut catalog = db.catalog(&mut b)?;
        let mut root = None;
        while let Some(item) = catalog.next_record()? {
            if item.name().raw_bytes() == b"Items" {
                root = item.table_definition();
            }
        }
        root.ok_or("Items missing")?
    };
    let definition = db.table_definition(root, &mut b)?;
    let index = db.index_tree(&definition, 0, &mut b)?;
    assert_eq!(index.entries().len(), 30_000);
    assert!(index.nodes().iter().any(|node| node.depth() == 3));
    assert_eq!(
        index
            .entries()
            .iter()
            .filter(|entry| entry.key().raw_bytes() == [0, 255])
            .count(),
        1000
    );
    let original = fs::read(directory.target())?;
    let mut insufficient = ResourceBudget::new(
        ResourceLimits::default().with_max_allocation_bytes(crate::ByteCount::new(100)),
    );
    assert!(
        crate::create_database_with_table_rows(directory.target(), &requests, &mut insufficient)
            .is_err()
    );
    assert_eq!(fs::read(directory.target())?, original);
    Ok(())
}
