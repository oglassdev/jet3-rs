use super::*;

fn columns() -> [ColumnSpec<'static>; 2] {
    [
        ColumnSpec::new(b"Value", ColumnType::Binary { max_len: nz(255) }),
        ColumnSpec::new(b"Tag", ColumnType::Long),
    ]
}

#[test]
fn binary_row_boundaries_match_native_trailers() -> Result<(), Box<dyn std::error::Error>> {
    let columns = columns();
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
        let image = database_bytes(&columns, &[&native])?;
        let mut budget = budget_for(&image);
        let source = SliceSource::new(&image, budget.read_budget())?;
        let mut database = DatabaseReader::from_source(source, &mut budget)?;
        let table = database.table_definition(PageNumber::new(ROOT as u64), &mut budget)?;
        let mut rows = database.rows(&table, &mut budget)?;
        assert_eq!(
            rows.next_row()?
                .ok_or("missing row")?
                .field(ColumnOrdinal::new(0)),
            Some(crate::RawField::Bytes(payload.as_slice()))
        );
    }
    assert_eq!(
        encode(&layout, &[RowValue::Binary(&[]), RowValue::Long(1)])?,
        encode(&layout, &[RowValue::Null, RowValue::Long(1)])?
    );
    Ok(())
}

#[test]
fn binary_row_rejects_invalid_jump_ordinals_and_bounds() -> Result<(), Box<dyn std::error::Error>> {
    let columns = columns();
    for (size, jump) in [(248, 2), (248, 0xfe), (248, 1), (253, 0xff), (247, 0xff)] {
        let mut raw = vec![2, 1, 0, 0, 0];
        raw.extend_from_slice(&vec![0x5a; size]);
        raw.extend_from_slice(&[(size + 5) as u8, 5, jump, 1, 3]);
        let image = database_bytes(&columns, &[&raw])?;
        let mut budget = budget_for(&image);
        let source = SliceSource::new(&image, budget.read_budget())?;
        let mut database = DatabaseReader::from_source(source, &mut budget)?;
        let table = database.table_definition(PageNumber::new(ROOT as u64), &mut budget)?;
        let mut rows = database.rows(&table, &mut budget)?;
        assert!(matches!(
            rows.next_row(),
            Err(crate::RowError::UnsupportedWideVariableOffsets { .. }
                | crate::RowError::InvalidFixedBoundary { .. }
                | crate::RowError::InvalidVariableBounds { .. })
        ));
    }
    Ok(())
}
