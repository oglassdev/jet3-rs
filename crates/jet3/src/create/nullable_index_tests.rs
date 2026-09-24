use super::initial_index_tests::*;
use crate::WriteError;
use crate::testkit::create;
use crate::testkit::{index, table};
use crate::{
    ColumnSpec, ColumnType, ComposeError, DatabaseReader, IndexColumnSpec, IndexDirection,
    IndexKind, IndexNullPolicy, IndexSpec, PageNumber, ResourceBudget, ResourceLimits, RowValue,
    TableRows, TableSpec, create::api_tests::*,
};
use std::fs;

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
        let directory = TempDir::new("create")?;
        let indexes = [index(b"ById", &TWO, kind)];
        let table = table(b"Items", &COLUMNS, &indexes);
        create(directory.target(), &[TableRows { table, rows: &rows }])?;
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
            let directory = TempDir::new("create")?;
            let fields = [field(0, direction)];
            let indexes = [index(b"ById", &fields, kind)];
            let table = table(b"Items", &[ID], &indexes);
            create(
                directory.target(),
                &[TableRows {
                    table,
                    rows: &[&[RowValue::Null], &[RowValue::Null]],
                }],
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
    let directory = TempDir::new("create")?;
    fs::write(directory.target(), b"preserve")?;
    let rows: &[&[RowValue<'_>]] = &[&[RowValue::Null, RowValue::Long(1)]];
    for kind in [
        IndexKind::Primary,
        IndexKind::Ordinary.with_null_policy(IndexNullPolicy::Required),
    ] {
        let indexes = [index(b"ById", &TWO, kind)];
        let table = table(b"Items", &COLUMNS, &indexes);
        assert!(matches!(
            create(directory.target(), &[TableRows { table, rows }]),
            Err(WriteError::Compose(ComposeError::NullInitialIndexKey {
                row: 0
            }))
        ));
        assert_eq!(fs::read(directory.target())?, b"preserve");
    }
    let indexes = [index(b"ById", &TWO, IndexKind::Unique)];
    let table = table(b"Items", &COLUMNS, &indexes);
    let duplicate: &[RowValue<'_>] = &[RowValue::Long(1), RowValue::Long(2)];
    assert!(matches!(
        create(
            directory.target(),
            &[TableRows {
                table,
                rows: &[duplicate, duplicate]
            }]
        ),
        Err(WriteError::Compose(
            ComposeError::DuplicateInitialCompositeIndexKey { values: [1, 2] }
        ))
    ));
    assert_eq!(fs::read(directory.target())?, b"preserve");
    let invalid = [IndexSpec {
        kind: IndexKind::Primary.with_null_policy(IndexNullPolicy::IgnoreAllNull),
        ..indexes[0]
    }];
    assert!(
        create(
            directory.target(),
            &[TableRows {
                table: TableSpec {
                    indexes: &invalid,
                    ..table
                },
                rows: &[]
            }]
        )
        .is_err()
    );
    assert_eq!(fs::read(directory.target())?, b"preserve");
    Ok(())
}

#[test]
fn variable_width_duplicate_runs_span_three_levels_and_later_table_maps() -> TestResult {
    let directory = TempDir::new("create")?;
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
    let indexes = [index(b"ById", &TWO, IndexKind::Unique)];
    let table = table(b"Items", &COLUMNS, &indexes);
    let requests = [
        crate::TableRows {
            table: crate::testkit::table(b"Empty", &[ID], &[]),
            rows: &[],
        },
        crate::TableRows { table, rows: &rows },
    ];
    create(directory.target(), &requests)?;
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
        crate::create_database(
            directory.target(),
            &crate::DatabaseSpec {
                tables: &requests,
                ..crate::DatabaseSpec::default()
            },
            &mut insufficient
        )
        .is_err()
    );
    assert_eq!(fs::read(directory.target())?, original);
    Ok(())
}

#[test]
fn nullable_auto_components_and_corrupt_flags_or_keys_are_checked() -> TestResult {
    let directory = TempDir::new("create")?;
    let columns = [ID, ColumnSpec::new(b"B", ColumnType::AutoIncrement)];
    let indexes = [index(b"ById", &TWO, IndexKind::Unique)];
    let table = table(b"Items", &columns, &indexes);
    let rows: &[&[RowValue<'_>]] = &[
        &[RowValue::Null, RowValue::AutoIncrement],
        &[RowValue::Long(1), RowValue::AutoIncrement],
    ];
    create(directory.target(), &[TableRows { table, rows }])?;
    assert_eq!(
        tree(&directory.target())?.entries()[0].key().raw_bytes(),
        &[0, 0x80, 0x7f, 0xff, 0xff, 0xfe]
    );
    let original = fs::read(directory.target())?;
    let mut corrupt = original.clone();
    corrupt[23 * crate::PAGE_BYTES + 248] = 1;
    fs::write(directory.target(), corrupt)?;
    assert!(
        super::check::check_initial_rows(&directory.target(), &table, rows, &mut budget()).is_err()
    );
    let mut corrupt = original;
    // Two column records followed by their exact length-prefixed names.
    let flag_offset = 20 * crate::PAGE_BYTES
        + 43
        + 8
        + 36
        + columns
            .iter()
            .map(|column| 1 + column.name().len())
            .sum::<usize>()
        + 38;
    corrupt[flag_offset] = 4;
    fs::write(directory.target(), corrupt)?;
    let mut db = DatabaseReader::open(directory.target(), &mut budget())?;
    assert!(
        db.table_definition(PageNumber::new(20), &mut budget())
            .is_err()
    );
    Ok(())
}
