use super::*;

fn keyed(
    kind: crate::IndexKind,
    descending: bool,
    count: usize,
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
        .map(|i| [RowValue::Long(i as i32), RowValue::Long(77)])
        .collect();
    let rows: Vec<_> = values.iter().map(|v| v.as_slice()).collect();
    create_database_with_rows(
        fixture.path(),
        &TableSpec {
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
            let expected_key = crate::long_index_key::encode(
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
                index.get() as usize * PAGE_BYTES + crate::index_tree_page::ENTRY_AREA_OFFSET;
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
    for count in [200, 201] {
        let full = keyed(crate::IndexKind::Primary, false, count)?;
        let row = full.locator(1)?;
        let original = fs::read(full.path())?;
        let result = update_field(full.path(), request(row, RowValue::Long(-1)), &mut budget());
        if count == 200 {
            result?;
        } else {
            assert!(matches!(result, Err(UpdateError::Unsupported(_))));
            assert_eq!(fs::read(full.path())?, original);
        }
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
    let area = index + crate::index_tree_page::ENTRY_AREA_OFFSET;
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
    let mut raw = [0; PAGE_BYTES];
    raw[12..16].copy_from_slice(&3_u32.to_le_bytes());
    let mut prefix = [0; 8];
    prefix[4..8].copy_from_slice(&2_u32.to_le_bytes());
    assert!(crate::index_key_page::check_counts(&raw, &prefix, 3).is_err());
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
