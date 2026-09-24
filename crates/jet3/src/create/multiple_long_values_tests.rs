use crate::WriteError;
use crate::testkit::create;
use crate::testkit::index;
use crate::testkit::table;
use crate::{
    ByteCount, ColumnOrdinal, ColumnRef, ColumnSpec, ColumnType, ComposeError, DatabaseReader,
    DatabaseSpec, IndexColumnSpec, IndexDirection, IndexKind, IndexSpec, InlineLongValue,
    LongValue, LongValueChunkValue, MapRowLocator, PageNumber, ResourceBudget, ResourceLimits,
    RowValue, RowWriteError, TableRows, TableSpec, TextCodePage, ValueKind,
    create::{api::create_database, api_tests::*, check::ImageCheckError, initial_rows_tests::*},
};
use std::fs;

const INDEXES: [IndexSpec<'static>; 3] = [
    index(
        b"ById",
        &[IndexColumnSpec {
            column: ColumnRef::Name(b"Id"),
            direction: IndexDirection::Ascending,
        }],
        IndexKind::Primary,
    ),
    index(
        b"ByTag",
        &[IndexColumnSpec {
            column: ColumnRef::Name(b"Tag"),
            direction: IndexDirection::Descending,
        }],
        IndexKind::Ordinary,
    ),
    index(
        b"ByPair",
        &[
            IndexColumnSpec {
                column: ColumnRef::Name(b"Id"),
                direction: IndexDirection::Descending,
            },
            IndexColumnSpec {
                column: ColumnRef::Name(b"Tag"),
                direction: IndexDirection::Ascending,
            },
        ],
        IndexKind::Unique,
    ),
];

