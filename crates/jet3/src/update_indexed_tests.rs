use super::*;

pub(super) fn indexed() -> Result<Fixture, Box<dyn StdError>> {
    let fixture = simple()?;
    fs::remove_file(fixture.path())?;
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"Group", ColumnType::Long),
        ColumnSpec::new(b"Value", ColumnType::Long),
    ];
    let composite = [
        crate::IndexColumnSpec::descending(1),
        crate::IndexColumnSpec::ascending(0),
    ];
    let indexes = [crate::IndexSpec {
        name: b"ByGroup",
        kind: crate::IndexKind::Ordinary,
        fields: &composite,
    }];
    create_database_with_rows(
        fixture.path(),
        &TableSpec {
            name: b"Items",
            columns: &columns,
            indexes: &indexes,
        },
        &[
            &[RowValue::Long(1), RowValue::Long(3), RowValue::Long(77)],
            &[RowValue::Long(2), RowValue::Long(3), RowValue::Long(88)],
        ],
        &mut budget(),
    )?;
    Ok(fixture)
}

#[test]
fn nonkey_update_preserves_every_index_and_unrelated_byte() -> TestResult {
    let fixture = indexed()?;
    let row = fixture.locator(1)?;
    let mut original = fs::read(fixture.path())?;
    original.extend_from_slice(&[0xab; PAGE_BYTES]);
    fs::write(fixture.path(), &original)?;
    let mut b = budget();
    let mut db = DatabaseReader::open(fixture.path(), &mut b)?;
    let definition = guarded_table(&mut db, b"Items", Some(ColumnOrdinal::new(2)), &mut b)?;
    assert_eq!(definition.physical_indexes().len(), 1);
    let relative = {
        let mut cursor = db.rows(&definition, &mut b)?;
        let mut found = None;
        while let Some(view) = cursor.next_row()? {
            if view.locator() == row {
                found = view.present_fixed_field_range(ColumnOrdinal::new(2));
            }
        }
        found.ok_or("missing fixed field")?
    };
    let mut page = [0; PAGE_BYTES];
    db.read_raw_page(row.page(), &mut page, &mut b)?;
    let directory = RowDirectory::validate(row.page(), definition.root(), &page, &mut b)?;
    let offset = row.page().get() as usize * PAGE_BYTES
        + directory.entry(&page, row.slot())?.range().start
        + relative.start;
    drop(db);
    update_field(
        fixture.path(),
        FieldUpdate {
            column: ColumnOrdinal::new(2),
            ..request(row, RowValue::Long(i32::MIN))
        },
        &mut budget(),
    )?;
    let mut expected = original;
    expected[offset..offset + 4].copy_from_slice(&i32::MIN.to_le_bytes());
    assert_eq!(fs::read(fixture.path())?, expected);
    // Insert/delete retain their unindexed-table guard.
    let mut db = DatabaseReader::open(fixture.path(), &mut b)?;
    assert!(matches!(
        writable_table(&mut db, b"Items", &mut b),
        Err(UpdateError::Unsupported(_))
    ));
    for column in [0, 1] {
        assert!(matches!(
            update_field(
                fixture.path(),
                FieldUpdate {
                    column: ColumnOrdinal::new(column),
                    ..request(row, RowValue::Long(9))
                },
                &mut budget()
            ),
            Err(UpdateError::Unsupported("indexed key column"))
        ));
        assert_eq!(fs::read(fixture.path())?, expected);
    }
    fixture.assert_only_original()
}

#[test]
fn inconsistent_or_out_of_range_index_mapping_refuses_publication() -> TestResult {
    let fixture = indexed()?;
    let row = fixture.locator(0)?;
    let original = fs::read(fixture.path())?;
    let mut b = budget();
    let mut db = DatabaseReader::open(fixture.path(), &mut b)?;
    let definition = guarded_table(&mut db, b"Items", Some(ColumnOrdinal::new(2)), &mut b)?;
    let record = definition.indexes()[0].raw_record();
    let root_offset = definition.root().get() as usize * PAGE_BYTES;
    let matches: Vec<_> = original[root_offset..root_offset + PAGE_BYTES]
        .windows(record.len())
        .enumerate()
        .filter(|(_, bytes)| *bytes == record.as_slice())
        .map(|(offset, _)| offset)
        .collect();
    assert_eq!(matches.len(), 1);
    let offset = root_offset + matches[0];
    drop(db);
    // EXP-0059 logical selectors: mismatched selectors and out-of-range references.
    for (first, second) in [(1_u32, 0_u32), (1, 1)] {
        let mut damaged = original.clone();
        damaged[offset..offset + 4].copy_from_slice(&first.to_le_bytes());
        damaged[offset + 4..offset + 8].copy_from_slice(&second.to_le_bytes());
        fs::write(fixture.path(), &damaged)?;
        assert!(matches!(
            update_field(
                fixture.path(),
                FieldUpdate {
                    column: ColumnOrdinal::new(2),
                    ..request(row, RowValue::Long(4))
                },
                &mut budget()
            ),
            Err(UpdateError::Definition(_))
        ));
        assert_eq!(fs::read(fixture.path())?, damaged);
        fixture.assert_only_original()?;
    }
    Ok(())
}
