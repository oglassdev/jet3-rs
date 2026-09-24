use super::insert::*;
use crate::{
    ByteCount, ColumnSpec, ColumnType, DatabaseReader, PAGE_BYTES, PageNumber, PublishStage,
    ResourceBudget, ResourceLimits, RowLocator, RowValue, TableSpec, UpdateError,
};
use std::error::Error as StdError;
use std::fs;
use std::path::PathBuf;
pub(super) type TestResult = Result<(), Box<dyn StdError>>;
pub(super) use crate::testkit::budget;
pub(super) struct Fixture {
    pub(super) directory: crate::testkit::TempDir,
    pub(super) root: PageNumber,
    pages: Vec<PageNumber>,
}
impl Fixture {
    pub(super) fn new(
        columns: &[ColumnSpec<'_>],
        rows: &[&[RowValue<'_>]],
    ) -> Result<Self, Box<dyn StdError>> {
        let directory = crate::testkit::TempDir::new("insert")?;
        let path = directory.join("source.mdb");
        crate::create_database(
            &path,
            &crate::DatabaseSpec {
                tables: &[crate::TableRows {
                    table: TableSpec {
                        validation: crate::TableValidation::NONE,
                        name: b"Rows",
                        columns,
                        indexes: &[],
                    },
                    rows,
                }],
                ..crate::DatabaseSpec::default()
            },
            &mut budget(),
        )?;
        let mut b = budget();
        let mut db = DatabaseReader::open(&path, &mut b)?;
        let def = crate::write::update::writable_table(&mut db, b"Rows", &mut b)?;
        let root = def.root();
        let mut pages = Vec::new();
        {
            let mut rows = db.rows(&def, &mut b)?;
            while let Some(row) = rows.next_row()? {
                if !pages.contains(&row.locator().page()) {
                    pages.push(row.locator().page());
                }
            }
        }
        drop(db);
        let mut bytes = fs::read(&path)?;
        for page in &pages {
            let base = page.get() as usize * PAGE_BYTES;
            let source: &[u8; PAGE_BYTES] = bytes[base..base + PAGE_BYTES].try_into()?;
            let directory =
                crate::row::directory::RowDirectory::validate(*page, root, source, &mut b)?;
            let start = directory
                .entry(source, (directory.row_count() - 1) as u8)?
                .range()
                .start;
            let free = start - 10 - 2 * usize::from(directory.row_count());
            // EXP-0162 closed-page free metadata; retain arbitrary unused data.
            bytes[base + 2..base + 4].copy_from_slice(&(free as u16).to_le_bytes());
        }
        bytes.extend_from_slice(&[0xb6; PAGE_BYTES]);
        fs::write(&path, bytes)?;
        Ok(Self {
            directory,
            root,
            pages,
        })
    }
    pub(super) fn longs(count: usize) -> Result<Self, Box<dyn StdError>> {
        let values: Vec<_> = (0..count)
            .map(|n| [RowValue::Long(n as i32), RowValue::Long(-(n as i32))])
            .collect();
        let rows: Vec<_> = values.iter().map(|r| r.as_slice()).collect();
        Self::new(
            &[
                ColumnSpec::new(b"Id", ColumnType::Long),
                ColumnSpec::new(b"Value", ColumnType::Long),
            ],
            &rows,
        )
    }
    pub(super) fn path(&self) -> PathBuf {
        self.directory.join("source.mdb")
    }
    pub(super) fn clean(&self) -> TestResult {
        assert_eq!(fs::read_dir(&self.directory)?.count(), 1);
        Ok(())
    }
}

#[test]
fn append_and_append_after_tombstone_preserve_every_unplanned_byte() -> TestResult {
    for deleted in [false, true] {
        let f = Fixture::longs(4)?;
        let page = f.pages[0];
        if deleted {
            crate::delete_row(
                f.path(),
                crate::RowDelete {
                    table: b"Rows",
                    row: RowLocator::new(page, 3),
                },
                &mut budget(),
            )?;
        }
        let before = fs::read(f.path())?;
        let locator = insert_row(
            f.path(),
            b"Rows",
            &[RowValue::Long(99), RowValue::Long(-9900)],
            &mut budget(),
        )?;
        assert_eq!(locator, RowLocator::new(page, 4));
        let base = page.get() as usize * PAGE_BYTES;
        let root = f.root.get() as usize * PAGE_BYTES;
        let start = if deleted { 2008 } else { 1998 };
        let free = if deleted { 1988 } else { 1978 };
        let mut expected = before.clone();
        expected[base + 2..base + 4].copy_from_slice(&(free as u16).to_le_bytes());
        expected[base + 8..base + 10].copy_from_slice(&5_u16.to_le_bytes());
        expected[base + 18..base + 20].copy_from_slice(&(start as u16).to_le_bytes());
        expected[base + start..base + start + 10]
            .copy_from_slice(&[2, 99, 0, 0, 0, 0x54, 0xd9, 0xff, 0xff, 3]);
        expected[root + 12..root + 16]
            .copy_from_slice(&(if deleted { 4_u32 } else { 5 }).to_le_bytes());
        assert_eq!(fs::read(f.path())?, expected);
        let mut b = budget();
        let mut db = DatabaseReader::open(f.path(), &mut b)?;
        let def = db.table_definition(f.root, &mut b)?;
        let mut cursor = db.rows(&def, &mut b)?;
        let mut count = 0;
        while let Some(row) = cursor.next_row()? {
            count += 1;
            if row.locator() == locator {
                assert_eq!(
                    row.field(crate::ColumnOrdinal::new(1))
                        .and_then(|f| f.raw_bytes()),
                    Some((-9900_i32).to_le_bytes().as_slice())
                );
            }
        }
        assert_eq!(count, if deleted { 4 } else { 5 });
        f.clean()?;
    }
    Ok(())
}

#[test]
fn scalar_layout_and_variable_offsets_use_existing_schema() -> TestResult {
    let columns = [
        ColumnSpec::new(b"Flag", ColumnType::Boolean),
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(
            b"Text",
            ColumnType::Text {
                max_len: crate::definition::column_writer::nz(255),
            },
        ),
        ColumnSpec::new(
            b"Bytes",
            ColumnType::Binary {
                max_len: crate::definition::column_writer::nz(255),
            },
        ),
    ];
    let f = Fixture::new(
        &columns,
        &[&[
            RowValue::Boolean(false),
            RowValue::Long(1),
            RowValue::Text(b"old"),
            RowValue::Binary(&[1, 2]),
        ]],
    )?;
    let text = [b'x'; 100];
    let binary = [0xa5; 100];
    let locator = insert_row(
        f.path(),
        b"Rows",
        &[
            RowValue::Boolean(true),
            RowValue::Null,
            RowValue::Text(&text),
            RowValue::Binary(&binary),
        ],
        &mut budget(),
    )?;
    let mut b = budget();
    let mut db = DatabaseReader::open(f.path(), &mut b)?;
    let def = db.table_definition(f.root, &mut b)?;
    let mut rows = db.rows(&def, &mut b)?;
    let mut found = false;
    while let Some(row) = rows.next_row()? {
        if row.locator() == locator {
            found = true;
            assert_eq!(
                row.field(crate::ColumnOrdinal::new(2))
                    .and_then(|f| f.raw_bytes()),
                Some(text.as_slice())
            );
            assert_eq!(
                row.field(crate::ColumnOrdinal::new(3))
                    .and_then(|f| f.raw_bytes()),
                Some(binary.as_slice())
            );
            assert_eq!(
                row.field(crate::ColumnOrdinal::new(1))
                    .and_then(|f| f.raw_bytes()),
                None
            );
        }
    }
    assert!(found);
    f.clean()
}

#[test]
fn later_page_selection_and_capacity_boundary() -> TestResult {
    let f = Fixture::longs(200)?;
    assert_eq!(f.pages.len(), 2);
    let locator = insert_row(
        f.path(),
        b"Rows",
        &[RowValue::Long(99), RowValue::Long(99)],
        &mut budget(),
    )?;
    assert_eq!(locator.page(), f.pages[1]);
    let owner = PageNumber::new(20);
    let page = PageNumber::new(23);
    for free in [11_u16, 12] {
        let mut bytes = [0; PAGE_BYTES];
        bytes[0] = 1;
        bytes[4..8].copy_from_slice(&20_u32.to_le_bytes());
        bytes[8..10].copy_from_slice(&1_u16.to_le_bytes());
        bytes[2..4].copy_from_slice(&free.to_le_bytes());
        bytes[10..12].copy_from_slice(&(12 + free).to_le_bytes());
        let value =
            crate::row::data_page::DataPageEditor::open(page, owner, &bytes, &mut budget())?
                .append(&[0; 10], None, &mut budget())?;
        assert_eq!(value.is_some(), free == 12);
    }
    f.clean()
}

#[test]
fn corrupt_metadata_values_and_resources_preserve_original() -> TestResult {
    let f = Fixture::longs(4)?;
    let original = fs::read(f.path())?;
    let base = f.pages[0].get() as usize * PAGE_BYTES;
    let root = f.root.get() as usize * PAGE_BYTES;
    for (offset, value) in [(base + 2, 0), (root + 12, 0), (base + 11, 0x87)] {
        let mut bad = original.clone();
        bad[offset] = value;
        fs::write(f.path(), &bad)?;
        assert!(
            insert_row(
                f.path(),
                b"Rows",
                &[RowValue::Long(1), RowValue::Long(2)],
                &mut budget()
            )
            .is_err()
        );
        assert_eq!(fs::read(f.path())?, bad);
    }
    fs::write(f.path(), &original)?;
    for values in [
        &[RowValue::Long(1)][..],
        &[RowValue::Byte(1), RowValue::Long(2)][..],
    ] {
        assert!(insert_row(f.path(), b"Rows", values, &mut budget()).is_err());
        assert_eq!(fs::read(f.path())?, original);
    }
    for limits in [
        ResourceLimits::default().with_max_allocation_bytes(ByteCount::new(0)),
        ResourceLimits::default().with_max_total_work_units(1),
        ResourceLimits::default().with_max_encoded_bytes(ByteCount::new(9)),
    ] {
        assert!(
            insert_row(
                f.path(),
                b"Rows",
                &[RowValue::Long(1), RowValue::Long(2)],
                &mut ResourceBudget::new(limits)
            )
            .is_err()
        );
        assert_eq!(fs::read(f.path())?, original);
        f.clean()?;
    }
    let error = insert_with_hook(
        &f.path(),
        b"Rows",
        &[RowValue::Long(1), RowValue::Long(2)],
        &mut budget(),
        |stage| -> Result<(), std::io::Error> {
            if stage == PublishStage::Validation {
                for entry in fs::read_dir(&f.directory)? {
                    let path = entry?.path();
                    if path != f.path() {
                        let mut bytes = fs::read(&path)?;
                        bytes[64] ^= 1;
                        fs::write(path, bytes)?;
                    }
                }
            }
            Ok(())
        },
    );
    assert!(matches!(error,Err(UpdateError::Publish(e)) if e.stage()==PublishStage::Validation));
    assert_eq!(fs::read(f.path())?, original);
    f.clean()
}

#[test]
fn unowned_available_map_membership_is_refused() -> TestResult {
    let f = Fixture::longs(4)?;
    let mut b = budget();
    let mut db = DatabaseReader::open(f.path(), &mut b)?;
    let def = db.table_definition(f.root, &mut b)?;
    let locator = def.maps().available();
    let mut bytes = [0; PAGE_BYTES];
    let page = db.read_classified_page(locator.page(), &mut bytes, &mut b)?;
    let range = crate::locate_usage_map(page, locator, &mut b)?.range();
    drop(db);
    let original = fs::read(f.path())?;
    {
        let mut bad = original.clone();
        let base = locator.page().get() as usize * PAGE_BYTES;
        // EXP-0057 inline bitmap: an unowned in-file page.
        bad[base + range.start + 5..base + range.end].fill(0);
        bad[base + range.start + 5] = 1;
        fs::write(f.path(), &bad)?;
        assert!(
            insert_row(
                f.path(),
                b"Rows",
                &[RowValue::Long(1), RowValue::Long(2)],
                &mut budget()
            )
            .is_err()
        );
        assert_eq!(fs::read(f.path())?, bad);
    }
    f.clean()
}

#[test]
fn physical_slot_limit_and_table_count_overflow_are_structured() -> TestResult {
    let mut bytes = [0; PAGE_BYTES];
    bytes[0] = 1;
    bytes[4..8].copy_from_slice(&20_u32.to_le_bytes());
    for count in [254_u16, 255, 256] {
        bytes[8..10].copy_from_slice(&count.to_le_bytes());
        for slot in 0..count as usize {
            bytes[10 + 2 * slot..12 + 2 * slot]
                .copy_from_slice(&((2048 - 2 * (slot + 1)) as u16).to_le_bytes());
        }
        bytes[2..4].copy_from_slice(&(2038 - 4 * count).to_le_bytes());
        assert_eq!(
            crate::row::data_page::DataPageEditor::open(
                PageNumber::new(23),
                PageNumber::new(20),
                &bytes,
                &mut budget()
            )?
            .append(&[1, 1], None, &mut budget())?
            .is_some(),
            count == 254
        );
        assert_eq!(crate::row::data_page::has_capacity(&bytes, 2), count == 254);
    }
    bytes[12..16].copy_from_slice(&u32::MAX.to_le_bytes());
    assert!(matches!(
        crate::row::data_page::count_table_row(&bytes, u32::MAX, true, &mut budget()),
        Err(UpdateError::Mismatch("table row count overflow"))
    ));
    Ok(())
}

#[test]
fn saturated_page_keeps_its_rows_and_moves_insertion_to_another_page() -> TestResult {
    let values: Vec<_> = (0..255).map(|id| [RowValue::Byte(id)]).collect();
    let rows: Vec<_> = values.iter().map(|row| row.as_slice()).collect();
    let fixture = Fixture::new(&[ColumnSpec::new(b"Id", ColumnType::Byte)], &rows)?;
    assert_eq!(fixture.pages.len(), 1);
    let page = fixture.pages[0];
    // EXP-0305: a live page remains saturated even after a row is deleted.
    crate::delete_row(
        fixture.path(),
        crate::RowDelete {
            table: b"Rows",
            row: RowLocator::new(page, 1),
        },
        &mut budget(),
    )?;
    let before = fs::read(fixture.path())?;
    let inserted = insert_row(
        fixture.path(),
        b"Rows",
        &[RowValue::Byte(255)],
        &mut budget(),
    )?;
    assert_ne!(inserted.page(), page);
    assert_eq!(inserted.slot(), 0);
    let after = fs::read(fixture.path())?;
    let base = page.get() as usize * PAGE_BYTES;
    assert_eq!(
        before[base..base + PAGE_BYTES],
        after[base..base + PAGE_BYTES]
    );
    let mut resources = budget();
    let mut database = DatabaseReader::open(fixture.path(), &mut resources)?;
    let definition = database.table_definition(fixture.root, &mut resources)?;
    let mut rows = database.rows(&definition, &mut resources)?;
    let mut ids = Vec::new();
    while let Some(row) = rows.next_row()? {
        ids.push(
            row.field(crate::ColumnOrdinal::new(0))
                .and_then(|field| field.raw_bytes())
                .ok_or("Id absent")?[0],
        );
    }
    ids.sort_unstable();
    assert_eq!(ids, (0_u8..=255).filter(|id| *id != 1).collect::<Vec<_>>());
    fixture.clean()
}
