//! Scalar row values shared by index mutation and validation (EXP-0061/0150/0248).
use crate::{ColumnOrdinal, RowValue, RowView, TextCodePage, UpdateError, ValueKind};

pub(crate) fn read<'value>(
    row: &mut RowView<'value, '_>,
    columns: &[bool; u8::MAX as usize],
) -> Result<[RowValue<'value>; u8::MAX as usize], UpdateError> {
    let mut values = [RowValue::Null; u8::MAX as usize];
    for (ordinal, selected) in columns.iter().enumerate() {
        if !selected {
            continue;
        }
        values[ordinal] = read_column(row, ColumnOrdinal::new(ordinal as u16))?;
    }
    Ok(values)
}

pub(crate) fn read_column<'value>(
    row: &mut RowView<'value, '_>,
    column: ColumnOrdinal,
) -> Result<RowValue<'value>, UpdateError> {
    let value = row
        .value(column, TextCodePage::Windows1252)?
        .ok_or(UpdateError::NotFound("index key column"))?;
    Ok(match value.kind() {
        ValueKind::Null => RowValue::Null,
        ValueKind::Boolean(v) => RowValue::Boolean(*v),
        ValueKind::Byte(v) => RowValue::Byte(*v),
        ValueKind::Integer(v) => RowValue::Integer(*v),
        ValueKind::Long(v) => RowValue::Long(*v),
        ValueKind::Currency(v) => RowValue::Currency { scaled: v.scaled() },
        ValueKind::Single(v) => RowValue::Single(*v),
        ValueKind::Double(v) => RowValue::Double(*v),
        ValueKind::DateTime(v) => RowValue::DateTime { days: v.days() },
        ValueKind::Binary(_) => RowValue::Binary(
            row.field(column)
                .and_then(|field| field.raw_bytes())
                .ok_or(UpdateError::Mismatch("missing binary key bytes"))?,
        ),
        ValueKind::Text(_) => RowValue::Text(
            row.field(column)
                .and_then(|field| field.raw_bytes())
                .ok_or(UpdateError::Mismatch("missing text key bytes"))?,
        ),
        ValueKind::Guid(value) => RowValue::Guid(value.display_bytes()),
        _ => return Err(UpdateError::Unsupported("non-numeric index value")),
    })
}
