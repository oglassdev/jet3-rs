use super::*;

fn expected_delete(before: &[u8], f: &Fixture, slot: u8) -> Result<Vec<u8>, Box<dyn StdError>> {
    let base = f.row.page().get() as usize * PAGE_BYTES;
    let source: &[u8; PAGE_BYTES] = before[base..base + PAGE_BYTES].try_into()?;
    let directory =
        crate::row_directory::RowDirectory::validate(f.row.page(), f.root, source, &mut budget())?;
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
fn first_middle_and_tail_unequal_rows_preserve_slots_and_vacated_slack() -> ResultTest {
    let f = Fixture::new(4)?;
    fs::remove_file(f.path())?;
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(
            b"Payload",
            ColumnType::Text {
                max_len: crate::column_definition_writer::nz(255),
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
    crate::create_database_with_rows(
        f.path(),
        &TableSpec {
            name: b"Rows",
            columns: &columns,
            indexes: &[],
        },
        &rows,
        &mut budget(),
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
fn repeated_deletions_shift_empty_tombstones_until_one_live_row_remains() -> ResultTest {
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
    let before = fs::read(f.path())?;
    assert!(matches!(
        delete_row(
            f.path(),
            RowDelete {
                row: RowLocator::new(f.row.page(), 2),
                ..f.request()
            },
            &mut budget()
        ),
        Err(UpdateError::Unsupported(
            "sole live row with other physical slots"
        ))
    ));
    assert_eq!(fs::read(f.path())?, before);
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
fn malformed_compaction_sources_and_nonempty_flags_preserve_original() -> ResultTest {
    let f = Fixture::new(4)?;
    let before = fs::read(f.path())?;
    let base = f.row.page().get() as usize * PAGE_BYTES;
    for (offset, word) in [
        (12, 0xc7ec_u16),
        (12, 0x87ec),
        (12, 0x07f6),
        (14, 0x07ff),
        (12, 0x0001),
        (12, 0xc800),
        (2, 0),
    ] {
        let mut bad = before.clone();
        bad[base + offset..base + offset + 2].copy_from_slice(&word.to_le_bytes());
        fs::write(f.path(), &bad)?;
        assert!(
            delete_row(
                f.path(),
                RowDelete {
                    row: RowLocator::new(f.row.page(), 0),
                    ..f.request()
                },
                &mut budget()
            )
            .is_err()
        );
        assert_eq!(fs::read(f.path())?, bad);
        f.clean()?;
    }
    Ok(())
}

#[test]
fn compaction_budget_and_full_private_verification_preserve_original() -> ResultTest {
    let f = Fixture::new(4)?;
    let request = RowDelete {
        row: RowLocator::new(f.row.page(), 0),
        ..f.request()
    };
    let before = fs::read(f.path())?;
    let mut exact = budget();
    delete_row(f.path(), request, &mut exact)?;
    for limits in [
        ResourceLimits::default()
            .with_max_encoded_bytes(ByteCount::new(exact.encoded_bytes().get() - 1)),
        ResourceLimits::default().with_max_total_work_units(exact.total_work_units() - 1),
    ] {
        fs::write(f.path(), &before)?;
        assert!(delete_row(f.path(), request, &mut ResourceBudget::new(limits)).is_err());
        assert_eq!(fs::read(f.path())?, before);
        f.clean()?;
    }
    for offset in [
        64,
        f.row.page().get() as usize * PAGE_BYTES + 100,
        f.row.page().get() as usize * PAGE_BYTES + 2020,
    ] {
        let error = delete_with_hook(
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
            matches!(error,Err(UpdateError::Publish(e)) if e.stage()==PublishStage::Validation)
        );
        assert_eq!(fs::read(f.path())?, before);
        f.clean()?;
    }
    Ok(())
}
