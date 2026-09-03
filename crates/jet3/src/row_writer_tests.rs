use super::{RowColumnLayout, RowValue, RowWriteError, encode_row};
use crate::column_definition_writer::nz;
use crate::{
    ByteCount, ColumnOrdinal, ColumnPhysicalType, ColumnSpec, ColumnStorageClass, ColumnType,
    DatabaseReader, Error, JET3_PAGE_SIZE, LongValueMapSpec, MapRowLocator, PageNumber, ReadLimits,
    ResourceBudget, ResourceLimitKind, ResourceLimits, SliceSource, TableDefinitionKind,
    TableDefinitionSpec, TextCodePage, ValueKind, encode_table_definition,
};

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const ROOT: usize = 1;
const MAP_PAGE: usize = 2;
const DATA_PAGE: usize = 3;

fn all_type_columns() -> Vec<ColumnSpec<'static>> {
    vec![
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"Flag", ColumnType::Boolean),
        ColumnSpec::new(b"Small", ColumnType::Byte),
        ColumnSpec::new(b"Short", ColumnType::Integer),
        ColumnSpec::new(b"Money", ColumnType::Currency),
        ColumnSpec::new(b"Ratio", ColumnType::Single),
        ColumnSpec::new(b"Precise", ColumnType::Double),
        ColumnSpec::new(b"When", ColumnType::DateTime),
        ColumnSpec::new(b"Blob", ColumnType::Binary { max_len: nz(16) }),
        ColumnSpec::new(b"Name", ColumnType::Text { max_len: nz(50) }),
        ColumnSpec::new(b"Code", ColumnType::FixedText { len: nz(3) }),
        ColumnSpec::new(b"Ole", ColumnType::LongBinary),
        ColumnSpec::new(b"Notes", ColumnType::Memo),
        ColumnSpec::new(b"Rid", ColumnType::Guid),
    ]
}

fn write_rows(page: &mut [u8], owner: u32, rows: &[&[u8]]) {
    page[0] = 1;
    page[4..8].copy_from_slice(&owner.to_le_bytes());
    page[8..10].copy_from_slice(&(rows.len() as u16).to_le_bytes());
    let mut start = PAGE_BYTES;
    for (index, row) in rows.iter().enumerate() {
        start -= row.len();
        page[10 + 2 * index..12 + 2 * index].copy_from_slice(&(start as u16).to_le_bytes());
        page[start..start + row.len()].copy_from_slice(row);
    }
}

/// Builds a one-table database whose data page holds the given rows.
fn database_bytes(
    columns: &[ColumnSpec<'_>],
    rows: &[&[u8]],
) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
    let mut bytes = vec![0_u8; 4 * PAGE_BYTES];
    bytes[4..19].copy_from_slice(b"Standard Jet DB");
    bytes[0x41] = 0x4e;
    bytes[0x42..0x50].copy_from_slice(&[
        0x86, 0xfb, 0xec, 0x37, 0x5d, 0x44, 0x9c, 0xfa, 0xc6, 0x5e, 0x28, 0xe6, 0x13, 0xb6,
    ]);
    // One typed long-value map group per Memo or LongBinary column, reusing
    // the two table map rows.
    let long_value_maps: Vec<LongValueMapSpec> = columns
        .iter()
        .enumerate()
        .filter(|(_, column)| {
            matches!(
                column.physical_type(),
                ColumnPhysicalType::Memo | ColumnPhysicalType::LongBinary
            )
        })
        .map(|(ordinal, _)| LongValueMapSpec {
            column: ordinal as u16,
            owned: MapRowLocator::new(PageNumber::new(MAP_PAGE as u64), 0),
            available: MapRowLocator::new(PageNumber::new(MAP_PAGE as u64), 1),
        })
        .collect();
    let spec = TableDefinitionSpec {
        kind: TableDefinitionKind::User,
        columns,
        system_column_classes: &[],
        physical_indexes: &[],
        indexes: &[],
        owned_map: MapRowLocator::new(PageNumber::new(MAP_PAGE as u64), 0),
        available_map: MapRowLocator::new(PageNumber::new(MAP_PAGE as u64), 1),
        row_count: rows.len() as u32,
        long_value_maps: &long_value_maps,
    };
    let mut budget = ResourceBudget::new(ResourceLimits::default());
    encode_table_definition(
        &spec,
        &mut bytes[ROOT * PAGE_BYTES..(ROOT + 1) * PAGE_BYTES],
        &mut budget,
    )?;
    let owned = [0, 0, 0, 0, 0, 1 << DATA_PAGE];
    let available = [0, 0, 0, 0, 0];
    write_rows(
        &mut bytes[MAP_PAGE * PAGE_BYTES..(MAP_PAGE + 1) * PAGE_BYTES],
        0,
        &[&owned, &available],
    );
    write_rows(
        &mut bytes[DATA_PAGE * PAGE_BYTES..(DATA_PAGE + 1) * PAGE_BYTES],
        ROOT as u32,
        rows,
    );
    Ok(bytes)
}

