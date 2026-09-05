//! Deterministic nullable Long candidates; validation is separately preregistered.
use jet3::{
    ColumnSpec, ColumnType, IndexColumnSpec, IndexKind, IndexNullPolicy, IndexSpec, ResourceBudget,
    ResourceLimits, RowValue, TableRows, TableSpec, create_database_with_table_rows,
};
use std::{env, error::Error};

fn main() -> Result<(), Box<dyn Error>> {
    let args: Vec<_> = env::args().skip(1).collect();
    let [mode, path] = args.as_slice() else {
        return Err("usage: nullable_index_candidate MODE OUTPUT.mdb".into());
    };
    let (kind, composite, generated, count) = match mode.as_str() {
        "unique" => (IndexKind::Unique, false, false, 12),
        "ignore" => (
            IndexKind::Unique.with_null_policy(IndexNullPolicy::IgnoreAllNull),
            false,
            false,
            12,
        ),
        "required" => (
            IndexKind::Ordinary.with_null_policy(IndexNullPolicy::Required),
            false,
            false,
            12,
        ),
        "composite" => (IndexKind::Unique, true, false, 30_000),
        "composite-ignore" => (
            IndexKind::Ordinary.with_null_policy(IndexNullPolicy::IgnoreAllNull),
            true,
            false,
            1200,
        ),
        "auto" => (IndexKind::Unique, true, true, 1200),
        _ => return Err("unknown mode".into()),
    };
    let columns = [
        ColumnSpec::new(b"A", ColumnType::Long),
        ColumnSpec::new(
            b"B",
            if generated {
                ColumnType::AutoIncrement
            } else {
                ColumnType::Long
            },
        ),
        ColumnSpec::new(b"Payload", ColumnType::Long),
    ];
    let fields = [
        IndexColumnSpec::ascending(0),
        IndexColumnSpec::descending(1),
    ];
    let indexes = [IndexSpec {
        name: b"ByKey",
        kind,
        fields: &fields[..if composite { 2 } else { 1 }],
    }];
    let values: Vec<_> = (0..count)
        .map(|n| {
            let (a, b) = if kind.null_policy() == IndexNullPolicy::Required {
                (RowValue::Long(n % 3), RowValue::Long(-n))
            } else if composite {
                match n % 4 {
                    0 => (RowValue::Null, RowValue::Null),
                    1 => (RowValue::Null, RowValue::Long(1)),
                    2 => (RowValue::Long(1), RowValue::Null),
                    _ => (RowValue::Long(n), RowValue::Long(-n)),
                }
            } else {
                (
                    if n % 3 == 0 {
                        RowValue::Null
                    } else {
                        RowValue::Long(n - 6)
                    },
                    RowValue::Long(-n),
                )
            };
            [
                a,
                if generated {
                    RowValue::AutoIncrement
                } else {
                    b
                },
                RowValue::Long(n),
            ]
        })
        .collect();
    let rows: Vec<_> = values.iter().map(|row| row.as_slice()).collect();
    let empty_columns = [ColumnSpec::new(b"Id", ColumnType::Long)];
    let tables = [
        TableRows {
            table: TableSpec {
                name: b"Empty",
                columns: &empty_columns,
                indexes: &[],
            },
            rows: &[],
        },
        TableRows {
            table: TableSpec {
                name: b"Rows",
                columns: &columns,
                indexes: &indexes,
            },
            rows: &rows,
        },
    ];
    create_database_with_table_rows(
        path,
        &tables,
        &mut ResourceBudget::new(ResourceLimits::default()),
    )?;
    Ok(())
}
