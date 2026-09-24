use super::index_tests::*;
use crate::*;
use std::fs;

fn relation<'a>(
    name: &'a [u8],
    parent: &'a [u8],
    child: &'a [u8],
    fields: &'a [RelationshipField<'a>],
) -> RelationshipSpec<'a> {
    RelationshipSpec {
        unique: false,
        enforce: false,
        join: RelationshipJoin::Inner,
        cascade_updates: false,
        cascade_deletes: false,
        name,
        parent: TableRef::Name(parent),
        child: TableRef::Name(child),
        fields,
    }
}

fn pair<'a>(parent: &'a [u8], child: &'a [u8]) -> RelationshipField<'a> {
    RelationshipField {
        parent: ColumnRef::Name(parent),
        child: ColumnRef::Name(child),
    }
}

fn catalog(fixture: &Fixture) -> Result<Vec<CatalogRelationship>, Box<dyn std::error::Error>> {
    let mut b = budget();
    let mut database = DatabaseReader::open(fixture.path(), &mut b)?;
    Ok(database.relationship_catalog(&mut b)?)
}

fn create_table(fixture: &Fixture, name: &[u8], columns: &[ColumnSpec<'_>]) -> TestResult {
    let indexes = [IndexSpec {
        name: b"PrimaryKey",
        fields: &[IndexColumnSpec::ascending(0)],
        kind: IndexKind::Primary,
    }];
    edit_schema(
        fixture.path(),
        SchemaEdit::CreateTable {
            table: TableSpec {
                validation: crate::TableValidation::NONE,
                name,
                columns,
                indexes: &indexes,
            },
        },
        &mut budget(),
    )?;
    Ok(())
}

fn refused(fixture: &Fixture, edit: SchemaEdit<'_>) -> TestResult {
    let before = fs::read(fixture.path())?;
    assert!(edit_schema(fixture.path(), edit, &mut budget()).is_err());
    assert_eq!(fs::read(fixture.path())?, before);
    Ok(())
}

#[test]
fn unenforced_relationships_are_catalog_only_and_follow_table_edits() -> TestResult {
    let fixture = Fixture::new(&[
        &[RowValue::Long(7), RowValue::Memo(b"a")],
        &[RowValue::Long(7), RowValue::Memo(b"b")],
    ])?;
    create_table(
        &fixture,
        b"Owners",
        &[ColumnSpec::new(b"Id", ColumnType::Long)],
    )?;
    create_table(
        &fixture,
        b"Children",
        &[
            ColumnSpec::new(b"Id", ColumnType::Long),
            ColumnSpec::new(
                b"Parent",
                ColumnType::Text {
                    max_len: std::num::NonZeroU8::new(10).ok_or("size")?,
                },
            ),
            ColumnSpec::new(b"Note", ColumnType::Memo),
            ColumnSpec::new(b"Owner", ColumnType::Long),
        ],
    )?;
    let before = [fixture.table()?, {
        let mut b = budget();
        let mut database = DatabaseReader::open(fixture.path(), &mut b)?;
        crate::write::update::indexed_writable_table(&mut database, b"Children", &mut b)?
    }];
    // EXP-0301: a non-unique parent, mismatched types and Memo keys are accepted.
    let loose = [pair(b"Id", b"Parent")];
    let memo = [pair(b"Payload", b"Note")];
    for spec in [
        relation(b"Loose", b"Items", b"Children", &loose),
        RelationshipSpec {
            join: RelationshipJoin::Right,
            ..relation(b"MemoLink", b"Items", b"Children", &memo)
        },
        RelationshipSpec {
            unique: false,
            enforce: true,
            ..relation(b"Owned", b"Owners", b"Children", &[pair(b"Id", b"Owner")])
        },
    ] {
        edit_schema(
            fixture.path(),
            SchemaEdit::CreateRelationship { relationship: spec },
            &mut budget(),
        )?;
    }
    let mut b = budget();
    let mut database = DatabaseReader::open(fixture.path(), &mut b)?;
    let items = crate::write::update::indexed_writable_table(&mut database, b"Items", &mut b)?;
    let children =
        crate::write::update::indexed_writable_table(&mut database, b"Children", &mut b)?;
    assert_eq!(items.indexes(), before[0].indexes());
    assert_eq!(children.relationships().count(), 1);
    let report = crate::relationship::catalog::validate(&mut database, &mut b)?;
    assert_eq!((report.verified, report.unenforced), (1, 2));
    let relations = catalog(&fixture)?;
    let attributes: Vec<_> = relations
        .iter()
        .map(|relation| (relation.name().to_vec(), relation.raw_attributes()))
        .collect();
    assert_eq!(
        attributes,
        [
            (b"Loose".to_vec(), 2),
            (b"MemoLink".to_vec(), 0x0200_0002),
            (b"Owned".to_vec(), 0)
        ]
    );
    assert!(!relations[1].enforced() && relations[1].join() == RelationshipJoin::Right);

    // Unenforced keys are not checked; the enforced relationship still is.
    insert_row(
        fixture.path(),
        b"Children",
        &[
            RowValue::Long(1),
            RowValue::Text(b"99"),
            RowValue::Memo(b"orphan"),
            RowValue::Null,
        ],
        &mut budget(),
    )?;
    let orphan = fs::read(fixture.path())?;
    assert!(matches!(
        insert_row(
            fixture.path(),
            b"Children",
            &[
                RowValue::Long(2),
                RowValue::Null,
                RowValue::Null,
                RowValue::Long(5),
            ],
            &mut budget(),
        ),
        Err(UpdateError::RelationshipConstraint { .. })
    ));
    assert_eq!(fs::read(fixture.path())?, orphan);

    refused(
        &fixture,
        SchemaEdit::DropColumn {
            table: b"Children",
            column: b"Note",
        },
    )?;
    refused(
        &fixture,
        SchemaEdit::DropColumn {
            table: b"Items",
            column: b"Id",
        },
    )?;
    refused(
        &fixture,
        SchemaEdit::CreateRelationship {
            relationship: RelationshipSpec {
                cascade_deletes: true,
                ..relation(b"Cascading", b"Items", b"Children", &loose)
            },
        },
    )?;
    refused(
        &fixture,
        SchemaEdit::CreateRelationship {
            relationship: relation(b"loose", b"Items", b"Children", &loose),
        },
    )?;
    edit_schema(
        fixture.path(),
        SchemaEdit::DropRelationship { name: b"MemoLink" },
        &mut budget(),
    )?;
    edit_schema(
        fixture.path(),
        SchemaEdit::RenameColumn {
            table: b"Children",
            column: b"Parent",
            name: b"ItemKey",
        },
        &mut budget(),
    )?;
    edit_schema(
        fixture.path(),
        SchemaEdit::RenameTable {
            table: b"Items",
            name: b"Things",
        },
        &mut budget(),
    )?;
    let wide = [pair(b"Id", b"Id"); 11];
    edit_schema(
        fixture.path(),
        SchemaEdit::CreateRelationship {
            relationship: relation(b"Wide", b"Things", b"Children", &wide),
        },
        &mut budget(),
    )?;
    assert_eq!(catalog(&fixture)?[2].fields().len(), 11);
    edit_schema(
        fixture.path(),
        SchemaEdit::DropRelationship { name: b"Wide" },
        &mut budget(),
    )?;
    let relations = catalog(&fixture)?;
    assert_eq!(relations.len(), 2);
    assert_eq!(relations[0].parent_table(), b"Things");
    assert_eq!(relations[0].fields()[0].child(), b"ItemKey");
    // Dropping either endpoint of an unenforced relationship removes it (EXP-0301).
    edit_schema(
        fixture.path(),
        SchemaEdit::DropTable { table: b"Things" },
        &mut budget(),
    )?;
    let relations = catalog(&fixture)?;
    assert_eq!(relations.len(), 1);
    assert_eq!(relations[0].name(), b"Owned");
    refused(&fixture, SchemaEdit::DropTable { table: b"Owners" })?;
    let mut b = budget();
    DatabaseReader::open(fixture.path(), &mut b)?.validate(TextCodePage::Windows1252, &mut b)?;
    Ok(())
}

#[test]
fn join_types_are_stored_in_the_attributes_only() -> TestResult {
    let fixture = Fixture::new(&[&[RowValue::Long(7), RowValue::Memo(b"a")]])?;
    fixture.create(b"Unique", IndexKind::Unique, IndexDirection::Ascending)?;
    create_table(
        &fixture,
        b"Children",
        &[
            ColumnSpec::new(b"Id", ColumnType::Long),
            ColumnSpec::new(b"Parent", ColumnType::Long),
        ],
    )?;
    let fields = [pair(b"Id", b"Parent")];
    let spec = RelationshipSpec {
        unique: false,
        enforce: true,
        join: RelationshipJoin::Left,
        cascade_deletes: true,
        ..relation(b"Joined", b"Items", b"Children", &fields)
    };
    edit_schema(
        fixture.path(),
        SchemaEdit::CreateRelationship { relationship: spec },
        &mut budget(),
    )?;
    assert_eq!(catalog(&fixture)?[0].raw_attributes(), 0x0100_1000);
    edit_schema(
        fixture.path(),
        SchemaEdit::ReplaceRelationship {
            name: b"Joined",
            relationship: RelationshipSpec {
                join: RelationshipJoin::Inner,
                ..spec
            },
        },
        &mut budget(),
    )?;
    assert_eq!(catalog(&fixture)?[0].raw_attributes(), 0x1000);
    let mut b = budget();
    let mut database = DatabaseReader::open(fixture.path(), &mut b)?;
    assert_eq!(
        crate::relationship::catalog::validate(&mut database, &mut b)?.verified,
        1
    );
    Ok(())
}

#[test]
fn database_creation_stores_joins_and_refuses_unenforced_relationships() -> TestResult {
    let fixture = Fixture::new(&[])?;
    let target = fixture.0.join("created.mdb");
    let fields = [pair(b"Id", b"Id")];
    let tables = [TableRows {
        table: TableSpec {
            validation: crate::TableValidation::NONE,
            name: b"Items",
            columns: &[ColumnSpec::new(b"Id", ColumnType::Long)],
            indexes: &[IndexSpec {
                name: b"PrimaryKey",
                fields: &[IndexColumnSpec::ascending(0)],
                kind: IndexKind::Primary,
            }],
        },
        rows: &[],
    }];
    let result = create_database_with_relationships_and_rows(
        &target,
        &tables,
        &[relation(b"Self", b"Items", b"Items", &fields)],
        &mut budget(),
    );
    assert!(matches!(
        result,
        Err(CreateDatabaseError::Compose(
            ComposeError::UnsupportedRelationship { .. }
        ))
    ));
    assert!(!target.exists());
    let joined = RelationshipSpec {
        unique: false,
        enforce: true,
        join: RelationshipJoin::LeftAndRight,
        cascade_updates: true,
        ..relation(b"Self", b"Items", b"Items", &fields)
    };
    let fields = [pair(b"Id", b"Parent")];
    let tables = [TableRows {
        table: TableSpec {
            columns: &[
                ColumnSpec::new(b"Id", ColumnType::Long),
                ColumnSpec::new(b"Parent", ColumnType::Long),
            ],
            ..tables[0].table
        },
        rows: &[],
    }];
    create_database_with_relationships_and_rows(
        &target,
        &tables,
        &[RelationshipSpec {
            fields: &fields,
            ..joined
        }],
        &mut budget(),
    )?;
    let mut b = budget();
    let mut database = DatabaseReader::open(&target, &mut b)?;
    let relations = database.relationship_catalog(&mut b)?;
    assert_eq!(relations[0].raw_attributes(), 0x0300_0100);
    assert!(relations[0].interpreted() && relations[0].cascade_updates());
    assert_eq!(
        crate::relationship::catalog::validate(&mut database, &mut b)?.verified,
        1
    );
    Ok(())
}

#[test]
fn one_to_one_mutations_enforce_child_uniqueness_and_allow_repeated_nulls() -> TestResult {
    let fixture = Fixture::new(&[&[RowValue::Long(7), RowValue::Memo(b"parent")]])?;
    fixture.create(b"Unique", IndexKind::Unique, IndexDirection::Ascending)?;
    create_table(
        &fixture,
        b"Children",
        &[
            ColumnSpec::new(b"Id", ColumnType::Long),
            ColumnSpec::new(b"Parent", ColumnType::Long),
        ],
    )?;
    let fields = [pair(b"Id", b"Parent")];
    let spec = RelationshipSpec {
        unique: true,
        enforce: true,
        cascade_updates: true,
        cascade_deletes: true,
        ..relation(b"One", b"Items", b"Children", &fields)
    };
    edit_schema(
        fixture.path(),
        SchemaEdit::CreateRelationship { relationship: spec },
        &mut budget(),
    )?;
    for (id, value) in [
        (1, RowValue::Long(7)),
        (2, RowValue::Null),
        (3, RowValue::Null),
    ] {
        insert_row(
            fixture.path(),
            b"Children",
            &[RowValue::Long(id), value],
            &mut budget(),
        )?;
    }
    let before = fs::read(fixture.path())?;
    assert!(
        insert_row(
            fixture.path(),
            b"Children",
            &[RowValue::Long(4), RowValue::Long(7)],
            &mut budget()
        )
        .is_err()
    );
    assert_eq!(fs::read(fixture.path())?, before);
    let mut b = budget();
    let mut db = DatabaseReader::open(fixture.path(), &mut b)?;
    let parent = fixture.table()?;
    let locator = db
        .rows(&parent, &mut b)?
        .next_row()?
        .ok_or("parent")?
        .locator();
    crate::update_row(
        fixture.path(),
        crate::RowUpdate {
            table: b"Items",
            row: locator,
            values: &[RowValue::Long(8), RowValue::Memo(b"parent")],
        },
        &mut budget(),
    )?;
    crate::delete_row(
        fixture.path(),
        crate::RowDelete {
            table: b"Items",
            row: locator,
        },
        &mut budget(),
    )?;
    let mut b = budget();
    let mut db = DatabaseReader::open(fixture.path(), &mut b)?;
    let child = crate::write::update::indexed_writable_table(&mut db, b"Children", &mut b)?;
    assert_eq!(child.row_count(), 2);
    assert_eq!(
        crate::relationship::catalog::validate(&mut db, &mut b)?.verified,
        1
    );
    assert_eq!(catalog(&fixture)?[0].raw_attributes(), 4353);
    edit_schema(
        fixture.path(),
        SchemaEdit::ReplaceRelationship {
            name: b"One",
            relationship: RelationshipSpec {
                enforce: false,
                cascade_updates: false,
                cascade_deletes: false,
                ..spec
            },
        },
        &mut budget(),
    )?;
    assert_eq!(catalog(&fixture)?[0].raw_attributes(), 3);
    for id in [4, 5] {
        insert_row(
            fixture.path(),
            b"Children",
            &[RowValue::Long(id), RowValue::Long(99)],
            &mut budget(),
        )?;
    }
    refused(
        &fixture,
        SchemaEdit::ReplaceRelationship {
            name: b"One",
            relationship: spec,
        },
    )?;
    Ok(())
}
