use super::delete::*;
pub(super) use crate::testkit::TestResult;
use crate::testkit::create;
use crate::testkit::table;
use crate::{
    ByteCount, ColumnSpec, ColumnType, DatabaseReader, PAGE_BYTES, PageNumber, PublishStage,
    ResourceBudget, ResourceLimits, RowLocator, RowValue, WriteError,
};
use std::error::Error as StdError;
use std::fs;
use std::path::PathBuf;
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
        let root = {
            let mut catalog = db.catalog(&mut b)?;
            let mut root = None;
            while let Some(record) = catalog.next_record()? {
                if record.name().raw_bytes() == b"Rows" {
                    root = record.table_definition();
                }
            }
            root.ok_or("missing table")?
        };
        let def = db.table_definition(root, &mut b)?;
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
    let mut b = budget();
    let mut db = DatabaseReader::open(f.path(), &mut b)?;
    let def = db.table_definition(f.root, &mut b)?;
    let mut cursor = db.rows(&def, &mut b)?;
    let mut ids = Vec::new();
    while let Some(row) = cursor.next_row()? {
        ids.push(
            row.field(crate::ColumnOrdinal::new(0))
                .and_then(|v| v.raw_bytes())
                .ok_or("missing value")?
                .to_vec(),
        );
    }
    assert_eq!(
        ids,
        vec![
            0_i32.to_le_bytes().to_vec(),
            1_i32.to_le_bytes().to_vec(),
            2_i32.to_le_bytes().to_vec()
        ]
    );
    assert!(delete_row(f.path(), f.request(), &mut budget()).is_err());
    assert_eq!(fs::read(f.path())?, expected);
    f.clean()
}

#[test]
fn unsupported_locators_pages_and_metadata_preserve_original() -> TestResult {
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
        fs::write(f.path(), &bad)?;
        assert!(delete_row(f.path(), f.request(), &mut budget()).is_err());
        assert_eq!(fs::read(f.path())?, bad);
    }
    f.clean()
}

#[test]
fn resource_and_publication_failures_preserve_original() -> TestResult {
    let f = Fixture::new(4)?;
    let original = fs::read(f.path())?;
    for limits in [
        ResourceLimits::default().with_max_allocation_bytes(ByteCount::new(0)),
        ResourceLimits::default().with_max_total_work_units(1),
        ResourceLimits::default().with_max_encoded_bytes(ByteCount::new(7)),
    ] {
        assert!(delete_row(f.path(), f.request(), &mut ResourceBudget::new(limits)).is_err());
        assert_eq!(fs::read(f.path())?, original);
        f.clean()?;
    }
    for stage in [
        PublishStage::PrivateCopyCreation,
        PublishStage::Copy,
        PublishStage::Mutation,
        PublishStage::Metadata,
        PublishStage::Validation,
        PublishStage::FileSync,
        PublishStage::PrePublish,
        PublishStage::Publish,
    ] {
        let result = delete_with_hook(&f.path(), f.request(), &mut budget(), |current| {
            if current == stage {
                Err(std::io::Error::other("injected"))
            } else {
                Ok(())
            }
        });
        assert!(matches!(result, Err(WriteError::Publish(e)) if e.stage() == stage));
        assert_eq!(fs::read(f.path())?, original);
        f.clean()?;
    }
    Ok(())
}

#[test]
fn private_corruption_and_shared_read_budget_are_detected() -> TestResult {
    let f = Fixture::new(4)?;
    let original = fs::read(f.path())?;
    let result = delete_with_hook(
        &f.path(),
        f.request(),
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
    assert!(matches!(result, Err(WriteError::Publish(e)) if e.stage() == PublishStage::Validation));
    assert_eq!(fs::read(f.path())?, original);
    let mut exact = budget();
    delete_row(f.path(), f.request(), &mut exact)?;
    let total = exact.read_budget().total_read();
    fs::write(f.path(), &original)?;
    let limits = crate::ReadLimits::new(
        ByteCount::new(1_000_000),
        ByteCount::new(1_000_000),
        ByteCount::new(total.get() - 1),
    );
    assert!(
        delete_row(
            f.path(),
            f.request(),
            &mut ResourceBudget::new(ResourceLimits::new(limits))
        )
        .is_err()
    );
    assert_eq!(fs::read(f.path())?, original);
    f.clean()
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
