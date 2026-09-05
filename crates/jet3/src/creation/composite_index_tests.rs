use super::*;

#[test]
fn descending_signed_boundaries_encode_and_sort_with_original_locators() -> TestResult {
    let directory = TestDirectory::create()?;
    let indexes = [IndexSpec {
        fields: &[field(0, IndexDirection::Descending)],
        ..one_index(IndexKind::Unique)[0]
    }];
    let table = TableSpec {
        name: b"Items",
        columns: &[ID],
        indexes: &indexes,
    };
    let rows: &[&[RowValue<'_>]] = &[
        &[RowValue::Long(i32::MIN)],
        &[RowValue::Long(-1)],
        &[RowValue::Long(0)],
        &[RowValue::Long(i32::MAX)],
    ];
    create_database_with_rows(directory.target(), &table, rows, &mut budget())?;
    let index = tree(&directory.target())?;
    for (entry, (key, slot)) in index.entries().iter().zip([
        ([0x80, 0, 0, 0, 0], 3),
        ([0x80, 0x7f, 0xff, 0xff, 0xff], 2),
        ([0x80, 0x80, 0, 0, 0], 1),
        ([0x80, 0xff, 0xff, 0xff, 0xff], 0),
    ]) {
        assert_eq!(entry.key().raw_bytes(), key);
        assert_eq!(
            entry.row(),
            crate::RowLocator::new(PageNumber::new(24), slot)
        );
    }
    assert!(matches!(
        create_database_with_rows(
            directory.target(),
            &table,
            &[rows[0], rows[0]],
            &mut budget()
        ),
        Err(CreateDatabaseError::Compose(
            ComposeError::DuplicateInitialIndexKey { value: i32::MIN }
        ))
    ));
    Ok(())
}

#[test]
fn mixed_components_respect_declared_order_and_count_complete_duplicate_keys() -> TestResult {
    for directions in [
        [IndexDirection::Ascending, IndexDirection::Descending],
        [IndexDirection::Descending, IndexDirection::Ascending],
    ] {
        let directory = TestDirectory::create()?;
        let indexes = [IndexSpec {
            fields: &[field(1, directions[0]), field(0, directions[1])],
            ..one_index(IndexKind::Ordinary)[0]
        }];
        let table = TableSpec {
            name: b"Items",
            columns: &[ID, SEQUENCE],
            indexes: &indexes,
        };
        let rows: &[&[RowValue<'_>]] = &[
            &[RowValue::Long(i32::MIN), RowValue::Long(i32::MAX)],
            &[RowValue::Long(i32::MAX), RowValue::Long(i32::MIN)],
            &[RowValue::Long(0), RowValue::Long(-1)],
            &[RowValue::Long(1), RowValue::Long(-1)],
            &[RowValue::Long(0), RowValue::Long(-1)],
        ];
        create_database_with_rows(directory.target(), &table, rows, &mut budget())?;
        let index = tree(&directory.target())?;
        let (slots, first_key) = if directions[0] == IndexDirection::Ascending {
            ([1, 3, 2, 4, 0], [0x7f, 0, 0, 0, 0, 0x80, 0, 0, 0, 0])
        } else {
            ([0, 2, 4, 3, 1], [0x80, 0, 0, 0, 0, 0x7f, 0, 0, 0, 0])
        };
        assert_eq!(index.entries()[0].key().raw_bytes(), first_key);
        assert_eq!(
            index
                .entries()
                .iter()
                .map(|entry| entry.row().slot())
                .collect::<Vec<_>>(),
            slots
        );
        let bytes = fs::read(directory.target())?;
        assert_eq!(
            &bytes[20 * crate::PAGE_BYTES + 47..20 * crate::PAGE_BYTES + 51],
            &4_u32.to_le_bytes()
        );
        for kind in [IndexKind::Primary, IndexKind::Unique] {
            let indexes = [IndexSpec { kind, ..indexes[0] }];
            let table = TableSpec {
                indexes: &indexes,
                ..table
            };
            assert!(matches!(
                create_database_with_rows(directory.target(), &table, rows, &mut budget()),
                Err(CreateDatabaseError::Compose(
                    ComposeError::DuplicateInitialCompositeIndexKey { values: [-1, 0] }
                ))
            ));
            assert_eq!(fs::read(directory.target())?, bytes);
        }
    }
    Ok(())
}

#[test]
fn composite_capacity_and_multiple_row_pages_preserve_locators_and_destination() -> TestResult {
    let directory = TestDirectory::create()?;
    let indexes = [IndexSpec {
        fields: &[
            field(0, IndexDirection::Ascending),
            field(1, IndexDirection::Descending),
        ],
        ..one_index(IndexKind::Unique)[0]
    }];
    let table = TableSpec {
        name: b"Items",
        columns: &[
            ID,
            SEQUENCE,
            ColumnSpec::new(b"Payload", ColumnType::Text { max_len: nz(255) }),
        ],
        indexes: &indexes,
    };
    let payload = [b'x'; 255];
    let values = (0..129)
        .map(|value| {
            [
                RowValue::Long(value),
                RowValue::Long(-value),
                RowValue::Text(&payload),
            ]
        })
        .collect::<Vec<_>>();
    let rows = values.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    create_database_with_rows(directory.target(), &table, &rows[..128], &mut budget())?;
    let index = tree(&directory.target())?;
    assert_eq!(index.entries().len(), 128);
    for (ordinal, entry) in index.entries().iter().enumerate() {
        assert_eq!(
            entry.row(),
            crate::RowLocator::new(
                PageNumber::new(24 + ordinal as u64 / 7),
                (ordinal % 7) as u8
            )
        );
    }
    let original = fs::read(directory.target())?;
    assert_eq!(
        &original[23 * crate::PAGE_BYTES + 2..23 * crate::PAGE_BYTES + 4],
        &8_u16.to_le_bytes()
    );
    let expanded = TestDirectory::create()?;
    create_database_with_rows(expanded.target(), &table, &rows, &mut budget())?;
    assert_eq!(tree(&expanded.target())?.nodes().len(), 3);
    assert_eq!(tree(&expanded.target())?.entries().len(), 129);
    let mut changed = original;
    changed[23 * crate::PAGE_BYTES + 248 + 9] ^= 1;
    fs::write(directory.target(), changed)?;
    assert!(
        crate::creation::api::check_initial_rows(
            &directory.target(),
            &table,
            &rows[..128],
            &mut budget()
        )
        .is_err()
    );
    Ok(())
}

#[test]
fn required_null_second_component_and_three_fields_are_refused_without_publication() -> TestResult {
    let directory = TestDirectory::create()?;
    let indexes = [IndexSpec {
        fields: &[
            field(0, IndexDirection::Ascending),
            field(1, IndexDirection::Descending),
        ],
        ..one_index(IndexKind::Ordinary.with_null_policy(crate::IndexNullPolicy::Required))[0]
    }];
    let table = TableSpec {
        name: b"Items",
        columns: &[ID, SEQUENCE],
        indexes: &indexes,
    };
    assert!(matches!(
        create_database_with_rows(
            directory.target(),
            &table,
            &[&[RowValue::Long(0), RowValue::Null]],
            &mut budget()
        ),
        Err(CreateDatabaseError::Compose(
            ComposeError::NullInitialIndexKey { row: 0 }
        ))
    ));
    let indexes = [IndexSpec {
        fields: &[
            field(0, IndexDirection::Ascending),
            field(1, IndexDirection::Descending),
            field(2, IndexDirection::Ascending),
        ],
        ..indexes[0]
    }];
    let table = TableSpec {
        columns: &[ID, SEQUENCE, ColumnSpec::new(b"Third", ColumnType::Long)],
        indexes: &indexes,
        ..table
    };
    assert!(matches!(
        create_database_with_rows(directory.target(), &table, &[], &mut budget()),
        Err(CreateDatabaseError::Compose(
            ComposeError::UnsupportedInitialIndexSchema
        ))
    ));
    assert!(directory.entries()?.is_empty());
    Ok(())
}
