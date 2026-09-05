//! Deterministic mixed-table and empty-first initial-row candidates.
use jet3::{
    ColumnSpec, ColumnType, IndexColumnSpec, IndexKind, IndexSpec, ResourceBudget, ResourceLimits,
    RowValue, TableRows, TableSpec, create_database_with_table_rows,
};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args_os().skip(1);
    let path = args
        .next()
        .ok_or("usage: multi_table_row_candidate OUTPUT.mdb mixed|empty-first")?;
    let arm = args.next().ok_or("missing arm")?;
    let id = [ColumnSpec::new(b"Id", ColumnType::Long)];
    let indexes = [IndexSpec {
        name: b"ById",
        fields: &[IndexColumnSpec::ascending(b"Id")],
        kind: IndexKind::Primary,
    }];
    let numbers = (-254..=254)
        .map(|id| [RowValue::Long(id)])
        .collect::<Vec<_>>();
    let first_rows = numbers.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    let memo = (0..4096)
        .map(|offset| b'A' + (offset % 26) as u8)
        .collect::<Vec<_>>();
    let ole = (0..2048)
        .map(|offset| (offset % 256) as u8)
        .collect::<Vec<_>>();
    let empty = TableRows {
        table: TableSpec {
            name: b"Empty",
            columns: &id,
            indexes: &[],
        },
        rows: &[],
    };
    let mut budget = ResourceBudget::new(ResourceLimits::default());
    match arm.to_str() {
        Some("mixed") => create_database_with_table_rows(
            path,
            &[
                TableRows {
                    table: TableSpec {
                        name: b"Numbers",
                        columns: &id,
                        indexes: &[],
                    },
                    rows: &first_rows,
                },
                TableRows {
                    table: TableSpec {
                        name: b"Keys",
                        columns: &id,
                        indexes: &indexes,
                    },
                    rows: &[
                        &[RowValue::Long(3)],
                        &[RowValue::Long(-1)],
                        &[RowValue::Long(2)],
                    ],
                },
                TableRows {
                    table: TableSpec {
                        name: b"Notes",
                        columns: &[ColumnSpec::new(b"Payload", ColumnType::Memo)],
                        indexes: &[],
                    },
                    rows: &[&[RowValue::Memo(&memo)], &[RowValue::Null]],
                },
                empty,
            ],
            &mut budget,
        )?,
        Some("empty-first") => create_database_with_table_rows(
            path,
            &[
                empty,
                TableRows {
                    table: TableSpec {
                        name: b"Binary",
                        columns: &[ColumnSpec::new(b"Payload", ColumnType::LongBinary)],
                        indexes: &[],
                    },
                    rows: &[&[RowValue::LongBinary(&ole)]],
                },
            ],
            &mut budget,
        )?,
        _ => return Err("arm must be mixed or empty-first".into()),
    }
    Ok(())
}