#[test]
fn mixed_columns_indexes_and_generated_ids_keep_independent_payloads_and_maps() -> TestResult {
    let single = [b's'; 33];
    let chained = [b'c'; 4096];
    let full_ole = [0xa5; 2036];
    let chained_ole = [0x5a; 2037];
    for generated in [false, true] {
        let directory = TempDir::new("create")?;
        let columns = [
            ColumnSpec::new(b"FirstMemo", ColumnType::Memo),
            ColumnSpec::new(
                b"Id",
                if generated {
                    ColumnType::AutoIncrement
                } else {
                    ColumnType::Long
                },
            ),
            ColumnSpec::new(b"Blob", ColumnType::LongBinary),
            ColumnSpec::new(b"Tag", ColumnType::Long),
            ColumnSpec::new(b"LastMemo", ColumnType::Memo),
        ];
        let indexes = INDEXES;
        let table = table(b"Items", &columns, &indexes);
        let values = (0..205)
            .map(|id| {
                let (first, blob, last) = match id {
                    0 => (
                        RowValue::Memo(&single),
                        RowValue::LongBinary(&full_ole),
                        RowValue::Memo(&chained),
                    ),
                    1 => (
                        RowValue::Memo(&single[..32]),
                        RowValue::LongBinary(&chained_ole),
                        RowValue::Memo(b"a"),
                    ),
                    2 => (RowValue::Null, RowValue::Null, RowValue::Memo(&single)),
                    3 => (
                        RowValue::Memo(&chained),
                        RowValue::LongBinary(&full_ole[..32]),
                        RowValue::Null,
                    ),
                    4 => (
                        RowValue::Memo(b"b"),
                        RowValue::LongBinary(&full_ole[..1]),
                        RowValue::Memo(&single[..32]),
                    ),
                    _ => (RowValue::Null, RowValue::Null, RowValue::Null),
                };
                [
                    first,
                    if generated {
                        RowValue::AutoIncrement
                    } else {
                        RowValue::Long(id + 1)
                    },
                    blob,
                    if id == 0 {
                        RowValue::Null
                    } else {
                        RowValue::Long(id % 3)
                    },
                    last,
                ]
            })
            .collect::<Vec<_>>();
        let rows = values.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
        let mut requests = Vec::new();
        if generated {
            requests.push(TableRows {
                table: crate::testkit::table(b"First", &[ID], &[]),
                rows: &[],
            });
        }
        requests.push(TableRows { table, rows: &rows });
        create(directory.target(), &requests)?;
        let original = fs::read(directory.target())?;
        let mut operation = budget();
        let mut database = DatabaseReader::open(directory.target(), &mut operation)?;
        let tables = requests
            .iter()
            .map(|request| request.table)
            .collect::<Vec<_>>();
        let roots =
            crate::create::check::image_table_roots(&mut database, &tables, &mut operation)?;
        let root = roots.last().copied().flatten().ok_or("missing table")?;
        let definition = database.table_definition(root, &mut operation)?;
        assert_eq!(definition.row_count(), 205);
        assert_eq!(definition.long_value_maps().len(), 3);
        assert_eq!(definition.physical_indexes().len(), 3);
        if generated {
            let start = root.get() as usize * crate::PAGE_BYTES;
            assert_eq!(&original[start + 16..start + 20], &205_i32.to_le_bytes());
        }
        let first = crate::create::composer::initial_payload_start(
            &table,
            root,
            !generated,
            &mut crate::ResourceBudget::new(crate::ResourceLimits::default()),
        )?;
        let mut next = first;
        let mut expected_pages = [Vec::new(), Vec::new(), Vec::new()];
        for row in &values {
            for (group, column) in [0, 2, 4].into_iter().enumerate() {
                let payload = match row[column] {
                    RowValue::Memo(payload) | RowValue::LongBinary(payload) => payload,
                    _ => continue,
                };
                if payload.len() <= 32 {
                    continue;
                }
                let chained = payload.len() > 2036;
                let fragment_size = if chained { 2032 } else { 2036 };
                for fragment in payload.chunks(fragment_size) {
                    expected_pages[group].push((next, !chained && fragment.len() <= 2033));
                    next += 1;
                }
            }
        }
        assert_eq!(next - first, 11);
        let map = root.get() + 1;
        for (group, (column, expected)) in [0, 2, 4].into_iter().zip(&expected_pages).enumerate() {
            let actual = &definition.long_value_maps()[group];
            assert_eq!(actual.column(), ColumnOrdinal::new(column));
            assert_eq!(
                actual.owned(),
                MapRowLocator::new(PageNumber::new(map), 5 + 2 * group as u8)
            );
            assert_eq!(
                actual.available(),
                MapRowLocator::new(PageNumber::new(map), 6 + 2 * group as u8)
            );
            for page in 0..database.geometry().page_count() {
                let member = expected.iter().find(|(number, _)| *number == page);
                assert_eq!(
                    map_bit(&original, map, actual.owned().row(), page)?,
                    member.is_some()
                );
                assert_eq!(
                    map_bit(&original, map, actual.available().row(), page)?,
                    member.is_some_and(|(_, available)| *available)
                );
                if member.is_some() {
                    assert!(!map_bit(&original, map, 0, page)?);
                    for index in 0..3 {
                        assert!(!map_bit(&original, map, 2 + index, page)?);
                    }
                }
            }
        }
        for ordinal in 0..3 {
            let tree = database.index_tree(&definition, ordinal, &mut operation)?;
            assert_eq!(tree.entries().len(), 205);
            assert!(tree.nodes().len() > 1);
        }
        let mut cursor = database.rows(&definition, &mut operation)?;
        for (ordinal, expected) in values.iter().enumerate() {
            let mut row = cursor.next_row()?.ok_or("missing row")?;
            assert!(
                matches!(row.value(ColumnOrdinal::new(1), TextCodePage::Windows1252)?.ok_or("missing id")?.kind(), ValueKind::Long(value) if *value == ordinal as i32 + 1)
            );
            let mut external = Vec::new();
            for column in [0, 2, 4] {
                let actual = row
                    .value(ColumnOrdinal::new(column), TextCodePage::Windows1252)?
                    .ok_or("missing column")?;
                let payload = match expected[usize::from(column)] {
                    RowValue::Null => {
                        assert!(matches!(actual.kind(), ValueKind::Null));
                        continue;
                    }
                    RowValue::Memo(payload) | RowValue::LongBinary(payload) => payload,
                    _ => return Err("unexpected fixture".into()),
                };
                match actual.kind() {
                    ValueKind::LongValue(LongValue::Inline { value, .. }) => {
                        let bytes = match value {
                            InlineLongValue::Text(text) => text.raw_bytes(),
                            InlineLongValue::Binary(bytes) => bytes,
                        };
                        assert_eq!(bytes, payload);
                    }
                    ValueKind::LongValue(LongValue::External(reference)) => {
                        external.push((*reference, payload))
                    }
                    _ => return Err("unexpected payload kind".into()),
                }
            }
            for (reference, payload) in external {
                let mut stream = cursor.long_value(reference)?;
                let mut actual = Vec::new();
                while let Some(chunk) = stream.next_chunk()? {
                    actual.extend_from_slice(match chunk.value() {
                        LongValueChunkValue::Text(text) => text.raw_bytes(),
                        LongValueChunkValue::Binary(bytes) => bytes,
                    });
                }
                assert_eq!(actual, payload);
            }
        }
        assert!(cursor.next_row()?.is_none());
    }
    Ok(())
}

