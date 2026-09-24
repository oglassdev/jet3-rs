use super::delete::*;
pub(super) use crate::testkit::TestResult;
use crate::testkit::create;
use crate::testkit::table;
use crate::{
    ByteCount, ColumnSpec, ColumnType, DatabaseReader, MapRowLocator, PAGE_BYTES, PageNumber,
    PublishStage, ResourceBudget, ResourceLimits, RowLocator, RowValue, WriteError,
};
use std::error::Error as StdError;
use std::fs;
use std::path::{Path, PathBuf};
pub(super) struct Fixture {
    pub(super) directory: crate::testkit::TempDir,
    pub(super) row: RowLocator,
    pub(super) root: PageNumber,
}
pub(super) use crate::testkit::budget;
impl Fixture {
    pub(super) fn new(count: usize) -> Result<Self, Box<dyn StdError>> {
        let directory = crate::testkit::TempDir::new("delete")?;
        let path = directory.join("source.mdb");
        let columns = [
            ColumnSpec::new(b"Id", ColumnType::Long),
            ColumnSpec::new(b"Value", ColumnType::Long),
        ];
        let values: Vec<_> = (0..count)
            .map(|n| [RowValue::Long(n as i32), RowValue::Long(-(n as i32))])
            .collect();
        let rows: Vec<_> = values.iter().map(|r| r.as_slice()).collect();
        create(
            &path,
            &[crate::TableRows {
                table: table(b"Rows", &columns, &[]),
                rows: &rows,
            }],
        )?;
        let mut b = budget();
        let mut db = DatabaseReader::open(&path, &mut b)?;
        let def = crate::write::update::writable_table(&mut db, b"Rows", &mut b)?;
        let root = def.root();
        let row = {
            let mut cursor = db.rows(&def, &mut b)?;
            let mut row = None;
            while let Some(value) = cursor.next_row()? {
                row = Some(value.locator());
            }
            row.ok_or("missing row")?
        };
        drop(db);
        let mut image = fs::read(&path)?;
        let base = row.page().get() as usize * PAGE_BYTES;
        // EXP-0162 closed DAO pages carry the exact contiguous free-byte count.
        let free = 2048 - 10 - 12 * count;
        image[base + 2..base + 4].copy_from_slice(&(free as u16).to_le_bytes());
        image[base + 100] = 0xa7;
        image.extend_from_slice(&[0xb6; PAGE_BYTES]);
        fs::write(&path, image)?;
        Ok(Self {
            directory,
            row,
            root,
        })
    }
    pub(super) fn path(&self) -> PathBuf {
        self.directory.join("source.mdb")
    }
    pub(super) fn request(&self) -> RowDelete<'_> {
        RowDelete {
            table: b"Rows",
            row: self.row,
        }
    }
    pub(super) fn clean(&self) -> TestResult {
        assert_eq!(fs::read_dir(&self.directory)?.count(), 1);
        Ok(())
    }
}

pub(super) type UsageMaps = [(MapRowLocator, std::ops::Range<usize>); 3];
/// Global, owned and available usage-map records of the table rooted at `root`.
pub(super) fn usage_maps(path: &Path, root: PageNumber) -> Result<UsageMaps, Box<dyn StdError>> {
    let mut b = budget();
    let mut db = DatabaseReader::open(path, &mut b)?;
    let def = db.table_definition(root, &mut b)?;
    let locations = [
        MapRowLocator::new(PageNumber::new(1), 0),
        def.maps().owned(),
        def.maps().available(),
    ];
    let mut result = std::array::from_fn(|i| (locations[i], 0..0));
    for (location, range) in &mut result {
        let mut bytes = [0; PAGE_BYTES];
        let page = db.read_classified_page(location.page(), &mut bytes, &mut b)?;
        *range = crate::locate_usage_map(page, *location, &mut b)?.range();
    }
    Ok(result)
}

fn expected_delete(before: &[u8], f: &Fixture, slot: u8) -> Result<Vec<u8>, Box<dyn StdError>> {
    let base = f.row.page().get() as usize * PAGE_BYTES;
    let source: &[u8; PAGE_BYTES] = before[base..base + PAGE_BYTES].try_into()?;
    let directory =
        crate::row::directory::RowDirectory::validate(f.row.page(), f.root, source, &mut budget())?;
    let mut expected = before.to_vec();
    let mut end = PAGE_BYTES;
    let mut live = 0_u32;
    for ordinal in 0..directory.row_count() {
        let entry = directory.entry(source, ordinal as u8)?;
        let word = if ordinal == u16::from(slot) || entry.range().is_empty() {
            end as u16 | 0xc000
        } else {
            let bytes = &source[entry.range()];
            let start = end - bytes.len();
            expected[base + start..base + end].copy_from_slice(bytes);
            end = start;
            live += 1;
            start as u16
        };
        let offset = base + 10 + 2 * ordinal as usize;
        expected[offset..offset + 2].copy_from_slice(&word.to_le_bytes());
    }
    expected[base + 2..base + 4]
        .copy_from_slice(&((end - 10 - 2 * directory.row_count() as usize) as u16).to_le_bytes());
    let root = f.root.get() as usize * PAGE_BYTES;
    expected[root + 12..root + 16].copy_from_slice(&live.to_le_bytes());
    Ok(expected)
}

