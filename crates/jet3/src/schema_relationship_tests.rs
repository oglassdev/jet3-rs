use super::*;

#[test]
fn relationship_lifecycle_shares_indexes_preserves_rows_and_drops_with_child() -> TestResult {
    for descending in [false, true] {
        let fixture = Fixture::new(&[&[RowValue::Long(7), RowValue::Memo(b"parent")]])?;
        fixture.create(
            b"UniqueParent",
            IndexKind::Unique,
            if descending {
                IndexDirection::Descending
            } else {
                IndexDirection::Ascending
            },
        )?;
        edit_schema(
            fixture.path(),
            SchemaEdit::CreateTable {
                table: TableSpec {
                    name: b"Children",
                    columns: &[
                        ColumnSpec::new(b"Id", ColumnType::Long),
                        ColumnSpec::new(b"Parent", ColumnType::Long),
                    ],
                    indexes: &[IndexSpec {
                        name: b"ExistingForeign",
                        fields: &[IndexColumnSpec::ascending(1)],
                        kind: IndexKind::Ordinary,
                    }],
                },
            },
            &mut budget(),
        )?;
        insert_row(
            fixture.path(),
            b"Children",
            &[RowValue::Long(1), RowValue::Long(7)],
            &mut budget(),
        )?;
        let spec = RelationshipSpec {
            name: b"Related",
            parent: TableRef::Name(b"Items"),
            child: TableRef::Name(b"Children"),
            fields: &[RelationshipField {
                parent: ColumnRef::Ordinal(0),
                child: ColumnRef::Ordinal(1),
            }],
            cascade_updates: false,
            cascade_deletes: false,
        };
        edit_schema(
            fixture.path(),
            SchemaEdit::CreateRelationship { relationship: spec },
            &mut budget(),
        )?;
        let mut b = budget();
        let mut database = DatabaseReader::open(fixture.path(), &mut b)?;
        assert_eq!(
            crate::relationship_catalog::validate(&mut database, &mut b)?.verified,
            1
        );
        let child = crate::update::indexed_writable_table(&mut database, b"Children", &mut b)?;
        assert_eq!(child.physical_indexes().len(), 1);
        let before = fs::read(fixture.path())?;
        assert!(
            edit_schema(
                fixture.path(),
                SchemaEdit::DropTable { table: b"Items" },
                &mut budget()
            )
            .is_err()
        );
        assert!(
            edit_schema(
                fixture.path(),
                SchemaEdit::ReplaceRelationship {
                    name: b"Related",
                    relationship: RelationshipSpec {
                        child: TableRef::Name(b"Missing"),
                        ..spec
                    }
                },
                &mut budget()
            )
            .is_err()
        );
        assert_eq!(fs::read(fixture.path())?, before);
        edit_schema(
            fixture.path(),
            SchemaEdit::ReplaceRelationship {
                name: b"Related",
                relationship: RelationshipSpec {
                    name: b"Cascading",
                    cascade_updates: true,
                    cascade_deletes: true,
                    ..spec
                },
            },
            &mut budget(),
        )?;
        edit_schema(
            fixture.path(),
            SchemaEdit::RenameColumn {
                table: b"Items",
                column: b"Id",
                name: b"ParentId",
            },
            &mut budget(),
        )?;
        edit_schema(
            fixture.path(),
            SchemaEdit::RenameTable {
                table: b"Items",
                name: b"Parents",
            },
            &mut budget(),
        )?;
        edit_schema(
            fixture.path(),
            SchemaEdit::DropTable { table: b"Children" },
            &mut budget(),
        )?;
        let mut b = budget();
        let mut database = DatabaseReader::open(fixture.path(), &mut b)?;
        assert_eq!(
            crate::relationship_catalog::validate(&mut database, &mut b)?.verified,
            0
        );
        let parent = crate::update::indexed_writable_table(&mut database, b"Parents", &mut b)?;
        assert_eq!(parent.indexes().len(), 1);
        assert_eq!(parent.row_count(), 1);
    }
    Ok(())
}

#[test]
fn self_relationship_add_drop_and_orphan_refusal_are_atomic() -> TestResult {
    let fixture = Fixture::new(&[&[RowValue::Long(7), RowValue::Null]])?;
    fixture.create(b"PrimaryKey", IndexKind::Primary, IndexDirection::Ascending)?;
    edit_schema(
        fixture.path(),
        SchemaEdit::CreateColumn {
            table: b"Items",
            column: ColumnSpec::new(b"Parent", ColumnType::Long),
        },
        &mut budget(),
    )?;
    let spec = RelationshipSpec {
        name: b"SelfParent",
        parent: TableRef::Name(b"Items"),
        child: TableRef::Name(b"Items"),
        fields: &[RelationshipField {
            parent: ColumnRef::Name(b"Id"),
            child: ColumnRef::Name(b"Parent"),
        }],
        cascade_updates: true,
        cascade_deletes: true,
    };
    edit_schema(
        fixture.path(),
        SchemaEdit::CreateRelationship { relationship: spec },
        &mut budget(),
    )?;
    let table = fixture.table()?;
    assert_eq!(table.indexes().len(), 3);
    assert_eq!(table.physical_indexes().len(), 2);
    assert_eq!(table.relationships().count(), 2);
    let before = fs::read(fixture.path())?;
    assert!(
        insert_row(
            fixture.path(),
            b"Items",
            &[RowValue::Long(8), RowValue::Null, RowValue::Long(99)],
            &mut budget()
        )
        .is_err()
    );
    assert_eq!(fs::read(fixture.path())?, before);
    edit_schema(
        fixture.path(),
        SchemaEdit::DropRelationship {
            name: b"SelfParent",
        },
        &mut budget(),
    )?;
    assert_eq!(fixture.table()?.indexes().len(), 1);
    insert_row(
        fixture.path(),
        b"Items",
        &[RowValue::Long(8), RowValue::Null, RowValue::Long(99)],
        &mut budget(),
    )?;
    let before = fs::read(fixture.path())?;
    assert!(
        edit_schema(
            fixture.path(),
            SchemaEdit::CreateRelationship { relationship: spec },
            &mut budget()
        )
        .is_err()
    );
    assert_eq!(fs::read(fixture.path())?, before);
    Ok(())
}
