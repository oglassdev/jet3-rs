use super::writer_tests::*;
use crate::{
    ColumnPhysicalType, ColumnSpec, ColumnStorageClass, ColumnType, PAGE_BYTES, RowError,
    TableDefinitionKind,
    definition::column_writer::nz,
    row::writer::{RowColumnLayout, RowValue, RowWriteError, encode_row},
};

use crate::testkit::{TestResult, budget};

fn variable_columns(count: usize) -> Vec<ColumnSpec<'static>> {
    let mut columns = vec![ColumnSpec::new(b"Id", ColumnType::Long)];
    const NAMES: [&[u8]; 8] = [b"A", b"B", b"C", b"D", b"E", b"F", b"G", b"H"];
    columns.extend(
        NAMES[..count]
            .iter()
            .map(|name| ColumnSpec::new(name, ColumnType::Text { max_len: nz(255) })),
    );
    columns
}

fn binary_columns() -> [ColumnSpec<'static>; 2] {
    [
        ColumnSpec::new(b"Value", ColumnType::Binary { max_len: nz(255) }),
        ColumnSpec::new(b"Tag", ColumnType::Long),
    ]
}

#[test]
fn multiple_variable_trailers_match_native_boundary_vectors() -> TestResult {
    for (sizes, length, trailer) in [
        (vec![245, 1], 256, vec![251, 250, 5, 2, 7]),
        (vec![246, 1], 258, vec![252, 251, 5, 255, 2, 7]),
        (vec![255, 247], 514, vec![251, 4, 5, 255, 1, 2, 7]),
        (vec![255, 255, 255], 779, vec![2, 3, 4, 5, 3, 2, 1, 3, 15]),
    ] {
        let columns = variable_columns(sizes.len());
        let payloads: Vec<_> = sizes
            .iter()
            .enumerate()
            .map(|(i, &n)| vec![b'A' + i as u8; n])
            .collect();
        let mut values = vec![RowValue::Long(1)];
        values.extend(payloads.iter().map(|p| RowValue::Text(p)));
        let raw = encode(&layouts(&columns)?, &values)?;
        assert_eq!(raw.len(), length);
        assert!(raw.ends_with(&trailer));
        let fields = read_fields(&columns, &raw)?;
        for (field, payload) in fields[1..].iter().zip(&payloads) {
            assert_eq!(field.as_ref(), Some(payload));
        }
    }
    Ok(())
}

#[test]
fn binary_row_boundaries_match_native_trailers() -> TestResult {
    let columns = binary_columns();
    let layout = layouts(&columns)?;
    for (size, trailer) in [
        (247, &[0xfc, 5, 1, 3][..]),
        (248, &[0xfd, 5, 0xff, 1, 3]),
        (249, &[0xfe, 5, 0xff, 1, 3]),
        (253, &[2, 5, 1, 1, 3]),
        (255, &[4, 5, 1, 1, 3]),
    ] {
        let payload = vec![0x5a; size];
        // EXP-0245: independently transcribed physical header and trailer.
        let mut native = vec![2, 1, 0, 0, 0];
        native.extend_from_slice(&payload);
        native.extend_from_slice(trailer);
        assert_eq!(
            encode(&layout, &[RowValue::Binary(&payload), RowValue::Long(1)])?,
            native
        );
        assert_eq!(read_fields(&columns, &native)?[0], Some(payload));
    }
    assert_eq!(
        encode(&layout, &[RowValue::Binary(&[]), RowValue::Long(1)])?,
        encode(&layout, &[RowValue::Null, RowValue::Long(1)])?
    );
    Ok(())
}

#[test]
fn jump_order_ordinal_and_threshold_corruption_are_rejected() -> TestResult {
    let columns = variable_columns(3);
    let raw = encode(
        &layouts(&columns)?,
        &[
            RowValue::Long(1),
            RowValue::Text(&[b'A'; 255]),
            RowValue::Text(&[b'B'; 255]),
            RowValue::Text(&[b'C'; 255]),
        ],
    )?;
    for jumps in [[1, 2, 3], [0, 2, 1], [3, 2, 4], [255, 2, 1]] {
        let mut damaged = raw.clone();
        let start = damaged.len() - 5;
        damaged[start..start + 3].copy_from_slice(&jumps);
        read_error(&columns, &damaged)?;
    }
    let columns = binary_columns();
    for (size, jump) in [(248, 2), (248, 0xfe), (248, 1), (253, 0xff), (247, 0xff)] {
        let mut raw = vec![2, 1, 0, 0, 0];
        raw.extend_from_slice(&vec![0x5a; size]);
        raw.extend_from_slice(&[(size + 5) as u8, 5, jump, 1, 3]);
        assert!(is_trailer_error(&read_error(&columns, &raw)?));
    }
    Ok(())
}

#[test]
fn appended_variable_columns_are_null_in_unchanged_old_rows() -> TestResult {
    let columns = variable_columns(8);
    let mut raw = encode(
        &layouts(&columns[..3])?,
        &[
            RowValue::Long(1),
            RowValue::Text(&[b'A'; 255]),
            RowValue::Text(&[b'B'; 255]),
        ],
    )?;
    *raw.last_mut().ok_or("missing presence byte")? = 0xff;
    let fields = read_fields(&columns, &raw)?;
    assert_eq!(fields[1].as_deref(), Some(&[b'A'; 255][..]));
    assert!(fields[3..].iter().all(Option::is_none));
    Ok(())
}