type ObservedRows = Vec<(u8, Vec<u8>)>;
fn observed(f: &Fixture) -> Result<ObservedRows, Box<dyn StdError>> {
    let mut b = budget();
    let mut db = DatabaseReader::open(f.path(), &mut b)?;
    let definition = db.table_definition(f.root, &mut b)?;
    let mut rows = db.rows(&definition, &mut b)?;
    let mut values = Vec::new();
    while let Some(row) = rows.next_row()? {
        assert_eq!(row.locator(), row.storage_locator());
        assert_eq!(row.locator().page(), f.row.page());
        values.push((
            row.locator().slot(),
            row.field(crate::ColumnOrdinal::new(0))
                .and_then(|v| v.raw_bytes())
                .ok_or("missing Id")?
                .to_vec(),
        ));
    }
    Ok(values)
}

#[test]
fn tail_tombstone_count_and_free_bytes_preserve_all_other_bytes() -> TestResult {
    let f = Fixture::new(4)?;
    let before = fs::read(f.path())?;
    delete_row(f.path(), f.request(), &mut budget())?;
    let mut expected = before.clone();
    let page = f.row.page().get() as usize * PAGE_BYTES;
    expected[page + 2..page + 4].copy_from_slice(&2000_u16.to_le_bytes());
    expected[page + 16..page + 18].copy_from_slice(&0xc7e2_u16.to_le_bytes());
    let root = f.root.get() as usize * PAGE_BYTES;
    expected[root + 12..root + 16].copy_from_slice(&3_u32.to_le_bytes());
    assert_eq!(fs::read(f.path())?, expected);
    assert_eq!(
        observed(&f)?,
        (0..3_u8)
            .map(|i| (i, i32::from(i).to_le_bytes().to_vec()))
            .collect::<Vec<_>>()
    );
    assert!(delete_row(f.path(), f.request(), &mut budget()).is_err());
    assert_eq!(fs::read(f.path())?, expected);
    f.clean()
}

#[test]
fn first_middle_and_tail_unequal_rows_preserve_slots_and_vacated_slack() -> TestResult {
    let f = Fixture::new(4)?;
    fs::remove_file(f.path())?;
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(
            b"Payload",
            ColumnType::Text {
                max_len: crate::definition::column_writer::nz(255),
            },
        ),
    ];
    let text = [b'x'; 255];
    let values = [
        [RowValue::Long(1), RowValue::Text(b"a")],
        [RowValue::Long(2), RowValue::Text(&text[..170])],
        [RowValue::Long(3), RowValue::Text(b"short")],
        [RowValue::Long(4), RowValue::Text(&text)],
    ];
    let rows: Vec<_> = values.iter().map(|r| r.as_slice()).collect();
    create(
        f.path(),
        &[crate::TableRows {
            table: table(b"Rows", &columns, &[]),
            rows: &rows,
        }],
    )?;
    let before = fs::read(f.path())?;
    for slot in [0, 1, 2, 3] {
        fs::write(f.path(), &before)?;
        delete_row(
            f.path(),
            RowDelete {
                row: RowLocator::new(f.row.page(), slot),
                ..f.request()
            },
            &mut budget(),
        )?;
        assert_eq!(fs::read(f.path())?, expected_delete(&before, &f, slot)?);
        assert_eq!(
            observed(&f)?,
            (0..4_u8)
                .filter(|i| *i != slot)
                .map(|i| (i, (i as i32 + 1).to_le_bytes().to_vec()))
                .collect::<Vec<_>>()
        );
        f.clean()?;
    }
    Ok(())
}

