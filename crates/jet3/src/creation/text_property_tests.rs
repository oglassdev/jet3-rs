//! EXP-0299 text properties: API limits, refusals of unevaluated rules and the sort-order guard.
use super::*;
use crate::{
    ColumnOrdinal, FieldUpdate, PropertyChange, RowDelete, RowUpdate, RowValue, SchemaEdit,
    SortOrder, TableValidation, TextCodePage, UpdateError, create_database_with_rows, delete_row,
    edit_schema, insert_row, update_field, update_row,
};

fn table<'a>(
    name: &'a [u8],
    columns: &'a [ColumnSpec<'a>],
    validation: TableValidation<'a>,
) -> TableSpec<'a> {
    TableSpec {
        name,
        columns,
        indexes: &[],
        validation,
    }
}

fn first_row(
    path: &std::path::Path,
    name: &[u8],
) -> Result<crate::RowLocator, Box<dyn std::error::Error>> {
    let mut work = budget();
    let mut db = DatabaseReader::open(path, &mut work)?;
    let table = crate::update::indexed_writable_table(&mut db, name, &mut work)?;
    let mut rows = db.rows(&table, &mut work)?;
    Ok(rows.next_row()?.ok_or("row")?.locator())
}

fn properties(
    path: &std::path::Path,
    name: &[u8],
) -> Result<crate::TableProperties, Box<dyn std::error::Error>> {
    let mut work = budget();
    let mut db = DatabaseReader::open(path, &mut work)?;
    let table = crate::update::indexed_writable_table(&mut db, name, &mut work)?;
    Ok(db.table_properties(&table, &mut work)?)
}

fn unsupported(result: Result<(), UpdateError>) -> bool {
    match result {
        Err(UpdateError::Unsupported(_)) => true,
        Err(UpdateError::Publish(error)) => std::error::Error::source(&error)
            .and_then(|source| source.downcast_ref::<UpdateError>())
            .is_some_and(|source| matches!(source, UpdateError::Unsupported(_))),
        _ => false,
    }
}

#[test]
fn stored_rules_refuse_row_writes_and_preserve_the_file() -> TestResult {
    let directory = TestDirectory::create()?;
    let path = directory.target();
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"Amount", ColumnType::Long).with_default_value(b"5"),
    ];
    let plain = table(b"Items", &columns, TableValidation::NONE);
    create_database_with_rows(
        &path,
        &plain,
        &[&[RowValue::Long(1), RowValue::Null]],
        &mut budget(),
    )?;
    let row = first_row(&path, b"Items")?;
    // DAO stores explicit Null over a default (EXP-0299); Rust never applies defaults.
    let inserted = insert_row(
        &path,
        b"Items",
        &[RowValue::Long(2), RowValue::Null],
        &mut budget(),
    )?;
    let set = |rule| SchemaEdit::SetColumnProperties {
        table: b"Items",
        column: b"Amount",
        default_value: PropertyChange::Keep,
        validation_rule: rule,
        validation_text: PropertyChange::Set(b"positive"),
        description: PropertyChange::Keep,
    };
    edit_schema(&path, set(PropertyChange::Set(b">0")), &mut budget())?;
    let stored = properties(&path, b"Items")?;
    assert_eq!(stored.columns()[1].default_value(), Some(b"5".as_slice()));
    assert_eq!(
        stored.columns()[1].validation_rule(),
        Some(b">0\0".as_slice())
    );
    let before = fs::read(&path)?;
    let refused = |error: UpdateError| matches!(error, UpdateError::ValidationRule { column: Some(column) } if column == ColumnOrdinal::new(1));
    let values = [RowValue::Long(3), RowValue::Long(1)];
    assert!(insert_row(&path, b"Items", &values, &mut budget()).is_err_and(refused));
    let request = RowUpdate {
        table: b"Items",
        row,
        values: &values,
    };
    assert!(update_row(&path, request, &mut budget()).is_err_and(refused));
    let request = FieldUpdate {
        table: b"Items",
        row,
        column: ColumnOrdinal::new(0),
        value: RowValue::Long(9),
    };
    assert!(update_field(&path, request, &mut budget()).is_err_and(refused));
    assert_eq!(fs::read(&path)?, before);
    let request = RowDelete {
        table: b"Items",
        row: inserted,
    };
    delete_row(&path, request, &mut budget())?;
    edit_schema(&path, set(PropertyChange::Clear), &mut budget())?;
    assert_eq!(
        properties(&path, b"Items")?.columns()[1].validation_rule(),
        None
    );
    insert_row(&path, b"Items", &values, &mut budget())?;
    let mut work = budget();
    let mut db = DatabaseReader::open(&path, &mut work)?;
    db.validate(TextCodePage::Windows1252, &mut work)?;
    let definition = crate::update::indexed_writable_table(&mut db, b"Items", &mut work)?;
    let mut rows = db.rows(&definition, &mut work)?;
    let mut first = rows.next_row()?.ok_or("row")?;
    let amount = first
        .value(ColumnOrdinal::new(1), TextCodePage::Windows1252)?
        .ok_or("amount")?;
    assert!(matches!(amount.kind(), crate::ValueKind::Null));
    Ok(())
}

