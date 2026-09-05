use super::*;
use std::collections::BTreeSet;

const GROUP: ColumnSpec<'static> = ColumnSpec::new(b"Group", ColumnType::Currency);
const PRIMARY: [IndexColumnSpec<'static>; 1] = [field(0, IndexDirection::Ascending)];
const GROUP_KEY: [IndexColumnSpec<'static>; 1] = [field(1, IndexDirection::Descending)];
const MIXED: [IndexColumnSpec<'static>; 2] = [
    field(1, IndexDirection::Ascending),
    field(0, IndexDirection::Descending),
];
fn indexes() -> [IndexSpec<'static>; 3] {
    [
        IndexSpec {
            name: b"ZPrimary",
            kind: IndexKind::Primary,
            fields: &PRIMARY,
        },
        IndexSpec {
            name: b"AGroup",
            kind: IndexKind::Ordinary,
            fields: &GROUP_KEY,
        },
        IndexSpec {
            name: b"MMixed",
            kind: IndexKind::Unique,
            fields: &MIXED,
        },
    ]
}

#[test]
fn three_separate_trees_counts_and_maps_precede_a_later_table() -> TestResult {
    let directory = TestDirectory::create()?;
    let indexes = indexes();
    let columns = [
        ID,
        GROUP,
        ColumnSpec::new(b"Payload", ColumnType::Text { max_len: nz(255) }),
    ];
    let payload = [b'x'; 180];
    let values: Vec<_> = (0..201)
        .rev()
        .map(|id| {
            [
                RowValue::Long(id),
                RowValue::Currency {
                    scaled: i64::from(id % 3),
                },
                RowValue::Text(&payload),
            ]
        })
        .collect();
    let rows: Vec<_> = values.iter().map(|r| r.as_slice()).collect();
    let later_indexes = one_index(IndexKind::Primary);
    let requests = [
        crate::TableRows {
            table: TableSpec {
                name: b"Items",
                columns: &columns,
                indexes: &indexes,
            },
            rows: &rows,
        },
        crate::TableRows {
            table: TableSpec {
                name: b"Later",
                columns: &[ID],
                indexes: &later_indexes,
            },
            rows: &[&[RowValue::Long(99)]],
        },
    ];
    crate::create_database_with_table_rows(directory.target(), &requests, &mut budget())?;
    let bytes = fs::read(directory.target())?;
    let mut b = budget();
    let mut db = DatabaseReader::open(directory.target(), &mut b)?;
    let def = db.table_definition(PageNumber::new(20), &mut b)?;
    assert_eq!(
        def.indexes()
            .iter()
            .map(|i| i.physical_index())
            .collect::<Vec<_>>(),
        [1, 2, 0]
    );
    let mut locations = Vec::new();
    {
        let mut cursor = db.rows(&def, &mut b)?;
        while let Some(row) = cursor.next_row()? {
            locations.push(row.locator());
        }
    }
    assert_eq!(locations.len(), 201);
    assert_eq!(locations[0].page(), PageNumber::new(26));
    let mut all_pages = BTreeSet::new();
    for ordinal in 0..3 {
        let physical = &def.physical_indexes()[ordinal];
        assert_eq!(
            physical.distinct_key_count(),
            if ordinal == 1 { 3 } else { 201 }
        );
        let tree = db.index_tree(&def, ordinal as u16, &mut b)?;
        assert_eq!(tree.root(), PageNumber::new(23 + ordinal as u64));
        assert!(tree.nodes().len() >= 3);
        let mut expected: Vec<_> = (0..201_usize).collect();
        expected.sort_by_key(|&input| {
            let id = 200 - input as i32;
            match ordinal {
                0 => (id, 0),
                1 => (-(id % 3), input as i32),
                _ => (id % 3, -id),
            }
        });
        assert_eq!(
            tree.entries().iter().map(|e| e.row()).collect::<Vec<_>>(),
            expected.iter().map(|&i| locations[i]).collect::<Vec<_>>()
        );
        for node in tree.nodes() {
            assert!(all_pages.insert(node.page().get()));
            assert!(map_bit(&bytes, 21, (2 + ordinal) as u8, node.page().get())?);
            for other in 0..3 {
                if other != ordinal {
                    assert!(!map_bit(&bytes, 21, (2 + other) as u8, node.page().get())?);
                }
            }
            assert!(!map_bit(&bytes, 21, 0, node.page().get())?);
        }
    }
    let later_root = all_pages.iter().max().ok_or("no index pages")? + 1;
    let later = db.table_definition(PageNumber::new(later_root), &mut b)?;
    let tree = db.index_tree(&later, 0, &mut b)?;
    assert_eq!(tree.entries().len(), 1);
    assert_eq!(tree.entries()[0].row().page().get(), later_root + 3);
    Ok(())
}

#[test]
fn independent_null_policies_generated_ids_and_empty_trees() -> TestResult {
    let indexes = [
        IndexSpec {
            name: b"ZId",
            kind: IndexKind::Primary,
            fields: &PRIMARY,
        },
        IndexSpec {
            name: b"AGroup",
            kind: IndexKind::Ordinary.with_null_policy(crate::IndexNullPolicy::IgnoreAllNull),
            fields: &GROUP_KEY,
        },
    ];
    let columns = [ColumnSpec::new(b"Id", ColumnType::AutoIncrement), GROUP];
    let table = TableSpec {
        name: b"Items",
        columns: &columns,
        indexes: &indexes,
    };
    let rows: &[&[RowValue<'_>]] = &[
        &[RowValue::AutoIncrement, RowValue::Null],
        &[RowValue::AutoIncrement, RowValue::Currency { scaled: 5 }],
        &[RowValue::AutoIncrement, RowValue::Currency { scaled: 5 }],
    ];
    for requested in [&rows[..0], rows] {
        let directory = TestDirectory::create()?;
        create_database_with_rows(directory.target(), &table, requested, &mut budget())?;
        let mut b = budget();
        let mut db = DatabaseReader::open(directory.target(), &mut b)?;
        let def = db.table_definition(PageNumber::new(20), &mut b)?;
        for ordinal in 0..2 {
            let tree = db.index_tree(&def, ordinal, &mut b)?;
            assert_eq!(
                tree.entries().len(),
                if requested.is_empty() {
                    0
                } else if ordinal == 0 {
                    3
                } else {
                    2
                }
            );
            assert_eq!(
                def.physical_indexes()[ordinal as usize].distinct_key_count(),
                if requested.is_empty() {
                    0
                } else if ordinal == 0 {
                    3
                } else {
                    1
                }
            );
        }
    }
    Ok(())
}

#[test]
fn later_index_corruption_and_publication_failures_are_detected() -> TestResult {
    let directory = TestDirectory::create()?;
    let indexes = indexes();
    let columns = [ID, GROUP];
    let table = TableSpec {
        name: b"Items",
        columns: &columns,
        indexes: &indexes,
    };
    let rows: &[&[RowValue<'_>]] = &[
        &[RowValue::Long(1), RowValue::Currency { scaled: 7 }],
        &[RowValue::Long(2), RowValue::Currency { scaled: 8 }],
    ];
    create_database_with_rows(directory.target(), &table, rows, &mut budget())?;
    let original = fs::read(directory.target())?;
    let map_start = u16::from_le_bytes(
        original[21 * crate::PAGE_BYTES + 16..21 * crate::PAGE_BYTES + 18].try_into()?,
    ) as usize;
    // Later physical key, locator, distinct count and map membership.
    for offset in [
        24 * crate::PAGE_BYTES + 249,
        25 * crate::PAGE_BYTES + 265,
        20 * crate::PAGE_BYTES + 55,
        21 * crate::PAGE_BYTES + map_start + 5 + 24 / 8,
    ] {
        let mut bad = original.clone();
        bad[offset] ^= 1;
        fs::write(directory.target(), bad)?;
        assert!(
            crate::creation::api::check_initial_rows(
                &directory.target(),
                &table,
                rows,
                &mut budget()
            )
            .is_err()
        );
    }
    fs::write(directory.target(), &original)?;
    assert!(create_database_with_rows(directory.target(), &table, rows, &mut budget()).is_err());
    assert_eq!(fs::read(directory.target())?, original);
    let one = TableSpec {
        indexes: &indexes[..1],
        ..table
    };
    let mut charged = budget();
    crate::creation::composer::InitialLongIndex::for_table(&one, 20, &mut charged)?;
    let mut limited = ResourceBudget::new(
        ResourceLimits::default().with_max_allocation_bytes(charged.allocation_bytes()),
    );
    assert!(
        crate::creation::composer::InitialLongIndex::for_table(&table, 20, &mut limited).is_err()
    );
    Ok(())
}

#[test]
fn second_unique_index_refuses_duplicates_and_later_tables_keep_their_bound() -> TestResult {
    let directory = TestDirectory::create()?;
    let mut indexes = indexes();
    indexes[1].kind = IndexKind::Unique;
    let table = TableSpec {
        name: b"Items",
        columns: &[ID, GROUP],
        indexes: &indexes[..2],
    };
    let rows: &[&[RowValue<'_>]] = &[
        &[RowValue::Long(1), RowValue::Currency { scaled: 7 }],
        &[RowValue::Long(2), RowValue::Currency { scaled: 7 }],
    ];
    assert!(matches!(
        create_database_with_rows(directory.target(), &table, rows, &mut budget()),
        Err(CreateDatabaseError::Compose(
            ComposeError::DuplicateInitialScalarIndexKey
        ))
    ));
    let requests = [
        crate::TableRows {
            table: TableSpec {
                name: b"First",
                columns: &[ID],
                indexes: &[],
            },
            rows: &[],
        },
        crate::TableRows {
            table,
            rows: &rows[..1],
        },
    ];
    assert!(
        crate::create_database_with_table_rows(directory.target(), &requests, &mut budget())
            .is_err()
    );
    assert!(directory.entries()?.is_empty());
    Ok(())
}

#[test]
fn aggregate_index_pages_cannot_exceed_the_inline_map() -> TestResult {
    let directory = TestDirectory::create()?;
    let fields = [field(0, IndexDirection::Ascending)];
    let indexes = [b"First".as_slice(), b"Second", b"Third"].map(|name| IndexSpec {
        name,
        kind: IndexKind::Ordinary,
        fields: &fields,
    });
    let table = TableSpec {
        name: b"Items",
        columns: &[ID],
        indexes: &indexes,
    };
    let row = [RowValue::Long(1)];
    let rows = vec![row.as_slice(); 60000];
    assert!(matches!(
        create_database_with_rows(directory.target(), &table, &rows, &mut budget()),
        Err(CreateDatabaseError::Compose(ComposeError::UsageMap(_)))
    ));
    assert!(directory.entries()?.is_empty());
    Ok(())
}
