use super::row_update::*;
use crate::{
    ByteCount, ColumnOrdinal, ColumnSpec, ColumnStorageClass, ColumnType, DatabaseReader,
    PAGE_BYTES, PublishStage, ResourceBudget, ResourceLimits, RowColumnLayout, RowLocator,
    RowValue, TableSpec, UpdateError, row::data_page::DataPageEditor,
};
use std::error::Error as StdError;
use std::{fs, num::NonZeroU8, path::PathBuf};
type SnapshotRow = (RowLocator, Vec<Option<Vec<u8>>>);
pub(super) type TestResult = Result<(), Box<dyn StdError>>;
pub(super) use crate::testkit::budget;
pub(super) struct Fixture {
    pub(super) dir: crate::testkit::TempDir,
    pub(super) root: crate::PageNumber,
    pub(super) locators: Vec<RowLocator>,
}
impl Fixture {
    pub(super) fn path(&self) -> PathBuf {
        self.dir.join("rows.mdb")
    }
    pub(super) fn new(count: usize) -> Result<Self, Box<dyn StdError>> {
        Self::with_index(count, false)
    }
    pub(super) fn with_index(count: usize, indexed: bool) -> Result<Self, Box<dyn StdError>> {
        let dir = crate::testkit::TempDir::new("row-update")?;
        let columns = [
            ColumnSpec::new(b"Id", ColumnType::Long),
            ColumnSpec::new(
                b"Text",
                ColumnType::Text {
                    max_len: NonZeroU8::MAX,
                },
            ),
            ColumnSpec::new(
                b"Bytes",
                ColumnType::Binary {
                    max_len: NonZeroU8::MAX,
                },
            ),
            ColumnSpec::new(b"Flag", ColumnType::Boolean),
        ];
        let values: Vec<_> = (0..count)
            .map(|n| {
                [
                    RowValue::Long(n as i32),
                    RowValue::Text(b"original text"),
                    RowValue::Binary(b"bytes"),
                    RowValue::Boolean(true),
                ]
            })
            .collect();
        let rows: Vec<_> = values.iter().map(|r| r.as_slice()).collect();
        let path = dir.join("rows.mdb");
        let keys = [crate::IndexColumnSpec::descending(0)];
        let indexes = [crate::IndexSpec {
            name: b"ById",
            kind: crate::IndexKind::Unique,
            fields: &keys,
        }];
        crate::create_database_with_rows(
            &path,
            &TableSpec {
                validation: crate::TableValidation::NONE,
                name: b"Rows",
                columns: &columns,
                indexes: if indexed { &indexes } else { &[] },
            },
            &rows,
            &mut budget(),
        )?;
        let mut b = budget();
        let mut db = DatabaseReader::open(&path, &mut b)?;
        let root = {
            let mut c = db.catalog(&mut b)?;
            let mut root = None;
            while let Some(r) = c.next_record()? {
                if r.name().raw_bytes() == b"Rows" {
                    root = r.table_definition();
                }
            }
            root.ok_or("root")?
        };
        let def = db.table_definition(root, &mut b)?;
        let mut locators = Vec::new();
        {
            let mut c = db.rows(&def, &mut b)?;
            while let Some(r) = c.next_row()? {
                locators.push(r.locator());
            }
        }
        Ok(Self {
            dir,
            root,
            locators,
        })
    }
    pub(super) fn request<'a>(&self, slot: usize, values: &'a [RowValue<'a>]) -> RowUpdate<'a> {
        RowUpdate {
            table: b"Rows",
            row: self.locators[slot],
            values,
        }
    }
    pub(super) fn rows(&self) -> Result<Vec<SnapshotRow>, Box<dyn StdError>> {
        let mut b = budget();
        let mut db = DatabaseReader::open(self.path(), &mut b)?;
        let def = db.table_definition(self.root, &mut b)?;
        let mut c = db.rows(&def, &mut b)?;
        let mut out = Vec::new();
        while let Some(r) = c.next_row()? {
            out.push((
                r.locator(),
                (0..3)
                    .map(|i| {
                        r.field(ColumnOrdinal::new(i))
                            .and_then(|v| v.raw_bytes())
                            .map(|v| v.to_vec())
                    })
                    .collect(),
            ));
        }
        Ok(out)
    }
}
pub(super) fn word(bytes: &[u8], offset: usize) -> usize {
    u16::from_le_bytes([bytes[offset], bytes[offset + 1]]) as usize
}
// Repack the complete page in physical-slot order, preserving the old slack image.
fn expected(before: &[u8], locator: RowLocator, new: &[u8]) -> Vec<u8> {
    let base = locator.page().get() as usize * PAGE_BYTES;
    let count = word(before, base + 8);
    let mut expected = before.to_vec();
    let mut end = PAGE_BYTES;
    for slot in 0..count {
        let raw = word(before, base + 10 + 2 * slot);
        let oldstart = raw & 0x1fff;
        let oldend = if slot == 0 {
            PAGE_BYTES
        } else {
            word(before, base + 8 + 2 * slot) & 0x1fff
        };
        let row = if slot == usize::from(locator.slot()) {
            new
        } else {
            &before[base + oldstart..base + oldend]
        };
        end -= row.len();
        expected[base + end..base + end + row.len()].copy_from_slice(row);
        expected[base + 10 + 2 * slot..base + 12 + 2 * slot]
            .copy_from_slice(&((raw & 0xe000 | end) as u16).to_le_bytes());
    }
    expected[base + 2..base + 4].copy_from_slice(&((end - 10 - 2 * count) as u16).to_le_bytes());
    expected
}
fn encoded(f: &Fixture, values: &[RowValue<'_>]) -> Result<Vec<u8>, Box<dyn StdError>> {
    let mut b = budget();
    let mut db = DatabaseReader::open(f.path(), &mut b)?;
    let def = db.table_definition(f.root, &mut b)?;
    let layout: Vec<RowColumnLayout> = def.columns().iter().map(Into::into).collect();
    let mut out = [0; PAGE_BYTES];
    let n = crate::encode_row(&layout, values, &mut out, &mut b)?.get() as usize;
    Ok(out[..n].to_vec())
}
#[test]
fn growth_shrink_nulls_and_equal_width_preserve_slots_and_all_other_bytes() -> TestResult {
    for slot in 0..3 {
        let f = Fixture::new(3)?;
        let baseline = f.rows()?;
        for values in [
            [
                RowValue::Long(-7),
                RowValue::Text(&[b'x'; 200]),
                RowValue::Binary(&[0xa5; 30]),
                RowValue::Boolean(false),
            ],
            [
                RowValue::Null,
                RowValue::Null,
                RowValue::Binary(b""),
                RowValue::Boolean(true),
            ],
            [
                RowValue::Long(99),
                RowValue::Text(b"restored"),
                RowValue::Null,
                RowValue::Boolean(false),
            ],
            [
                RowValue::Long(98),
                RowValue::Text(b"samewide"),
                RowValue::Null,
                RowValue::Boolean(true),
            ],
        ] {
            let before = fs::read(f.path())?;
            let raw = encoded(&f, &values)?;
            let wanted = expected(&before, f.locators[slot], &raw);
            update_row(f.path(), f.request(slot, &values), &mut budget())?;
            assert_eq!(fs::read(f.path())?, wanted);
            let actual = f.rows()?;
            assert_eq!(actual.len(), 3);
            for (i, row) in actual.iter().enumerate() {
                assert_eq!(row.0, f.locators[i]);
                if i != slot {
                    assert_eq!(row, &baseline[i]);
                }
            }
            assert_eq!(
                actual[slot].1[0],
                match values[0] {
                    RowValue::Long(v) => Some(v.to_le_bytes().to_vec()),
                    _ => None,
                }
            );
        }
    }
    Ok(())
}
#[test]
fn known_empty_tombstones_and_single_live_row_keep_physical_slots() -> TestResult {
    let f = Fixture::new(4)?;
    for slot in [0, 2, 3] {
        crate::delete_row(
            f.path(),
            crate::RowDelete {
                table: b"Rows",
                row: f.locators[slot],
            },
            &mut budget(),
        )?;
    }
    let values = [
        RowValue::Long(42),
        RowValue::Text(&[b'a'; 180]),
        RowValue::Binary(b"new"),
        RowValue::Boolean(false),
    ];
    let before = fs::read(f.path())?;
    let wanted = expected(&before, f.locators[1], &encoded(&f, &values)?);
    update_row(f.path(), f.request(1, &values), &mut budget())?;
    assert_eq!(fs::read(f.path())?, wanted);
    assert_eq!(f.rows()?.len(), 1);
    let sole = Fixture::new(1)?;
    update_row(sole.path(), sole.request(0, &values), &mut budget())?;
    assert_eq!(sole.rows()?.len(), 1);
    Ok(())
}
#[test]
fn schema_locators_corruption_and_capacity_refuse_without_publication() -> TestResult {
    let f = Fixture::new(3)?;
    let original = fs::read(f.path())?;
    let values = [
        RowValue::Long(2),
        RowValue::Text(b"short"),
        RowValue::Binary(b"b"),
        RowValue::Boolean(false),
    ];
    for request in [
        RowUpdate {
            table: b"Missing",
            ..f.request(0, &values)
        },
        RowUpdate {
            row: RowLocator::new(f.root, 0),
            ..f.request(0, &values)
        },
        RowUpdate {
            values: &values[..2],
            ..f.request(0, &values)
        },
        RowUpdate {
            values: &[
                RowValue::Text(b"wrong"),
                RowValue::Null,
                RowValue::Null,
                RowValue::Boolean(true),
            ],
            ..f.request(0, &values)
        },
    ] {
        assert!(update_row(f.path(), request, &mut budget()).is_err());
        assert_eq!(fs::read(f.path())?, original);
    }
    let base = f.locators[0].page().get() as usize * PAGE_BYTES;
    let root = f.root.get() as usize * PAGE_BYTES;
    for (offset, value) in [
        (base + 2, 0),
        (root + 12, 0),
        (base + 11, 0x87),
        (base + 10, 0),
        (base + 4, 0),
    ] {
        let mut bad = original.clone();
        bad[offset] = value;
        fs::write(f.path(), &bad)?;
        assert!(update_row(f.path(), f.request(0, &values), &mut budget()).is_err());
        assert_eq!(fs::read(f.path())?, bad);
    }
    let source: [u8; PAGE_BYTES] = original[base..base + PAGE_BYTES].try_into()?;
    let old = word(&source, 10);
    let max = word(&source, 2) + (PAGE_BYTES - old);
    let editor = DataPageEditor::open(f.locators[0].page(), f.root, &source, &mut budget())?;
    assert!(matches!(
        editor.replace(0, &vec![0; max], None, &mut budget()),
        Ok(Some(_))
    ));
    assert!(matches!(
        editor.replace(0, &vec![0; max + 1], None, &mut budget()),
        Ok(None)
    ));
    Ok(())
}
#[test]
fn shared_budgets_and_private_verification_preserve_original() -> TestResult {
    let f = Fixture::new(3)?;
    let original = fs::read(f.path())?;
    let values = [
        RowValue::Null,
        RowValue::Text(b"new"),
        RowValue::Null,
        RowValue::Boolean(false),
    ];
    for limits in [
        ResourceLimits::default().with_max_allocation_bytes(ByteCount::new(0)),
        ResourceLimits::default().with_max_total_work_units(1),
        ResourceLimits::default().with_max_encoded_bytes(ByteCount::new(7)),
    ] {
        assert!(
            update_row(
                f.path(),
                f.request(1, &values),
                &mut ResourceBudget::new(limits)
            )
            .is_err()
        );
        assert_eq!(fs::read(f.path())?, original);
    }
    let result = update_with_hook(
        &f.path(),
        f.request(1, &values),
        &mut budget(),
        |stage| -> Result<(), std::io::Error> {
            if stage == PublishStage::Validation {
                for e in fs::read_dir(&f.dir)? {
                    let p = e?.path();
                    if p != f.path() {
                        let mut bytes = fs::read(&p)?;
                        bytes[64] ^= 1;
                        fs::write(p, bytes)?;
                    }
                }
            }
            Ok(())
        },
    );
    assert!(matches!(result,Err(UpdateError::Publish(e)) if e.stage()==PublishStage::Validation));
    assert_eq!(fs::read(f.path())?, original);
    let mut exact = budget();
    update_row(f.path(), f.request(1, &values), &mut exact)?;
    fs::write(f.path(), &original)?;
    let limits = crate::ReadLimits::new(
        ByteCount::new(1_000_000),
        ByteCount::new(1_000_000),
        ByteCount::new(exact.read_budget().total_read().get() - 1),
    );
    assert!(
        update_row(
            f.path(),
            f.request(1, &values),
            &mut ResourceBudget::new(ResourceLimits::new(limits))
        )
        .is_err()
    );
    assert_eq!(fs::read(f.path())?, original);
    assert_eq!(fs::read_dir(&f.dir)?.count(), 1);
    Ok(())
}

#[test]
fn available_map_and_variable_width_states_are_preserved() -> TestResult {
    let f = Fixture::new(3)?;
    let original_rows = f.rows()?;
    let wide = [
        RowValue::Long(0),
        RowValue::Text(&[b'x'; 200]),
        RowValue::Binary(&[0xaa; 130]),
        RowValue::Boolean(false),
    ];
    update_row(f.path(), f.request(0, &wide), &mut budget())?;
    let rows = f.rows()?;
    assert_eq!(rows[0].0, original_rows[0].0);
    assert_eq!(rows[0].1[1], Some(vec![b'x'; 200]));
    assert_eq!(rows[0].1[2], Some(vec![0xaa; 130]));
    assert_eq!(rows[1..], original_rows[1..]);
    let original = fs::read(f.path())?;
    let mut b = budget();
    let mut db = DatabaseReader::open(f.path(), &mut b)?;
    let definition = db.table_definition(f.root, &mut b)?;
    let locator = definition.maps().available();
    let mut page = [0; PAGE_BYTES];
    let classified = db.read_classified_page(locator.page(), &mut page, &mut b)?;
    let range = crate::locate_usage_map(classified, locator, &mut b)?.range();
    drop(db);
    let mut missing = original.clone();
    let base = locator.page().get() as usize * PAGE_BYTES;
    missing[base + range.start + 5..base + range.end].fill(0);
    fs::write(f.path(), &missing)?;
    let values = [
        RowValue::Long(0),
        RowValue::Null,
        RowValue::Null,
        RowValue::Boolean(false),
    ];
    update_row(f.path(), f.request(0, &values), &mut budget())?;
    let mut db = DatabaseReader::open(f.path(), &mut b)?;
    assert!(crate::alloc::patch::available(
        &mut db,
        &definition,
        f.locators[0].page(),
        &mut b
    )?);
    drop(db);
    Ok(())
}

#[test]
fn full_slot_directory_allows_replacement_but_hidden_payload_is_refused() -> TestResult {
    let f = Fixture::new(1)?;
    let original = fs::read(f.path())?;
    let base = f.locators[0].page().get() as usize * PAGE_BYTES;
    let mut page: [u8; PAGE_BYTES] = original[base..base + PAGE_BYTES].try_into()?;
    let start = word(&page, 10);
    let raw = page[start..].to_vec();
    page[8..10].copy_from_slice(&256_u16.to_le_bytes());
    for slot in 1..256 {
        page[10 + 2 * slot..12 + 2 * slot]
            .copy_from_slice(&((start as u16) | 0xc000).to_le_bytes());
    }
    page[2..4].copy_from_slice(&((start - 522) as u16).to_le_bytes());
    assert!(matches!(
        DataPageEditor::open(f.locators[0].page(), f.root, &page, &mut budget())?.replace(
            0,
            &raw,
            None,
            &mut budget()
        ),
        Ok(Some(_))
    ));
    let f = Fixture::new(3)?;
    let original = fs::read(f.path())?;
    let base = f.locators[0].page().get() as usize * PAGE_BYTES;
    let mut page: [u8; PAGE_BYTES] = original[base..base + PAGE_BYTES].try_into()?;
    page[13] |= 0xc0;
    assert!(matches!(
        DataPageEditor::open(f.locators[0].page(), f.root, &page, &mut budget())?.replace(
            0,
            &raw,
            None,
            &mut budget()
        ),
        Err(UpdateError::Unsupported(
            "replacement page contains an unsupported row slot"
        ))
    ));
    Ok(())
}

#[test]
fn boolean_zero_and_legacy_offsets_reach_public_row_replacement() -> TestResult {
    let f = Fixture::new(3)?;
    fs::remove_file(f.path())?;
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"Value", ColumnType::Long),
        ColumnSpec::new(
            b"Payload",
            ColumnType::Text {
                max_len: NonZeroU8::MAX,
            },
        ),
        ColumnSpec::new(
            b"Bytes",
            ColumnType::Binary {
                max_len: NonZeroU8::MAX,
            },
        ),
        ColumnSpec::new(b"Flag", ColumnType::Boolean),
    ];
    let original_values = [
        RowValue::Long(1),
        RowValue::Long(101),
        RowValue::Text(b"one"),
        RowValue::Binary(&[0, 17]),
        RowValue::Boolean(true),
    ];
    crate::create_database_with_rows(
        f.path(),
        &TableSpec {
            validation: crate::TableValidation::NONE,
            name: b"Rows",
            columns: &columns,
            indexes: &[],
        },
        &[&original_values, &original_values],
        &mut budget(),
    )?;
    let mut b = budget();
    let mut db = DatabaseReader::open(f.path(), &mut b)?;
    let definition = db.table_definition(f.root, &mut b)?;
    let raw = *definition.columns()[4].raw_record();
    assert_eq!(
        definition.columns()[4].storage(),
        ColumnStorageClass::Fixed { offset: 0 }
    );
    drop(db);
    let original = fs::read(f.path())?;
    let base = f.root.get() as usize * PAGE_BYTES;
    let positions: Vec<_> = original[base..base + PAGE_BYTES]
        .windows(raw.len())
        .enumerate()
        .filter_map(|(i, v)| (v == raw).then_some(base + i))
        .collect();
    assert_eq!(positions.len(), 1);
    let values = [
        RowValue::Long(1),
        RowValue::Null,
        RowValue::Text(&[b'g'; 60]),
        RowValue::Binary(&[1, 35, 69, 103, 137, 171, 205, 239]),
        RowValue::Boolean(false),
    ];
    for offset in [0_u16, 8] {
        let mut before = original.clone();
        before[positions[0] + 14..positions[0] + 16].copy_from_slice(&offset.to_le_bytes());
        fs::write(f.path(), &before)?;
        let wanted = expected(&before, f.locators[0], &encoded(&f, &values)?);
        update_row(f.path(), f.request(0, &values), &mut budget())?;
        assert_eq!(fs::read(f.path())?, wanted);
        assert_eq!(f.rows()?.len(), 2);
    }
    Ok(())
}

