use super::*;

type MapRecords = [(MapRowLocator, std::ops::Range<usize>); 3];
fn maps(f: &Fixture) -> Result<MapRecords, Box<dyn StdError>> {
    let mut b = budget();
    let mut db = DatabaseReader::open(f.path(), &mut b)?;
    let def = db.table_definition(f.root, &mut b)?;
    let locators = [
        MapRowLocator::new(PageNumber::new(1), 0),
        def.maps().owned(),
        def.maps().available(),
    ];
    let mut result = std::array::from_fn(|i| (locators[i], 0..0));
    for (locator, range) in &mut result {
        let mut bytes = [0; PAGE_BYTES];
        let page = db.read_classified_page(locator.page(), &mut bytes, &mut b)?;
        *range = crate::locate_usage_map(page, *locator, &mut b)?.range();
    }
    Ok(result)
}

#[test]
fn empty_and_full_pages_append_exactly_one_page_then_reuse_it() -> TestResult {
    for full in [false, true] {
        let text = [b'z'; 180];
        let columns = [
            ColumnSpec::new(b"Id", ColumnType::Long),
            ColumnSpec::new(b"Value", ColumnType::Long),
            ColumnSpec::new(
                b"Text",
                ColumnType::Text {
                    max_len: crate::column_definition_writer::nz(255),
                },
            ),
        ];
        let values: Vec<_> = (0..if full { 10 } else { 0 })
            .map(|i| [RowValue::Long(i), RowValue::Long(-i), RowValue::Text(&text)])
            .collect();
        let rows: Vec<_> = values.iter().map(|r| r.as_slice()).collect();
        let f = Fixture::new(&columns, &rows)?;
        let map_records = maps(&f)?;
        let before = fs::read(f.path())?;
        let page = before.len() / PAGE_BYTES;
        let locator = insert_row(
            f.path(),
            b"Rows",
            &[
                RowValue::Long(88),
                RowValue::Long(-8800),
                RowValue::Text(&text),
            ],
            &mut budget(),
        )?;
        assert_eq!(locator, RowLocator::new(PageNumber::new(page as u64), 0));
        let mut expected = before.clone();
        let root = f.root.get() as usize * PAGE_BYTES;
        expected[root + 12..root + 16]
            .copy_from_slice(&(if full { 11_u32 } else { 1 }).to_le_bytes());
        for (role, (locator, range)) in map_records.iter().enumerate() {
            let offset = locator.page().get() as usize * PAGE_BYTES + range.start + 5 + page / 8;
            if role == 0 {
                expected[offset] &= !(1 << (page % 8));
            } else {
                expected[offset] |= 1 << (page % 8);
            }
        }
        let mut new_page = [0_u8; PAGE_BYTES];
        new_page[0..4].copy_from_slice(&[1, 1, 0x33, 0x07]); // 1843 contiguous free bytes.
        new_page[4..8].copy_from_slice(&(f.root.get() as u32).to_le_bytes());
        new_page[8..12].copy_from_slice(&[1, 0, 0x3f, 0x07]); // Slot 0 starts at 1855.
        new_page[1855..1864].copy_from_slice(&[3, 88, 0, 0, 0, 0xa0, 0xdd, 0xff, 0xff]);
        new_page[1864..2044].copy_from_slice(&text);
        new_page[2044..].copy_from_slice(&[189, 9, 1, 7]);
        expected.extend_from_slice(&new_page);
        assert_eq!(fs::read(f.path())?, expected);
        let second = insert_row(
            f.path(),
            b"Rows",
            &[
                RowValue::Long(99),
                RowValue::Long(-9900),
                RowValue::Text(&text),
            ],
            &mut budget(),
        )?;
        assert_eq!(second, RowLocator::new(locator.page(), 1));
        assert_eq!(fs::metadata(f.path())?.len(), expected.len() as u64);
        let mut b = budget();
        let mut db = DatabaseReader::open(f.path(), &mut b)?;
        let def = db.table_definition(f.root, &mut b)?;
        let mut reader = db.rows(&def, &mut b)?;
        let mut count = 0;
        while reader.next_row()?.is_some() {
            count += 1;
        }
        assert_eq!(count, if full { 12 } else { 2 });
        f.clean()?;
    }
    Ok(())
}

#[test]
fn eof_map_coverage_free_bit_and_aliases_refuse_without_publication() -> TestResult {
    let f = Fixture::longs(0)?;
    let records = maps(&f)?;
    let original = fs::read(f.path())?;
    let page = original.len() / PAGE_BYTES;
    let root = f.root.get() as usize * PAGE_BYTES;
    let global = records[0].0.page().get() as usize * PAGE_BYTES + records[0].1.start;
    let mut corruptions = Vec::new();
    let mut in_use = original.clone();
    in_use[global + 5 + page / 8] &= !(1 << (page % 8));
    corruptions.push(in_use);
    let mut out_of_range = original.clone();
    out_of_range[global + 1..global + 5].copy_from_slice(&((page + 1) as u32).to_le_bytes());
    corruptions.push(out_of_range);
    let mut indirect = original.clone();
    indirect[global] = 1;
    corruptions.push(indirect);
    let mut alias = original.clone();
    let owned: [u8; 4] = alias[root + 35..root + 39].try_into()?;
    alias[root + 39..root + 43].copy_from_slice(&owned);
    corruptions.push(alias);
    let mut missing = original.clone();
    missing[root + 35] = 254;
    corruptions.push(missing);
    for bad in corruptions {
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
        f.clean()?;
    }
    // Both owned and available can reside on one page, but not in the same record.
    assert_eq!(records[1].0.page(), records[2].0.page());
    fs::write(f.path(), &original)?;
    insert_row(
        f.path(),
        b"Rows",
        &[RowValue::Long(1), RowValue::Long(2)],
        &mut budget(),
    )?;
    f.clean()
}

