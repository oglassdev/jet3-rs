use super::*;

type Maps = [(crate::MapRowLocator, std::ops::Range<usize>); 3];
fn maps(f: &Fixture) -> Result<Maps, Box<dyn StdError>> {
    let mut b = budget();
    let mut db = DatabaseReader::open(f.path(), &mut b)?;
    let def = db.table_definition(f.root, &mut b)?;
    let locations = [
        crate::MapRowLocator::new(PageNumber::new(1), 0),
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

#[test]
fn sole_release_preserves_all_except_observed_fields_and_three_map_bits() -> ResultTest {
    let f = Fixture::new(1)?;
    let records = maps(&f)?;
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
    // Current insertion intentionally appends EOF; it does not reuse released pages.
    let locator = crate::insert_row(
        f.path(),
        b"Rows",
        &[RowValue::Long(99), RowValue::Long(-9900)],
        &mut budget(),
    )?;
    assert_eq!(
        locator.page().get(),
        before.len() as u64 / PAGE_BYTES as u64
    );
    assert_eq!(
        &fs::read(f.path())?[base..base + PAGE_BYTES],
        &expected[base..base + PAGE_BYTES]
    );
    f.clean()
}

#[test]
fn release_map_mismatch_alias_and_indirect_references_refuse_atomically() -> ResultTest {
    let f = Fixture::new(1)?;
    let before = fs::read(f.path())?;
    let records = maps(&f)?;
    for (location, range) in &records {
        let bit = f.row.page().get() as usize;
        let offset = location.page().get() as usize * PAGE_BYTES + range.start + 5 + bit / 8;
        let mut bad = before.clone();
        bad[offset] ^= 1 << (bit % 8);
        fs::write(f.path(), &bad)?;
        assert!(delete_row(f.path(), f.request(), &mut budget()).is_err());
        assert_eq!(fs::read(f.path())?, bad);
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
fn release_budget_and_private_verification_failures_preserve_original() -> ResultTest {
    let f = Fixture::new(1)?;
    let before = fs::read(f.path())?;
    let mut exact = budget();
    delete_row(f.path(), f.request(), &mut exact)?;
    for limits in [
        ResourceLimits::default()
            .with_max_encoded_bytes(ByteCount::new(exact.encoded_bytes().get() - 1)),
        ResourceLimits::default().with_max_total_work_units(exact.total_work_units() - 1),
    ] {
        fs::write(f.path(), &before)?;
        assert!(delete_row(f.path(), f.request(), &mut ResourceBudget::new(limits)).is_err());
        assert_eq!(fs::read(f.path())?, before);
        f.clean()?;
    }
    for offset in [64, f.row.page().get() as usize * PAGE_BYTES + 2040] {
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
                            bytes[offset] ^= 1;
                            fs::write(path, bytes)?;
                        }
                    }
                }
                Ok(())
            },
        );
        assert!(
            matches!(result,Err(UpdateError::Publish(e)) if e.stage()==PublishStage::Validation)
        );
        assert_eq!(fs::read(f.path())?, before);
        f.clean()?;
    }
    Ok(())
}

#[test]
fn sole_row_on_later_page_releases_only_that_page_and_keeps_other_rows() -> ResultTest {
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
fn null_long_value_schema_remains_outside_release_scope() -> ResultTest {
    let f = Fixture::new(1)?;
    fs::remove_file(f.path())?;
    let columns = [ColumnSpec::new(b"Memo", ColumnType::Memo)];
    crate::create_database_with_rows(
        f.path(),
        &TableSpec {
            name: b"Rows",
            columns: &columns,
            indexes: &[],
        },
        &[&[RowValue::Null]],
        &mut budget(),
    )?;
    let before = fs::read(f.path())?;
    assert!(matches!(
        delete_row(f.path(), f.request(), &mut budget()),
        Err(UpdateError::Unsupported(_))
    ));
    assert_eq!(fs::read(f.path())?, before);
    f.clean()
}
