use super::*;
use std::num::NonZeroU8;

fn replace_and_compare(
    kind: ColumnType,
    initial: RowValue<'_>,
    replacement: RowValue<'_>,
    raw: &[u8],
) -> TestResult {
    let fixture = Fixture::new(
        &[
            ColumnSpec::new(b"Left", ColumnType::Long),
            ColumnSpec::new(b"Target", kind),
            ColumnSpec::new(b"Right", ColumnType::Long),
        ],
        &[&[RowValue::Long(123), initial, RowValue::Long(-456)]],
    )?;
    let row = fixture.locator(0)?;
    let mut original = fs::read(fixture.path())?;
    original[row.page().get() as usize * PAGE_BYTES + 100] = 0xa9;
    original.extend_from_slice(&[0xe7; PAGE_BYTES]);
    fs::write(fixture.path(), &original)?;
    let mut b = budget();
    let mut db = DatabaseReader::open(fixture.path(), &mut b)?;
    let definition = db.table_definition(crate::PageNumber::new(20), &mut b)?;
    let relative = {
        let mut rows = db.rows(&definition, &mut b)?;
        rows.next_row()?
            .ok_or("missing row")?
            .present_fixed_field_range(ColumnOrdinal::new(1))
            .ok_or("missing field")?
    };
    assert_eq!(relative.len(), raw.len());
    let mut page = [0; PAGE_BYTES];
    db.read_raw_page(row.page(), &mut page, &mut b)?;
    let directory = RowDirectory::validate(row.page(), definition.root(), &page, &mut b)?;
    let start = row.page().get() as usize * PAGE_BYTES
        + directory.entry(&page, row.slot())?.range().start
        + relative.start;
    let mut expected = original;
    expected[start..start + raw.len()].copy_from_slice(raw);
    update_field(
        fixture.path(),
        FieldUpdate {
            column: ColumnOrdinal::new(1),
            ..request(row, replacement)
        },
        &mut budget(),
    )?;
    assert_eq!(fs::read(fixture.path())?, expected);
    fixture.assert_only_original()
}

#[test]
fn fixed_scalar_types_preserve_every_surrounding_byte() -> TestResult {
    let guid = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15];
    for (kind, initial, value, bytes) in [
        (
            ColumnType::Byte,
            RowValue::Byte(0),
            RowValue::Byte(u8::MAX),
            vec![255],
        ),
        (
            ColumnType::Integer,
            RowValue::Integer(0),
            RowValue::Integer(i16::MIN),
            i16::MIN.to_le_bytes().to_vec(),
        ),
        (
            ColumnType::Long,
            RowValue::Long(0),
            RowValue::Long(i32::MAX),
            i32::MAX.to_le_bytes().to_vec(),
        ),
        (
            ColumnType::Currency,
            RowValue::Currency { scaled: 0 },
            RowValue::Currency { scaled: i64::MIN },
            i64::MIN.to_le_bytes().to_vec(),
        ),
        (
            ColumnType::Single,
            RowValue::Single(1.0),
            RowValue::Single(-0.0),
            (-0.0_f32).to_le_bytes().to_vec(),
        ),
        (
            ColumnType::Double,
            RowValue::Double(1.0),
            RowValue::Double(-0.0),
            (-0.0_f64).to_le_bytes().to_vec(),
        ),
        (
            ColumnType::DateTime,
            RowValue::DateTime { days: 1.0 },
            RowValue::DateTime { days: -1.25 },
            (-1.25_f64).to_le_bytes().to_vec(),
        ),
        (
            ColumnType::Guid,
            RowValue::Guid([0; 16]),
            RowValue::Guid(guid),
            vec![3, 2, 1, 0, 5, 4, 7, 6, 8, 9, 10, 11, 12, 13, 14, 15],
        ),
    ] {
        replace_and_compare(kind, initial, value, &bytes)?;
    }
    Ok(())
}

