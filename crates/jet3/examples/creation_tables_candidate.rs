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
    schema_name_width: usize,
) -> Result<(), CreateDatabaseError> {
    let names = (0..count)
        .map(|n| {
            let prefix = format!("T{n:02}");
            format!(
                "{prefix}{}",
                "x".repeat(name_width.saturating_sub(prefix.len()))
            )
        })
        .collect::<Vec<_>>();
    let column_names = (0..width)
        .map(|n| {
            let prefix = format!("C{n:02}");
            format!(
                "{prefix}{}",
                "c".repeat(schema_name_width.saturating_sub(prefix.len()))
            )
        })
        .collect::<Vec<_>>();
    let index_names = ["ZPrimary", "ASecond", "MUnique"].map(|name| {
        format!(
            "{name}{}",
            "i".repeat(schema_name_width.min(63).saturating_sub(name.len()))
        )
    });
    let columns = column_names
        .iter()
        .map(|name| ColumnSpec::new(name.as_bytes(), ColumnType::Long))
        .collect::<Vec<_>>();
    let primary = [IndexColumnSpec::ascending(0)];
    let descending = [IndexColumnSpec::descending(1)];
    let unique = [IndexColumnSpec::ascending(2)];
    let indexes = [
        IndexSpec {
            name: index_names[0].as_bytes(),
            fields: &primary,
            kind: IndexKind::Primary,
        },
        IndexSpec {
            name: index_names[1].as_bytes(),
            fields: &descending,
            kind: IndexKind::Ordinary,
        },
        IndexSpec {
            name: index_names[2].as_bytes(),
            fields: &unique,
            kind: IndexKind::Unique,
        },
    ];
    let tables = names
        .iter()
        .enumerate()
        .map(|(n, name)| TableSpec {
            validation: jet3::TableValidation::NONE,
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
    for (name, count, width, populated, name_width, schema_name_width) in [
        ("five-empty", 5, 1, false, 3, 0),
        ("six-indexed", 6, 3, true, 3, 0),
        ("catalog-short", 40, 1, false, 3, 0),
        ("catalog-wide", 30, 32, false, 48, 0),
        ("catalog-aces", 110, 1, false, 3, 0),
        ("catalog-names", 40, 3, false, 48, 0),
        ("counter-128", 128, 1, false, 3, 0),
        ("counter-255", 255, 1, false, 3, 0),
        ("counter-256", 256, 1, false, 3, 0),
        ("names-boundary", 6, 3, true, 64, 64),
    ] {
        create(
            &directory.join(format!("{name}.mdb")),
            count,
            width,
            populated,
            name_width,
            schema_name_width,
        )?;
        cases.push_str(&format!(
            "{name}\t{count}\t{width}\t{}\t{name_width}\t{schema_name_width}\n",
            if populated { 17 } else { 0 }
        ));
    }
    let refusal = directory.join("refused.mdb");
    if !matches!(
        create(&refusal, 32640, 1, false, 3, 0),
        Err(CreateDatabaseError::Compose(
            ComposeError::TableCountOverflow {
                count: 32640,
                maximum: 32639
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
        "creation-counter\t32640\tTableCountOverflow\n",
    )?;
    Ok(())
}
