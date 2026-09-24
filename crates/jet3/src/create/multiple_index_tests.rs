use super::initial_index_tests::*;
use crate::WriteError;
use crate::testkit::create;
use crate::testkit::{index, table};
use crate::{
    ColumnSpec, ColumnType, ComposeError, DatabaseReader, IndexColumnSpec, IndexDirection,
    IndexKind, IndexSpec, PageNumber, ResourceBudget, ResourceLimits, RowValue, TableRows,
    TableSpec,
    create::{api_tests::*, initial_rows_tests::*},
    definition::column_writer::nz,
};
use std::collections::BTreeSet;
use std::fs;

const GROUP: ColumnSpec<'static> = ColumnSpec::new(b"Group", ColumnType::Currency);
const PRIMARY: [IndexColumnSpec<'static>; 1] = [field(0, IndexDirection::Ascending)];
const GROUP_KEY: [IndexColumnSpec<'static>; 1] = [field(1, IndexDirection::Descending)];
const MIXED: [IndexColumnSpec<'static>; 2] = [
    field(1, IndexDirection::Ascending),
    field(0, IndexDirection::Descending),
];
fn indexes() -> [IndexSpec<'static>; 3] {
    [
        index(b"ZPrimary", &PRIMARY, IndexKind::Primary),
        index(b"AGroup", &GROUP_KEY, IndexKind::Ordinary),
        index(b"MMixed", &MIXED, IndexKind::Unique),
    ]
}

#[test]
fn three_separate_trees_counts_and_maps_precede_a_later_table() -> TestResult {
    let directory = TempDir::new("create")?;
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
            table: table(b"Items", &columns, &indexes),
            rows: &rows,
        },
        crate::TableRows {
            table: table(b"Later", &[ID], &later_indexes),
            rows: &[&[RowValue::Long(99)]],
        },
    ];
    create(directory.target(), &requests)?;
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
        index(b"ZId", &PRIMARY, IndexKind::Primary),
        index(
            b"AGroup",
            &GROUP_KEY,
            IndexKind::Ordinary.with_null_policy(crate::IndexNullPolicy::IgnoreAllNull),
        ),
    ];
    let columns = [ColumnSpec::new(b"Id", ColumnType::AutoIncrement), GROUP];
    let table = table(b"Items", &columns, &indexes);
    let rows: &[&[RowValue<'_>]] = &[
        &[RowValue::AutoIncrement, RowValue::Null],
        &[RowValue::AutoIncrement, RowValue::Currency { scaled: 5 }],
        &[RowValue::AutoIncrement, RowValue::Currency { scaled: 5 }],
    ];
    for requested in [&rows[..0], rows] {
        let directory = TempDir::new("create")?;
        create(
            directory.target(),
            &[TableRows {
                table,
                rows: requested,
            }],
        )?;
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
    let directory = TempDir::new("create")?;
    let indexes = indexes();
    let columns = [ID, GROUP];
    let table = table(b"Items", &columns, &indexes);
    let rows: &[&[RowValue<'_>]] = &[
        &[RowValue::Long(1), RowValue::Currency { scaled: 7 }],
        &[RowValue::Long(2), RowValue::Currency { scaled: 8 }],
    ];
    create(directory.target(), &[TableRows { table, rows }])?;
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
            crate::create::check::check_initial_rows(
                &directory.target(),
                &table,
                rows,
                &mut budget()
            )
            .is_err()
        );
    }
    fs::write(directory.target(), &original)?;
    assert!(create(directory.target(), &[TableRows { table, rows }]).is_err());
    assert_eq!(fs::read(directory.target())?, original);
    let one = TableSpec {
        indexes: &indexes[..1],
        ..table
    };
    let mut charged = budget();
    crate::create::composer::InitialScalarIndex::for_table(&one, 20, &mut charged)?;
    let mut limited = ResourceBudget::new(
        ResourceLimits::default().with_max_allocation_bytes(charged.allocation_bytes()),
    );
    assert!(
        crate::create::composer::InitialScalarIndex::for_table(&table, 20, &mut limited).is_err()
    );
    Ok(())
}

#[test]
fn second_unique_index_refuses_duplicates_on_first_and_later_tables() -> TestResult {
    let directory = TempDir::new("create")?;
    let mut indexes = indexes();
    indexes[1].kind = IndexKind::Unique;
    let table = table(b"Items", &[ID, GROUP], &indexes[..2]);
    let rows: &[&[RowValue<'_>]] = &[
        &[RowValue::Long(1), RowValue::Currency { scaled: 7 }],
        &[RowValue::Long(2), RowValue::Currency { scaled: 7 }],
    ];
    assert!(matches!(
        create(directory.target(), &[TableRows { table, rows }]),
        Err(WriteError::Compose(
            ComposeError::DuplicateInitialScalarIndexKey
        ))
    ));
    let requests = [
        crate::TableRows {
            table: crate::testkit::table(b"First", &[ID], &[]),
            rows: &[],
        },
        crate::TableRows { table, rows },
    ];
    assert!(matches!(
        create(directory.target(), &requests),
        Err(WriteError::Compose(
            ComposeError::DuplicateInitialScalarIndexKey
        ))
    ));
    assert!(directory.entries()?.is_empty());
    Ok(())
}

#[test]
fn aggregate_index_pages_extend_independent_maps() -> TestResult {
    let directory = TempDir::new("create")?;
    let fields = [field(0, IndexDirection::Ascending)];
    let indexes = [b"First".as_slice(), b"Second", b"Third"]
        .map(|name| index(name, &fields, IndexKind::Ordinary));
    let table = table(b"Items", &[ID], &indexes);
    let row = [RowValue::Long(1)];
    let rows = vec![row.as_slice(); 60000];
    create(directory.target(), &[TableRows { table, rows: &rows }])?;
    assert!(fs::metadata(directory.target())?.len() > 1024 * crate::PAGE_BYTES as u64);
    Ok(())
}
