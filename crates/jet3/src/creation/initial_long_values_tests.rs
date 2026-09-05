use super::*;

fn payload_value(kind: ColumnType, payload: &[u8]) -> RowValue<'_> {
    if kind == ColumnType::Memo {
        RowValue::Memo(payload)
    } else {
        RowValue::LongBinary(payload)
    }
}

#[test]
fn payload_boundaries_round_trip_with_separate_column_maps() -> TestResult {
    for kind in [ColumnType::Memo, ColumnType::LongBinary] {
        for (length, pages, available_last) in [
            (1, 0, false),
            (32, 0, false),
            (33, 1, true),
            (512, 1, true),
            (2036, 1, false),
            (2037, 2, true),
            (2048, 2, true),
            (4064, 2, false),
            (4096, 3, true),
        ] {
            let directory = TestDirectory::create()?;
            let columns = [ID, ColumnSpec::new(b"Payload", kind)];
            let table = TableSpec {
                name: b"Items",
                columns: &columns,
                indexes: &[],
            };
            let payload = vec![b'a'; length];
            let values = [RowValue::Long(1), payload_value(kind, &payload)];
            let rows: &[&[RowValue<'_>]] = &[&values, &[RowValue::Long(2), RowValue::Null]];
            create_database_with_rows(directory.target(), &table, rows, &mut budget())?;
            let bytes = fs::read(directory.target())?;
            assert_eq!(bytes.len(), (24 + pages) * crate::PAGE_BYTES);
            assert_eq!(page_rows(&bytes, 23 + pages), 2);
            for page in 23..23 + pages {
                assert!(map_bit(&bytes, 21, 2, page as u64)?);
                assert!(!map_bit(&bytes, 21, 0, page as u64)?);
                assert_eq!(
                    &bytes[page * crate::PAGE_BYTES + 4..page * crate::PAGE_BYTES + 8],
                    b"LVAL"
                );
                assert_eq!(
                    map_bit(&bytes, 21, 3, page as u64)?,
                    page == 22 + pages && available_last
                );
            }
            assert!(map_bit(&bytes, 21, 0, (23 + pages) as u64)?);
            assert!(!map_bit(&bytes, 21, 2, (23 + pages) as u64)?);
            assert!(!map_bit(&bytes, 21, 2, 22)?);
        }
    }
    Ok(())
}

#[test]
fn multiple_payloads_and_data_pages_keep_distinct_references() -> TestResult {
    let directory = TestDirectory::create()?;
    let columns = [ID, ColumnSpec::new(b"Payload", ColumnType::LongBinary)];
    let table = TableSpec {
        name: b"Items",
        columns: &columns,
        indexes: &[],
    };
    let payloads = (0_u8..100)
        .map(|value| vec![value; 512])
        .collect::<Vec<_>>();
    let values = payloads
        .iter()
        .enumerate()
        .map(|(id, bytes)| [RowValue::Long(id as i32), RowValue::LongBinary(bytes)])
        .collect::<Vec<_>>();
    let rows = values.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    create_database_with_rows(directory.target(), &table, &rows, &mut budget())?;
    let bytes = fs::read(directory.target())?;
    assert_eq!(bytes.len(), 125 * crate::PAGE_BYTES);
    assert_eq!(page_rows(&bytes, 123) + page_rows(&bytes, 124), 100);
    for page in 23..123 {
        assert!(map_bit(&bytes, 21, 2, page as u64)?);
    }
    for page in 123..125 {
        assert!(map_bit(&bytes, 21, 0, page as u64)?);
    }
    Ok(())
}

#[test]
fn payload_refusals_and_resource_limits_preserve_destination() -> TestResult {
    let directory = TestDirectory::create()?;
    let columns = [ColumnSpec::new(b"Payload", ColumnType::LongBinary)];
    let table = TableSpec {
        name: b"Items",
        columns: &columns,
        indexes: &[],
    };
    create_database_with_rows(
        directory.target(),
        &table,
        &[&[RowValue::Null]],
        &mut budget(),
    )?;
    let original = fs::read(directory.target())?;
    for value in [RowValue::LongBinary(&[]), RowValue::LongValue(&[0; 12])] {
        assert!(matches!(
            create_database_with_rows(directory.target(), &table, &[&[value]], &mut budget()),
            Err(CreateDatabaseError::Compose(
                ComposeError::InitialLongValue { .. }
            ))
        ));
    }
    assert!(matches!(
        create_database_with_rows(
            directory.target(),
            &table,
            &[&[RowValue::Memo(b"a")]],
            &mut budget()
        ),
        Err(CreateDatabaseError::Compose(ComposeError::Row(
            RowWriteError::TypeMismatch { .. }
        )))
    ));
    let payload = vec![0; 2032 * 1002];
    assert!(matches!(
        create_database_with_rows(
            directory.target(),
            &table,
            &[&[RowValue::LongBinary(&payload)]],
            &mut budget()
        ),
        Err(CreateDatabaseError::Compose(ComposeError::UsageMap(
            crate::UsageMapWriteError::PageOutOfMap { .. }
        )))
    ));
    let mut limited = ResourceBudget::new(
        ResourceLimits::default().with_max_allocation_bytes(crate::ByteCount::new(2048)),
    );
    assert!(
        create_database_with_rows(
            directory.target(),
            &table,
            &[&[RowValue::LongBinary(&payload[..4096])]],
            &mut limited
        )
        .is_err()
    );
    assert_eq!(fs::read(directory.target())?, original);
    assert_eq!(directory.entries()?, ["created.mdb"]);
    Ok(())
}

#[test]
fn candidate_check_rejects_long_value_owner_pointer_and_payload_corruption() -> TestResult {
    let directory = TestDirectory::create()?;
    let columns = [ColumnSpec::new(b"Payload", ColumnType::LongBinary)];
    let table = TableSpec {
        name: b"Items",
        columns: &columns,
        indexes: &[],
    };
    let payload = [42; 2048];
    let rows: &[&[RowValue<'_>]] = &[&[RowValue::LongBinary(&payload)]];
    create_database_with_rows(directory.target(), &table, rows, &mut budget())?;
    let original = fs::read(directory.target())?;
    // First chained row fills page 23 from offset 12: pointer then payload.
    for offset in [
        23 * crate::PAGE_BYTES + 4,
        23 * crate::PAGE_BYTES + 13,
        23 * crate::PAGE_BYTES + 16,
    ] {
        let mut changed = original.clone();
        changed[offset] ^= 1;
        fs::write(directory.target(), changed)?;
        assert!(
            super::super::super::check_initial_rows(
                &directory.target(),
                &table,
                rows,
                &mut budget()
            )
            .is_err()
        );
    }
    Ok(())
}

#[test]
fn long_value_allocation_leaves_room_for_the_final_data_page() -> TestResult {
    let directory = TestDirectory::create()?;
    let columns = [ColumnSpec::new(b"Payload", ColumnType::LongBinary)];
    let table = TableSpec {
        name: b"Items",
        columns: &columns,
        indexes: &[],
    };
    let payload = vec![1; 2032 * 1001];
    create_database_with_rows(
        directory.target(),
        &table,
        &[&[RowValue::LongBinary(&payload[..2032 * 1000])]],
        &mut budget(),
    )?;
    let original = fs::read(directory.target())?;
    assert_eq!(original.len(), 1024 * crate::PAGE_BYTES);
    assert!(map_bit(&original, 21, 2, 1022)?);
    assert!(!map_bit(&original, 21, 2, 1023)?);
    assert!(map_bit(&original, 21, 0, 1023)?);
    assert!(matches!(
        create_database_with_rows(
            directory.target(),
            &table,
            &[&[RowValue::LongBinary(&payload)]],
            &mut budget()
        ),
        Err(CreateDatabaseError::Compose(ComposeError::UsageMap(
            crate::UsageMapWriteError::PageOutOfMap { .. }
        )))
    ));
    assert_eq!(fs::read(directory.target())?, original);
    Ok(())
}