#[test]
fn table_rules_refuse_initial_and_later_rows() -> TestResult {
    let directory = TestDirectory::create()?;
    let path = directory.target();
    let columns = [ColumnSpec::new(b"A", ColumnType::Long)];
    let rule = TableValidation {
        rule: Some(b"[A]>0"),
        text: Some(b"positive"),
    };
    let ruled = table(b"Checked", &columns, rule);
    let error = create_database_with_rows(&path, &ruled, &[&[RowValue::Long(1)]], &mut budget());
    assert!(matches!(
        error,
        Err(CreateDatabaseError::Compose(
            ComposeError::ValidationRuleRows
        ))
    ));
    assert!(!path.exists());
    create_database(&path, &[ruled], &mut budget())?;
    let stored = properties(&path, b"Checked")?;
    assert_eq!(stored.validation_rule(), Some(b"[A]>0".as_slice()));
    assert_eq!(stored.validation_text(), Some(b"positive".as_slice()));
    let before = fs::read(&path)?;
    assert!(matches!(
        insert_row(&path, b"Checked", &[RowValue::Long(1)], &mut budget()),
        Err(UpdateError::ValidationRule { column: None })
    ));
    assert_eq!(fs::read(&path)?, before);
    let clear = SchemaEdit::SetTableProperties {
        table: b"Checked",
        validation_rule: PropertyChange::Clear,
        validation_text: PropertyChange::Keep,
    };
    edit_schema(&path, clear, &mut budget())?;
    let cleared = fs::read(&path)?;
    edit_schema(&path, clear, &mut budget())?;
    assert_eq!(fs::read(&path)?, cleared);
    insert_row(&path, b"Checked", &[RowValue::Long(1)], &mut budget())?;
    assert_eq!(
        properties(&path, b"Checked")?.validation_text(),
        Some(b"positive".as_slice())
    );
    Ok(())
}

#[test]
fn unsupported_property_requests_are_refused_before_writing() -> TestResult {
    let directory = TestDirectory::create()?;
    let path = directory.target();
    let long = [b'a'; crate::column_properties::MAX_TEXT_PROPERTY + 1];
    let guid = ColumnSpec::new(b"G", ColumnType::Guid);
    let auto = ColumnSpec::new(b"N", ColumnType::AutoIncrement);
    let text = ColumnSpec::new(b"T", ColumnType::Memo);
    for column in [
        guid.with_validation_rule(b"Is Not Null"),
        guid.with_validation_text(b"message"),
        auto.with_default_value(b"1"),
        text.with_default_value(b""),
        text.with_description(b"a\0b"),
        text.with_validation_text(b"\x81"),
        text.with_validation_rule(&long),
    ] {
        let columns = [column];
        let result = create_database(
            &path,
            &[table(b"T", &columns, TableValidation::NONE)],
            &mut budget(),
        );
        assert!(matches!(
            result,
            Err(CreateDatabaseError::Compose(ComposeError::Schema(
                TableSchemaPlanError::InvalidTextProperty {
                    column: Some(0),
                    ..
                }
            )))
        ));
        assert!(!path.exists());
    }
    let columns = [guid, auto.with_description(b"numbered")];
    create_database(
        &path,
        &[table(b"T", &columns, TableValidation::NONE)],
        &mut budget(),
    )?;
    let before = fs::read(&path)?;
    for (column, rule) in [(b"G".as_slice(), b"Is Not Null".as_slice()), (b"N", b"")] {
        let edit = SchemaEdit::SetColumnProperties {
            table: b"T",
            column,
            default_value: PropertyChange::Keep,
            validation_rule: PropertyChange::Set(rule),
            validation_text: PropertyChange::Keep,
            description: PropertyChange::Keep,
        };
        assert!(unsupported(edit_schema(&path, edit, &mut budget())));
    }
    assert_eq!(fs::read(&path)?, before);
    // Existing AutoIncrement fields accept rules after creation, as in DAO.
    let edit = SchemaEdit::SetColumnProperties {
        table: b"T",
        column: b"N",
        default_value: PropertyChange::Set(b"1"),
        validation_rule: PropertyChange::Set(b">0"),
        validation_text: PropertyChange::Keep,
        description: PropertyChange::Keep,
    };
    edit_schema(&path, edit, &mut budget())?;
    let stored = properties(&path, b"T")?;
    assert_eq!(
        stored.columns()[1].description(),
        Some(b"numbered".as_slice())
    );
    assert_eq!(stored.columns()[1].default_value(), Some(b"1".as_slice()));
    Ok(())
}