#[test]
fn fixed_text_exact_width_boundaries_and_mismatches() -> TestResult {
    for width in [1_u8, 255] {
        let len = NonZeroU8::new(width).ok_or("zero width")?;
        let original = vec![b'a'; usize::from(width)];
        let replacement = vec![0xe9; usize::from(width)];
        replace_and_compare(
            ColumnType::FixedText { len },
            RowValue::Text(&original),
            RowValue::Text(&replacement),
            &replacement,
        )?;
        let fixture = Fixture::new(
            &[ColumnSpec::new(b"Target", ColumnType::FixedText { len })],
            &[&[RowValue::Text(&original)]],
        )?;
        let bytes = fs::read(fixture.path())?;
        for bad in [
            vec![b'b'; usize::from(width) - 1],
            vec![b'b'; usize::from(width) + 1],
        ] {
            assert!(matches!(
                update_field(
                    fixture.path(),
                    request(fixture.locator(0)?, RowValue::Text(&bad)),
                    &mut budget()
                ),
                Err(UpdateError::Encoding(
                    crate::RowWriteError::InvalidWidth { .. }
                ))
            ));
            assert_eq!(fs::read(fixture.path())?, bytes);
            fixture.assert_only_original()?;
        }
    }
    Ok(())
}

#[test]
fn checked_single_field_encoder_rejects_noop_and_invalid_layouts() -> TestResult {
    use crate::{ColumnPhysicalType as T, ColumnStorageClass as S, RowColumnLayout as L};
    for (layout, value) in [
        (L::new(T::Long, S::Fixed { offset: 0 }, 4), RowValue::Null),
        (
            L::new(T::Long, S::Fixed { offset: 0 }, 4),
            RowValue::AutoIncrement,
        ),
        (
            L::new(T::Long, S::Fixed { offset: 0 }, 4),
            RowValue::Byte(1),
        ),
        (
            L::new(T::Long, S::Fixed { offset: 0 }, 5),
            RowValue::Long(1),
        ),
        (
            L::new(T::Long, S::Fixed { offset: u16::MAX }, 4),
            RowValue::Long(1),
        ),
        (
            L::new(T::Boolean, S::Fixed { offset: 0 }, 1),
            RowValue::Boolean(true),
        ),
        (
            L::new(T::Text, S::Variable { index: 0 }, 4),
            RowValue::Text(b"text"),
        ),
        (
            L::new(T::Binary, S::Fixed { offset: 0 }, 4),
            RowValue::Binary(b"data"),
        ),
        (
            L::new(T::Memo, S::Variable { index: 0 }, 0),
            RowValue::LongValue(&[0; 12]),
        ),
    ] {
        let mut output = [0xa5; 16];
        assert!(
            crate::row_writer::encode_present_fixed_field(
                3,
                layout,
                value,
                &mut output,
                &mut budget()
            )
            .is_err()
        );
        assert_eq!(output, [0xa5; 16]);
    }
    let mut output = [0xa5; 3];
    assert!(matches!(
        crate::row_writer::encode_present_fixed_field(
            3,
            L::new(T::Long, S::Fixed { offset: 7 }, 4),
            RowValue::Long(1),
            &mut output,
            &mut budget()
        ),
        Err(crate::RowWriteError::OutputTooSmall { .. })
    ));
    assert_eq!(output, [0xa5; 3]);
    Ok(())
}

#[test]
fn wide_fixed_text_with_variable_payload_preserves_all_other_bytes() -> TestResult {
    let fixture = Fixture::new(
        &[
            ColumnSpec::new(b"Id", ColumnType::Long),
            ColumnSpec::new(
                b"Target",
                ColumnType::FixedText {
                    len: NonZeroU8::MAX,
                },
            ),
            ColumnSpec::new(
                b"Payload",
                ColumnType::Text {
                    max_len: NonZeroU8::MAX,
                },
            ),
        ],
        &[
            &[
                RowValue::Long(1),
                RowValue::Text(&[b'a'; 255]),
                RowValue::Text(b"first"),
            ],
            &[
                RowValue::Long(2),
                RowValue::Text(&[b'a'; 255]),
                RowValue::Text(b"second"),
            ],
        ],
    )?;
    let row = fixture.locator(1)?;
    let original = fs::read(fixture.path())?;
    let page_start = row.page().get() as usize * PAGE_BYTES;
    let slot_offset = page_start + 10 + usize::from(row.slot()) * 2;
    let row_start = usize::from(u16::from_le_bytes([
        original[slot_offset],
        original[slot_offset + 1],
    ]));
    let mut expected = original;
    expected[page_start + row_start + 5..page_start + row_start + 260].fill(0xe9);
    update_field(
        fixture.path(),
        FieldUpdate {
            column: ColumnOrdinal::new(1),
            ..request(row, RowValue::Text(&[0xe9; 255]))
        },
        &mut budget(),
    )?;
    assert_eq!(fs::read(fixture.path())?, expected);
    fixture.assert_only_original()
}
