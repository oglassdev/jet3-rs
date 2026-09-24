use super::writer_tests::*;
use crate::{ColumnSpec, ColumnType, RowError, row::writer::RowValue};

use crate::testkit::TestResult;

/// Every field of a fixed-only row decodes as present.
fn assert_all_present(columns: &[ColumnSpec<'_>], encoded: &[u8]) -> TestResult {
    assert!(read_fields(columns, encoded)?.iter().all(Option::is_some));
    Ok(())
}

#[test]
fn fixed_only_rows_match_native_minimum_and_bitmap_boundaries() -> TestResult {
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
        assert_all_present(&columns, &expected)?;
        let mut oversized = expected.clone();
        oversized.insert(1, 0);
        assert!(matches!(
            read_error(&columns, &oversized)?,
            RowError::InvalidFixedBoundary { .. }
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
        assert_all_present(&columns, &encoded)?;
    }
    Ok(())
}
