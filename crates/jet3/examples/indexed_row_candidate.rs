//! Deterministic primary, unique, and duplicate-key ordinary DAO candidates.
use jet3::{
    ColumnSpec, ColumnType, IndexColumnSpec, IndexKind, IndexSpec, ResourceBudget, ResourceLimits,
    RowValue, TableSpec, create_database_with_rows,
};
use std::num::NonZeroU8;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args_os().skip(1);
    let path = args
        .next()
        .ok_or("usage: indexed_row_candidate OUTPUT.mdb [primary|unique|ordinary]")?;
    let kind = match args
        .next()
        .as_deref()
        .and_then(|value| value.to_str())
        .unwrap_or("primary")
    {
        "primary" => IndexKind::Primary,
        "unique" => IndexKind::Unique,
        "ordinary" => IndexKind::Ordinary,
        _ => return Err("index kind must be primary, unique, or ordinary".into()),
    };
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(
            b"Payload",
            ColumnType::Text {
                max_len: NonZeroU8::new(255).ok_or("invalid length")?,
            },
        ),
    ];
    let indexes = [IndexSpec {
        name: b"ById",
        fields: &[IndexColumnSpec::ascending(b"Id")],
        kind,
    }];
    let table = TableSpec {
        name: b"Rows",
        columns: &columns,
        indexes: &indexes,
    };
    let payloads = (0_u8..20)
        .map(|position| [b'a' + position; 255])
        .collect::<Vec<_>>();
    let values = payloads
        .iter()
        .enumerate()
        .map(|(position, payload)| {
            let key = if kind == IndexKind::Ordinary {
                9 - (position % 10) as i32
            } else {
                9 - position as i32
            };
            [RowValue::Long(key), RowValue::Text(payload)]
        })
        .collect::<Vec<_>>();
    let rows = values.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    create_database_with_rows(
        path,
        &table,
        &rows,
        &mut ResourceBudget::new(ResourceLimits::default()),
    )?;
    Ok(())
}