#[test]
fn non_general_sort_orders_are_readable_but_not_writable() -> TestResult {
    let directory = TestDirectory::create()?;
    let path = directory.target();
    let columns = [ColumnSpec::new(b"A", ColumnType::Long)];
    create_database(
        &path,
        &[table(b"T", &columns, TableValidation::NONE)],
        &mut budget(),
    )?;
    let mut work = budget();
    let root = {
        let mut db = DatabaseReader::open(&path, &mut work)?;
        crate::update::indexed_writable_table(&mut db, b"T", &mut work)?.root()
    };
    let mut bytes = fs::read(&path)?;
    // EXP-0299: Nordic marks page zero at 0x3a and each column record's context.
    bytes[0x3a] = 0xf9;
    let definition = root.get() as usize * crate::PAGE_BYTES;
    let page = &mut bytes[definition..definition + crate::PAGE_BYTES];
    let context = page
        .windows(4)
        .position(|window| window == [0x09, 0x04, 0xe4, 0x04])
        .ok_or("column context")?;
    page[context] = 0x1d;
    fs::write(&path, &bytes)?;
    let mut db = DatabaseReader::open(&path, &mut work)?;
    let table = db.table_definition(root, &mut work)?;
    assert_eq!(
        table.columns()[0].raw_encoding_context(),
        &[0x1d, 0x04, 0xe4, 0x04]
    );
    assert_eq!(
        db.header().sort_order(),
        SortOrder::Other {
            raw: [0xf9, 0xc7, 0x9f, 0x46]
        }
    );
    db.validate(TextCodePage::Windows1252, &mut work)?;
    let refused = |error: UpdateError| matches!(error, UpdateError::UnsupportedSortOrder { .. });
    assert!(insert_row(&path, b"T", &[RowValue::Long(1)], &mut budget()).is_err_and(refused));
    let edit = SchemaEdit::DropColumn {
        table: b"T",
        column: b"A",
    };
    assert!(edit_schema(&path, edit, &mut budget()).is_err_and(refused));
    assert_eq!(fs::read(&path)?, bytes);
    let mut unobserved = bytes.clone();
    unobserved[definition + context + 2] = 0xe5;
    fs::write(&path, &unobserved)?;
    let mut db = DatabaseReader::open(&path, &mut work)?;
    assert!(matches!(
        db.table_definition(root, &mut work),
        Err(crate::TableDefinitionError::InvalidColumnEncodingContext { .. })
    ));
    Ok(())
}

