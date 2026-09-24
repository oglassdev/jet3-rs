use super::update_tests::*;
use crate::{
    ColumnOrdinal, ColumnSpec, ColumnType, DatabaseReader, PAGE_BYTES, PublishStage,
    ResourceBudget, ResourceLimits, RowValue, TableSpec, create_database_with_rows,
    row::directory::RowDirectory, write::update::*,
};
use std::error::Error as StdError;
use std::fs;

fn keyed(
    kind: crate::IndexKind,
    descending: bool,
    count: usize,
) -> Result<Fixture, Box<dyn StdError>> {
    keyed_from(kind, descending, count, 0)
}

fn keyed_from(
    kind: crate::IndexKind,
    descending: bool,
    count: usize,
    first: i32,
) -> Result<Fixture, Box<dyn StdError>> {
    let fixture = simple()?;
    fs::remove_file(fixture.path())?;
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"Value", ColumnType::Long),
    ];
    let keys = [if descending {
        crate::IndexColumnSpec::descending(0)
    } else {
        crate::IndexColumnSpec::ascending(0)
    }];
    let indexes = [crate::IndexSpec {
        name: b"Key",
        kind,
        fields: &keys,
    }];
    let values: Vec<_> = (0..count)
        .map(|i| [RowValue::Long(first + i as i32), RowValue::Long(77)])
        .collect();
    let rows: Vec<_> = values.iter().map(|v| v.as_slice()).collect();
    create_database_with_rows(
        fixture.path(),
        &TableSpec {
            validation: crate::TableValidation::NONE,
            name: b"Items",
            columns: &columns,
            indexes: &indexes,
        },
        &rows,
        &mut budget(),
    )?;
    Ok(fixture)
}

fn definition(fixture: &Fixture) -> Result<crate::TableDefinition, Box<dyn StdError>> {
    let mut b = budget();
    let mut db = DatabaseReader::open(fixture.path(), &mut b)?;
    Ok(guarded_table(&mut db, b"Items", true, &mut b)?)
}

#[test]
fn unique_long_reorders_leaf_preserving_counts_bitmap_and_slack() -> TestResult {
    for (kind, descending) in [
        (crate::IndexKind::Primary, false),
        (crate::IndexKind::Unique, true),
    ] {
        let fixture = keyed(kind, descending, 3)?;
        let row = fixture.locator(1)?;
        let table = definition(&fixture)?;
        let index = table.physical_indexes()[0].root();
        let mut original = fs::read(fixture.path())?;
        original[index.get() as usize * PAGE_BYTES + 1000] = 0xa7;
        original.extend_from_slice(&[0xb3; PAGE_BYTES]);
        fs::write(fixture.path(), &original)?;
        for value in [i32::MIN, i32::MAX, -1] {
            fs::write(fixture.path(), &original)?;
            update_field(
                fixture.path(),
                request(row, RowValue::Long(value)),
                &mut budget(),
            )?;
            let after = fs::read(fixture.path())?;
            let mut b = budget();
            let mut db = DatabaseReader::open(fixture.path(), &mut b)?;
            let tree = db.index_tree(&table, 0, &mut b)?;
            let values = if descending {
                [i32::MAX, 2, 0]
            } else {
                [i32::MIN, 0, 2]
            };
            if value == values[0] {
                assert_eq!(tree.entries()[0].row(), row);
            }
            assert_eq!(tree.entries().iter().filter(|e| e.row() == row).count(), 1);
            let expected_key = crate::index::key::long::encode(
                value,
                table.physical_indexes()[0].fields()[0].direction(),
            );
            assert_eq!(
                tree.entries()
                    .iter()
                    .find(|e| e.row() == row)
                    .ok_or("key absent")?
                    .key()
                    .raw_bytes(),
                expected_key
            );
            let mut page = [0; PAGE_BYTES];
            db.read_raw_page(row.page(), &mut page, &mut b)?;
            let dir = RowDirectory::validate(row.page(), table.root(), &page, &mut b)?;
            let field = row.page().get() as usize * PAGE_BYTES
                + dir.entry(&page, row.slot())?.range().start
                + 1;
            let mut expected = original.clone();
            expected[field..field + 4].copy_from_slice(&value.to_le_bytes());
            let start =
                index.get() as usize * PAGE_BYTES + crate::index::tree::page::ENTRY_AREA_OFFSET;
            expected[start..start + 27].copy_from_slice(&after[start..start + 27]);
            assert_eq!(after, expected);
            let mut cursor = db.rows(&table, &mut b)?;
            while let Some(view) = cursor.next_row()? {
                if view.locator() == row {
                    assert_eq!(
                        view.field(ColumnOrdinal::new(0))
                            .and_then(|f| f.raw_bytes()),
                        Some(value.to_le_bytes().as_slice())
                    );
                }
            }
        }
        fixture.assert_only_original()?;
    }
    Ok(())
}

