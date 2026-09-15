use super::*;
use crate::{
    ByteCount, ColumnOrdinal, InlineLongValue, LongValue, LongValueChunkValue, MapRowLocator,
    PageImageError, TableRows, TextCodePage, ValueKind, create_database_with_table_rows,
};

const INDEXES: [IndexSpec<'static>; 3] = [
    IndexSpec {
        name: b"ById",
        fields: &[IndexColumnSpec {
            column: ColumnRef::Name(b"Id"),
            direction: IndexDirection::Ascending,
        }],
        kind: IndexKind::Primary,
    },
    IndexSpec {
        name: b"ByTag",
        fields: &[IndexColumnSpec {
            column: ColumnRef::Name(b"Tag"),
            direction: IndexDirection::Descending,
        }],
        kind: IndexKind::Ordinary,
    },
    IndexSpec {
        name: b"ByPair",
        fields: &[
            IndexColumnSpec {
                column: ColumnRef::Name(b"Id"),
                direction: IndexDirection::Descending,
            },
            IndexColumnSpec {
                column: ColumnRef::Name(b"Tag"),
                direction: IndexDirection::Ascending,
            },
        ],
        kind: IndexKind::Unique,
    },
];

#[test]
fn mixed_columns_indexes_and_generated_ids_keep_independent_payloads_and_maps() -> TestResult {
    let single = [b's'; 33];
    let chained = [b'c'; 4096];
    let full_ole = [0xa5; 2036];
    let chained_ole = [0x5a; 2037];
    for generated in [false, true] {
        let directory = TestDirectory::create()?;
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
        let table = TableSpec {
            name: b"Items",
            columns: &columns,
            indexes: &indexes,
        };
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
                table: TableSpec {
                    name: b"First",
                    columns: &[ID],
                    indexes: &[],
                },
                rows: &[],
            });
        }
        requests.push(TableRows { table, rows: &rows });
        create_database_with_table_rows(directory.target(), &requests, &mut budget())?;
        let original = fs::read(directory.target())?;
        let mut operation = budget();
        let mut database = DatabaseReader::open(directory.target(), &mut operation)?;
        let tables = requests
            .iter()
            .map(|request| request.table)
            .collect::<Vec<_>>();
        let roots =
            crate::creation::api::candidate_table_roots(&mut database, &tables, &mut operation)?;
        let root = roots.last().copied().flatten().ok_or("missing table")?;
        let definition = database.table_definition(root, &mut operation)?;
        assert_eq!(definition.row_count(), 205);
        assert_eq!(definition.long_value_maps().len(), 3);
        assert_eq!(definition.physical_indexes().len(), 3);
        if generated {
            let start = root.get() as usize * crate::PAGE_BYTES;
            assert_eq!(&original[start + 16..start + 20], &205_i32.to_le_bytes());
        }
        let first = crate::creation::composer::initial_payload_start(&table, root, !generated)?;
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
fn map_page_capacity_bounds_long_columns_after_all_index_maps() -> TestResult {
    let names = (0..7)
        .map(|column| format!("Memo{column}").into_bytes())
        .collect::<Vec<_>>();
    let indexes = INDEXES;
    for (index_count, fitting) in [(0, 6), (1, 6), (2, 5), (3, 5)] {
        for long_count in [fitting, fitting + 1] {
            let directory = TestDirectory::create()?;
            let mut columns = vec![ID, ColumnSpec::new(b"Tag", ColumnType::Long)];
            columns.extend(
                names[..long_count]
                    .iter()
                    .map(|name| ColumnSpec::new(name, ColumnType::Memo)),
            );
            let table = TableSpec {
                name: b"Items",
                columns: &columns,
                indexes: &indexes[..index_count],
            };
            let result = create_database(directory.target(), &[table], &mut budget());
            if long_count == fitting {
                result?;
                let bytes = fs::read(directory.target())?;
                assert_eq!(
                    page_rows(&bytes, 21),
                    (2 + index_count + 2 * fitting) as u16
                );
                let mut row = vec![RowValue::Long(1), RowValue::Long(2)];
                row.extend((0..long_count).map(|_| RowValue::Memo(b"a")));
                create_database_with_rows(
                    directory.path.join("populated.mdb"),
                    &table,
                    &[&row],
                    &mut budget(),
                )?;
            } else {
                assert!(matches!(
                    result,
                    Err(CreateDatabaseError::Compose(ComposeError::Page(
                        PageImageError::PageFull { .. }
                    )))
                ));
                assert!(directory.entries()?.is_empty());
            }
        }
    }
    Ok(())
}

