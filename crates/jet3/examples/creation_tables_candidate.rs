//! Generates bounded EXP-0222/0241 catalog and table-layout candidates.
use jet3::{
    ColumnSpec, ColumnType, ComposeError, CreateDatabaseError, IndexColumnSpec, IndexKind,
    IndexSpec, ResourceBudget, ResourceLimits, RowValue, TableRows, TableSpec, create_database,
    create_database_with_table_rows,
};
use std::path::Path;

fn create(
    path: &Path,
    count: usize,
    width: usize,
    populated: bool,
    name_width: usize,
) -> Result<(), CreateDatabaseError> {
    let names = (0..count)
        .map(|n| format!("T{n:02}{}", "x".repeat(name_width - 3)))
        .collect::<Vec<_>>();
    let column_names = (0..width).map(|n| format!("C{n:02}")).collect::<Vec<_>>();
    let columns = column_names
        .iter()
        .map(|name| ColumnSpec::new(name.as_bytes(), ColumnType::Long))
        .collect::<Vec<_>>();
    let primary = [IndexColumnSpec::ascending(0)];
    let descending = [IndexColumnSpec::descending(1)];
    let unique = [IndexColumnSpec::ascending(2)];
    let indexes = [
        IndexSpec {
            name: b"ZPrimary",
            fields: &primary,
            kind: IndexKind::Primary,
        },
        IndexSpec {
            name: b"ASecond",
            fields: &descending,
            kind: IndexKind::Ordinary,
        },
        IndexSpec {
            name: b"MUnique",
            fields: &unique,
            kind: IndexKind::Unique,
        },
    ];
    let tables = names
        .iter()
        .enumerate()
        .map(|(n, name)| TableSpec {
            name: name.as_bytes(),
            columns: &columns,
            indexes: &indexes[..if populated {
                [3, 0, 1, 2, 3, 3][n % 6]
            } else {
                0
            }],
        })
        .collect::<Vec<_>>();
    let mut budget = ResourceBudget::new(ResourceLimits::default());
    if populated {
        let rows = (0..17)
            .map(|n| {
                [
                    RowValue::Long(n),
                    RowValue::Long(n % 3),
                    RowValue::Long(n - 8),
                ]
            })
            .collect::<Vec<_>>();
        let rows = rows
            .iter()
            .map(<[RowValue<'_>; 3]>::as_slice)
            .collect::<Vec<_>>();
        let requests = tables
            .iter()
            .map(|table| TableRows {
                table: *table,
                rows: &rows,
            })
            .collect::<Vec<_>>();
        create_database_with_table_rows(path, &requests, &mut budget)
    } else {
        create_database(path, &tables, &mut budget)
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let directory = std::env::args_os()
        .nth(1)
        .ok_or("usage: creation_tables_candidate OUTPUT_DIRECTORY")?;
    let directory = Path::new(&directory);
    std::fs::create_dir_all(directory)?;
    let mut cases = String::new();
    for (name, count, width, populated, name_width) in [
        ("five-empty", 5, 1, false, 3),
        ("six-indexed", 6, 3, true, 3),
        ("catalog-short", 40, 1, false, 3),
        ("catalog-wide", 30, 32, false, 48),
        ("catalog-aces", 110, 1, false, 3),
        ("catalog-names", 40, 3, false, 48),
    ] {
        create(
            &directory.join(format!("{name}.mdb")),
            count,
            width,
            populated,
            name_width,
        )?;
        cases.push_str(&format!(
            "{name}\t{count}\t{width}\t{}\t{name_width}\n",
            if populated { 17 } else { 0 }
        ));
    }
    let refusal = directory.join("refused.mdb");
    if !matches!(
        create(&refusal, 128, 1, false, 3),
        Err(CreateDatabaseError::Compose(
            ComposeError::TableCountOverflow {
                count: 128,
                maximum: 127
            }
        ))
    ) {
        return Err("creation counter refusal changed".into());
    }
    if refusal.exists() {
        return Err("refused creation published a file".into());
    }
    std::fs::write(directory.join("cases.tsv"), cases)?;
    std::fs::write(
        directory.join("refusals.tsv"),
        "creation-counter\t128\tTableCountOverflow\n",
    )?;
    Ok(())
}