#[test]
fn duplicate_noop_multilevel_and_budget_bounds_preserve_source() -> TestResult {
    let fixture = keyed(crate::IndexKind::Primary, false, 3)?;
    let row = fixture.locator(1)?;
    let original = fs::read(fixture.path())?;
    assert!(matches!(
        update_field(
            fixture.path(),
            request(row, RowValue::Long(2)),
            &mut budget()
        ),
        Err(UpdateError::Unsupported("duplicate unique key"))
    ));
    assert_eq!(fs::read(fixture.path())?, original);
    update_field(
        fixture.path(),
        request(row, RowValue::Long(1)),
        &mut budget(),
    )?;
    assert_eq!(fs::read(fixture.path())?, original);
    let mut limited = ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(1));
    assert!(
        update_field(
            fixture.path(),
            request(row, RowValue::Long(5)),
            &mut limited
        )
        .is_err()
    );
    assert_eq!(fs::read(fixture.path())?, original);
    let mut encoded = ResourceBudget::new(
        ResourceLimits::default().with_max_encoded_bytes(crate::ByteCount::new(100)),
    );
    assert!(
        update_field(
            fixture.path(),
            request(row, RowValue::Long(-1)),
            &mut encoded
        )
        .is_err()
    );
    assert_eq!(fs::read(fixture.path())?, original);
    for count in [200, 201] {
        let full = keyed(crate::IndexKind::Primary, false, count)?;
        let row = full.locator(1)?;
        update_field(full.path(), request(row, RowValue::Long(-1)), &mut budget())?;
        let mut b = budget();
        let mut db = DatabaseReader::open(full.path(), &mut b)?;
        let table = definition(&full)?;
        crate::index::mutation::load(&mut db, &table, &mut b)?;
        assert_eq!(db.index_tree(&table, 0, &mut b)?.entries()[0].row(), row);
    }
    Ok(())
}

#[test]
fn stale_keys_counts_compression_and_locator_aliases_refuse() -> TestResult {
    let fixture = keyed(crate::IndexKind::Primary, false, 3)?;
    let row = fixture.locator(1)?;
    let table = definition(&fixture)?;
    let original = fs::read(fixture.path())?;
    let index = table.physical_indexes()[0].root().get() as usize * PAGE_BYTES;
    let area = index + crate::index::tree::page::ENTRY_AREA_OFFSET;
    for (offset, value) in [
        (area + 4, 0x01),
        (area + 22, 3),
        (area + 8, 1),
        (table.root().get() as usize * PAGE_BYTES + 12, 4),
        (index + 20, 1),
    ] {
        let mut damaged = original.clone();
        damaged[offset] = value;
        fs::write(fixture.path(), &damaged)?;
        assert!(
            update_field(
                fixture.path(),
                request(row, RowValue::Long(5)),
                &mut budget()
            )
            .is_err()
        );
        assert_eq!(fs::read(fixture.path())?, damaged);
    }
    Ok(())
}

#[test]
fn two_page_patch_failure_preserves_original() -> TestResult {
    let fixture = keyed(crate::IndexKind::Primary, false, 3)?;
    let row = fixture.locator(1)?;
    let original = fs::read(fixture.path())?;
    let result = update_with_hook(
        &fixture.path(),
        request(row, RowValue::Long(-5)),
        &mut budget(),
        |stage| {
            if stage == PublishStage::Validation {
                Err(std::io::Error::other("injected"))
            } else {
                Ok(())
            }
        },
    );
    assert!(matches!(result, Err(UpdateError::Publish(_))));
    assert_eq!(fs::read(fixture.path())?, original);
    fixture.assert_only_original()
}

#[test]
fn branch_fences_require_schema_width_even_when_their_bounds_are_valid() -> TestResult {
    use crate::index::tree::page::{ENTRY_AREA_OFFSET, boundaries};
    for longer in [false, true] {
        let f = keyed_from(crate::IndexKind::Primary, false, 450, -199)?;
        let row = f.locator(1)?;
        let table = definition(&f)?;
        let root = table.physical_indexes()[0].root();
        let before = fs::read(f.path())?;
        let base = root.get() as usize * PAGE_BYTES;
        let mut page: [u8; PAGE_BYTES] = before[base..base + PAGE_BYTES].try_into()?;
        let ends: Vec<_> = boundaries(&page).collect();
        assert_eq!(ends, [13, 26]);
        let old_fence = page[ENTRY_AREA_OFFSET..ENTRY_AREA_OFFSET + 9].to_vec();
        let mut payload = page[ENTRY_AREA_OFFSET..ENTRY_AREA_OFFSET + 26].to_vec();
        let delta = if longer {
            payload.insert(5, 1);
            1_isize
        } else {
            assert_eq!(payload.remove(4), 0);
            -1
        };
        let new_first_end = ends[0].checked_add_signed(delta).ok_or("boundary")?;
        let fence = &payload[..new_first_end - 4];
        assert!(old_fence.as_slice() < fence);
        // The next child starts at Long(1); both corrupt fences stay in its gap.
        assert!(
            fence < crate::index::key::long::encode(1, crate::IndexDirection::Ascending).as_slice()
        );
        page[22..ENTRY_AREA_OFFSET].fill(0);
        for end in ends {
            let end = end.checked_add_signed(delta).ok_or("boundary")?;
            page[22 + end / 8] |= 1 << (end % 8);
        }
        page[2..4].copy_from_slice(
            &((PAGE_BYTES - ENTRY_AREA_OFFSET - payload.len()) as u16).to_le_bytes(),
        );
        page[ENTRY_AREA_OFFSET..ENTRY_AREA_OFFSET + payload.len()].copy_from_slice(&payload);
        let mut bad = before;
        bad[base..base + PAGE_BYTES].copy_from_slice(&page);
        fs::write(f.path(), &bad)?;
        assert!(matches!(
            update_field(f.path(), request(row, RowValue::Long(1000)), &mut budget()),
            Err(UpdateError::Mismatch("numeric index key shape"))
        ));
        assert_eq!(fs::read(f.path())?, bad);
        f.assert_only_original()?;
    }
    Ok(())
}
