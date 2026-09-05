use super::*;
use crate::{
    ColumnSpec, ColumnType, IndexColumnSpec, IndexKind, IndexSpec, ResourceLimits, RowDelete,
    TableRows, TableSpec,
};
use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
type TestResult = Result<(), Box<dyn StdError>>;
static NEXT: AtomicU64 = AtomicU64::new(0);
fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}
struct Fixture {
    directory: PathBuf,
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.directory);
    }
}
impl Fixture {
    fn new(count: usize, descending: bool, kind: IndexKind) -> Result<Self, Box<dyn StdError>> {
        let directory = std::env::temp_dir().join(format!(
            "jet3-indexed-mutations-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&directory)?;
        let f = Self { directory };
        let columns = [
            ColumnSpec::new(b"Id", ColumnType::Long),
            ColumnSpec::new(b"Value", ColumnType::Long),
        ];
        let fields = [if descending {
            IndexColumnSpec::descending(0)
        } else {
            IndexColumnSpec::ascending(0)
        }];
        let indexes = [IndexSpec {
            name: b"ById",
            kind,
            fields: &fields,
        }];
        let values: Vec<_> = (0..count)
            .map(|n| [RowValue::Long(n as i32), RowValue::Long(-(n as i32))])
            .collect();
        let rows: Vec<_> = values.iter().map(|r| r.as_slice()).collect();
        let extra = [RowValue::Long(99), RowValue::Long(88)];
        let tables = [
            TableRows {
                table: TableSpec {
                    name: b"Unrelated",
                    columns: &columns,
                    indexes: &[],
                },
                rows: &[&extra],
            },
            TableRows {
                table: TableSpec {
                    name: b"Rows",
                    columns: &columns,
                    indexes: &indexes,
                },
                rows: &rows,
            },
        ];
        crate::create_database_with_table_rows(f.path(), &tables, &mut budget())?;
        let mut bytes = fs::read(f.path())?;
        bytes.extend_from_slice(&[0xb7; PAGE_BYTES]);
        fs::write(f.path(), bytes)?;
        Ok(f)
    }
    fn path(&self) -> PathBuf {
        self.directory.join("source.mdb")
    }
    fn definition(&self) -> Result<crate::TableDefinition, Box<dyn StdError>> {
        let mut b = budget();
        let mut db = DatabaseReader::open(self.path(), &mut b)?;
        Ok(crate::update::indexed_writable_table(
            &mut db, b"Rows", &mut b,
        )?)
    }
    fn rows(&self) -> Result<Vec<(i32, RowLocator)>, Box<dyn StdError>> {
        let mut b = budget();
        let mut db = DatabaseReader::open(self.path(), &mut b)?;
        let def = crate::update::indexed_writable_table(&mut db, b"Rows", &mut b)?;
        let mut cursor = db.rows(&def, &mut b)?;
        let mut result = Vec::new();
        while let Some(row) = cursor.next_row()? {
            let raw = row
                .field(crate::ColumnOrdinal::new(0))
                .and_then(|f| f.raw_bytes())
                .ok_or("Id")?;
            result.push((i32::from_le_bytes(raw.try_into()?), row.locator()));
        }
        Ok(result)
    }
    fn validate(&self) -> TestResult {
        let mut b = budget();
        let mut db = DatabaseReader::open(self.path(), &mut b)?;
        let def = crate::update::indexed_writable_table(&mut db, b"Rows", &mut b)?;
        let leaf = crate::unique_leaf::validate(&mut db, &def, &mut b)?;
        leaf.check_map(&mut db, &def, &mut b)?;
        assert_eq!(fs::read_dir(&self.directory)?.count(), 1);
        Ok(())
    }
}
fn page(bytes: &[u8], number: crate::PageNumber) -> Result<&[u8; PAGE_BYTES], Box<dyn StdError>> {
    let start = number.get() as usize * PAGE_BYTES;
    Ok(bytes[start..start + PAGE_BYTES].try_into()?)
}
fn preserve(
    before: &[u8],
    after: &[u8],
    table: &crate::TableDefinition,
    row: RowLocator,
    insertion: bool,
) -> TestResult {
    let mut expected = before.to_vec();
    let root = table.root().get() as usize * PAGE_BYTES;
    expected[root + 12..root + 16].copy_from_slice(&after[root + 12..root + 16]);
    expected[root + 47..root + 51].copy_from_slice(&after[root + 47..root + 51]);
    let leaf = table.physical_indexes()[0].root();
    let base = leaf.get() as usize * PAGE_BYTES;
    let count = crate::index_tree_page::boundaries(page(after, leaf)?).count();
    assert_eq!(
        &after[base + 2..base + 4],
        &((1800 - count * 9) as u16).to_le_bytes()
    );
    assert_eq!(
        crate::index_tree_page::boundaries(page(after, leaf)?).collect::<Vec<_>>(),
        (1..=count).map(|n| n * 9).collect::<Vec<_>>()
    );
    expected[base + 2..base + 4].copy_from_slice(&after[base + 2..base + 4]);
    expected[base + 22..base + 248 + count * 9]
        .copy_from_slice(&after[base + 22..base + 248 + count * 9]);
    let old = page(before, row.page())?;
    let new = page(after, row.page())?;
    let dir =
        crate::row_directory::RowDirectory::validate(row.page(), table.root(), old, &mut budget())?;
    let next =
        crate::row_directory::RowDirectory::validate(row.page(), table.root(), new, &mut budget())?;
    let base = row.page().get() as usize * PAGE_BYTES;
    expected[base + 2..base + 4].copy_from_slice(&new[2..4]);
    let lowest = dir.entry(old, (dir.row_count() - 1) as u8)?.range().start;
    if insertion {
        assert_eq!(next.row_count(), dir.row_count() + 1);
        expected[base + 8..base + 10].copy_from_slice(&new[8..10]);
        let slot = 10 + usize::from(dir.row_count()) * 2;
        expected[base + slot..base + slot + 2].copy_from_slice(&new[slot..slot + 2]);
        let added = next.entry(new, row.slot())?.range();
        assert_eq!(added.end, lowest);
        expected[base + added.start..base + added.end].copy_from_slice(&new[added]);
    } else {
        assert_eq!(next.row_count(), dir.row_count());
        let removed = dir.entry(old, row.slot())?.range();
        let affected_start = lowest + removed.len();
        expected[base + affected_start..base + removed.end]
            .copy_from_slice(&new[affected_start..removed.end]);
        let slot = 10 + usize::from(row.slot()) * 2;
        let end = 10 + usize::from(dir.row_count()) * 2;
        expected[base + slot..base + end].copy_from_slice(&new[slot..end]);
    }
    assert_eq!(after, expected);
    Ok(())
}
#[test]
fn indexed_rows_insert_delete_and_repeat_preserve_three_page_scope() -> TestResult {
    for (kind, descending) in [(IndexKind::Primary, false), (IndexKind::Unique, true)] {
        let f = Fixture::new(4, descending, kind)?;
        for value in [i32::MIN, i32::MAX] {
            let before = fs::read(f.path())?;
            let table = f.definition()?;
            let prior = f.rows()?;
            let row = insert_row(
                f.path(),
                b"Rows",
                &[RowValue::Long(value), RowValue::Long(73)],
                &mut budget(),
            )?;
            preserve(&before, &fs::read(f.path())?, &table, row, true)?;
            f.validate()?;
            for pair in prior {
                assert!(f.rows()?.contains(&pair));
            }
        }
        for id in [1, 0, 3, i32::MIN] {
            let before = fs::read(f.path())?;
            let table = f.definition()?;
            let prior = f.rows()?;
            let row = prior.iter().find(|(v, _)| *v == id).ok_or("row")?.1;
            crate::delete_row(
                f.path(),
                RowDelete {
                    table: b"Rows",
                    row,
                },
                &mut budget(),
            )?;
            preserve(&before, &fs::read(f.path())?, &table, row, false)?;
            f.validate()?;
            assert_eq!(
                f.rows()?,
                prior
                    .into_iter()
                    .filter(|(v, _)| *v != id)
                    .collect::<Vec<_>>()
            );
        }
    }
    Ok(())
}
#[test]
fn indexed_rows_actual_leaf_capacity_and_last_row_refuse_without_publication() -> TestResult {
    let f = Fixture::new(199, false, IndexKind::Primary)?;
    insert_row(
        f.path(),
        b"Rows",
        &[RowValue::Long(-1), RowValue::Long(3)],
        &mut budget(),
    )?;
    f.validate()?;
    let before = fs::read(f.path())?;
    assert!(matches!(
        insert_row(
            f.path(),
            b"Rows",
            &[RowValue::Long(-2), RowValue::Long(3)],
            &mut budget()
        ),
        Err(UpdateError::Unsupported("full root leaf"))
    ));
    assert_eq!(fs::read(f.path())?, before);
    for count in [1, 201] {
        let f = Fixture::new(count, false, IndexKind::Primary)?;
        let before = fs::read(f.path())?;
        let row = f.rows()?[0].1;
        assert!(
            crate::delete_row(
                f.path(),
                RowDelete {
                    table: b"Rows",
                    row
                },
                &mut budget()
            )
            .is_err()
        );
        assert_eq!(fs::read(f.path())?, before);
        if count != 1 {
            assert!(
                insert_row(
                    f.path(),
                    b"Rows",
                    &[RowValue::Long(-1), RowValue::Long(3)],
                    &mut budget()
                )
                .is_err()
            );
            assert_eq!(fs::read(f.path())?, before);
        }
    }
    Ok(())
}
#[test]
fn indexed_rows_duplicate_null_ordinary_and_budget_refuse() -> TestResult {
    let f = Fixture::new(3, false, IndexKind::Unique)?;
    let before = fs::read(f.path())?;
    for value in [RowValue::Long(1), RowValue::Null] {
        assert!(
            insert_row(
                f.path(),
                b"Rows",
                &[value, RowValue::Long(5)],
                &mut budget()
            )
            .is_err()
        );
        assert_eq!(fs::read(f.path())?, before);
    }
    let mut limited = ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(1));
    assert!(
        insert_row(
            f.path(),
            b"Rows",
            &[RowValue::Long(-1), RowValue::Long(5)],
            &mut limited
        )
        .is_err()
    );
    assert_eq!(fs::read(f.path())?, before);
    let f = Fixture::new(3, false, IndexKind::Ordinary)?;
    let before = fs::read(f.path())?;
    assert!(
        insert_row(
            f.path(),
            b"Rows",
            &[RowValue::Long(-1), RowValue::Long(5)],
            &mut budget()
        )
        .is_err()
    );
    assert!(
        crate::delete_row(
            f.path(),
            RowDelete {
                table: b"Rows",
                row: f.rows()?[0].1
            },
            &mut budget()
        )
        .is_err()
    );
    assert_eq!(fs::read(f.path())?, before);
    Ok(())
}
#[test]
fn indexed_rows_corrupt_keys_counts_map_and_leaf_framing_refuse() -> TestResult {
    let f = Fixture::new(3, false, IndexKind::Primary)?;
    let original = fs::read(f.path())?;
    let table = f.definition()?;
    let row = f.rows()?[1].1;
    let index = table.physical_indexes()[0].root().get() as usize * PAGE_BYTES;
    let location = table.physical_indexes()[0].usage_map();
    let map = page(&original, location.page())?;
    let directory = crate::row_directory::RowDirectory::validate(
        location.page(),
        crate::PageNumber::new(0),
        map,
        &mut budget(),
    )?;
    let map_start = location.page().get() as usize * PAGE_BYTES
        + directory.entry(map, location.row())?.range().start;
    for (offset, value) in [
        (index + 252, 99),
        (index + 256, 1),
        (index + 2, 0),
        (index + 20, 1),
        (index + 23, 0),
        (table.root().get() as usize * PAGE_BYTES + 12, 9),
        (table.root().get() as usize * PAGE_BYTES + 47, 9),
        (map_start + 5, 0xff),
    ] {
        let mut bytes = original.clone();
        bytes[offset] = value;
        fs::write(f.path(), &bytes)?;
        assert!(
            insert_row(
                f.path(),
                b"Rows",
                &[RowValue::Long(-1), RowValue::Long(5)],
                &mut budget()
            )
            .is_err(),
            "offset {offset}"
        );
        assert!(
            crate::delete_row(
                f.path(),
                RowDelete {
                    table: b"Rows",
                    row
                },
                &mut budget()
            )
            .is_err(),
            "offset {offset}"
        );
        assert_eq!(fs::read(f.path())?, bytes);
    }
    Ok(())
}
#[test]
fn indexed_rows_private_corruption_preserves_original() -> TestResult {
    let f = Fixture::new(3, false, IndexKind::Primary)?;
    let before = fs::read(f.path())?;
    let result = insert_with_hook(
        &f.path(),
        b"Rows",
        &[RowValue::Long(-1), RowValue::Long(5)],
        &mut budget(),
        |stage| {
            if stage == PublishStage::Validation {
                let path = fs::read_dir(&f.directory)?
                    .filter_map(Result::ok)
                    .map(|e| e.path())
                    .find(|p| *p != f.path())
                    .ok_or_else(|| std::io::Error::other("private"))?;
                let mut bytes = fs::read(&path)?;
                bytes[100] ^= 1;
                fs::write(path, bytes)?;
            }
            Ok::<(), std::io::Error>(())
        },
    );
    assert!(result.is_err());
    assert_eq!(fs::read(f.path())?, before);
    assert_eq!(fs::read_dir(&f.directory)?.count(), 1);
    Ok(())
}

#[test]
fn indexed_rows_no_data_capacity_and_multiple_indexes_refuse() -> TestResult {
    let f = Fixture::new(3, false, IndexKind::Primary)?;
    let table = f.definition()?;
    let map = table.maps().available();
    let mut bytes = fs::read(f.path())?;
    let raw = page(&bytes, map.page())?;
    let directory = crate::row_directory::RowDirectory::validate(
        map.page(),
        crate::PageNumber::new(0),
        raw,
        &mut budget(),
    )?;
    let range = directory.entry(raw, map.row())?.range();
    let start = map.page().get() as usize * PAGE_BYTES + range.start + 5;
    let end = map.page().get() as usize * PAGE_BYTES + range.end;
    bytes[start..end].fill(0);
    fs::write(f.path(), &bytes)?;
    assert!(matches!(
        insert_row(
            f.path(),
            b"Rows",
            &[RowValue::Long(-1), RowValue::Long(2)],
            &mut budget()
        ),
        Err(UpdateError::Unsupported(
            "indexed insertion requires populated page capacity"
        ))
    ));
    assert_eq!(fs::read(f.path())?, bytes);
    fs::remove_file(f.path())?;
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"Value", ColumnType::Long),
    ];
    let fields = [IndexColumnSpec::ascending(0)];
    let indexes = [
        IndexSpec {
            name: b"One",
            kind: IndexKind::Primary,
            fields: &fields,
        },
        IndexSpec {
            name: b"Two",
            kind: IndexKind::Unique,
            fields: &fields,
        },
    ];
    let values = [
        [RowValue::Long(1), RowValue::Long(2)],
        [RowValue::Long(2), RowValue::Long(3)],
    ];
    crate::create_database_with_rows(
        f.path(),
        &TableSpec {
            name: b"Rows",
            columns: &columns,
            indexes: &indexes,
        },
        &[&values[0], &values[1]],
        &mut budget(),
    )?;
    let before = fs::read(f.path())?;
    let row = f.rows()?[0].1;
    assert!(
        insert_row(
            f.path(),
            b"Rows",
            &[RowValue::Long(-1), RowValue::Long(2)],
            &mut budget()
        )
        .is_err()
    );
    assert!(
        crate::delete_row(
            f.path(),
            RowDelete {
                table: b"Rows",
                row
            },
            &mut budget()
        )
        .is_err()
    );
    assert_eq!(fs::read(f.path())?, before);
    Ok(())
}
