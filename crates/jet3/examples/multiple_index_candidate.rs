//! Finite multiple populated-index candidates for EXP-0193.
use jet3::{
    ColumnSpec, ColumnType, IndexColumnSpec, IndexKind, IndexSpec, ResourceBudget, ResourceLimits,
    RowValue, TableSpec, create_database_with_rows,
};
use std::{env, fs, path::Path};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = env::args().skip(1).collect();
    let [directory] = args.as_slice() else {
        return Err("usage: multiple_index_candidate NEW_DIRECTORY".into());
    };
    let directory = Path::new(directory);
    fs::create_dir(directory)?;
    for mixed in [false, true] {
        let columns = [
            ColumnSpec::new(b"Id", ColumnType::Long),
            ColumnSpec::new(b"Group", ColumnType::Long),
            ColumnSpec::new(
                b"Value",
                if mixed {
                    ColumnType::Currency
                } else {
                    ColumnType::Long
                },
            ),
        ];
        let primary = [IndexColumnSpec::ascending(0)];
        let ordinary = if mixed {
            vec![
                IndexColumnSpec::descending(2),
                IndexColumnSpec::ascending(1),
            ]
        } else {
            vec![IndexColumnSpec::descending(1)]
        };
        let unique = [
            IndexColumnSpec::descending(1),
            IndexColumnSpec::ascending(2),
        ];
        let mut indexes = vec![
            IndexSpec {
                name: b"ZPrimary",
                kind: IndexKind::Primary,
                fields: &primary,
            },
            IndexSpec {
                name: b"AGroup",
                kind: IndexKind::Ordinary,
                fields: &ordinary,
            },
        ];
        if !mixed {
            indexes.push(IndexSpec {
                name: b"MMixed",
                kind: IndexKind::Unique,
                fields: &unique,
            });
        }
        let rows: Vec<_> = (0..if mixed { 30 } else { 201 })
            .map(|n| {
                vec![
                    RowValue::Long(n + 1),
                    RowValue::Long(n % 3 - 1),
                    if mixed {
                        if n % 5 == 0 {
                            RowValue::Null
                        } else {
                            RowValue::Currency {
                                scaled: i64::from(n % 4 - 2),
                            }
                        }
                    } else {
                        RowValue::Long(n - 100)
                    },
                ]
            })
            .collect();
        let row_refs: Vec<_> = rows.iter().map(Vec::as_slice).collect();
        create_database_with_rows(
            directory.join(if mixed {
                "mixed-null.mdb"
            } else {
                "three-long.mdb"
            }),
            &TableSpec {
                name: b"Rows",
                columns: &columns,
                indexes: &indexes,
            },
            &row_refs,
            &mut ResourceBudget::new(ResourceLimits::default()),
        )?;
    }
    Ok(())
}