#[test]
fn long_value_maps_spill_after_the_last_index_map_slot() -> TestResult {
    let names = (0..8)
        .map(|column| format!("Memo{column}").into_bytes())
        .collect::<Vec<_>>();
    for index_count in 0..=3 {
        for long_count in [6, 7, 8] {
            let directory = TempDir::new("create")?;
            let mut columns = vec![ID, ColumnSpec::new(b"Tag", ColumnType::Long)];
            columns.extend(
                names[..long_count]
                    .iter()
                    .map(|name| ColumnSpec::new(name, ColumnType::Memo)),
            );
            let table = table(b"Items", &columns, &INDEXES[..index_count]);
            create(directory.target(), &[TableRows::empty(table)])?;
            let bytes = fs::read(directory.target())?;
            let map_rows = 2 + index_count + 2 * long_count;
            assert_eq!(page_rows(&bytes, 21), map_rows.min(15) as u16);
            if map_rows > 15 {
                assert_eq!(page_rows(&bytes, 22), (map_rows - 15) as u16);
            }
            let mut row = vec![RowValue::Long(1), RowValue::Long(2)];
            row.extend((0..long_count).map(|_| RowValue::Memo(b"a")));
            create(
                directory.join("populated.mdb"),
                &[TableRows {
                    table,
                    rows: &[&row],
                }],
            )?;
        }
    }
    Ok(())
}