#[test]
fn repeated_deletions_shift_empty_tombstones_until_one_live_row_remains() -> TestResult {
    let f = Fixture::new(5)?;
    let mut remaining = vec![0, 1, 2, 3, 4];
    for slot in [1, 3, 0, 4] {
        let before = fs::read(f.path())?;
        delete_row(
            f.path(),
            RowDelete {
                row: RowLocator::new(f.row.page(), slot),
                ..f.request()
            },
            &mut budget(),
        )?;
        assert_eq!(fs::read(f.path())?, expected_delete(&before, &f, slot)?);
        remaining.retain(|v| *v != slot);
        assert_eq!(
            observed(&f)?,
            remaining
                .iter()
                .map(|v| (*v, (*v as i32).to_le_bytes().to_vec()))
                .collect::<Vec<_>>()
        );
        let after = fs::read(f.path())?;
        assert!(
            delete_row(
                f.path(),
                RowDelete {
                    row: RowLocator::new(f.row.page(), slot),
                    ..f.request()
                },
                &mut budget()
            )
            .is_err()
        );
        assert_eq!(fs::read(f.path())?, after);
    }
    let inserted = crate::insert_row(
        f.path(),
        b"Rows",
        &[RowValue::Long(99), RowValue::Long(-9900)],
        &mut budget(),
    )?;
    assert_eq!(inserted, RowLocator::new(f.row.page(), 5));
    let before = fs::read(f.path())?;
    delete_row(
        f.path(),
        RowDelete {
            row: RowLocator::new(f.row.page(), 2),
            ..f.request()
        },
        &mut budget(),
    )?;
    assert_eq!(fs::read(f.path())?, expected_delete(&before, &f, 2)?);
    assert_eq!(observed(&f)?, vec![(5, 99_i32.to_le_bytes().to_vec())]);
    f.clean()
}

#[test]
fn unsupported_locators_and_malformed_pages_preserve_original() -> TestResult {
    let f = Fixture::new(4)?;
    let original = fs::read(f.path())?;
    for request in [
        RowDelete {
            table: b"Missing",
            ..f.request()
        },
        RowDelete {
            row: RowLocator::new(f.row.page(), 200),
            ..f.request()
        },
        RowDelete {
            row: RowLocator::new(f.root, 0),
            ..f.request()
        },
    ] {
        assert!(delete_row(f.path(), request, &mut budget()).is_err());
        assert_eq!(fs::read(f.path())?, original);
    }
    let refuse = |slot: u8, bad: &[u8]| -> TestResult {
        fs::write(f.path(), bad)?;
        let request = RowDelete {
            row: RowLocator::new(f.row.page(), slot),
            ..f.request()
        };
        assert!(delete_row(f.path(), request, &mut budget()).is_err());
        assert_eq!(fs::read(f.path())?, bad);
        f.clean()
    };
    let page = f.row.page().get() as usize * PAGE_BYTES;
    let root = f.root.get() as usize * PAGE_BYTES;
    for (offset, byte) in [
        (page + 2, 0),
        (root + 12, 0),
        (page + 17, 0x27),
        (page + 11, 0x87),
        (page + 16, 0),
    ] {
        let mut bad = original.clone();
        bad[offset] = byte;
        refuse(f.row.slot(), &bad)?;
    }
    // Compacting deletions also validate every moved slot and its flags.
    for (offset, word) in [
        (12, 0xc7ec_u16),
        (12, 0x87ec),
        (12, 0x07f6),
        (14, 0x07ff),
        (12, 0x0001),
        (12, 0xc800),
        (2, 0),
    ] {
        let mut bad = original.clone();
        bad[page + offset..page + offset + 2].copy_from_slice(&word.to_le_bytes());
        refuse(0, &bad)?;
    }
    Ok(())
}

