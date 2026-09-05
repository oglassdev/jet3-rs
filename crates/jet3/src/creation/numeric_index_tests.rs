use super::*;

#[test]
fn observed_numeric_bytes_directions_and_locators_survive_publication() -> TestResult {
    for (column, values, ascending) in [
        (
            ColumnType::Boolean,
            [RowValue::Boolean(false), RowValue::Boolean(true)],
            vec![vec![0x7f, 0], vec![0x7f, 0xff]],
        ),
        (
            ColumnType::Byte,
            [RowValue::Byte(255), RowValue::Byte(0)],
            vec![vec![0x7f, 0], vec![0x7f, 0xff]],
        ),
        (
            ColumnType::Integer,
            [RowValue::Integer(i16::MAX), RowValue::Integer(i16::MIN)],
            vec![vec![0x7f, 0, 0], vec![0x7f, 0xff, 0xff]],
        ),
        (
            ColumnType::Currency,
            [
                RowValue::Currency { scaled: i64::MAX },
                RowValue::Currency { scaled: i64::MIN },
            ],
            vec![
                vec![0x7f, 0, 0, 0, 0, 0, 0, 0, 0],
                vec![0x7f, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff],
            ],
        ),
        (
            ColumnType::Single,
            [RowValue::Single(1.0), RowValue::Single(-1.0)],
            vec![
                vec![0x7f, 0x40, 0x7f, 0xff, 0xff],
                vec![0x7f, 0xbf, 0x80, 0, 0],
            ],
        ),
        (
            ColumnType::Double,
            [RowValue::Double(1.0), RowValue::Double(-1.0)],
            vec![
                vec![0x7f, 0x40, 0x0f, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff],
                vec![0x7f, 0xbf, 0xf0, 0, 0, 0, 0, 0, 0],
            ],
        ),
    ] {
        for direction in [IndexDirection::Ascending, IndexDirection::Descending] {
            let directory = TestDirectory::create()?;
            let indexes = [IndexSpec {
                fields: &[field(0, direction)],
                ..one_index(IndexKind::Unique)[0]
            }];
            let table = TableSpec {
                name: b"Items",
                columns: &[ColumnSpec::new(b"Value", column)],
                indexes: &indexes,
            };
            create_database_with_rows(
                directory.target(),
                &table,
                &[&[values[0]], &[values[1]]],
                &mut budget(),
            )?;
            let actual = tree(&directory.target())?;
            let mut expected = ascending.clone();
            let slots = if direction == IndexDirection::Ascending {
                [1, 0]
            } else {
                [0, 1]
            };
            if direction == IndexDirection::Descending {
                for key in &mut expected {
                    for byte in key {
                        *byte ^= 0xff;
                    }
                }
                expected.reverse();
            }
            assert_eq!(actual.entries().len(), 2);
            for ((entry, key), slot) in actual.entries().iter().zip(expected).zip(slots) {
                assert_eq!(entry.key().raw_bytes(), key);
                assert_eq!(entry.row().slot(), slot);
            }
        }
    }
    Ok(())
}