#[test]
fn every_external_column_is_checked_and_refusals_preserve_the_destination() -> TestResult {
    let directory = TempDir::new("create")?;
    let columns = [
        NOTE,
        ColumnSpec::new(b"Blob", ColumnType::LongBinary),
        ColumnSpec::new(b"Last", ColumnType::Memo),
    ];
    let table = table(b"Items", &columns, &[]);
    let payloads = [[b'a'; 33], [b'b'; 33], [b'c'; 33]];
    let rows: &[&[RowValue<'_>]] = &[&[
        RowValue::Memo(&payloads[0]),
        RowValue::LongBinary(&payloads[1]),
        RowValue::Memo(&payloads[2]),
    ]];
    create(directory.target(), &[TableRows { table, rows }])?;
    let original = fs::read(directory.target())?;
    for column in 0..3 {
        let mut changed = original.clone();
        changed[(23 + column) * crate::PAGE_BYTES + crate::PAGE_BYTES - 1] ^= 1;
        fs::write(directory.target(), changed)?;
        assert!(matches!(
            crate::create::check::check_initial_rows(
                &directory.target(),
                &table,
                rows,
                &mut budget()
            ),
            Err(ImageCheckError::Mismatch {
                detail: "initial long-value payload"
            })
        ));
    }
    let pages = crate::create::composer::compose_database_with_table_rows(
        &[TableRows { table, rows }],
        &mut budget(),
    )?
    .into_pages();
    for row in 2..8 {
        let mut changed = original.clone();
        let entry = 21 * crate::PAGE_BYTES + 10 + 2 * row;
        let offset = usize::from(u16::from_le_bytes([changed[entry], changed[entry + 1]]));
        changed[21 * crate::PAGE_BYTES + offset + 5 + 23 / 8] ^= 1 << (23 % 8);
        fs::write(directory.target(), changed)?;
        assert!(matches!(
            crate::create::check::check_long_value_written_pages(
                &directory.target(),
                &[table],
                &pages,
                &mut budget()
            ),
            Err(ImageCheckError::Mismatch {
                detail: "long-value written page"
            })
        ));
    }
    fs::write(directory.target(), &original)?;
    type Accepts = fn(&ComposeError) -> bool;
    let cases: [(RowValue<'_>, Accepts); 3] = [
        (RowValue::Memo(b""), |error| {
            matches!(
                error,
                ComposeError::Row(RowWriteError::ZeroLengthNotAllowed { .. })
            )
        }),
        (RowValue::LongValue(&[0; 12]), |error| {
            matches!(error, ComposeError::InitialLongValue { .. })
        }),
        (RowValue::LongBinary(b"wrong type"), |error| {
            matches!(error, ComposeError::Row(RowWriteError::TypeMismatch { .. }))
        }),
    ];
    for (rejected, accepts) in cases {
        let row = [rows[0][0], rows[0][1], rejected];
        match create(
            directory.target(),
            &[TableRows {
                table,
                rows: &[&row],
            }],
        ) {
            Err(WriteError::Compose(error)) if accepts(&error) => {}
            other => return Err(format!("{rejected:?}: {other:?}").into()),
        }
    }
    let invalid_option = [NOTE, columns[1].with_allow_zero_length(), columns[2]];
    assert!(matches!(
        create(
            directory.target(),
            &[TableRows {
                table: TableSpec {
                    columns: &invalid_option,
                    ..table
                },
                rows
            }]
        ),
        Err(WriteError::Compose(ComposeError::UnsupportedMemoOption))
    ));
    let mut limited = ResourceBudget::new(
        ResourceLimits::default().with_max_allocation_bytes(ByteCount::new(2048)),
    );
    assert!(matches!(
        create_database(
            directory.target(),
            &DatabaseSpec {
                tables: &[TableRows { table, rows }],
                ..DatabaseSpec::default()
            },
            &mut limited
        ),
        Err(WriteError::Compose(_))
    ));
    assert_eq!(fs::read(directory.target())?, original);
    assert_eq!(directory.entries()?, ["created.mdb"]);
    Ok(())
}

#[test]
fn per_column_header_allocation_is_charged_before_row_encoding() -> TestResult {
    let table = TableSpec {
        validation: crate::TableValidation::NONE,
        name: b"Items",
        columns: &[NOTE, ColumnSpec::new(b"Blob", ColumnType::LongBinary)],
        indexes: &[],
    };
    let layout = crate::create::composer::initial_row_layout(&table, &mut budget())?;
    let mut limited =
        ResourceBudget::new(ResourceLimits::default().with_max_allocation_bytes(ByteCount::new(0)));
    let mut output = [0xa5; crate::PAGE_BYTES];
    let mut next = 23;
    assert!(matches!(
        crate::create::composer::encode_initial_row(
            &layout,
            table.columns,
            &[RowValue::Memo(b"a"), RowValue::LongBinary(b"bb")],
            0,
            &mut next,
            &mut output,
            &mut limited,
        ),
        Err(ComposeError::Encoding(
            crate::Error::ResourceLimitExceeded {
                kind: crate::ResourceLimitKind::AllocationBytes,
                ..
            }
        ))
    ));
    assert_eq!(output, [0xa5; crate::PAGE_BYTES]);
    assert_eq!(next, 23);
    Ok(())
}

#[test]
fn combined_external_columns_extend_independent_maps() -> TestResult {
    let directory = TempDir::new("create")?;
    let columns = [NOTE, ColumnSpec::new(b"Blob", ColumnType::LongBinary)];
    let table = table(b"Items", &columns, &[]);
    let payload = vec![b'x'; 2032 * 501];
    let first = RowValue::Memo(&payload[..2032 * 500]);
    create(
        directory.target(),
        &[TableRows {
            table,
            rows: &[&[first, RowValue::LongBinary(&payload[..2032 * 500])]],
        }],
    )?;
    let original = fs::read(directory.target())?;
    assert_eq!(original.len(), 1024 * crate::PAGE_BYTES);
    assert!(map_bit(&original, 21, 2, 522)?);
    assert!(!map_bit(&original, 21, 2, 523)?);
    assert!(map_bit(&original, 21, 4, 523)?);
    assert!(map_bit(&original, 21, 4, 1022)?);
    assert!(map_bit(&original, 21, 0, 1023)?);
    let grown = directory.target().with_file_name("grown.mdb");
    create(
        &grown,
        &[TableRows {
            table,
            rows: &[&[first, RowValue::LongBinary(&payload)]],
        }],
    )?;
    assert!(fs::metadata(grown)?.len() > 1024 * crate::PAGE_BYTES as u64);
    assert_eq!(fs::read(directory.target())?, original);
    Ok(())
}

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
            let directory = TempDir::new("create")?;
            let columns = [ID, ColumnSpec::new(b"Payload", kind)];
            let table = table(b"Items", &columns, &[]);
            let payload = vec![b'a'; length];
            let values = [RowValue::Long(1), payload_value(kind, &payload)];
            let rows: &[&[RowValue<'_>]] = &[&values, &[RowValue::Long(2), RowValue::Null]];
            create(directory.target(), &[TableRows { table, rows }])?;
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
fn empty_ole_creation_has_the_same_storage_as_null() -> TestResult {
    let null = TempDir::new("create")?;
    let empty = TempDir::new("create")?;
    let columns = [ID, ColumnSpec::new(b"Payload", ColumnType::LongBinary)];
    let table = table(b"Items", &columns, &[]);
    for (directory, value) in [(&null, RowValue::Null), (&empty, RowValue::LongBinary(b""))] {
        create(
            directory.target(),
            &[TableRows {
                table,
                rows: &[
                    &[RowValue::Long(1), value],
                    &[RowValue::Long(2), RowValue::LongBinary(b"x")],
                ],
            }],
        )?;
    }
    assert_eq!(fs::read(empty.target())?, fs::read(null.target())?);
    Ok(())
}

#[test]
fn candidate_check_rejects_long_value_owner_pointer_and_payload_corruption() -> TestResult {
    let directory = TempDir::new("create")?;
    let columns = [ColumnSpec::new(b"Payload", ColumnType::LongBinary)];
    let table = table(b"Items", &columns, &[]);
    let payload = [42; 2048];
    let rows: &[&[RowValue<'_>]] = &[&[RowValue::LongBinary(&payload)]];
    create(directory.target(), &[TableRows { table, rows }])?;
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