#[test]
fn exact_budgets_and_private_verification_guard_each_delete_shape() -> TestResult {
    // Tail tombstone, compacting deletion and sole-row page release.
    for (count, slot, page_offsets) in [
        (4, 3, &[][..]),
        (4, 0, &[100, 2020][..]),
        (1, 0, &[2040][..]),
    ] {
        let f = Fixture::new(count)?;
        let request = RowDelete {
            row: RowLocator::new(f.row.page(), slot),
            ..f.request()
        };
        let before = fs::read(f.path())?;
        let mut exact = budget();
        delete_row(f.path(), request, &mut exact)?;
        let reads = crate::ReadLimits::new(
            ByteCount::new(1_000_000),
            ByteCount::new(1_000_000),
            ByteCount::new(exact.read_budget().total_read().get() - 1),
        );
        for limits in [
            ResourceLimits::default()
                .with_max_encoded_bytes(ByteCount::new(exact.encoded_bytes().get() - 1)),
            ResourceLimits::default().with_max_total_work_units(exact.total_work_units() - 1),
            ResourceLimits::new(reads),
            ResourceLimits::default().with_max_allocation_bytes(ByteCount::new(0)),
            ResourceLimits::default().with_max_total_work_units(1),
            ResourceLimits::default().with_max_encoded_bytes(ByteCount::new(7)),
        ] {
            fs::write(f.path(), &before)?;
            assert!(
                delete_row(f.path(), request, &mut ResourceBudget::new(limits)).is_err(),
                "{count} rows, slot {slot}"
            );
            assert_eq!(fs::read(f.path())?, before);
            f.clean()?;
        }
        let page = f.row.page().get() as usize * PAGE_BYTES;
        for offset in std::iter::once(64).chain(page_offsets.iter().map(|o| page + o)) {
            let result = delete_with_hook(
                &f.path(),
                request,
                &mut budget(),
                |stage| -> Result<(), std::io::Error> {
                    if stage == PublishStage::Validation {
                        for entry in fs::read_dir(&f.directory)? {
                            let path = entry?.path();
                            if path != f.path() {
                                let mut bytes = fs::read(&path)?;
                                bytes[offset] ^= 1;
                                fs::write(path, bytes)?;
                            }
                        }
                    }
                    Ok(())
                },
            );
            assert!(
                matches!(result, Err(WriteError::Publish(e)) if e.stage() == PublishStage::Validation),
                "{count} rows, slot {slot}, offset {offset}"
            );
            assert_eq!(fs::read(f.path())?, before);
            f.clean()?;
        }
    }
    Ok(())
}

#[test]
fn deletion_restores_available_membership() -> TestResult {
    let f = Fixture::new(4)?;
    let mut b = budget();
    let mut db = DatabaseReader::open(f.path(), &mut b)?;
    let definition = db.table_definition(f.root, &mut b)?;
    let locator = definition.maps().available();
    let mut page = [0; PAGE_BYTES];
    let classified = db.read_classified_page(locator.page(), &mut page, &mut b)?;
    let range = crate::locate_usage_map(classified, locator, &mut b)?.range();
    drop(db);
    let mut original = fs::read(f.path())?;
    // EXP-0057 inline bitmap prefix: retain tag/base and remove membership bits.
    let start = locator.page().get() as usize * PAGE_BYTES + range.start + 5;
    let end = locator.page().get() as usize * PAGE_BYTES + range.end;
    original[start..end].fill(0);
    fs::write(f.path(), &original)?;
    delete_row(f.path(), f.request(), &mut budget())?;
    let mut db = DatabaseReader::open(f.path(), &mut b)?;
    assert!(crate::alloc::patch::available(
        &mut db,
        &definition,
        f.request().row.page(),
        &mut b
    )?);
    f.clean()
}

#[test]
fn sole_release_preserves_all_except_observed_fields_and_three_map_bits() -> TestResult {
    let f = Fixture::new(1)?;
    let records = usage_maps(&f.path(), f.root)?;
    let before = fs::read(f.path())?;
    delete_row(f.path(), f.request(), &mut budget())?;
    let base = f.row.page().get() as usize * PAGE_BYTES;
    let root = f.root.get() as usize * PAGE_BYTES;
    let mut expected = before.clone();
    expected[base] = 9;
    expected[base + 2..base + 4].copy_from_slice(&2036_u16.to_le_bytes());
    expected[base + 10..base + 12].copy_from_slice(&0xc800_u16.to_le_bytes());
    expected[root + 12..root + 16].copy_from_slice(&0_u32.to_le_bytes());
    for (role, (location, range)) in records.iter().enumerate() {
        let bit = f.row.page().get() as usize;
        let offset = location.page().get() as usize * PAGE_BYTES + range.start + 5 + bit / 8;
        if role == 0 {
            expected[offset] |= 1 << (bit % 8);
        } else {
            expected[offset] &= !(1 << (bit % 8));
        }
    }
    assert_eq!(fs::read(f.path())?, expected);
    assert_eq!(records[1].0.page(), records[2].0.page());
    let mut b = budget();
    let mut db = DatabaseReader::open(f.path(), &mut b)?;
    let def = db.table_definition(f.root, &mut b)?;
    assert!(db.rows(&def, &mut b)?.next_row()?.is_none());
    drop(db);
    let locator = crate::insert_row(
        f.path(),
        b"Rows",
        &[RowValue::Long(99), RowValue::Long(-9900)],
        &mut budget(),
    )?;
    assert_eq!(locator.page(), f.row.page());
    assert_eq!(locator.slot(), 0);
    let after = fs::read(f.path())?;
    assert_eq!(after.len(), before.len());
    assert_eq!(
        &after[base + 12..base + 2038],
        &expected[base + 12..base + 2038]
    );
    f.clean()
}

