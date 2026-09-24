use super::*;

#[test]
fn fixed_only_rows_match_native_minimum_and_bitmap_boundaries()
-> Result<(), Box<dyn std::error::Error>> {
    for (kind, value, expected) in [
        (
            ColumnType::Boolean,
            RowValue::Boolean(true),
            vec![1, 0, 0, 1],
        ),
        (ColumnType::Byte, RowValue::Byte(1), vec![1, 1, 0, 1]),
        (ColumnType::Integer, RowValue::Integer(1), vec![1, 1, 0, 1]),
    ] {
        let columns = [ColumnSpec::new(b"F0", kind)];
        assert_eq!(encode(&layouts(&columns)?, &[value])?, expected);
        read_row(&columns, &expected)?;
        let mut oversized = expected.clone();
        oversized.insert(1, 0);
        assert!(matches!(
            read_row(&columns, &oversized)
                .err()
                .and_then(|e| e.downcast::<crate::RowError>().ok())
                .as_deref(),
            Some(crate::RowError::InvalidFixedBoundary { .. })
        ));
    }
    for count in [1_usize, 8, 9, 16, 17, 24, 25] {
        let names = (0..count).map(|n| format!("F{n}")).collect::<Vec<_>>();
        let columns = names
            .iter()
            .map(|n| ColumnSpec::new(n.as_bytes(), ColumnType::Boolean))
            .collect::<Vec<_>>();
        let values = vec![RowValue::Boolean(true); count];
        let encoded = encode(&layouts(&columns)?, &values)?;
        assert_eq!(encoded.len(), 3 + count.div_ceil(8));
        assert_eq!(&encoded[..3], &[count as u8, 0, 0]);
        read_row(&columns, &encoded)?;
    }
    Ok(())
}

fn read_row(columns: &[ColumnSpec<'_>], encoded: &[u8]) -> Result<(), Box<dyn std::error::Error>> {
    let bytes = database_bytes(columns, &[encoded])?;
    let mut budget = budget_for(&bytes);
    let source = SliceSource::new(&bytes, budget.read_budget())?;
    let mut database = DatabaseReader::from_source(source, &mut budget)?;
    let definition = database.table_definition(PageNumber::new(ROOT as u64), &mut budget)?;
    let mut rows = database.rows(&definition, &mut budget)?;
    let row = rows.next_row()?.ok_or("missing row")?;
    for column in definition.columns() {
        assert!(
            !row.field(column.ordinal())
                .ok_or("missing field")?
                .is_null()
        );
    }
    Ok(())
}
