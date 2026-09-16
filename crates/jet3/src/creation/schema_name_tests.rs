use super::*;
use crate::{RowValue, TableRows, TextCodePage};

#[test]
fn cp1252_schema_names_preserve_bytes_and_order_logical_indexes() -> TestResult {
    let directory = TestDirectory::create()?;
    let names: &[&[u8]] = &[b"ById", b"Z", b"a", b"\xc1", b"\xe6", b"B"];
    let fields = [IndexColumnSpec::ascending(0)];
    let indexes = names
        .iter()
        .map(|name| IndexSpec {
            name,
            fields: &fields,
            kind: IndexKind::Ordinary,
        })
        .collect::<Vec<_>>();
    let columns = [
        ColumnSpec::new(b"Identit\xe9", ColumnType::Long),
        ColumnSpec::new(b"caf\xe9", ColumnType::Text { max_len: nz(8) }).with_allow_zero_length(),
        ColumnSpec::new(b"m\xe9m\xf8", ColumnType::Memo).with_allow_zero_length(),
    ];
    let table = TableSpec {
        name: b"T\xe2ble \xc6",
        columns: &columns,
        indexes: &indexes,
    };
    let row = [RowValue::Long(1), RowValue::Text(b""), RowValue::Memo(b"")];
    crate::create_database_with_rows(directory.target(), &table, &[&row], &mut budget())?;
    crate::insert_row(
        directory.target(),
        table.name,
        &[RowValue::Long(2), RowValue::Text(b""), RowValue::Memo(b"")],
        &mut budget(),
    )?;
    let mut work = budget();
    let mut db = DatabaseReader::open(directory.target(), &mut work)?;
    let definition = crate::update::indexed_writable_table(&mut db, table.name, &mut work)?;
    assert_eq!(
        definition
            .columns()
            .iter()
            .map(|c| c.name().raw_bytes())
            .collect::<Vec<_>>(),
        columns.iter().map(ColumnSpec::name).collect::<Vec<_>>()
    );
    assert_eq!(
        definition
            .indexes()
            .iter()
            .map(|i| i.name().raw_bytes())
            .collect::<Vec<_>>(),
        [b"a".as_slice(), b"\xc1", b"\xe6", b"B", b"ById", b"Z"]
    );
    assert_eq!(
        definition
            .indexes()
            .iter()
            .map(|i| i.physical_index())
            .collect::<Vec<_>>(),
        [2, 3, 4, 5, 0, 1]
    );
    assert_eq!(definition.row_count(), 2);
    let report = db.validate(TextCodePage::Windows1252, &mut work)?;
    assert_eq!(report.indexes_with_verified_keys, 13);
    Ok(())
}

#[test]
fn collation_equal_names_are_refused_before_publication() -> TestResult {
    let directory = TestDirectory::create()?;
    let pairs: &[(&[u8], &[u8])] = &[
        (b"Case", b"case"),
        (b"AE", b"\xc6"),
        (b"ss", b"\xdf"),
        (b"Caf\xe9", b"CAF\xc9"),
        (b"Q'", b"Q\x92"),
    ];
    for &(a, b) in pairs {
        let tables = [a, b].map(|name| TableSpec {
            name,
            columns: &[ID],
            indexes: &[],
        });
        assert!(matches!(
            create_database(directory.target(), &tables, &mut budget()),
            Err(CreateDatabaseError::Compose(
                ComposeError::DuplicateTableName {
                    first: 0,
                    second: 1
                }
            ))
        ));
        let columns = [a, b].map(|name| ColumnSpec::new(name, ColumnType::Long));
        let table = TableSpec {
            name: b"Items",
            columns: &columns,
            indexes: &[],
        };
        assert!(matches!(
            create_database(directory.target(), &[table], &mut budget()),
            Err(CreateDatabaseError::Compose(ComposeError::Schema(
                TableSchemaPlanError::Definition(crate::TableDefinitionWriteError::DuplicateName {
                    role: "column",
                    ordinal: 1
                })
            )))
        ));
        let fields = [IndexColumnSpec::ascending(0)];
        let indexes = [a, b].map(|name| IndexSpec {
            name,
            fields: &fields,
            kind: IndexKind::Ordinary,
        });
        let table = TableSpec {
            columns: &[ID],
            indexes: &indexes,
            ..table
        };
        assert!(matches!(
            create_database(directory.target(), &[table], &mut budget()),
            Err(CreateDatabaseError::Compose(ComposeError::Schema(
                TableSchemaPlanError::Definition(crate::TableDefinitionWriteError::DuplicateName {
                    role: "logical index",
                    ordinal: 1
                })
            )))
        ));
        assert!(directory.entries()?.is_empty());
    }
    Ok(())
}

#[test]
fn accented_relationship_endpoints_validate_and_enforce_mutations() -> TestResult {
    let directory = TestDirectory::create()?;
    let columns = [
        ColumnSpec::new(b"Identit\xe9", ColumnType::Long),
        ColumnSpec::new(b"Parent\xe9", ColumnType::Long),
    ];
    let indexes = [IndexSpec {
        name: b"Cl\xe9",
        fields: &[IndexColumnSpec::ascending(0)],
        kind: IndexKind::Primary,
    }];
    let tables = [b"P\xe4rent".as_slice(), b"Ch\xeeld"].map(|name| TableSpec {
        name,
        columns: &columns,
        indexes: &indexes,
    });
    let relation = crate::RelationshipSpec {
        name: b"R\xe9lation \xc6",
        parent: crate::TableRef::Name(tables[0].name),
        child: crate::TableRef::Name(tables[1].name),
        fields: &[crate::RelationshipField {
            parent: ColumnRef::Name(columns[0].name()),
            child: ColumnRef::Name(columns[1].name()),
        }],
    };
    let row = [RowValue::Long(1), RowValue::Null];
    let requests = [
        TableRows {
            table: tables[0],
            rows: &[&row],
        },
        TableRows {
            table: tables[1],
            rows: &[],
        },
    ];
    crate::create_database_with_relationships_and_rows(
        directory.target(),
        &requests,
        &[relation],
        &mut budget(),
    )?;
    crate::insert_row(
        directory.target(),
        tables[1].name,
        &[RowValue::Long(2), RowValue::Long(1)],
        &mut budget(),
    )?;
    let original = fs::read(directory.target())?;
    assert!(matches!(
        crate::insert_row(
            directory.target(),
            tables[1].name,
            &[RowValue::Long(3), RowValue::Long(99)],
            &mut budget()
        ),
        Err(crate::UpdateError::RelationshipConstraint { value: 99, .. })
    ));
    assert_eq!(fs::read(directory.target())?, original);
    let mut work = budget();
    let mut db = DatabaseReader::open(directory.target(), &mut work)?;
    let report = db.validate(TextCodePage::Windows1252, &mut work)?;
    assert_eq!(report.relationships_with_verified_keys, 1);
    assert_eq!(report.uninterpreted_relationship_rows, 0);
    assert!(report.relationship_inventory_checked);
    Ok(())
}