#[test]
fn release_map_mismatch_alias_and_indirect_references_refuse_atomically() -> TestResult {
    let f = Fixture::new(1)?;
    let before = fs::read(f.path())?;
    let records = usage_maps(&f.path(), f.root)?;
    for (role, (location, range)) in records.iter().enumerate() {
        let bit = f.row.page().get() as usize;
        let offset = location.page().get() as usize * PAGE_BYTES + range.start + 5 + bit / 8;
        let mut bad = before.clone();
        bad[offset] ^= 1 << (bit % 8);
        fs::write(f.path(), &bad)?;
        if role == 2 {
            delete_row(f.path(), f.request(), &mut budget())?;
        } else {
            assert!(delete_row(f.path(), f.request(), &mut budget()).is_err());
            assert_eq!(fs::read(f.path())?, bad);
        }
    }
    let global = records[0].0.page().get() as usize * PAGE_BYTES + records[0].1.start;
    let root = f.root.get() as usize * PAGE_BYTES;
    for mode in 0..4 {
        let mut bad = before.clone();
        match mode {
            0 => bad[global] = 1,
            1 => bad[global + 1..global + 5]
                .copy_from_slice(&(f.row.page().get() as u32 + 1).to_le_bytes()),
            2 => {
                let owned: [u8; 4] = bad[root + 35..root + 39].try_into()?;
                bad[root + 39..root + 43].copy_from_slice(&owned);
            }
            _ => bad[root + 35] = 250,
        }
        fs::write(f.path(), &bad)?;
        assert!(delete_row(f.path(), f.request(), &mut budget()).is_err());
        assert_eq!(fs::read(f.path())?, bad);
        f.clean()?;
    }
    Ok(())
}

#[test]
fn sole_row_on_later_page_releases_only_that_page_and_keeps_other_rows() -> TestResult {
    let mut f = Fixture::new(1)?;
    fs::remove_file(f.path())?;
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"Value", ColumnType::Long),
    ];
    let values: Vec<_> = (0..170)
        .map(|i| [RowValue::Long(i), RowValue::Long(-i)])
        .collect();
    let rows: Vec<_> = values.iter().map(|r| r.as_slice()).collect();
    create(
        f.path(),
        &[crate::TableRows {
            table: table(b"Rows", &columns, &[]),
            rows: &rows,
        }],
    )?;
    let mut b = budget();
    let mut db = DatabaseReader::open(f.path(), &mut b)?;
    let def = db.table_definition(f.root, &mut b)?;
    let mut reader = db.rows(&def, &mut b)?;
    let mut first = None;
    while let Some(row) = reader.next_row()? {
        if first.is_none() {
            first = Some(row.locator().page());
        }
        f.row = row.locator();
    }
    assert_eq!(f.row.slot(), 0);
    assert_ne!(Some(f.row.page()), first);
    drop(reader);
    drop(db);
    let before = fs::read(f.path())?;
    delete_row(f.path(), f.request(), &mut budget())?;
    let after = fs::read(f.path())?;
    let first = first.ok_or("missing first page")?.get() as usize * PAGE_BYTES;
    assert_eq!(
        &after[first..first + PAGE_BYTES],
        &before[first..first + PAGE_BYTES]
    );
    assert_eq!(after[f.row.page().get() as usize * PAGE_BYTES], 9);
    let root = f.root.get() as usize * PAGE_BYTES;
    assert_eq!(&after[root + 12..root + 16], &169_u32.to_le_bytes());
    let mut b = budget();
    let mut db = DatabaseReader::open(f.path(), &mut b)?;
    let def = db.table_definition(f.root, &mut b)?;
    let mut reader = db.rows(&def, &mut b)?;
    let mut count = 0;
    while reader.next_row()?.is_some() {
        count += 1;
    }
    assert_eq!(count, 169);
    f.clean()
}

#[test]
fn null_long_value_row_releases_only_its_data_page() -> TestResult {
    let f = Fixture::new(1)?;
    fs::remove_file(f.path())?;
    let columns = [ColumnSpec::new(b"Memo", ColumnType::Memo)];
    create(
        f.path(),
        &[crate::TableRows {
            table: table(b"Rows", &columns, &[]),
            rows: &[&[RowValue::Null]],
        }],
    )?;
    let before = fs::read(f.path())?;
    delete_row(f.path(), f.request(), &mut budget())?;
    assert_eq!(fs::metadata(f.path())?.len(), before.len() as u64);
    let mut b = budget();
    let mut db = DatabaseReader::open(f.path(), &mut b)?;
    let table = db.table_definition(f.root, &mut b)?;
    assert_eq!(table.row_count(), 0);
    assert!(db.rows(&table, &mut b)?.next_row()?.is_none());
    f.clean()
}