fn layouts(columns: &[ColumnSpec<'_>]) -> Result<Vec<RowColumnLayout>, Box<dyn std::error::Error>> {
    let bytes = database_bytes(columns, &[])?;
    let definition = open_definition(&bytes)?;
    Ok(definition
        .columns()
        .iter()
        .map(RowColumnLayout::from)
        .collect())
}

fn open_definition(bytes: &[u8]) -> Result<crate::TableDefinition, Box<dyn std::error::Error>> {
    let mut budget = budget_for(bytes);
    let source = SliceSource::new(bytes, budget.read_budget())?;
    let mut database = DatabaseReader::from_source(source, &mut budget)?;
    Ok(database.table_definition(PageNumber::new(ROOT as u64), &mut budget)?)
}

fn budget_for(bytes: &[u8]) -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::new(ReadLimits::new(
        ByteCount::new(bytes.len() as u64),
        JET3_PAGE_SIZE,
        ByteCount::new(u64::MAX),
    )))
}

fn encode(layout: &[RowColumnLayout], values: &[RowValue<'_>]) -> Result<Vec<u8>, RowWriteError> {
    let mut output = vec![0xa5_u8; PAGE_BYTES];
    let mut budget = ResourceBudget::new(ResourceLimits::default());
    let length = encode_row(layout, values, &mut output, &mut budget)?;
    output.truncate(length.get() as usize);
    Ok(output)
}

#[test]
fn round_trips_every_type_and_nulls_through_the_row_decoder()
-> Result<(), Box<dyn std::error::Error>> {
    let columns = all_type_columns();
    let layout = layouts(&columns)?;
    let guid = [
        0x01, 0x23, 0x45, 0x67, 0x89, 0xab, 0xcd, 0xef, 0x10, 0x32, 0x54, 0x76, 0x98, 0xba, 0xdc,
        0xfe,
    ];
    let mut memo = 0x8000_0005_u32.to_le_bytes().to_vec();
    memo.extend_from_slice(&[0; 8]);
    memo.extend_from_slice(b"hello");
    let full = [
        RowValue::Long(-7),
        RowValue::Boolean(true),
        RowValue::Byte(200),
        RowValue::Integer(-2),
        RowValue::Currency { scaled: 12_345 },
        RowValue::Single(1.5),
        RowValue::Double(-2.25),
        RowValue::DateTime { days: 36_526.5 },
        RowValue::Binary(&[1, 2, 3]),
        RowValue::Text(b"Caf\xe9 \x80"),
        RowValue::Text(b"ABC"),
        RowValue::LongValue(&memo),
        RowValue::LongValue(&memo),
        RowValue::Guid(guid),
    ];
    let sparse = [
        RowValue::Long(1),
        RowValue::Null,
        RowValue::Null,
        RowValue::Integer(9),
        RowValue::Null,
        RowValue::Null,
        RowValue::Null,
        RowValue::Null,
        RowValue::Null,
        RowValue::Text(b""),
        RowValue::Null,
        RowValue::Null,
        RowValue::Null,
        RowValue::Null,
    ];
    let full_row = encode(&layout, &full)?;
    let sparse_row = encode(&layout, &sparse)?;
    let bytes = database_bytes(&columns, &[&full_row, &sparse_row])?;
    let definition = open_definition(&bytes)?;
    let mut budget = budget_for(&bytes);
    let source = SliceSource::new(&bytes, budget.read_budget())?;
    let mut database = DatabaseReader::from_source(source, &mut budget)?;
    let mut rows = database.rows(&definition, &mut budget)?;

    let kind = |row: &mut crate::RowView<'_, '_>,
                ordinal: u16|
     -> Result<String, Box<dyn std::error::Error>> {
        let value = row
            .value(ColumnOrdinal::new(ordinal), TextCodePage::Windows1252)?
            .ok_or("missing column")?;
        Ok(format!("{:?}", value.kind()))
    };
    {
        let mut row = rows.next_row()?.ok_or("missing full row")?;
        assert_eq!(row.raw_bytes()[0], 14);
        assert_eq!(kind(&mut row, 0)?, format!("{:?}", ValueKind::Long(-7)));
        assert_eq!(
            kind(&mut row, 1)?,
            format!("{:?}", ValueKind::Boolean(true))
        );
        assert_eq!(kind(&mut row, 2)?, format!("{:?}", ValueKind::Byte(200)));
        assert_eq!(kind(&mut row, 3)?, format!("{:?}", ValueKind::Integer(-2)));
        assert!(kind(&mut row, 4)?.contains("scaled: 12345"));
        assert_eq!(kind(&mut row, 5)?, format!("{:?}", ValueKind::Single(1.5)));
        assert_eq!(
            kind(&mut row, 6)?,
            format!("{:?}", ValueKind::Double(-2.25))
        );
        assert!(kind(&mut row, 7)?.contains("days: 36526.5"));
        assert_eq!(
            kind(&mut row, 8)?,
            format!("{:?}", ValueKind::Binary(&[1, 2, 3]))
        );
        let text = row
            .value(ColumnOrdinal::new(9), TextCodePage::Windows1252)?
            .ok_or("missing text")?;
        let ValueKind::Text(text) = text.kind() else {
            return Err("expected text".into());
        };
        assert_eq!(text.as_str(), "Café €");
        let fixed_text = row
            .value(ColumnOrdinal::new(10), TextCodePage::Windows1252)?
            .ok_or("missing fixed text")?;
        assert_eq!(fixed_text.raw_bytes(), Some(&b"ABC"[..]));
        assert_eq!(
            row.field(ColumnOrdinal::new(11))
                .and_then(|f| f.raw_bytes()),
            Some(&memo[..])
        );
        let ole = row
            .value(ColumnOrdinal::new(12), TextCodePage::Windows1252)?
            .ok_or("missing memo")?;
        assert!(format!("{:?}", ole.kind()).contains("hello"));
        let rid = row
            .value(ColumnOrdinal::new(13), TextCodePage::Windows1252)?
            .ok_or("missing guid")?;
        let ValueKind::Guid(rid) = rid.kind() else {
            return Err("expected guid".into());
        };
        assert_eq!(rid.display_bytes(), guid);
    }
    {
        let mut row = rows.next_row()?.ok_or("missing sparse row")?;
        assert_eq!(kind(&mut row, 0)?, format!("{:?}", ValueKind::Long(1)));
        assert_eq!(
            kind(&mut row, 1)?,
            format!("{:?}", ValueKind::Boolean(false))
        );
        assert_eq!(kind(&mut row, 2)?, format!("{:?}", ValueKind::Null));
        assert_eq!(kind(&mut row, 3)?, format!("{:?}", ValueKind::Integer(9)));
        assert_eq!(kind(&mut row, 8)?, format!("{:?}", ValueKind::Null));
        assert_eq!(
            row.field(ColumnOrdinal::new(9)).and_then(|f| f.raw_bytes()),
            Some(&b""[..])
        );
        assert_eq!(kind(&mut row, 12)?, format!("{:?}", ValueKind::Null));
        assert_eq!(kind(&mut row, 13)?, format!("{:?}", ValueKind::Null));
    }
    assert!(rows.next_row()?.is_none());
    Ok(())
}

#[test]
fn reproduces_exp_0060_controls_and_wide_single_variable_rows()
-> Result<(), Box<dyn std::error::Error>> {
    // EXP-0060 variable-only control: `02 41 42 43 44 45 06 02 01 02 03`.
    let variable_only = [
        RowColumnLayout::new(
            ColumnPhysicalType::Text,
            ColumnStorageClass::Variable { index: 0 },
            50,
        ),
        RowColumnLayout::new(
            ColumnPhysicalType::Text,
            ColumnStorageClass::Variable { index: 1 },
            50,
        ),
    ];
    assert_eq!(
        encode(
            &variable_only,
            &[RowValue::Text(b"ABCDE"), RowValue::Text(b"")]
        )?,
        [
            0x02, 0x41, 0x42, 0x43, 0x44, 0x45, 0x06, 0x06, 0x01, 0x02, 0x03
        ]
    );
    // EXP-0060 mixed control: `03 40 30 20 10 2a 6d 69 78 65 64 0b 06 01 07`.
    let mixed = [
        RowColumnLayout::new(
            ColumnPhysicalType::Long,
            ColumnStorageClass::Fixed { offset: 0 },
            4,
        ),
        RowColumnLayout::new(
            ColumnPhysicalType::Byte,
            ColumnStorageClass::Fixed { offset: 4 },
            1,
        ),
        RowColumnLayout::new(
            ColumnPhysicalType::Text,
            ColumnStorageClass::Variable { index: 0 },
            50,
        ),
    ];
    assert_eq!(
        encode(
            &mixed,
            &[
                RowValue::Long(0x1020_3040),
                RowValue::Byte(0x2a),
                RowValue::Text(b"mixed")
            ]
        )?,
        [
            0x03, 0x40, 0x30, 0x20, 0x10, 0x2a, 0x6d, 0x69, 0x78, 0x65, 0x64, 0x0b, 0x06, 0x01,
            0x07
        ]
    );
    // EXP-0060 265-byte overflow target: low bytes `04 05`, jump `01`, count, presence `03`.
    let wide = [
        RowColumnLayout::new(
            ColumnPhysicalType::Long,
            ColumnStorageClass::Fixed { offset: 0 },
            4,
        ),
        RowColumnLayout::new(
            ColumnPhysicalType::Text,
            ColumnStorageClass::Variable { index: 0 },
            255,
        ),
    ];
    let row = encode(&wide, &[RowValue::Long(5), RowValue::Text(&[b'O'; 255])])?;
    assert_eq!(row.len(), 265);
    assert_eq!(&row[260..], &[0x04, 0x05, 0x01, 0x01, 0x03]);
    Ok(())
}

#[test]
fn accepts_the_absolute_maximum_single_page_row() -> Result<(), RowWriteError> {
    let full = [0x5a_u8; 255];
    let tail = [0xa5_u8; 249];
    let mut layout: Vec<_> = (0_u16..7)
        .map(|index| {
            RowColumnLayout::new(
                ColumnPhysicalType::Text,
                ColumnStorageClass::Fixed {
                    offset: index * 255,
                },
                255,
            )
        })
        .collect();
    layout.push(RowColumnLayout::new(
        ColumnPhysicalType::Text,
        ColumnStorageClass::Fixed { offset: 7 * 255 },
        249,
    ));
    let mut values = vec![RowValue::Text(&full); 7];
    values.push(RowValue::Text(&tail));

    assert_eq!(encode(&layout, &values)?.len(), PAGE_BYTES - 12);
    Ok(())
}

#[test]
fn rejects_mismatches_unsupported_shapes_small_output_and_exhausted_budget() {
    let long = RowColumnLayout::new(
        ColumnPhysicalType::Long,
        ColumnStorageClass::Fixed { offset: 0 },
        4,
    );
    let text = |index| {
        RowColumnLayout::new(
            ColumnPhysicalType::Text,
            ColumnStorageClass::Variable { index },
            255,
        )
    };
    assert_eq!(
        encode(&[long], &[RowValue::Byte(1)]),
        Err(RowWriteError::TypeMismatch {
            ordinal: 0,
            physical_type: ColumnPhysicalType::Long,
        })
    );
    assert_eq!(
        encode(&[long], &[]),
        Err(RowWriteError::ValueCountMismatch {
            expected: 1,
            actual: 0,
        })
    );
    let invalid_size = RowColumnLayout::new(
        ColumnPhysicalType::Long,
        ColumnStorageClass::Fixed { offset: 0 },
        1,
    );
    let mut untouched = [0xa5_u8; 16];
    assert_eq!(
        encode_row(
            &[invalid_size],
            &[RowValue::Long(0x4433_2211)],
            &mut untouched,
            &mut ResourceBudget::new(ResourceLimits::default())
        ),
        Err(RowWriteError::InvalidColumnSize {
            ordinal: 0,
            physical_type: ColumnPhysicalType::Long,
            size: 1,
        })
    );
    assert_eq!(untouched, [0xa5; 16]);
    assert_eq!(
        encode(&[long, long], &[RowValue::Long(1), RowValue::Long(2)]),
        Err(RowWriteError::InvalidFixedOffset {
            ordinal: 1,
            offset: 0,
            expected: 4,
        })
    );
    assert_eq!(
        encode(&[text(0)], &[RowValue::Text(&[0; 256])]),
        Err(RowWriteError::InvalidWidth {
            ordinal: 0,
            physical_type: ColumnPhysicalType::Text,
            expected: 255,
            actual: 256,
        })
    );
    assert_eq!(
        encode(&[text(1)], &[RowValue::Text(b"")]),
        Err(RowWriteError::InvalidVariableIndex {
            ordinal: 0,
            index: 1,
            variable_count: 1,
        })
    );
    assert_eq!(
        encode(
            &[text(0), text(1)],
            &[RowValue::Text(&[0; 200]), RowValue::Text(&[0; 100])]
        ),
        Err(RowWriteError::UnsupportedWideVariableOffsets {
            variable_count: 2,
            row_length: 306,
        })
    );
    let fixed_text = [0_u8; 255];
    let oversized_layout: Vec<_> = (0..9)
        .map(|index| {
            RowColumnLayout::new(
                ColumnPhysicalType::Text,
                ColumnStorageClass::Fixed {
                    offset: index * 255,
                },
                255,
            )
        })
        .collect();
    assert_eq!(
        encode(
            &oversized_layout,
            &vec![RowValue::Text(&fixed_text); oversized_layout.len()]
        ),
        Err(RowWriteError::RowTooLong {
            length: 2_298,
            maximum: PAGE_BYTES - 12,
        })
    );
    let many = vec![long; 256];
    assert_eq!(
        encode(&many, &vec![RowValue::Null; 256]),
        Err(RowWriteError::TooManyColumns {
            count: 256,
            maximum: 255,
        })
    );
    let mut small = [0_u8; 5];
    let mut budget = ResourceBudget::new(ResourceLimits::default());
    assert_eq!(
        encode_row(&[long], &[RowValue::Long(1)], &mut small, &mut budget),
        Err(RowWriteError::OutputTooSmall {
            needed: 6,
            available: 5,
        })
    );
    let mut output = [0_u8; 16];
    let mut exhausted =
        ResourceBudget::new(ResourceLimits::default().with_max_encoded_bytes(ByteCount::new(2)));
    assert_eq!(
        encode_row(&[long], &[RowValue::Long(1)], &mut output, &mut exhausted),
        Err(RowWriteError::Resource(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::EncodedBytes,
            requested: 5,
            maximum: 2,
        }))
    );
    assert!(
        RowWriteError::TooManyColumns {
            count: 0,
            maximum: 0
        }
        .to_string()
        .contains("row encoding failed")
    );
}
