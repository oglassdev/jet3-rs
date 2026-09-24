//! Deterministic AutoIncrement candidates; no DAO acceptance is asserted.
use jet3::{
    ColumnRef, ColumnSpec, ColumnType, DatabaseSpec, IndexColumnSpec, IndexDirection, IndexKind,
    IndexSpec, ResourceBudget, ResourceLimits, RowValue, TableRows, TableSpec, create_database,
};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = std::env::args().collect::<Vec<_>>();
    if args.len() != 3 {
        return Err("usage: autoincrement_candidate OUTPUT.mdb unindexed|indexed|multi".into());
    }
    let count = match args[2].as_str() {
        "unindexed" | "multi" => 256,
        "indexed" => 10,
        _ => return Err("unknown arm".into()),
    };
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::AutoIncrement),
        ColumnSpec::new(b"Tag", ColumnType::Long),
    ];
    let fields = [IndexColumnSpec {
        column: ColumnRef::Ordinal(0),
        direction: IndexDirection::Ascending,
    }];
    let indexes = [IndexSpec {
        name: b"PrimaryKey",
        fields: &fields,
        kind: IndexKind::Primary,
    }];
    let values = (1..=count)
        .map(|tag| [RowValue::AutoIncrement, RowValue::Long(tag)])
        .collect::<Vec<_>>();
    let rows = values.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    let second = [RowValue::AutoIncrement, RowValue::Long(-1)];
    let second_rows = [second.as_slice()];
    let mut requests = vec![TableRows {
        table: TableSpec {
            validation: jet3::TableValidation::NONE,
            name: b"Rows",
            columns: &columns,
            indexes: if args[2] == "indexed" { &indexes } else { &[] },
        },
        rows: &rows,
    }];
    if args[2] == "multi" {
        requests.push(TableRows {
            table: TableSpec {
                validation: jet3::TableValidation::NONE,
                name: b"Later",
                columns: &columns,
                indexes: &indexes,
            },
            rows: &second_rows,
        });
    }
    create_database(
        &args[1],
        &DatabaseSpec {
            tables: &requests,
            ..DatabaseSpec::default()
        },
        &mut ResourceBudget::new(ResourceLimits::default()),
    )?;
    Ok(())
}