#[test]
fn indexed_variable_row_replacement_preserves_locators_and_other_rows() -> TestResult {
    let f = Fixture::with_index(202, true)?;
    let prior = f.rows()?;
    let target = prior.len() - 1;
    for (key, text, binary) in [
        (-7, &b"a longer replacement text"[..], &b"longer bytes"[..]),
        (i32::MAX, &b"x"[..], &b"y"[..]),
    ] {
        let values = [
            RowValue::Long(key),
            RowValue::Text(text),
            RowValue::Binary(binary),
            RowValue::Boolean(false),
        ];
        update_row(f.path(), f.request(target, &values), &mut budget())?;
        let after = f.rows()?;
        assert_eq!(after.len(), prior.len());
        for (ordinal, row) in after.iter().enumerate() {
            if ordinal == target {
                assert_eq!(row.0, prior[ordinal].0);
                assert_eq!(
                    row.1,
                    vec![
                        Some(key.to_le_bytes().to_vec()),
                        Some(text.to_vec()),
                        Some(binary.to_vec())
                    ]
                );
            } else {
                assert_eq!(row, &prior[ordinal]);
            }
        }
        let mut b = budget();
        let mut db = DatabaseReader::open(f.path(), &mut b)?;
        let table = db.table_definition(f.root, &mut b)?;
        crate::index::mutation::load(&mut db, &table, &mut b)?;
    }
    let before = fs::read(f.path())?;
    let duplicate = [
        RowValue::Long(0),
        RowValue::Text(b"duplicate"),
        RowValue::Null,
        RowValue::Boolean(true),
    ];
    assert!(matches!(
        update_row(f.path(), f.request(target, &duplicate), &mut budget()),
        Err(UpdateError::Unsupported("duplicate unique key"))
    ));
    assert_eq!(fs::read(f.path())?, before);
    Ok(())
}
