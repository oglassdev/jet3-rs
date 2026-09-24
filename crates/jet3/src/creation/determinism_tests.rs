use super::*;
use crate::{
    RelationshipField, RelationshipSpec, RowValue, TableRef, TableRows,
    create_database_with_relationship, create_database_with_relationship_rows,
    create_database_with_rows, create_database_with_table_rows,
};
use std::path::Path;

fn repeats(
    create: impl Fn(&Path, &mut ResourceBudget) -> Result<(), CreateDatabaseError>,
) -> TestResult {
    let first = TestDirectory::create()?;
    let second = TestDirectory::create()?;
    let mut measured = budget();
    create(&first.target(), &mut measured)?;
    let mut tight = ResourceBudget::new(
        ResourceLimits::default()
            .with_max_total_work_units(measured.total_work_units())
            .with_max_allocation_bytes(measured.allocation_bytes())
            .with_max_encoded_bytes(measured.encoded_bytes()),
    );
    let other_path = second.path.join("another-name.mdb");
    create(&other_path, &mut tight)?;
    assert_eq!(fs::read(first.target())?, fs::read(other_path)?);
    assert_eq!(first.entries()?, ["created.mdb"]);
    assert_eq!(second.entries()?, ["another-name.mdb"]);
    Ok(())
}

#[test]
fn empty_creation_is_independent_of_destination_and_successful_budget() -> TestResult {
    repeats(|path, operation| create_database(path, &[], operation))?;
    repeats(|path, operation| create_database_with_table_rows(path, &[], operation))?;
    let table = TableSpec {
        validation: crate::TableValidation::NONE,
        name: b"Empty",
        columns: &[ID, NOTE],
        indexes: &[],
    };
    repeats(|path, operation| create_database_with_rows(path, &table, &[], operation))
}

#[test]
fn populated_catalog_definitions_indexes_payloads_and_generated_ids_repeat() -> TestResult {
    let names = (0..40)
        .map(|number| format!("Table{number:02}").into_bytes())
        .collect::<Vec<_>>();
    let wide_names = (0..70)
        .map(|number| format!("Field{number:05}").into_bytes())
        .collect::<Vec<_>>();
    let wide_columns = wide_names
        .iter()
        .map(|name| ColumnSpec::new(name, ColumnType::Long))
        .collect::<Vec<_>>();
    let wide_values = (0..70).map(RowValue::Long).collect::<Vec<_>>();
    let wide_rows = [wide_values.as_slice()];
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::AutoIncrement),
        ColumnSpec::new(b"Name", ColumnType::Text { max_len: nz(255) }),
        ColumnSpec::new(b"Tag", ColumnType::Long),
        NOTE,
        ColumnSpec::new(b"Blob", ColumnType::LongBinary),
        ColumnSpec::new(b"Guid", ColumnType::Guid),
    ];
    let indexes = [
        IndexSpec {
            name: b"PrimaryKey",
            fields: &[field(0, IndexDirection::Ascending)],
            kind: IndexKind::Primary,
        },
        IndexSpec {
            name: b"ByPair",
            fields: &[
                field(1, IndexDirection::Descending),
                field(2, IndexDirection::Ascending),
            ],
            kind: IndexKind::Ordinary,
        },
        IndexSpec {
            name: b"ByGuid",
            fields: &[field(5, IndexDirection::Ascending)],
            kind: IndexKind::Unique,
        },
    ];
    let labels = (0..205)
        .map(|number| format!("Row{number:03}{}", "x".repeat(200)).into_bytes())
        .collect::<Vec<_>>();
    let memo = [b'm'; 4096];
    let blob = [0xa5; 2037];
    let values = labels
        .iter()
        .enumerate()
        .map(|(number, label)| {
            let length = match number % 4 {
                0 => 0,
                1 => 32,
                2 => 33,
                _ => 2037,
            };
            [
                RowValue::AutoIncrement,
                RowValue::Text(label),
                if number % 3 == 0 {
                    RowValue::Null
                } else {
                    RowValue::Long(number as i32 % 7)
                },
                if length == 0 {
                    RowValue::Null
                } else {
                    RowValue::Memo(&memo[..if length == 2037 { 4096 } else { length }])
                },
                if length == 0 {
                    RowValue::Null
                } else {
                    RowValue::LongBinary(&blob[..length])
                },
                RowValue::Guid([number as u8; 16]),
            ]
        })
        .collect::<Vec<_>>();
    let rows = values.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    let mut requests = names
        .iter()
        .map(|name| TableRows {
            table: TableSpec {
                validation: crate::TableValidation::NONE,
                name,
                columns: &[ID],
                indexes: &[],
            },
            rows: &[],
        })
        .collect::<Vec<_>>();
    requests.push(TableRows {
        table: TableSpec {
            validation: crate::TableValidation::NONE,
            name: b"Wide",
            columns: &wide_columns,
            indexes: &[],
        },
        rows: &wide_rows,
    });
    requests.push(TableRows {
        table: TableSpec {
            validation: crate::TableValidation::NONE,
            name: b"Items",
            columns: &columns,
            indexes: &indexes,
        },
        rows: &rows,
    });
    repeats(|path, operation| create_database_with_table_rows(path, &requests, operation))
}

#[test]
fn empty_and_populated_relationship_metadata_repeat() -> TestResult {
    let indexes = [IndexSpec {
        name: b"PrimaryKey",
        fields: &[IndexColumnSpec::ascending(b"Id")],
        kind: IndexKind::Primary,
    }];
    let tables = [
        TableSpec {
            validation: crate::TableValidation::NONE,
            name: b"Parents",
            columns: &[ID],
            indexes: &indexes,
        },
        TableSpec {
            validation: crate::TableValidation::NONE,
            name: b"Children",
            columns: &[CODE, ColumnSpec::new(b"ParentId", ColumnType::Long)],
            indexes: &[],
        },
    ];
    let relationship = RelationshipSpec {
        unique: false,
        enforce: true,
        join: crate::RelationshipJoin::Inner,
        cascade_updates: false,
        cascade_deletes: false,
        name: b"ParentChildren",
        parent: TableRef::Name(b"Parents"),
        child: TableRef::Name(b"Children"),
        fields: &[RelationshipField {
            parent: ColumnRef::Name(b"Id"),
            child: ColumnRef::Name(b"ParentId"),
        }],
    };
    repeats(|path, operation| {
        create_database_with_relationship(path, &tables, &relationship, operation)
    })?;
    let child = [RowValue::Text(b"child"), RowValue::Long(7)];
    let children = vec![child.as_slice(); 201];
    let requests = [
        TableRows {
            table: tables[0],
            rows: &[&[RowValue::Long(7)]],
        },
        TableRows {
            table: tables[1],
            rows: &children,
        },
    ];
    repeats(|path, operation| {
        create_database_with_relationship_rows(path, &requests, &relationship, operation)
    })
}
