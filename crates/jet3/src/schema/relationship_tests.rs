use super::index_tests::*;
use super::relationship_form_tests::{edit, refused, verified, writable};
use crate::testkit::index;
use crate::testkit::table;
use crate::*;
use std::fs;

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
        edit(
            &fixture,
            SchemaEdit::CreateTable {
                table: table(
                    b"Children",
                    &[
                        ColumnSpec::new(b"Id", ColumnType::Long),
                        ColumnSpec::new(b"Parent", ColumnType::Long),
                    ],
                    &[index(
                        b"ExistingForeign",
                        &[IndexColumnSpec::ascending(1)],
                        IndexKind::Ordinary,
                    )],
                ),
            },
        )?;
        insert_row(
            fixture.path(),
            b"Children",
            &[RowValue::Long(1), RowValue::Long(7)],
            &mut budget(),
        )?;
        let spec = RelationshipSpec {
            unique: false,
            enforce: true,
            join: crate::RelationshipJoin::Inner,
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
        edit(
            &fixture,
            SchemaEdit::CreateRelationship { relationship: spec },
        )?;
        assert_eq!(verified(&fixture)?, 1);
        let child = writable(&fixture, b"Children")?;
        assert_eq!(child.physical_indexes().len(), 1);
        edit(
            &fixture,
            SchemaEdit::DropIndex {
                table: b"Children",
                index: b"ExistingForeign",
            },
        )?;
        let retained = writable(&fixture, b"Children")?;
        assert_eq!(retained.physical_indexes(), child.physical_indexes());
        assert_eq!(retained.indexes().len(), 1);
        refused(&fixture, SchemaEdit::DropTable { table: b"Items" })?;
        refused(
            &fixture,
            SchemaEdit::ReplaceRelationship {
                name: b"Related",
                relationship: RelationshipSpec {
                    child: TableRef::Name(b"Missing"),
                    ..spec
                },
            },
        )?;
        edit(
            &fixture,
            SchemaEdit::ReplaceRelationship {
                name: b"Related",
                relationship: RelationshipSpec {
                    name: b"Cascading",
                    cascade_updates: true,
                    cascade_deletes: true,
                    ..spec
                },
            },
        )?;
        vary_parent_spelling(&fixture)?;
        edit(
            &fixture,
            SchemaEdit::RenameColumn {
                table: b"Items",
                column: b"Id",
                name: b"ParentId",
            },
        )?;
        edit(
            &fixture,
            SchemaEdit::RenameTable {
                table: b"Items",
                name: b"Parents",
            },
        )?;
        edit(&fixture, SchemaEdit::DropTable { table: b"Children" })?;
        assert_eq!(verified(&fixture)?, 0);
        let parent = writable(&fixture, b"Parents")?;
        assert_eq!(parent.indexes().len(), 1);
        assert_eq!(parent.row_count(), 1);
    }
    Ok(())
}

#[test]
fn self_relationship_add_drop_and_orphan_refusal_are_atomic() -> TestResult {
    let fixture = Fixture::new(&[&[RowValue::Long(7), RowValue::Null]])?;
    fixture.create(b"PrimaryKey", IndexKind::Primary, IndexDirection::Ascending)?;
    edit(
        &fixture,
        SchemaEdit::CreateColumn {
            table: b"Items",
            column: ColumnSpec::new(b"Parent", ColumnType::Long),
        },
    )?;
    let spec = RelationshipSpec {
        unique: false,
        enforce: true,
        join: crate::RelationshipJoin::Inner,
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
    edit(
        &fixture,
        SchemaEdit::CreateRelationship { relationship: spec },
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
    edit(
        &fixture,
        SchemaEdit::DropRelationship {
            name: b"SelfParent",
        },
    )?;
    assert_eq!(fixture.table()?.indexes().len(), 1);
    insert_row(
        fixture.path(),
        b"Items",
        &[RowValue::Long(8), RowValue::Null, RowValue::Long(99)],
        &mut budget(),
    )?;
    refused(
        &fixture,
        SchemaEdit::CreateRelationship { relationship: spec },
    )?;
    Ok(())
}

fn vary_parent_spelling(fixture: &Fixture) -> TestResult {
    crate::schema::edit::run(&fixture.path(), &mut budget(), |file, journal, budget| {
        crate::schema::edit::apply(file, journal, budget, |database, budget| {
            let table = crate::schema::catalog::table(database, b"MSysRelationships", budget)?;
            let object = crate::schema::catalog::column(&table, b"szReferencedObject")?;
            let column = crate::schema::catalog::column(&table, b"szReferencedColumn")?;
            let mut rows = database.rows(&table, budget)?;
            let row = rows
                .next_row()?
                .ok_or(WriteError::NotFound("relationship row"))?
                .locator();
            drop(rows);
            let graph =
                crate::row::mutation_graph::RowGraph::load(database, &table, Some(row), budget)?;
            let edits = crate::write::field_update::plan_fields(
                database,
                &table,
                graph,
                row,
                &[
                    (object, RowValue::Text(b"iTeMs")),
                    (column, RowValue::Text(b"iD")),
                ],
                budget,
            )?;
            Ok((edits, ()))
        })?;
        crate::schema::edit::apply(file, journal, budget, |database, budget| {
            crate::relationship::catalog::validate(database, budget)?;
            Ok((
                crate::write::page_edits::PageEdits::new(database.geometry().page_count()),
                (),
            ))
        })
    })?;
    Ok(())
}

#[test]
fn relationships_reuse_zero_logical_identity_after_index_drop() -> TestResult {
    let fixture = Fixture::new(&[&[RowValue::Long(7), RowValue::Null]])?;
    fixture.create(b"Discard", IndexKind::Ordinary, IndexDirection::Ascending)?;
    fixture.create(b"Retain", IndexKind::Unique, IndexDirection::Ascending)?;
    fixture.drop_index(b"Discard")?;
    edit(
        &fixture,
        SchemaEdit::CreateTable {
            table: table(
                b"Child",
                &[ColumnSpec::new(b"ParentId", ColumnType::Long)],
                &[],
            ),
        },
    )?;
    let spec = RelationshipSpec {
        unique: false,
        enforce: true,
        join: crate::RelationshipJoin::Inner,
        name: b"Relation",
        parent: TableRef::Name(b"Items"),
        child: TableRef::Name(b"Child"),
        fields: &[RelationshipField {
            parent: ColumnRef::Ordinal(0),
            child: ColumnRef::Ordinal(0),
        }],
        cascade_updates: false,
        cascade_deletes: false,
    };
    edit(
        &fixture,
        SchemaEdit::CreateRelationship { relationship: spec },
    )?;
    let parent = fixture.table()?;
    let relation = parent.relationships().next().ok_or("relation")?;
    assert_eq!(relation.name().raw_bytes(), b".r");
    assert_eq!(relation.raw_selector(), 0);
    assert_eq!(relation.raw_relation_ordinal(), 0);
    edit(&fixture, SchemaEdit::DropRelationship { name: b"Relation" })?;
    assert_eq!(fixture.table()?.indexes()[0].name().raw_bytes(), b"Retain");
    Ok(())
}
