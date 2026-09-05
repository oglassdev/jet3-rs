use super::*;
use crate::{
    ByteCount, ColumnSpec, ColumnType, ResourceLimits, TableSpec, create_database_with_rows,
};
use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

type TestResult = Result<(), Box<dyn StdError>>;
static NEXT: AtomicU64 = AtomicU64::new(0);
struct Fixture(PathBuf);
impl Fixture {
    fn new(
        columns: &[ColumnSpec<'_>],
        values: &[&[RowValue<'_>]],
    ) -> Result<Self, Box<dyn StdError>> {
        let directory = std::env::temp_dir().join(format!(
            "jet3-field-update-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&directory)?;
        let fixture = Self(directory);
        create_database_with_rows(
            fixture.path(),
            &TableSpec {
                name: b"Items",
                columns,
                indexes: &[],
            },
            values,
            &mut budget(),
        )?;
        Ok(fixture)
    }
    fn path(&self) -> PathBuf {
        self.0.join("source.mdb")
    }
    fn locator(&self, slot: u8) -> Result<RowLocator, Box<dyn StdError>> {
        let mut budget = budget();
        let mut database = DatabaseReader::open(self.path(), &mut budget)?;
        let root = {
            let mut catalog = database.catalog(&mut budget)?;
            let mut found = None;
            while let Some(record) = catalog.next_record()? {
                if record.name().raw_bytes() == b"Items" {
                    found = record.table_definition();
                }
            }
            found.ok_or("table absent")?
        };
        let definition = database.table_definition(root, &mut budget)?;
        let mut rows = database.rows(&definition, &mut budget)?;
        while let Some(row) = rows.next_row()? {
            if row.locator().slot() == slot {
                return Ok(row.locator());
            }
        }
        Err("row absent".into())
    }
    fn assert_only_original(&self) -> TestResult {
        assert_eq!(fs::read_dir(&self.0)?.count(), 1);
        Ok(())
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}
fn request(row: RowLocator, value: RowValue<'_>) -> FieldUpdate<'_> {
    FieldUpdate {
        table: b"Items",
        row,
        column: ColumnOrdinal::new(0),
        value,
    }
}
fn simple() -> Result<Fixture, Box<dyn StdError>> {
    Fixture::new(
        &[
            ColumnSpec::new(b"Id", ColumnType::Long),
            ColumnSpec::new(b"Other", ColumnType::Long),
        ],
        &[
            &[RowValue::Long(1), RowValue::Long(77)],
            &[RowValue::Null, RowValue::Long(88)],
        ],
    )
}

#[test]
fn four_byte_update_preserves_slack_opaque_pages_and_other_fields() -> TestResult {
    let fixture = simple()?;
    let row = fixture.locator(0)?;
    let mut original = fs::read(fixture.path())?;
    original[row.page().get() as usize * PAGE_BYTES + 64] = 0xa7;
    original.extend_from_slice(&[0xb6; PAGE_BYTES]);
    fs::write(fixture.path(), &original)?;
    for value in [i32::MIN, i32::MAX, -123, 0] {
        update_field(
            fixture.path(),
            request(row, RowValue::Long(value)),
            &mut budget(),
        )?;
        let after = fs::read(fixture.path())?;
        let mut expected = original.clone();
        let mut db = DatabaseReader::open(fixture.path(), &mut budget())?;
        let mut b = budget();
        let definition = db.table_definition(crate::PageNumber::new(20), &mut b)?;
        let offset = {
            let mut rows = db.rows(&definition, &mut b)?;
            let view = rows.next_row()?.ok_or("row absent")?;
            assert_eq!(
                view.field(ColumnOrdinal::new(0))
                    .and_then(|field| field.raw_bytes()),
                Some(value.to_le_bytes().as_slice())
            );
            view.present_fixed_field_range(ColumnOrdinal::new(0))
                .ok_or("field absent")?
                .start
        };
        let mut page = [0; PAGE_BYTES];
        db.read_raw_page(row.page(), &mut page, &mut b)?;
        let directory = RowDirectory::validate(row.page(), definition.root(), &page, &mut b)?;
        let start = row.page().get() as usize * PAGE_BYTES
            + directory.entry(&page, row.slot())?.range().start
            + offset;
        expected[start..start + 4].copy_from_slice(&value.to_le_bytes());
        assert_eq!(after, expected);
    }
    fixture.assert_only_original()
}

#[test]
fn bad_requests_and_resource_failures_preserve_original() -> TestResult {
    let fixture = simple()?;
    let row = fixture.locator(0)?;
    let original = fs::read(fixture.path())?;
    let invalid = [
        FieldUpdate {
            table: b"Missing",
            ..request(row, RowValue::Long(2))
        },
        FieldUpdate {
            column: ColumnOrdinal::new(99),
            ..request(row, RowValue::Long(2))
        },
        request(RowLocator::new(row.page(), 200), RowValue::Long(2)),
        request(
            RowLocator::new(crate::PageNumber::new(1), 0),
            RowValue::Long(2),
        ),
        request(row, RowValue::Null),
        request(row, RowValue::Byte(2)),
        request(fixture.locator(1)?, RowValue::Long(2)),
    ];
    for invalid in invalid {
        assert!(update_field(fixture.path(), invalid, &mut budget()).is_err());
        assert_eq!(fs::read(fixture.path())?, original);
        fixture.assert_only_original()?;
    }
    for limits in [
        ResourceLimits::default().with_max_allocation_bytes(ByteCount::new(0)),
        ResourceLimits::default().with_max_encoded_bytes(ByteCount::new(3)),
        ResourceLimits::default().with_max_total_work_units(1),
    ] {
        assert!(
            update_field(
                fixture.path(),
                request(row, RowValue::Long(2)),
                &mut ResourceBudget::new(limits)
            )
            .is_err()
        );
        assert_eq!(fs::read(fixture.path())?, original);
        fixture.assert_only_original()?;
    }
    Ok(())
}

#[test]
fn auto_columns_and_mismatched_values_are_refused() -> TestResult {
    for (kind, initial) in [
        (ColumnType::AutoIncrement, RowValue::AutoIncrement),
        (ColumnType::Byte, RowValue::Byte(1)),
    ] {
        let fixture = Fixture::new(&[ColumnSpec::new(b"Id", kind)], &[&[initial]])?;
        let original = fs::read(fixture.path())?;
        assert!(matches!(
            update_field(
                fixture.path(),
                request(fixture.locator(0)?, RowValue::Long(2)),
                &mut budget()
            ),
            Err(UpdateError::Unsupported(_) | UpdateError::Encoding(_))
        ));
        assert_eq!(fs::read(fixture.path())?, original);
    }
    Ok(())
}

#[test]
fn faults_at_each_prepublication_stage_preserve_original() -> TestResult {
    let fixture = simple()?;
    let row = fixture.locator(0)?;
    let original = fs::read(fixture.path())?;
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
        let result = update_with_hook(
            &fixture.path(),
            request(row, RowValue::Long(8)),
            &mut budget(),
            |current| {
                if current == stage {
                    Err(std::io::Error::other("injected"))
                } else {
                    Ok(())
                }
            },
        );
        assert!(matches!(result, Err(UpdateError::Publish(error)) if error.stage() == stage));
        assert_eq!(fs::read(fixture.path())?, original);
        fixture.assert_only_original()?;
    }
    Ok(())
}

#[test]
fn private_byte_corruption_is_rejected_by_streaming_verification() -> TestResult {
    let fixture = simple()?;
    let row = fixture.locator(0)?;
    let original = fs::read(fixture.path())?;
    for damage_target in [false, true] {
        let result = update_with_hook(
            &fixture.path(),
            request(row, RowValue::Long(8)),
            &mut budget(),
            |stage| -> Result<(), std::io::Error> {
                if stage == PublishStage::Validation {
                    for entry in fs::read_dir(&fixture.0)? {
                        let path = entry?.path();
                        if path != fixture.path() {
                            let mut bytes = fs::read(&path)?;
                            if damage_target {
                                bytes = original.clone();
                            } else {
                                bytes[64] ^= 1;
                            }
                            fs::write(path, bytes)?;
                        }
                    }
                }
                Ok(())
            },
        );
        assert!(
            matches!(result, Err(UpdateError::Publish(error)) if error.stage() == PublishStage::Validation)
        );
        assert_eq!(fs::read(fixture.path())?, original);
        fixture.assert_only_original()?;
    }
    Ok(())
}

#[test]
fn indexed_tables_are_refused_before_publication() -> TestResult {
    let fixture = simple()?;
    fs::remove_file(fixture.path())?;
    let columns = [ColumnSpec::new(b"Id", ColumnType::Long)];
    let keys = [crate::IndexColumnSpec::ascending(0)];
    let indexes = [crate::IndexSpec {
        name: b"Pk",
        kind: crate::IndexKind::Primary,
        fields: &keys,
    }];
    create_database_with_rows(
        fixture.path(),
        &TableSpec {
            name: b"Items",
            columns: &columns,
            indexes: &indexes,
        },
        &[&[RowValue::Long(1)]],
        &mut budget(),
    )?;
    let original = fs::read(fixture.path())?;
    assert!(matches!(
        update_field(
            fixture.path(),
            request(fixture.locator(0)?, RowValue::Long(3)),
            &mut budget()
        ),
        Err(UpdateError::Unsupported(_))
    ));
    assert_eq!(fs::read(fixture.path())?, original);
    fixture.assert_only_original()
}

#[test]
fn malformed_hidden_and_overflow_slots_are_refused() -> TestResult {
    let fixture = simple()?;
    let row = fixture.locator(0)?;
    let original = fs::read(fixture.path())?;
    let slot_offset = row.page().get() as usize * PAGE_BYTES + 10;
    let raw = u16::from_le_bytes([original[slot_offset], original[slot_offset + 1]]);
    for replacement in [0_u16, raw | 0x2000, raw | 0x8000, raw | 0x4000] {
        let mut damaged = original.clone();
        damaged[slot_offset..slot_offset + 2].copy_from_slice(&replacement.to_le_bytes());
        fs::write(fixture.path(), &damaged)?;
        assert!(
            update_field(
                fixture.path(),
                request(row, RowValue::Long(3)),
                &mut budget()
            )
            .is_err()
        );
        assert_eq!(fs::read(fixture.path())?, damaged);
        fixture.assert_only_original()?;
    }
    Ok(())
}

#[test]
fn copy_and_verification_share_the_planning_read_budget() -> TestResult {
    let fixture = simple()?;
    let row = fixture.locator(0)?;
    let original = fs::read(fixture.path())?;
    // Planning only reads pages. The copier's larger read is refused before I/O.
    let limits = crate::ReadLimits::new(
        ByteCount::new(1_000_000),
        ByteCount::new(PAGE_BYTES as u64),
        ByteCount::new(10_000_000),
    );
    let result = update_field(
        fixture.path(),
        request(row, RowValue::Long(3)),
        &mut ResourceBudget::new(ResourceLimits::new(limits)),
    );
    assert!(
        matches!(result, Err(UpdateError::Publish(error)) if error.stage() == PublishStage::Copy)
    );
    assert_eq!(fs::read(fixture.path())?, original);
    let mut exact = budget();
    update_field(fixture.path(), request(row, RowValue::Long(3)), &mut exact)?;
    let total = exact.read_budget().total_read().get();
    fs::write(fixture.path(), &original)?;
    for maximum in [total - 1, total] {
        let reads = crate::ReadLimits::new(
            ByteCount::new(1_000_000),
            ByteCount::new(1_000_000),
            ByteCount::new(maximum),
        );
        let result = update_field(
            fixture.path(),
            request(row, RowValue::Long(3)),
            &mut ResourceBudget::new(ResourceLimits::new(reads)),
        );
        if maximum < total {
            assert!(
                matches!(result, Err(UpdateError::Publish(error)) if error.stage() == PublishStage::Validation)
            );
            assert_eq!(fs::read(fixture.path())?, original);
        } else {
            result?;
        }
    }
    fixture.assert_only_original()
}

#[test]
fn a_valid_locator_from_another_table_is_rejected() -> TestResult {
    let fixture = simple()?;
    fs::remove_file(fixture.path())?;
    let columns = [ColumnSpec::new(b"Id", ColumnType::Long)];
    let tables = [
        crate::TableRows {
            table: TableSpec {
                name: b"Items",
                columns: &columns,
                indexes: &[],
            },
            rows: &[&[RowValue::Long(1)]],
        },
        crate::TableRows {
            table: TableSpec {
                name: b"Other",
                columns: &columns,
                indexes: &[],
            },
            rows: &[&[RowValue::Long(2)]],
        },
    ];
    crate::create_database_with_table_rows(fixture.path(), &tables, &mut budget())?;
    let original = fs::read(fixture.path())?;
    let wrong = FieldUpdate {
        table: b"Other",
        ..request(fixture.locator(0)?, RowValue::Long(3))
    };
    assert!(matches!(
        update_field(fixture.path(), wrong, &mut budget()),
        Err(UpdateError::Directory(
            crate::RowDirectoryError::UnexpectedOwner { .. }
        ))
    ));
    assert_eq!(fs::read(fixture.path())?, original);
    fixture.assert_only_original()
}

#[test]
fn relationship_catalog_rows_are_checked_without_user_indexes() -> TestResult {
    let fixture = simple()?;
    let row = fixture.locator(0)?;
    let original = fs::read(fixture.path())?;
    let mut b = budget();
    let mut database = DatabaseReader::open(fixture.path(), &mut b)?;
    let root = {
        let mut catalog = database.catalog(&mut b)?;
        let mut found = None;
        while let Some(record) = catalog.next_record()? {
            if record.name().raw_bytes() == b"MSysRelationships" {
                found = record.table_definition();
            }
        }
        found.ok_or("relationship catalog absent")?
    };
    let definition = database.table_definition(root, &mut b)?;
    let columns: Vec<_> = definition
        .columns()
        .iter()
        .map(crate::RowColumnLayout::from)
        .collect();
    let map = definition.maps().owned();
    let mut map_page = [0; PAGE_BYTES];
    database.read_raw_page(map.page(), &mut map_page, &mut b)?;
    let directory = crate::data_page_directory::DataPageDirectory::validate(&map_page, &mut b)
        .map_err(|_| "invalid fixture map directory")?;
    let map_range = directory
        .entry(&map_page, u16::from(map.row()))
        .ok_or("map row absent")?
        .range();
    assert_eq!(&map_page[map_range.start..map_range.start + 5], &[0; 5]);
    let page = crate::PageNumber::new((original.len() / PAGE_BYTES) as u64);
    for (object, referenced, refused) in [
        (Some(b"iTeMs".as_slice()), Some(b"Other".as_slice()), true),
        (Some(b"Other".as_slice()), Some(b"Items".as_slice()), true),
        (None, Some(b"Other".as_slice()), true),
        (Some(b"\x80".as_slice()), Some(b"Other".as_slice()), true),
        (
            Some(b"Other".as_slice()),
            Some(b"Elsewhere".as_slice()),
            false,
        ),
    ] {
        let mut raw = [0; 256];
        let length = crate::encode_row(
            &columns,
            &[
                RowValue::Text(b"MetadataOnly"),
                RowValue::Long(0),
                RowValue::Long(1),
                RowValue::Long(0),
                object.map_or(RowValue::Null, RowValue::Text),
                RowValue::Text(b"Id"),
                referenced.map_or(RowValue::Null, RowValue::Text),
                RowValue::Text(b"Id"),
            ],
            &mut raw,
            &mut b,
        )?;
        let mut data = crate::DataPageBuilder::new(root, &mut b)?;
        data.append_row(&raw[..length.get() as usize], &mut b)?;
        let mut input = original.clone();
        input.extend_from_slice(data.finish().as_bytes());
        let bit = page.get() as usize;
        input[map.page().get() as usize * PAGE_BYTES + map_range.start + 5 + bit / 8] |=
            1 << (bit % 8);
        let count = root.get() as usize * PAGE_BYTES + 12;
        input[count..count + 4].copy_from_slice(&1_u32.to_le_bytes());
        fs::write(fixture.path(), &input)?;
        let result = update_field(
            fixture.path(),
            request(row, RowValue::Long(3)),
            &mut budget(),
        );
        if refused {
            assert!(matches!(result, Err(UpdateError::Unsupported(_))));
            assert_eq!(fs::read(fixture.path())?, input);
        } else {
            result?;
        }
        fixture.assert_only_original()?;
    }
    Ok(())
}

#[path = "update_fixed_tests.rs"]
mod fixed;
