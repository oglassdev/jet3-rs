//! Generates EXP-0222 table-layout candidates and records catalog capacity refusals.
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
    create(&directory.join("five-empty.mdb"), 5, 1, false, 3)?;
    create(&directory.join("six-indexed.mdb"), 6, 3, true, 3)?;
    let mut cases = String::from("five-empty\t5\t1\t0\t3\nsix-indexed\t6\t3\t17\t3\n");
    let mut refusals = String::new();
    for (arm, width, name_width) in [("catalog-short", 1, 3), ("catalog-wide", 32, 48)] {
        let path = directory.join(format!("{arm}.mdb"));
        let mut last = 0;
        for count in 1..=128 {
            match create(&path, count, width, false, name_width) {
                Ok(()) => {
                    last = count;
                    std::fs::remove_file(&path)?;
                }
                Err(CreateDatabaseError::Compose(ComposeError::Page(
                    jet3::PageImageError::PageFull { .. },
                ))) => {
                    if path.exists() {
                        return Err("refused creation published a file".into());
                    }
                    refusals.push_str(&format!("{arm}\t{count}\tPageFull\n"));
                    break;
                }
                Err(error) => return Err(error.into()),
            }
        }
        if last == 0 || last == 128 {
            return Err("catalog capacity not reached".into());
        }
        create(&path, last, width, false, name_width)?;
        cases.push_str(&format!("{arm}\t{last}\t{width}\t0\t{name_width}\n"));
    }
    std::fs::write(directory.join("cases.tsv"), cases)?;
    std::fs::write(directory.join("refusals.tsv"), refusals)?;
    Ok(())
}