#[test]
fn trailer_growth_keeps_ordinal_254_distinct_from_the_unused_marker() -> TestResult {
    let mut columns = vec![RowColumnLayout::new(
        ColumnPhysicalType::Long,
        ColumnStorageClass::Fixed { offset: 0 },
        4,
    )];
    columns.extend((0..254).map(|index| {
        RowColumnLayout::new(
            ColumnPhysicalType::Text,
            ColumnStorageClass::Variable { index },
            255,
        )
    }));
    let mut values = vec![RowValue::Null; 255];
    values[0] = RowValue::Long(1);
    let empty = encode(&columns, &values)?;
    assert_eq!(empty.len(), 294);
    assert_eq!(&empty[260..262], &[255, 254]);
    values[254] = RowValue::Text(&[b'A'; 255]);
    let last = encode(&columns, &values)?;
    assert_eq!(last.len(), 550);
    assert_eq!(&last[515..518], &[255, 254, 254]);
    Ok(())
}

#[test]
fn variable_row_capacity_includes_all_trailer_bytes() -> TestResult {
    // EXP-0260: each layout accepts physical length 2012 and rejects 2013.
    for (variables, fixed, data_bytes) in [
        (1, 1748, 251),
        (8, 4, 1988),
        (32, 4, 1961),
        (254, 4, 1712),
        (255, 0, 1715),
    ] {
        let mut columns = Vec::new();
        let mut payloads = Vec::new();
        if fixed > 0 {
            columns.push(RowColumnLayout::new(
                ColumnPhysicalType::Long,
                ColumnStorageClass::Fixed { offset: 0 },
                4,
            ));
            payloads.push(Vec::new());
            let mut offset = 4;
            while offset < fixed {
                let size = (fixed - offset).min(255);
                columns.push(RowColumnLayout::new(
                    ColumnPhysicalType::Text,
                    ColumnStorageClass::Fixed { offset },
                    size,
                ));
                payloads.push(vec![b'F'; usize::from(size)]);
                offset += size;
            }
        }
        let fixed_count = columns.len();
        for index in 0..variables {
            columns.push(RowColumnLayout::new(
                ColumnPhysicalType::Text,
                ColumnStorageClass::Variable { index },
                255,
            ));
            payloads.push(vec![
                b'V';
                data_bytes / usize::from(variables)
                    + usize::from(
                        usize::from(index) < data_bytes % usize::from(variables)
                    )
            ]);
        }
        for extra in 0..=1 {
            if extra == 1 {
                payloads.last_mut().ok_or("missing last field")?.push(b'X');
            }
            let mut values: Vec<_> = payloads.iter().map(|p| RowValue::Text(p)).collect();
            if fixed_count > 0 {
                values[0] = RowValue::Long(1);
            }
            let mut output = [0xa5; PAGE_BYTES];
            let result = encode_row(&columns, &values, &mut output, &mut budget());
            if extra == 0 {
                assert_eq!(result?.get(), 2012);
            } else {
                assert_eq!(
                    result,
                    Err(RowWriteError::RowTooLong {
                        length: 2013,
                        maximum: 2012
                    })
                );
                assert_eq!(output, [0xa5; PAGE_BYTES]);
            }
        }
    }
    Ok(())
}

#[test]
fn schema_minimum_accounts_for_every_jump_byte() -> TestResult {
    let names: Vec<_> = (0..40).map(|i| format!("C{i:02}")).collect();
    for last_fixed in [180, 181] {
        let columns: Vec<_> = names
            .iter()
            .enumerate()
            .map(|(i, name)| {
                ColumnSpec::new(
                    name.as_bytes(),
                    if i < 8 {
                        ColumnType::FixedText {
                            len: nz(if i == 7 { last_fixed } else { 255 }),
                        }
                    } else {
                        ColumnType::Text { max_len: nz(255) }
                    },
                )
            })
            .collect();
        let result = crate::definition::table_layout::validate_column_layout(
            &columns,
            TableDefinitionKind::User,
            &[],
        );
        if last_fixed == 180 {
            assert_eq!(result?, 32);
        } else {
            assert_eq!(
                result,
                Err(crate::TableDefinitionWriteError::RowLayoutTooLarge {
                    minimum: 2013,
                    maximum: 2012
                })
            );
        }
    }
    Ok(())
}

#[test]
fn reader_rejects_a_variable_row_beyond_native_capacity() -> TestResult {
    let columns = variable_columns(8);
    let mut values = vec![RowValue::Long(1)];
    values.extend([RowValue::Text(&[b'X'; 255]); 7]);
    values.push(RowValue::Text(&[b'Y'; 203]));
    let mut raw = encode(&layouts(&columns)?, &values)?;
    assert_eq!(raw.len(), 2012);
    raw.insert(1993, b'Z');
    raw[1994] = (1994 % 256) as u8;
    assert_eq!(
        read_error(&columns, &raw)?,
        RowError::RowTooLong {
            length: 2013,
            maximum: 2012
        }
    );
    Ok(())
}