#[test]
fn rules_refuse_cascaded_updates_and_autoincrement_backfill() -> TestResult {
    let directory = TestDirectory::create()?;
    let path = directory.target();
    let columns = [ColumnSpec::new(b"Id", ColumnType::Long)];
    let key = [crate::IndexColumnSpec::ascending(b"Id")];
    let indexes = [crate::IndexSpec {
        name: b"PrimaryKey",
        fields: &key,
        kind: crate::IndexKind::Primary,
    }];
    let child_columns = [ColumnSpec::new(b"ParentId", ColumnType::Long)];
    let tables = [
        crate::TableRows {
            table: TableSpec {
                name: b"Parent",
                columns: &columns,
                indexes: &indexes,
                validation: TableValidation::NONE,
            },
            rows: &[&[RowValue::Long(1)]],
        },
        crate::TableRows {
            table: table(b"Child", &child_columns, TableValidation::NONE),
            rows: &[&[RowValue::Long(1)]],
        },
    ];
    let relationship = crate::RelationshipSpec {
        unique: false,
        enforce: true,
        join: crate::RelationshipJoin::Inner,
        cascade_updates: true,
        cascade_deletes: false,
        name: b"ParentChild",
        parent: crate::TableRef::Name(b"Parent"),
        child: crate::TableRef::Name(b"Child"),
        fields: &[crate::RelationshipField {
            parent: crate::ColumnRef::Name(b"Id"),
            child: crate::ColumnRef::Name(b"ParentId"),
        }],
    };
    crate::create_database_with_relationships_and_rows(
        &path,
        &tables,
        &[relationship],
        &mut budget(),
    )?;
    let row = first_row(&path, b"Parent")?;
    for table in [b"Parent".as_slice(), b"Child"] {
        let rule = SchemaEdit::SetTableProperties {
            table,
            validation_rule: PropertyChange::Set(b"True"),
            validation_text: PropertyChange::Keep,
        };
        edit_schema(&path, rule, &mut budget())?;
    }
    let before = fs::read(&path)?;
    let request = RowUpdate {
        table: b"Parent",
        row,
        values: &[RowValue::Long(2)],
    };
    assert!(matches!(
        update_row(&path, request, &mut budget()),
        Err(UpdateError::ValidationRule { column: None })
    ));
    let edit = SchemaEdit::CreateColumn {
        table: b"Child",
        column: ColumnSpec::new(b"Serial", ColumnType::AutoIncrement),
    };
    assert!(edit_schema(&path, edit, &mut budget()).is_err());
    assert_eq!(fs::read(&path)?, before);
    // Only the child's rule remains: the parent write is refused for its cascade.
    let clear = SchemaEdit::SetTableProperties {
        table: b"Parent",
        validation_rule: PropertyChange::Clear,
        validation_text: PropertyChange::Keep,
    };
    edit_schema(&path, clear, &mut budget())?;
    let before = fs::read(&path)?;
    let row = first_row(&path, b"Parent")?;
    let request = RowUpdate {
        table: b"Parent",
        row,
        values: &[RowValue::Long(2)],
    };
    assert!(update_row(&path, request, &mut budget()).is_err());
    assert_eq!(fs::read(&path)?, before);
    Ok(())
}

#[test]
fn chained_property_blobs_grow_and_shrink_without_touching_rows() -> TestResult {
    let directory = TestDirectory::create()?;
    let path = directory.target();
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"Note", ColumnType::Memo),
    ];
    let plain = table(b"Items", &columns, TableValidation::NONE);
    let memo = [b'm'; 3000];
    create_database_with_rows(
        &path,
        &plain,
        &[&[RowValue::Long(1), RowValue::Memo(&memo)]],
        &mut budget(),
    )?;
    let user_pages = |bytes: &[u8]| -> Result<Vec<Vec<u8>>, Box<dyn std::error::Error>> {
        let mut work = budget();
        let mut db = DatabaseReader::open(&path, &mut work)?;
        let table = crate::update::indexed_writable_table(&mut db, b"Items", &mut work)?;
        Ok(table
            .pages()
            .iter()
            .map(|page| {
                let start = page.get() as usize * crate::PAGE_BYTES;
                bytes[start..start + crate::PAGE_BYTES].to_vec()
            })
            .collect())
    };
    let before = user_pages(&fs::read(&path)?)?;
    let [a, b, c, d] =
        [b'a', b'b', b'c', b'd'].map(|byte| [byte; crate::column_properties::MAX_TEXT_PROPERTY]);
    for edit in [
        SchemaEdit::SetColumnProperties {
            table: b"Items",
            column: b"Note",
            default_value: PropertyChange::Set(&a),
            validation_rule: PropertyChange::Keep,
            validation_text: PropertyChange::Set(&b),
            description: PropertyChange::Set(&c),
        },
        SchemaEdit::SetTableProperties {
            table: b"Items",
            validation_rule: PropertyChange::Keep,
            validation_text: PropertyChange::Set(&d),
        },
    ] {
        edit_schema(&path, edit, &mut budget())?;
    }
    let stored = properties(&path, b"Items")?;
    let note = &stored.columns()[1];
    assert_eq!(note.default_value(), Some(a.as_slice()));
    assert_eq!(note.validation_text(), Some(b.as_slice()));
    assert_eq!(note.description(), Some(c.as_slice()));
    assert_eq!(stored.validation_text(), Some(d.as_slice()));
    assert_eq!(user_pages(&fs::read(&path)?)?, before);
    edit_schema(
        &path,
        SchemaEdit::SetColumnProperties {
            table: b"Items",
            column: b"Note",
            default_value: PropertyChange::Clear,
            validation_rule: PropertyChange::Keep,
            validation_text: PropertyChange::Clear,
            description: PropertyChange::Set(b"short"),
        },
        &mut budget(),
    )?;
    let stored = properties(&path, b"Items")?;
    assert_eq!(stored.columns()[1].default_value(), None);
    assert_eq!(stored.columns()[1].description(), Some(b"short".as_slice()));
    assert_eq!(stored.validation_text(), Some(d.as_slice()));
    assert_eq!(user_pages(&fs::read(&path)?)?, before);
    Ok(())
}
