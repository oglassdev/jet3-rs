use super::*;

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

#[test]
fn multiple_variable_trailers_match_native_boundary_vectors()
-> Result<(), Box<dyn std::error::Error>> {
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
        let bytes = database_bytes(&columns, &[&raw])?;
        let mut budget = budget_for(&bytes);
        let source = SliceSource::new(&bytes, budget.read_budget())?;
        let mut db = DatabaseReader::from_source(source, &mut budget)?;
        let table = db.table_definition(PageNumber::new(ROOT as u64), &mut budget)?;
        let mut rows = db.rows(&table, &mut budget)?;
        let row = rows.next_row()?.ok_or("missing wide row")?;
        for (i, payload) in payloads.iter().enumerate() {
            assert_eq!(
                row.field(ColumnOrdinal::new(i as u16 + 1)),
                Some(crate::RawField::Bytes(payload))
            );
        }
    }
    Ok(())
}

#[test]
fn jump_order_and_threshold_corruption_are_rejected() -> Result<(), Box<dyn std::error::Error>> {
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
        let bytes = database_bytes(&columns, &[&damaged])?;
        let mut budget = budget_for(&bytes);
        let source = SliceSource::new(&bytes, budget.read_budget())?;
        let mut db = DatabaseReader::from_source(source, &mut budget)?;
        let table = db.table_definition(PageNumber::new(ROOT as u64), &mut budget)?;
        assert!(db.rows(&table, &mut budget)?.next_row().is_err());
    }
    Ok(())
}

#[test]
fn appended_variable_columns_are_null_in_unchanged_old_rows()
-> Result<(), Box<dyn std::error::Error>> {
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
    let bytes = database_bytes(&columns, &[&raw])?;
    let mut budget = budget_for(&bytes);
    let source = SliceSource::new(&bytes, budget.read_budget())?;
    let mut db = DatabaseReader::from_source(source, &mut budget)?;
    let table = db.table_definition(PageNumber::new(ROOT as u64), &mut budget)?;
    let mut rows = db.rows(&table, &mut budget)?;
    let row = rows.next_row()?.ok_or("missing old row")?;
    assert_eq!(row.raw_bytes(), raw);
    assert_eq!(
        row.field(ColumnOrdinal::new(1)),
        Some(crate::RawField::Bytes(&[b'A'; 255]))
    );
    for ordinal in 3..9 {
        assert_eq!(
            row.field(ColumnOrdinal::new(ordinal)),
            Some(crate::RawField::Null)
        );
    }
    Ok(())
}

#[test]
fn trailer_growth_keeps_ordinal_254_distinct_from_the_unused_marker()
-> Result<(), Box<dyn std::error::Error>> {
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
