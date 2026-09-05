//! Deterministic multi-level Long index candidates for separate DAO validation.
use jet3::{
    ColumnRef, ColumnSpec, ColumnType, IndexColumnSpec, IndexDirection, IndexKind, IndexSpec,
    RelationshipColumn, RelationshipSpec, ResourceBudget, ResourceLimits, RowValue, TableRef,
    TableRows, TableSpec, create_database_with_relationship_rows, create_database_with_table_rows,
};
use std::path::Path;

const COLUMNS: [ColumnSpec<'static>; 2] = [
    ColumnSpec::new(b"Id", ColumnType::Long),
    ColumnSpec::new(b"Payload", ColumnType::Long),
];
const PRIMARY: [IndexSpec<'static>; 1] = [IndexSpec {
    name: b"ById",
    fields: &[IndexColumnSpec {
        column: ColumnRef::Ordinal(0),
        direction: IndexDirection::Ascending,
    }],
    kind: IndexKind::Primary,
}];
fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}
fn references<'a>(rows: &'a [[RowValue<'a>; 2]]) -> Vec<&'a [RowValue<'a>]> {
    rows.iter().map(|row| row.as_slice()).collect()
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let directory = std::env::args_os()
        .nth(1)
        .ok_or("usage: multi_level_index_candidate OUTPUT_DIRECTORY")?;
    let directory = Path::new(&directory);
    std::fs::create_dir_all(directory)?;
    let rows = (0..27801)
        .map(|position| [RowValue::Long(27800 - position), RowValue::Long(position)])
        .collect::<Vec<_>>();
    create_database_with_table_rows(
        directory.join("primary.mdb"),
        &[TableRows {
            table: TableSpec {
                name: b"Rows",
                columns: &COLUMNS,
                indexes: &PRIMARY,
            },
            rows: &references(&rows),
        }],
        &mut budget(),
    )?;

    let columns = [
        ColumnSpec::new(b"A", ColumnType::Long),
        ColumnSpec::new(b"B", ColumnType::Long),
        COLUMNS[1],
    ];
    let indexes = [IndexSpec {
        name: b"ByKey",
        fields: &[
            IndexColumnSpec {
                column: ColumnRef::Ordinal(1),
                direction: IndexDirection::Descending,
            },
            IndexColumnSpec::ascending(b"A"),
        ],
        kind: IndexKind::Ordinary,
    }];
    let rows = (0..12929)
        .map(|position| {
            [
                RowValue::Long(position / 400),
                RowValue::Long(position / 800 - 9),
                RowValue::Long(position),
            ]
        })
        .collect::<Vec<_>>();
    let references = rows.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    create_database_with_table_rows(
        directory.join("composite.mdb"),
        &[
            TableRows {
                table: TableSpec {
                    name: b"Empty",
                    columns: &COLUMNS,
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
                rows: &references,
            },
        ],
        &mut budget(),
    )?;

    let parent = (-100..=100)
        .map(|id| [RowValue::Long(id), RowValue::Long(id + 100)])
        .collect::<Vec<_>>();
    let child = (0..27801)
        .map(|position| [RowValue::Long(position % 3 - 1), RowValue::Long(position)])
        .collect::<Vec<_>>();
    let parent_rows = parent.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    let child_rows = child.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    create_database_with_relationship_rows(
        directory.join("relationship.mdb"),
        &[
            TableRows {
                table: TableSpec {
                    name: b"Parents",
                    columns: &COLUMNS,
                    indexes: &PRIMARY,
                },
                rows: &parent_rows,
            },
            TableRows {
                table: TableSpec {
                    name: b"Children",
                    columns: &COLUMNS,
                    indexes: &[],
                },
                rows: &child_rows,
            },
        ],
        &RelationshipSpec {
            name: b"ParentChildren",
            parent: RelationshipColumn {
                table: TableRef::Ordinal(0),
                column: ColumnRef::Ordinal(0),
            },
            child: RelationshipColumn {
                table: TableRef::Ordinal(1),
                column: ColumnRef::Ordinal(0),
            },
        },
        &mut budget(),
    )?;
    Ok(())
}