#[test]
fn last_inline_bit_is_allowed_but_map_growth_is_refused() -> TestResult {
    let f = Fixture::longs(0)?;
    let original = fs::read(f.path())?;
    for pages in [1023_usize, 1024] {
        let mut before = original.clone();
        before.resize(pages * PAGE_BYTES, 0xb6);
        fs::write(f.path(), &before)?;
        let result = insert_row(
            f.path(),
            b"Rows",
            &[RowValue::Long(1), RowValue::Long(2)],
            &mut budget(),
        );
        if pages == 1023 {
            assert_eq!(result?, RowLocator::new(PageNumber::new(1023), 0));
            assert_eq!(fs::metadata(f.path())?.len(), 1024 * PAGE_BYTES as u64);
        } else {
            assert!(matches!(
                result,
                Err(UpdateError::Unsupported("page outside existing inline map"))
            ));
            assert_eq!(fs::read(f.path())?, before);
        }
        f.clean()?;
    }
    Ok(())
}

#[test]
fn eof_budget_and_private_append_corruption_preserve_original() -> TestResult {
    let f = Fixture::longs(0)?;
    let before = fs::read(f.path())?;
    for limits in [
        ResourceLimits::new(crate::ReadLimits::new(
            ByteCount::new(before.len() as u64),
            crate::limits::DEFAULT_MAX_SINGLE_READ_BYTES,
            crate::limits::DEFAULT_MAX_TOTAL_READ_BYTES,
        )),
        ResourceLimits::default().with_max_encoded_bytes(ByteCount::new(PAGE_BYTES as u64)),
        ResourceLimits::default().with_max_total_work_units(1),
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
        assert_eq!(fs::read(f.path())?, before);
        f.clean()?;
    }
    for failure_stage in [PublishStage::Mutation, PublishStage::PrePublish] {
        let error = insert_with_hook(
            &f.path(),
            b"Rows",
            &[RowValue::Long(1), RowValue::Long(2)],
            &mut budget(),
            |stage| {
                if stage == failure_stage {
                    Err(std::io::Error::other("injected publication failure"))
                } else {
                    Ok(())
                }
            },
        );
        assert!(
            matches!(error, Err(UpdateError::Publish(error)) if error.stage() == failure_stage)
        );
        assert_eq!(fs::read(f.path())?, before);
        f.clean()?;
    }
    for mode in 0..4 {
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
                            match mode {
                                0 => bytes[before.len() + 100] ^= 1,
                                1 => {
                                    bytes.pop();
                                }
                                2 => bytes.push(0),
                                _ => bytes[64] ^= 1,
                            }
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

#[test]
fn later_table_ownership_and_minimum_row_availability() -> TestResult {
    let mut f = Fixture::longs(0)?;
    fs::remove_file(f.path())?;
    let first_columns = [ColumnSpec::new(b"Id", ColumnType::Long)];
    let names: [&[u8]; 7] = [b"A", b"B", b"C", b"D", b"E", b"F", b"G"];
    let columns = names.map(|name| {
        ColumnSpec::new(
            name,
            ColumnType::FixedText {
                len: crate::column_definition_writer::nz(255),
            },
        )
    });
    crate::create_database_with_table_rows(
        f.path(),
        &[
            crate::TableRows {
                table: TableSpec {
                    name: b"First",
                    columns: &first_columns,
                    indexes: &[],
                },
                rows: &[&[RowValue::Long(42)]],
            },
            crate::TableRows {
                table: TableSpec {
                    name: b"Rows",
                    columns: &columns,
                    indexes: &[],
                },
                rows: &[],
            },
        ],
        &mut budget(),
    )?;
    let mut b = budget();
    let mut db = DatabaseReader::open(f.path(), &mut b)?;
    f.root = crate::update::writable_table(&mut db, b"Rows", &mut b)?.root();
    drop(db);
    let records = maps(&f)?;
    let before = fs::read(f.path())?;
    let text = [b'x'; 255];
    let locator = insert_row(
        f.path(),
        b"Rows",
        &[RowValue::Text(&text); 7],
        &mut budget(),
    )?;
    assert_eq!(
        locator.page().get(),
        before.len() as u64 / PAGE_BYTES as u64
    );
    let after = fs::read(f.path())?;
    let page = locator.page().get() as usize;
    assert_eq!(
        &after[page * PAGE_BYTES + 4..page * PAGE_BYTES + 8],
        &(f.root.get() as u32).to_le_bytes()
    );
    let mut expected = before.clone();
    let root = f.root.get() as usize * PAGE_BYTES;
    expected[root + 12..root + 16].copy_from_slice(&1_u32.to_le_bytes());
    for (role, (location, range)) in records.iter().enumerate() {
        let offset = location.page().get() as usize * PAGE_BYTES + range.start + 5 + page / 8;
        if role == 0 {
            expected[offset] &= !(1 << (page % 8));
        }
        if role == 1 {
            expected[offset] |= 1 << (page % 8);
        }
        if role == 2 {
            assert_eq!(after[offset] & (1 << (page % 8)), 0);
        }
    }
    assert_eq!(&after[..before.len()], expected);
    // A second fixed-width row needs another EOF page; the first is not advertised available.
    let second = insert_row(f.path(), b"Rows", &[RowValue::Null; 7], &mut budget())?;
    assert_eq!(second.page().get(), locator.page().get() + 1);
    f.clean()
}
