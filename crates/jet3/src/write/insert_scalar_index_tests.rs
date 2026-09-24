use super::insert_indexed_tests::*;
use crate::{
    ColumnOrdinal, ColumnSpec, ColumnType, DatabaseReader, IndexColumnSpec, IndexKind,
    IndexNullPolicy, IndexSpec, PAGE_BYTES, ResourceBudget, ResourceLimits, RowDelete, RowUpdate,
    RowValue, TableSpec, WriteError, write::insert::*,
};
use std::error::Error as StdError;
use std::fs;

fn fixture(
    values: &[[RowValue<'_>; 3]],
    unique: bool,
    ignore: bool,
) -> Result<Fixture, Box<dyn StdError>> {
    let f = Fixture::new(0, false, IndexKind::Primary)?;
    fs::remove_file(f.path())?;
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"A", ColumnType::Long),
        ColumnSpec::new(b"B", ColumnType::Long),
    ];
    let indexes = [
        IndexSpec {
            name: b"Id",
            kind: IndexKind::Primary,
            fields: &[IndexColumnSpec::ascending(0)],
        },
        IndexSpec {
            name: b"A",
            kind: IndexKind::Ordinary,
            fields: &[IndexColumnSpec::descending(1)],
        },
        IndexSpec {
            name: b"AB",
            kind: (if unique {
                IndexKind::Unique
            } else {
                IndexKind::Ordinary
            })
            .with_null_policy(if ignore {
                IndexNullPolicy::IgnoreAllNull
            } else {
                IndexNullPolicy::Include
            }),
            fields: &[
                IndexColumnSpec::ascending(1),
                IndexColumnSpec::descending(2),
            ],
        },
    ];
    let rows: Vec<_> = values.iter().map(|v| v.as_slice()).collect();
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

fn counts(f: &Fixture) -> Result<Vec<u32>, Box<dyn StdError>> {
    Ok(f.definition()?
        .physical_indexes()
        .iter()
        .map(|i| {
            let p = i.sourced_prefix();
            u32::from_le_bytes([p[4], p[5], p[6], p[7]])
        })
        .collect())
}

fn replace(f: &Fixture, id: i32, a: RowValue<'_>, b: RowValue<'_>) -> TestResult {
    let row = f
        .rows()?
        .into_iter()
        .find(|(value, _)| *value == id)
        .ok_or("missing Id")?
        .1;
    crate::update_row(
        f.path(),
        RowUpdate {
            table: b"Rows",
            row,
            values: &[RowValue::Long(id), a, b],
        },
        &mut budget(),
    )?;
    f.validate()
}

#[test]
fn numeric_counters_retain_duplicate_and_null_edits_below_live_distinct_keys() -> TestResult {
    let f = fixture(
        &[
            [RowValue::Long(1), RowValue::Null, RowValue::Null],
            [RowValue::Long(2), RowValue::Null, RowValue::Null],
        ],
        true,
        true,
    )?;
    assert_eq!(counts(&f)?, [2, 1, 0]);
    replace(&f, 1, RowValue::Long(7), RowValue::Long(70))?;
    replace(&f, 2, RowValue::Long(8), RowValue::Long(80))?;
    assert_eq!(counts(&f)?, [2, 1, 0]);
    insert_row(
        f.path(),
        b"Rows",
        &[RowValue::Long(3), RowValue::Null, RowValue::Null],
        &mut budget(),
    )?;
    assert_eq!(counts(&f)?, [3, 2, 0]);
    replace(&f, 3, RowValue::Long(7), RowValue::Null)?;
    assert_eq!(counts(&f)?, [3, 2, 0]);
    let row = f
        .rows()?
        .into_iter()
        .find(|(id, _)| *id == 1)
        .ok_or("missing row")?
        .1;
    crate::delete_row(
        f.path(),
        RowDelete {
            table: b"Rows",
            row,
        },
        &mut budget(),
    )?;
    assert_eq!(counts(&f)?, [3, 2, 0]);
    insert_row(
        f.path(),
        b"Rows",
        &[RowValue::Long(4), RowValue::Long(7), RowValue::Long(70)],
        &mut budget(),
    )?;
    assert_eq!(counts(&f)?, [4, 2, 1]);
    f.validate()
}

#[test]
fn numeric_duplicate_records_grow_and_remove_exactly_one_locator() -> TestResult {
    let values: Vec<_> = (0..140)
        .map(|id| [RowValue::Long(id), RowValue::Long(7), RowValue::Long(70)])
        .collect();
    let f = fixture(&values, false, false)?;
    let inserted = insert_row(
        f.path(),
        b"Rows",
        &[RowValue::Long(140), RowValue::Long(7), RowValue::Long(70)],
        &mut budget(),
    )?;
    assert_eq!(counts(&f)?, [141, 1, 1]);
    replace(&f, 1, RowValue::Long(8), RowValue::Long(80))?;
    replace(&f, 2, RowValue::Long(9), RowValue::Long(90))?;
    assert_eq!(counts(&f)?, [141, 1, 1]);
    crate::delete_row(
        f.path(),
        RowDelete {
            table: b"Rows",
            row: inserted,
        },
        &mut budget(),
    )?;
    assert_eq!(f.rows()?.len(), 140);
    assert_eq!(counts(&f)?, [141, 1, 1]);
    let mut b = budget();
    let mut db = DatabaseReader::open(f.path(), &mut b)?;
    let table = f.definition()?;
    let tree = db.index_tree(&table, 2, &mut b)?;
    assert!(tree.nodes().len() > 1);
    assert!(tree.entries().iter().all(|e| e.row() != inserted));
    f.validate()
}

#[test]
fn numeric_later_unique_index_failure_preserves_all_rows_indexes_and_counters() -> TestResult {
    let f = fixture(
        &[
            [RowValue::Long(1), RowValue::Long(7), RowValue::Long(70)],
            [RowValue::Long(2), RowValue::Long(8), RowValue::Long(80)],
        ],
        true,
        false,
    )?;
    let original = fs::read(f.path())?;
    assert!(
        insert_row(
            f.path(),
            b"Rows",
            &[RowValue::Long(3), RowValue::Long(7), RowValue::Long(70)],
            &mut budget()
        )
        .is_err()
    );
    assert_eq!(fs::read(f.path())?, original);
    assert!(replace(&f, 2, RowValue::Long(7), RowValue::Long(70)).is_err());
    assert_eq!(fs::read(f.path())?, original);
    let row = f.rows()?[1].1;
    crate::update_field(
        f.path(),
        crate::FieldUpdate {
            table: b"Rows",
            row,
            column: ColumnOrdinal::new(2),
            value: RowValue::Long(90),
        },
        &mut budget(),
    )?;
    assert_eq!(counts(&f)?, [2, 2, 2]);
    f.validate()
}

#[test]
fn numeric_overlapping_index_maps_and_free_index_pages_are_refused() -> TestResult {
    let f = fixture(
        &[[RowValue::Long(1), RowValue::Long(7), RowValue::Long(70)]],
        false,
        false,
    )?;
    let table = f.definition()?;
    let original = fs::read(f.path())?;
    let member = table.physical_indexes()[0].root();
    for (location, message) in [
        (
            crate::MapRowLocator::new(
                table.physical_indexes()[1].usage_map().page(),
                table.physical_indexes()[1].usage_map().row(),
            ),
            "overlapping index page ownership",
        ),
        (
            crate::MapRowLocator::new(crate::PageNumber::new(1), 0),
            "mapped index page is globally free",
        ),
    ] {
        let mut b = budget();
        let mut db = DatabaseReader::open(f.path(), &mut b)?;
        let mut bytes = [0; PAGE_BYTES];
        let page = db.read_classified_page(location.page(), &mut bytes, &mut b)?;
        let map = crate::locate_usage_map(page, location, &mut b)?;
        let start = u32::from_le_bytes(map.raw()[1..5].try_into()?) as u64;
        let bit = (member.get() - start) as usize;
        let offset = location.page().get() as usize * PAGE_BYTES + map.range().start + 5 + bit / 8;
        let mut bad = original.clone();
        bad[offset] |= 1 << (bit % 8);
        fs::write(f.path(), &bad)?;
        let error = insert_row(
            f.path(),
            b"Rows",
            &[RowValue::Long(2), RowValue::Long(8), RowValue::Long(80)],
            &mut budget(),
        )
        .err()
        .ok_or("expected map failure")?;
        assert!(
            matches!(error,WriteError::Mismatch(detail) if detail==message),
            "{error:?}"
        );
        assert_eq!(fs::read(f.path())?, bad);
        fs::write(f.path(), &original)?;
    }
    Ok(())
}

#[test]
fn numeric_late_counter_and_encoding_limits_preserve_the_entire_file() -> TestResult {
    let f = fixture(
        &[[RowValue::Long(1), RowValue::Long(7), RowValue::Long(70)]],
        true,
        false,
    )?;
    let original = fs::read(f.path())?;
    let values = [RowValue::Long(2), RowValue::Long(8), RowValue::Long(80)];
    let mut measured = budget();
    insert_row(f.path(), b"Rows", &values, &mut measured)?;
    fs::write(f.path(), &original)?;
    let mut limited = ResourceBudget::new(
        ResourceLimits::default()
            .with_max_encoded_bytes(crate::ByteCount::new(measured.encoded_bytes().get() - 1)),
    );
    assert!(insert_row(f.path(), b"Rows", &values, &mut limited).is_err());
    assert_eq!(fs::read(f.path())?, original);
    let table = f.definition()?;
    // EXP-0059/0230: third physical prefix's retained counter.
    let offset = table.root().get() as usize * PAGE_BYTES + 43 + 2 * 8 + 4;
    let mut exhausted = original;
    exhausted[offset..offset + 4].copy_from_slice(&u32::MAX.to_le_bytes());
    fs::write(f.path(), &exhausted)?;
    assert!(matches!(
        insert_row(f.path(), b"Rows", &values, &mut budget()),
        Err(WriteError::Unsupported("index counter overflow"))
    ));
    assert_eq!(fs::read(f.path())?, exhausted);
    f.validate()
}
