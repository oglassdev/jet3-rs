//! Deterministic Memo/OLE initial-payload boundary candidates.
use jet3::{
    ColumnSpec, ColumnType, ResourceBudget, ResourceLimits, RowValue, TableSpec,
    create_database_with_rows,
};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args_os().skip(1);
    let path = args
        .next()
        .ok_or("usage: initial_long_value_candidate OUTPUT.mdb memo|ole")?;
    let kind = match args.next().as_deref().and_then(|arg| arg.to_str()) {
        Some("memo") => ColumnType::Memo,
        Some("ole") => ColumnType::LongBinary,
        _ => return Err("payload kind must be memo or ole".into()),
    };
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"Payload", kind),
    ];
    let table = TableSpec {
        name: b"Rows",
        columns: &columns,
        indexes: &[],
    };
    let payloads = [1, 32, 33, 512, 2036, 2037, 2048, 4064, 4096]
        .into_iter()
        .enumerate()
        .map(|(row, length)| {
            (0..length)
                .map(|offset| {
                    if kind == ColumnType::Memo {
                        b'A' + ((offset + row) % 26) as u8
                    } else {
                        ((offset + row) % 256) as u8
                    }
                })
                .collect::<Vec<_>>()
        })
        .collect::<Vec<_>>();
    let mut values = payloads
        .iter()
        .enumerate()
        .map(|(row, payload)| {
            [
                RowValue::Long(row as i32 + 1),
                if kind == ColumnType::Memo {
                    RowValue::Memo(payload)
                } else {
                    RowValue::LongBinary(payload)
                },
            ]
        })
        .collect::<Vec<_>>();
    values.push([RowValue::Long(10), RowValue::Null]);
    let rows = values.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    create_database_with_rows(
        path,
        &table,
        &rows,
        &mut ResourceBudget::new(ResourceLimits::default()),
    )?;
    Ok(())
}
