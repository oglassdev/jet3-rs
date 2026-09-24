//! Deterministic relationship candidate with repeated foreign keys across pages.
use jet3::{
    ColumnRef, ColumnSpec, ColumnType, IndexColumnSpec, IndexKind, IndexSpec, RelationshipField,
    RelationshipSpec, ResourceBudget, ResourceLimits, RowValue, TableRef, TableRows, TableSpec,
    create_database_with_relationship_rows,
};
use std::num::NonZeroU8;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let path = std::env::args_os()
        .nth(1)
        .ok_or("usage: relationship_row_candidate OUTPUT.mdb")?;
    let parent_columns = [
        ColumnSpec::new(b"Code2", ColumnType::Long),
        ColumnSpec::new(b"Key1", ColumnType::Long),
    ];
    let child_columns = [
        ColumnSpec::new(
            b"Label3",
            ColumnType::Text {
                max_len: NonZeroU8::new(255).ok_or("invalid width")?,
            },
        ),
        ColumnSpec::new(b"Account4", ColumnType::Long),
    ];
    let indexes = [IndexSpec {
        name: b"Primary9",
        fields: &[IndexColumnSpec::ascending(b"Key1")],
        kind: IndexKind::Primary,
    }];
    let parent = TableSpec {
        validation: jet3::TableValidation::NONE,
        name: b"Accounts7",
        columns: &parent_columns,
        indexes: &indexes,
    };
    let child = TableSpec {
        validation: jet3::TableValidation::NONE,
        name: b"Events9",
        columns: &child_columns,
        indexes: &[],
    };
    let relationship = RelationshipSpec {
        unique: false,
        enforce: true,
        join: jet3::RelationshipJoin::Inner,
        cascade_updates: false,
        cascade_deletes: false,
        name: b"Account7Events9",
        parent: TableRef::Name(b"Accounts7"),
        child: TableRef::Name(b"Events9"),
        fields: &[RelationshipField {
            parent: ColumnRef::Name(b"Key1"),
            child: ColumnRef::Name(b"Account4"),
        }],
    };
    let payloads = (0..20)
        .map(|position| [b'a' + position; 255])
        .collect::<Vec<_>>();
    let values = payloads
        .iter()
        .enumerate()
        .map(|(position, text)| {
            [
                RowValue::Text(text),
                RowValue::Long(1 + (position % 3) as i32),
            ]
        })
        .collect::<Vec<_>>();
    let child_rows = values.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    create_database_with_relationship_rows(
        path,
        &[
            TableRows {
                table: parent,
                rows: &[
                    &[RowValue::Long(9), RowValue::Long(1)],
                    &[RowValue::Long(8), RowValue::Long(2)],
                    &[RowValue::Long(7), RowValue::Long(3)],
                ],
            },
            TableRows {
                table: child,
                rows: &child_rows,
            },
        ],
        &relationship,
        &mut ResourceBudget::new(ResourceLimits::default()),
    )?;
    Ok(())
}