#[test]
fn nullable_numeric_composites_reuse_full_key_policy_and_scalar_duplicate_error() -> TestResult {
    let columns = [
        ColumnSpec::new(b"A", ColumnType::Currency),
        ColumnSpec::new(b"B", ColumnType::Double),
    ];
    let fields = [
        field(0, IndexDirection::Ascending),
        field(1, IndexDirection::Descending),
    ];
    let rows: &[&[RowValue<'_>]] = &[
        &[RowValue::Null, RowValue::Null],
        &[RowValue::Null, RowValue::Double(1.0)],
        &[RowValue::Null, RowValue::Double(1.0)],
        &[RowValue::Currency { scaled: 1 }, RowValue::Null],
        &[RowValue::Currency { scaled: 1 }, RowValue::Double(2.0)],
    ];
    for policy in [
        crate::IndexNullPolicy::Include,
        crate::IndexNullPolicy::IgnoreAllNull,
        crate::IndexNullPolicy::Required,
    ] {
        let directory = TestDirectory::create()?;
        let indexes = [IndexSpec {
            name: b"ByKey",
            kind: IndexKind::Unique.with_null_policy(policy),
            fields: &fields,
        }];
        let table = TableSpec {
            name: b"Items",
            columns: &columns,
            indexes: &indexes,
        };
        let result = create_database_with_rows(directory.target(), &table, rows, &mut budget());
        if policy == crate::IndexNullPolicy::Required {
            assert!(matches!(
                result,
                Err(CreateDatabaseError::Compose(
                    ComposeError::NullInitialIndexKey { row: 0 }
                ))
            ));
            assert!(directory.entries()?.is_empty());
            continue;
        }
        result?;
        let index = tree(&directory.target())?;
        assert_eq!(
            index.entries().len(),
            if policy == crate::IndexNullPolicy::Include {
                5
            } else {
                4
            }
        );
        assert_eq!(
            index.entries()[0].key().raw_bytes(),
            [0, 0x80, 0x40, 0x0f, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff]
        );
        fs::remove_file(directory.target())?;
        assert!(matches!(
            create_database_with_rows(
                directory.target(),
                &table,
                &[rows[4], rows[4]],
                &mut budget()
            ),
            Err(CreateDatabaseError::Compose(
                ComposeError::DuplicateInitialScalarIndexKey
            ))
        ));
        assert!(directory.entries()?.is_empty());
    }
    Ok(())
}

#[test]
fn wide_numeric_components_pack_across_leaf_boundaries() -> TestResult {
    let directory = TestDirectory::create()?;
    let fields = [
        field(0, IndexDirection::Ascending),
        field(1, IndexDirection::Descending),
    ];
    let indexes = [IndexSpec {
        name: b"ByKey",
        kind: IndexKind::Unique,
        fields: &fields,
    }];
    let table = TableSpec {
        name: b"Items",
        columns: &[
            ColumnSpec::new(b"A", ColumnType::Currency),
            ColumnSpec::new(b"B", ColumnType::Double),
        ],
        indexes: &indexes,
    };
    let values: Vec<_> = (0..170)
        .map(|n| [RowValue::Currency { scaled: n }, RowValue::Double(n as f64)])
        .collect();
    let rows: Vec<_> = values.iter().map(|r| r.as_slice()).collect();
    create_database_with_rows(directory.target(), &table, &rows, &mut budget())?;
    let index = tree(&directory.target())?;
    assert_eq!(index.entries().len(), 170);
    assert!(index.nodes().len() > 1);
    assert!(
        index
            .entries()
            .iter()
            .all(|entry| entry.key().raw_bytes().len() == 18)
    );
    assert!(
        index
            .entries()
            .windows(2)
            .all(|pair| pair[0].key().raw_bytes() < pair[1].key().raw_bytes())
    );
    Ok(())
}

#[test]
fn excluded_scalar_values_and_types_never_publish() -> TestResult {
    for (column, value) in [
        (ColumnType::Boolean, RowValue::Null),
        (ColumnType::Single, RowValue::Single(-0.0)),
        (ColumnType::Single, RowValue::Single(f32::INFINITY)),
        (ColumnType::Single, RowValue::Single(f32::NAN)),
        (ColumnType::Double, RowValue::Double(-0.0)),
        (ColumnType::Double, RowValue::Double(f64::NEG_INFINITY)),
        (ColumnType::Double, RowValue::Double(f64::NAN)),
        (ColumnType::DateTime, RowValue::DateTime { days: 1.0 }),
        (ColumnType::Guid, RowValue::Guid([0; 16])),
    ] {
        let directory = TestDirectory::create()?;
        let table = TableSpec {
            name: b"Items",
            columns: &[ColumnSpec::new(b"Value", column)],
            indexes: &one_index(IndexKind::Ordinary),
        };
        assert!(
            create_database_with_rows(directory.target(), &table, &[&[value]], &mut budget())
                .is_err()
        );
        assert!(directory.entries()?.is_empty());
    }
    Ok(())
}