#[test]
fn every_external_column_is_checked_and_refusals_preserve_the_destination() -> TestResult {
    let directory = TestDirectory::create()?;
    let columns = [
        NOTE,
        ColumnSpec::new(b"Blob", ColumnType::LongBinary),
        ColumnSpec::new(b"Last", ColumnType::Memo),
    ];
    let table = TableSpec {
        name: b"Items",
        columns: &columns,
        indexes: &[],
    };
    let payloads = [[b'a'; 33], [b'b'; 33], [b'c'; 33]];
    let rows: &[&[RowValue<'_>]] = &[&[
        RowValue::Memo(&payloads[0]),
        RowValue::LongBinary(&payloads[1]),
        RowValue::Memo(&payloads[2]),
    ]];
    create_database_with_rows(directory.target(), &table, rows, &mut budget())?;
    let original = fs::read(directory.target())?;
    for column in 0..3 {
        let mut changed = original.clone();
        changed[(23 + column) * crate::PAGE_BYTES + crate::PAGE_BYTES - 1] ^= 1;
        fs::write(directory.target(), changed)?;
        assert!(matches!(
            crate::creation::api::check_initial_rows(
                &directory.target(),
                &table,
                rows,
                &mut budget()
            ),
            Err(CandidateCheckError::Mismatch {
                detail: "initial long-value payload"
            })
        ));
    }
    let pages = crate::creation::composer::compose_database_with_table_rows(
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
            crate::creation::api::check_long_value_written_pages(
                &directory.target(),
                &[table],
                &pages,
                &mut budget()
            ),
            Err(CandidateCheckError::Mismatch {
                detail: "long-value written page"
            })
        ));
    }
    fs::write(directory.target(), &original)?;
    for rejected in [
        RowValue::Memo(b""),
        RowValue::LongValue(&[0; 12]),
        RowValue::LongBinary(b"wrong type"),
    ] {
        let row = [rows[0][0], rows[0][1], rejected];
        assert!(matches!(
            create_database_with_rows(directory.target(), &table, &[&row], &mut budget()),
            Err(CreateDatabaseError::Compose(_))
        ));
    }
    let opted_in = [NOTE.with_allow_zero_length(), columns[1], columns[2]];
    assert!(matches!(
        create_database_with_rows(
            directory.target(),
            &TableSpec {
                columns: &opted_in,
                ..table
            },
            rows,
            &mut budget()
        ),
        Err(CreateDatabaseError::Compose(
            ComposeError::UnsupportedMemoOption
        ))
    ));
    let mut limited = ResourceBudget::new(
        ResourceLimits::default().with_max_allocation_bytes(ByteCount::new(2048)),
    );
    assert!(matches!(
        create_database_with_rows(directory.target(), &table, rows, &mut limited),
        Err(CreateDatabaseError::Compose(_))
    ));
    assert_eq!(fs::read(directory.target())?, original);
    assert_eq!(directory.entries()?, ["created.mdb"]);
    Ok(())
}

#[test]
fn per_column_header_allocation_is_charged_before_row_encoding() -> TestResult {
    let table = TableSpec {
        name: b"Items",
        columns: &[NOTE, ColumnSpec::new(b"Blob", ColumnType::LongBinary)],
        indexes: &[],
    };
    let layout = crate::creation::composer::initial_row_layout(&table, &mut budget())?;
    let mut limited =
        ResourceBudget::new(ResourceLimits::default().with_max_allocation_bytes(ByteCount::new(0)));
    let mut output = [0xa5; crate::PAGE_BYTES];
    let mut next = 23;
    assert!(matches!(
        crate::creation::composer::encode_initial_row(
            &layout,
            false,
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
fn combined_external_columns_share_the_inline_page_limit() -> TestResult {
    let directory = TestDirectory::create()?;
    let columns = [NOTE, ColumnSpec::new(b"Blob", ColumnType::LongBinary)];
    let table = TableSpec {
        name: b"Items",
        columns: &columns,
        indexes: &[],
    };
    let payload = vec![b'x'; 2032 * 501];
    let first = RowValue::Memo(&payload[..2032 * 500]);
    create_database_with_rows(
        directory.target(),
        &table,
        &[&[first, RowValue::LongBinary(&payload[..2032 * 500])]],
        &mut budget(),
    )?;
    let original = fs::read(directory.target())?;
    assert_eq!(original.len(), 1024 * crate::PAGE_BYTES);
    assert!(map_bit(&original, 21, 2, 522)?);
    assert!(!map_bit(&original, 21, 2, 523)?);
    assert!(map_bit(&original, 21, 4, 523)?);
    assert!(map_bit(&original, 21, 4, 1022)?);
    assert!(map_bit(&original, 21, 0, 1023)?);
    assert!(matches!(
        create_database_with_rows(
            directory.target(),
            &table,
            &[&[first, RowValue::LongBinary(&payload)]],
            &mut budget()
        ),
        Err(CreateDatabaseError::Compose(ComposeError::UsageMap(
            crate::UsageMapWriteError::PageOutOfMap { .. }
        )))
    ));
    assert_eq!(fs::read(directory.target())?, original);
    Ok(())
}
