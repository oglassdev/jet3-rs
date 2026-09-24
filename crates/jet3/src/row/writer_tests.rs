use super::reader_tests::with_rows;
use super::writer::{RowColumnLayout, RowValue, RowWriteError, encode_row};
use crate::{
    ByteCount, ColumnOrdinal, ColumnPhysicalType, ColumnSpec, ColumnStorageClass, ColumnType,
    Error, LongValueMapSpec, MapRowLocator, PAGE_BYTES, PageNumber, ResourceBudget,
    ResourceLimitKind, ResourceLimits, RowError, TableDefinitionKind, TableDefinitionSpec,
    TextCodePage, ValueKind,
    definition::column_writer::nz,
    encode_table_definition,
    row::value::{CurrencyValue, DateTimeValue},
};

use crate::testkit::{TestResult, budget};

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
    let rows: Vec<_> = rows.iter().map(|row| (*row, 0)).collect();
    super::reader_tests::write_rows(page, owner, &rows);
}

/// Builds a one-table database whose data page holds the given rows.
pub(super) fn database_bytes(columns: &[ColumnSpec<'_>], rows: &[&[u8]]) -> TestResult<Vec<u8>> {
    let mut bytes = crate::testkit::database_image(4);
    let map_row = |row| MapRowLocator::new(PageNumber::new(MAP_PAGE as u64), row);
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
            owned: map_row(0),
            available: map_row(1),
        })
        .collect();
    let spec = TableDefinitionSpec {
        kind: TableDefinitionKind::User,
        columns,
        system_column_classes: &[],
        physical_indexes: &[],
        indexes: &[],
        owned_map: map_row(0),
        available_map: map_row(1),
        row_count: rows.len() as u32,
        long_value_maps: &long_value_maps,
    };
    encode_table_definition(
        &spec,
        &mut bytes[ROOT * PAGE_BYTES..(ROOT + 1) * PAGE_BYTES],
        crate::index::key::text::ENCODING_CONTEXT,
        &mut budget(),
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

pub(super) fn layouts(columns: &[ColumnSpec<'_>]) -> TestResult<Vec<RowColumnLayout>> {
    with_rows(&database_bytes(columns, &[])?, |_, definition| {
        Ok(definition
            .columns()
            .iter()
            .map(RowColumnLayout::from)
            .collect())
    })
}

pub(super) fn encode(
    layout: &[RowColumnLayout],
    values: &[RowValue<'_>],
) -> Result<Vec<u8>, RowWriteError> {
    let mut output = vec![0xa5_u8; PAGE_BYTES];
    let length = encode_row(layout, values, &mut output, &mut budget())?;
    output.truncate(length.get() as usize);
    Ok(output)
}

/// Stores `raw` as the only row of a `columns` table and returns its raw
/// fields, `None` for null.
pub(super) fn read_fields(
    columns: &[ColumnSpec<'_>],
    raw: &[u8],
) -> TestResult<Vec<Option<Vec<u8>>>> {
    with_rows(&database_bytes(columns, &[raw])?, |rows, definition| {
        let row = rows.next_row()?.ok_or("missing stored row")?;
        definition
            .columns()
            .iter()
            .map(|column| {
                let field = row.field(column.ordinal()).ok_or("missing field")?;
                Ok(field.raw_bytes().map(<[u8]>::to_vec))
            })
            .collect()
    })
}

/// Stores `raw` as the only row of a `columns` table and returns the
/// reader's refusal.
pub(super) fn read_error(columns: &[ColumnSpec<'_>], raw: &[u8]) -> TestResult<RowError> {
    with_rows(&database_bytes(columns, &[raw])?, |rows, _| {
        Ok(rows.next_row().err().ok_or("corrupt row accepted")?)
    })
}

/// A reader refusal of a variable-offset trailer or boundary.
pub(super) fn is_trailer_error(error: &RowError) -> bool {
    matches!(
        error,
        RowError::UnsupportedWideVariableOffsets { .. }
            | RowError::InvalidFixedBoundary { .. }
            | RowError::InvalidVariableBounds { .. }
    )
}

fn fixed(physical_type: ColumnPhysicalType, offset: u16, size: u16) -> RowColumnLayout {
    RowColumnLayout::new(physical_type, ColumnStorageClass::Fixed { offset }, size)
}

fn text(index: u16, size: u16) -> RowColumnLayout {
    RowColumnLayout::new(
        ColumnPhysicalType::Text,
        ColumnStorageClass::Variable { index },
        size,
    )
}

#[test]
fn round_trips_every_type_and_nulls_through_the_row_decoder() -> TestResult {
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
    let mut sparse = [RowValue::Null; 14];
    sparse[0] = RowValue::Long(1);
    sparse[3] = RowValue::Integer(9);
    sparse[9] = RowValue::Text(b"");
    let full_row = encode(&layout, &full)?;
    let sparse_row = encode(&layout, &sparse)?;
    let bytes = database_bytes(&columns, &[&full_row, &sparse_row])?;
    with_rows(&bytes, |rows, _| {
        {
            let mut row = rows.next_row()?.ok_or("missing full row")?;
            assert_eq!(row.raw_bytes()[0], 14);
            for (ordinal, expected) in [
                (0, ValueKind::Long(-7)),
                (1, ValueKind::Boolean(true)),
                (2, ValueKind::Byte(200)),
                (3, ValueKind::Integer(-2)),
                (4, ValueKind::Currency(CurrencyValue { scaled: 12_345 })),
                (5, ValueKind::Single(1.5)),
                (6, ValueKind::Double(-2.25)),
                (7, ValueKind::DateTime(DateTimeValue { days: 36_526.5 })),
                (8, ValueKind::Binary(&[1, 2, 3])),
            ] {
                let value = row
                    .value(ColumnOrdinal::new(ordinal), TextCodePage::Windows1252)?
                    .ok_or("missing column")?;
                assert_eq!(value.kind(), &expected, "column {ordinal}");
            }
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
            for (ordinal, expected) in [
                (0, ValueKind::Long(1)),
                (1, ValueKind::Boolean(false)),
                (2, ValueKind::Null),
                (3, ValueKind::Integer(9)),
                (8, ValueKind::Null),
                (12, ValueKind::Null),
                (13, ValueKind::Null),
            ] {
                let value = row
                    .value(ColumnOrdinal::new(ordinal), TextCodePage::Windows1252)?
                    .ok_or("missing column")?;
                assert_eq!(value.kind(), &expected, "column {ordinal}");
            }
            assert_eq!(
                row.field(ColumnOrdinal::new(9)).and_then(|f| f.raw_bytes()),
                Some(&b""[..])
            );
        }
        assert!(rows.next_row()?.is_none());
        Ok(())
    })
}

#[test]
fn reproduces_exp_0060_controls_and_wide_single_variable_rows() -> TestResult {
    // EXP-0060 variable-only control: `02 41 42 43 44 45 06 02 01 02 03`.
    assert_eq!(
        encode(
            &[text(0, 50), text(1, 50)],
            &[RowValue::Text(b"ABCDE"), RowValue::Text(b"")]
        )?,
        [
            0x02, 0x41, 0x42, 0x43, 0x44, 0x45, 0x06, 0x06, 0x01, 0x02, 0x03
        ]
    );
    // EXP-0060 mixed control: `03 40 30 20 10 2a 6d 69 78 65 64 0b 06 01 07`.
    let mixed = [
        fixed(ColumnPhysicalType::Long, 0, 4),
        fixed(ColumnPhysicalType::Byte, 4, 1),
        text(0, 50),
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
    let wide = [fixed(ColumnPhysicalType::Long, 0, 4), text(0, 255)];
    let row = encode(&wide, &[RowValue::Long(5), RowValue::Text(&[b'O'; 255])])?;
    assert_eq!(row.len(), 265);
    assert_eq!(&row[260..], &[0x04, 0x05, 0x01, 0x01, 0x03]);
    Ok(())
}

#[test]
fn accepts_the_native_fixed_row_capacity() -> Result<(), RowWriteError> {
    let full = [0x5a_u8; 255];
    let tail = [0xa5_u8; 211];
    let mut layout = vec![fixed(ColumnPhysicalType::Long, 0, 4)];
    layout.extend((0_u16..7).map(|index| fixed(ColumnPhysicalType::Text, 4 + index * 255, 255)));
    layout.push(fixed(ColumnPhysicalType::Text, 4 + 7 * 255, 211));
    let mut values = vec![RowValue::Long(1)];
    values.extend([RowValue::Text(&full); 7]);
    values.push(RowValue::Text(&tail));

    assert_eq!(encode(&layout, &values)?.len(), 2003);
    Ok(())
}

#[test]
fn rejects_mismatches_unsupported_shapes_small_output_and_exhausted_budget() {
    let long = fixed(ColumnPhysicalType::Long, 0, 4);
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
    let mut untouched = [0xa5_u8; 16];
    assert_eq!(
        encode_row(
            &[fixed(ColumnPhysicalType::Long, 0, 1)],
            &[RowValue::Long(0x4433_2211)],
            &mut untouched,
            &mut budget()
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
        encode(&[text(0, 255)], &[RowValue::Text(&[0; 256])]),
        Err(RowWriteError::InvalidWidth {
            ordinal: 0,
            physical_type: ColumnPhysicalType::Text,
            expected: 255,
            actual: 256,
        })
    );
    assert_eq!(
        encode(&[text(1, 255)], &[RowValue::Text(b"")]),
        Err(RowWriteError::InvalidVariableIndex {
            ordinal: 0,
            index: 1,
            variable_count: 1,
        })
    );
    let fixed_text = [0_u8; 255];
    let oversized_layout: Vec<_> = (0..9)
        .map(|index| fixed(ColumnPhysicalType::Text, index * 255, 255))
        .collect();
    assert_eq!(
        encode(
            &oversized_layout,
            &vec![RowValue::Text(&fixed_text); oversized_layout.len()]
        ),
        Err(RowWriteError::RowTooLong {
            length: 2_298,
            maximum: 2003,
        })
    );
    assert_eq!(
        encode(&vec![long; 256], &vec![RowValue::Null; 256]),
        Err(RowWriteError::TooManyColumns {
            count: 256,
            maximum: 255,
        })
    );
    assert_eq!(
        encode_row(&[long], &[RowValue::Long(1)], &mut [0; 5], &mut budget()),
        Err(RowWriteError::OutputTooSmall {
            needed: 6,
            available: 5,
        })
    );
    let mut exhausted =
        ResourceBudget::new(ResourceLimits::default().with_max_encoded_bytes(ByteCount::new(2)));
    assert_eq!(
        encode_row(&[long], &[RowValue::Long(1)], &mut [0; 16], &mut exhausted),
        Err(RowWriteError::Resource(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::EncodedBytes,
            requested: 5,
            maximum: 2,
        }))
    );
}

fn wide_prefix_columns() -> [ColumnSpec<'static>; 3] {
    [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"Value", ColumnType::FixedText { len: nz(255) }),
        ColumnSpec::new(b"Payload", ColumnType::Text { max_len: nz(255) }),
    ]
}

#[test]
fn reproduces_exp_0172_wide_fixed_prefix_rows() -> TestResult {
    let columns = wide_prefix_columns();
    let layout = layouts(&columns)?;
    for (id, payload) in [(1_i32, b"first".as_slice()), (2, b"second"), (3, b"third")] {
        // Independently transcribed EXP-0172 row bytes, including zero jump.
        let mut raw = vec![3];
        raw.extend_from_slice(&id.to_le_bytes());
        raw.extend_from_slice(&[b'a'; 255]);
        raw.extend_from_slice(payload);
        raw.extend_from_slice(&[(4 + payload.len()) as u8, 4, 0, 1, 7]);
        assert_eq!(
            encode(
                &layout,
                &[
                    RowValue::Long(id),
                    RowValue::Text(&[b'a'; 255]),
                    RowValue::Text(payload)
                ]
            )?,
            raw
        );
        let fields = read_fields(&columns, &raw)?;
        assert_eq!(fields[1].as_deref(), Some(&[b'a'; 255][..]));
        assert_eq!(fields[2].as_deref(), Some(payload));
    }
    let raw = encode(
        &layout,
        &[
            RowValue::Long(2),
            RowValue::Text(&[b'a'; 255]),
            RowValue::Text(b"second"),
        ],
    )?;
    for (from_end, value) in [(3, 1), (3, 3), (4, 3), (5, 3), (5, 11)] {
        let mut damaged = raw.clone();
        let offset = damaged.len() - from_end;
        damaged[offset] = value;
        assert!(is_trailer_error(&read_error(&columns, &damaged)?));
    }
    Ok(())
}

#[test]
fn wide_fixed_prefix_crosses_additional_boundary_blocks() -> TestResult {
    let columns = wide_prefix_columns();
    let layout = layouts(&columns)?;
    for size in [0, 251, 252, 255] {
        let payload = vec![b'x'; size];
        let raw = encode(
            &layout,
            &[
                RowValue::Long(2),
                RowValue::Text(&[b'a'; 255]),
                RowValue::Text(&payload),
            ],
        )?;
        let trailer = if size == 0 {
            vec![4, 4, 0, 1, 7]
        } else {
            vec![
                (260 + size) as u8,
                4,
                if size == 251 { 0xff } else { 1 },
                0,
                1,
                7,
            ]
        };
        assert!(raw.ends_with(&trailer));
        assert_eq!(read_fields(&columns, &raw)?[2], Some(payload));
    }
    // A missing second jump must not change the interpreted data boundary.
    let mut raw = vec![3];
    raw.extend_from_slice(&2_i32.to_le_bytes());
    raw.extend_from_slice(&[b'a'; 255]);
    raw.extend_from_slice(&[b'x'; 252]);
    raw.extend_from_slice(&[0, 4, 0, 1, 7]);
    assert!(is_trailer_error(&read_error(&columns, &raw)?));
    Ok(())
}

#[test]
fn boolean_zero_placeholder_does_not_relax_scalar_offsets() -> TestResult {
    let mut columns = [
        fixed(ColumnPhysicalType::Long, 0, 4),
        fixed(ColumnPhysicalType::Boolean, 0, 1),
        fixed(ColumnPhysicalType::Long, 4, 4),
    ];
    let values = [
        RowValue::Long(11),
        RowValue::Boolean(true),
        RowValue::Long(-22),
    ];
    let expected = [3, 11, 0, 0, 0, 234, 255, 255, 255, 7];
    assert_eq!(encode(&columns, &values)?, expected);
    columns[1] = fixed(ColumnPhysicalType::Boolean, 4, 1);
    assert_eq!(encode(&columns, &values)?, expected);
    columns[2] = fixed(ColumnPhysicalType::Long, 0, 4);
    assert_eq!(
        encode(&columns, &values),
        Err(RowWriteError::InvalidFixedOffset {
            ordinal: 2,
            offset: 0,
            expected: 4
        })
    );
    columns[2] = fixed(ColumnPhysicalType::Long, 4, 4);
    columns[1] = fixed(ColumnPhysicalType::Boolean, 2, 1);
    assert_eq!(
        encode(&columns, &values),
        Err(RowWriteError::InvalidFixedOffset {
            ordinal: 1,
            offset: 2,
            expected: 4
        })
    );
    Ok(())
}
