use super::initial_rows_tests::*;
use crate::WriteError;
use crate::{
    ColumnSpec, ColumnType, ComposeError, DatabaseSpec, ResourceBudget, ResourceLimits, RowValue,
    RowWriteError, TableRows, TableSpec, create::api_tests::*, create_database,
};
use std::fs;

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
            (2037, 2, false),
            (2048, 2, false),
            (4064, 2, false),
            (4096, 3, false),
        ] {
            let directory = TestDirectory::create()?;
            let columns = [ID, ColumnSpec::new(b"Payload", kind)];
            let table = TableSpec {
                validation: crate::TableValidation::NONE,
                name: b"Items",
                columns: &columns,
                indexes: &[],
            };
            let payload = vec![b'a'; length];
            let values = [RowValue::Long(1), payload_value(kind, &payload)];
            let rows: &[&[RowValue<'_>]] = &[&values, &[RowValue::Long(2), RowValue::Null]];
            create_database(
                directory.target(),
                &DatabaseSpec {
                    tables: &[TableRows { table, rows }],
                    ..DatabaseSpec::default()
                },
                &mut budget(),
            )?;
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
        validation: crate::TableValidation::NONE,
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
    create_database(
        directory.target(),
        &DatabaseSpec {
            tables: &[TableRows { table, rows: &rows }],
            ..DatabaseSpec::default()
        },
        &mut budget(),
    )?;
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
fn empty_ole_creation_has_the_same_storage_as_null() -> TestResult {
    let null = TestDirectory::create()?;
    let empty = TestDirectory::create()?;
    let columns = [ID, ColumnSpec::new(b"Payload", ColumnType::LongBinary)];
    let table = TableSpec {
        validation: crate::TableValidation::NONE,
        name: b"Items",
        columns: &columns,
        indexes: &[],
    };
    for (directory, value) in [(&null, RowValue::Null), (&empty, RowValue::LongBinary(b""))] {
        create_database(
            directory.target(),
            &DatabaseSpec {
                tables: &[TableRows {
                    table,
                    rows: &[
                        &[RowValue::Long(1), value],
                        &[RowValue::Long(2), RowValue::LongBinary(b"x")],
                    ],
                }],
                ..DatabaseSpec::default()
            },
            &mut budget(),
        )?;
    }
    assert_eq!(fs::read(empty.target())?, fs::read(null.target())?);
    Ok(())
}

#[test]
fn payload_refusals_and_resource_limits_preserve_destination() -> TestResult {
    let directory = TestDirectory::create()?;
    let columns = [ColumnSpec::new(b"Payload", ColumnType::LongBinary)];
    let table = TableSpec {
        validation: crate::TableValidation::NONE,
        name: b"Items",
        columns: &columns,
        indexes: &[],
    };
    create_database(
        directory.target(),
        &DatabaseSpec {
            tables: &[TableRows {
                table,
                rows: &[&[RowValue::Null]],
            }],
            ..DatabaseSpec::default()
        },
        &mut budget(),
    )?;
    let original = fs::read(directory.target())?;
    assert!(matches!(
        create_database(
            directory.target(),
            &DatabaseSpec {
                tables: &[TableRows {
                    table,
                    rows: &[&[RowValue::LongValue(&[0; 12])]]
                }],
                ..DatabaseSpec::default()
            },
            &mut budget()
        ),
        Err(WriteError::Compose(ComposeError::InitialLongValue { .. }))
    ));
    assert!(matches!(
        create_database(
            directory.target(),
            &DatabaseSpec {
                tables: &[TableRows {
                    table,
                    rows: &[&[RowValue::Memo(b"a")]]
                }],
                ..DatabaseSpec::default()
            },
            &mut budget()
        ),
        Err(WriteError::Compose(ComposeError::Row(
            RowWriteError::TypeMismatch { .. }
        )))
    ));
    let payload = vec![0; 4096];
    let mut limited = ResourceBudget::new(
        ResourceLimits::default().with_max_allocation_bytes(crate::ByteCount::new(2048)),
    );
    assert!(
        create_database(
            directory.target(),
            &DatabaseSpec {
                tables: &[TableRows {
                    table,
                    rows: &[&[RowValue::LongBinary(&payload[..4096])]]
                }],
                ..DatabaseSpec::default()
            },
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
        validation: crate::TableValidation::NONE,
        name: b"Items",
        columns: &columns,
        indexes: &[],
    };
    let payload = [42; 2048];
    let rows: &[&[RowValue<'_>]] = &[&[RowValue::LongBinary(&payload)]];
    create_database(
        directory.target(),
        &DatabaseSpec {
            tables: &[TableRows { table, rows }],
            ..DatabaseSpec::default()
        },
        &mut budget(),
    )?;
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
            super::check::check_initial_rows(&directory.target(), &table, rows, &mut budget())
                .is_err()
        );
    }
    Ok(())
}

#[test]
fn long_value_allocation_extends_maps_for_the_final_data_page() -> TestResult {
    let directory = TestDirectory::create()?;
    let columns = [ColumnSpec::new(b"Payload", ColumnType::LongBinary)];
    let table = TableSpec {
        validation: crate::TableValidation::NONE,
        name: b"Items",
        columns: &columns,
        indexes: &[],
    };
    let payload = vec![1; 2032 * 1001];
    create_database(
        directory.target(),
        &DatabaseSpec {
            tables: &[TableRows {
                table,
                rows: &[&[RowValue::LongBinary(&payload[..2032 * 1000])]],
            }],
            ..DatabaseSpec::default()
        },
        &mut budget(),
    )?;
    let original = fs::read(directory.target())?;
    assert_eq!(original.len(), 1024 * crate::PAGE_BYTES);
    assert!(map_bit(&original, 21, 2, 1022)?);
    assert!(!map_bit(&original, 21, 2, 1023)?);
    assert!(map_bit(&original, 21, 0, 1023)?);
    let grown = directory.target().with_file_name("grown.mdb");
    create_database(
        &grown,
        &DatabaseSpec {
            tables: &[TableRows {
                table,
                rows: &[&[RowValue::LongBinary(&payload)]],
            }],
            ..DatabaseSpec::default()
        },
        &mut budget(),
    )?;
    assert!(fs::metadata(grown)?.len() > 1024 * crate::PAGE_BYTES as u64);
    assert_eq!(fs::read(directory.target())?, original);
    Ok(())
}
