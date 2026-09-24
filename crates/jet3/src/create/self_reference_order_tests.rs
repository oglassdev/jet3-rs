//! EXP-0286 self-reference boundaries and physical index update order.
use super::descending_parent_tests::*;
use crate::testkit::create_spec;
use crate::testkit::{index, table};
use crate::{
    ColumnRef, ColumnSpec, ColumnType, DatabaseReader, IndexColumnSpec, IndexKind,
    RelationshipField, RelationshipSide, RowValue, TextCodePage,
    create::{DatabaseSpec, TableRows, api_relationship_graph_tests::*},
};
use std::fs;

#[test]
fn deleting_the_only_null_self_reference_removes_its_child_reference() -> TestResult {
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"ParentKey", ColumnType::Long),
        ColumnSpec::new(b"ParentId", ColumnType::Long),
    ];
    let indexes = [
        index(b"ById", ID, IndexKind::Primary),
        index(b"Key", DESC, IndexKind::Unique),
    ];
    let table = table(b"Node", &columns, &indexes);
    let mut relation = edge(0);
    relation.fields = &[RelationshipField {
        parent: ColumnRef::Ordinal(1),
        child: ColumnRef::Ordinal(2),
    }];
    let directory = TempDir::new("create")?;
    create_spec(
        directory.target(),
        &DatabaseSpec {
            tables: &[TableRows {
                table,
                rows: &[&[RowValue::Long(1), RowValue::Null, RowValue::Null]],
            }],
            relationships: &[relation],
            ..DatabaseSpec::default()
        },
    )?;
    let d = definition(&directory.target(), b"Node")?;
    let row = {
        let mut work = budget();
        let mut db = DatabaseReader::open(directory.target(), &mut work)?;
        db.rows(&d, &mut work)?
            .next_row()?
            .ok_or("self row")?
            .locator()
    };
    let before = fs::read(directory.target())?;
    assert!(matches!(
        crate::update_row(
            directory.target(),
            crate::RowUpdate {
                table: b"Node",
                row,
                values: &[RowValue::Long(1), RowValue::Long(4), RowValue::Null]
            },
            &mut budget()
        ),
        Err(crate::WriteError::NullRelationshipConstraint { .. })
    ));
    assert_eq!(fs::read(directory.target())?, before);
    crate::delete_row(
        directory.target(),
        crate::RowDelete {
            table: b"Node",
            row,
        },
        &mut budget(),
    )?;
    assert_eq!(definition(&directory.target(), b"Node")?.row_count(), 0);
    Ok(())
}

#[test]
fn parent_tree_after_foreign_requires_existing_self_keys() -> TestResult {
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"ParentKey", ColumnType::Long),
        ColumnSpec::new(b"ParentId", ColumnType::Long),
    ];
    let indexes = [
        index(b"ById", ID, IndexKind::Primary),
        index(b"Key", DESC, IndexKind::Unique),
    ];
    let table = table(b"Node", &columns, &indexes);
    let mut relation = edge(0);
    relation.fields = &[RelationshipField {
        parent: ColumnRef::Ordinal(1),
        child: ColumnRef::Ordinal(2),
    }];
    let states = [
        None,
        Some([RowValue::Long(1), RowValue::Null, RowValue::Null]),
        Some([RowValue::Long(1), RowValue::Long(1), RowValue::Null]),
        Some([RowValue::Long(1), RowValue::Long(1), RowValue::Long(1)]),
    ];
    for initial in states {
        let directory = TempDir::new("create")?;
        let initial_rows: Vec<&[RowValue<'_>]> =
            initial.as_ref().map(|r| r.as_slice()).into_iter().collect();
        create_spec(
            directory.target(),
            &DatabaseSpec {
                tables: &[TableRows {
                    table,
                    rows: &initial_rows,
                }],
                relationships: &[relation],
                ..DatabaseSpec::default()
            },
        )?;
        let before = fs::read(directory.target())?;
        let replacement = [RowValue::Long(1), RowValue::Long(4), RowValue::Long(4)];
        let result = if initial.is_some() {
            let d = definition(&directory.target(), b"Node")?;
            let row = {
                let mut work = budget();
                let mut db = DatabaseReader::open(directory.target(), &mut work)?;
                db.rows(&d, &mut work)?
                    .next_row()?
                    .ok_or("self row")?
                    .locator()
            };
            crate::update_row(
                directory.target(),
                crate::RowUpdate {
                    table: b"Node",
                    row,
                    values: &replacement,
                },
                &mut budget(),
            )
        } else {
            crate::insert_row(directory.target(), b"Node", &replacement, &mut budget()).map(|_| ())
        };
        assert!(matches!(
            result,
            Err(crate::WriteError::RelationshipConstraint { value: 4, .. })
        ));
        assert_eq!(fs::read(directory.target())?, before);
    }

    let directory = TempDir::new("create")?;
    create_spec(
        directory.target(),
        &DatabaseSpec {
            tables: &[TableRows {
                table,
                rows: &[
                    &[RowValue::Long(1), RowValue::Null, RowValue::Null],
                    &[RowValue::Long(2), RowValue::Long(1), RowValue::Long(1)],
                ],
            }],
            relationships: &[relation],
            ..DatabaseSpec::default()
        },
    )?;
    let d = definition(&directory.target(), b"Node")?;
    let row = {
        let mut work = budget();
        let mut db = DatabaseReader::open(directory.target(), &mut work)?;
        db.rows(&d, &mut work)?
            .next_row()?
            .ok_or("null self row")?
            .locator()
    };
    crate::update_row(
        directory.target(),
        crate::RowUpdate {
            table: b"Node",
            row,
            values: &[RowValue::Long(1), RowValue::Long(4), RowValue::Long(1)],
        },
        &mut budget(),
    )?;
    let mut db = DatabaseReader::open(directory.target(), &mut budget())?;
    assert_eq!(
        db.validate(TextCodePage::Windows1252, &mut budget())?
            .relationships_with_verified_keys,
        1
    );
    Ok(())
}

#[test]
fn self_key_checks_follow_physical_order_for_generated_and_declared_parents() -> TestResult {
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"ParentKey", ColumnType::Long),
        ColumnSpec::new(b"ParentId", ColumnType::Long),
    ];
    let foreign_fields = [IndexColumnSpec::ascending(ColumnRef::Ordinal(2))];
    for generated_parent_first in [false, true] {
        let indexes = if generated_parent_first {
            vec![
                index(b"ById", ID, IndexKind::Primary),
                index(b"Descending", DESC, IndexKind::Unique),
            ]
        } else {
            vec![
                index(b"ById", ID, IndexKind::Primary),
                index(b"Child", &foreign_fields, IndexKind::Ordinary),
                index(b"Ascending", ASC, IndexKind::Unique),
            ]
        };
        let table = table(b"Node", &columns, &indexes);
        let child = crate::testkit::table(b"Child", PAIR_COLUMNS, &[]);
        let mut external = edge(1);
        external.name = b"External";
        let mut self_relation = edge(0);
        self_relation.name = b"SelfLink";
        self_relation.fields = &[RelationshipField {
            parent: ColumnRef::Ordinal(1),
            child: ColumnRef::Ordinal(2),
        }];
        let relations = [external, self_relation];
        for insert in [false, true] {
            let directory = TempDir::new("create")?;
            let row = [RowValue::Long(1), RowValue::Long(1), RowValue::Long(1)];
            let initial: &[&[RowValue<'_>]] = if insert { &[] } else { &[&row] };
            create_spec(
                directory.target(),
                &DatabaseSpec {
                    tables: &[
                        TableRows {
                            table,
                            rows: initial,
                        },
                        TableRows {
                            table: child,
                            rows: &[],
                        },
                    ],
                    relationships: &relations[usize::from(!generated_parent_first)..],
                    ..DatabaseSpec::default()
                },
            )?;
            let d = definition(&directory.target(), b"Node")?;
            let foreign = d
                .relationships()
                .find(|r| r.side() == RelationshipSide::ForeignTable)
                .ok_or("self foreign index")?
                .physical_index();
            let parent = d
                .relationships()
                .find(|r| {
                    r.side() == RelationshipSide::PrimaryTable && r.related_table() == d.root()
                })
                .ok_or("self parent index")?
                .physical_index();
            assert_eq!(parent, 2);
            assert_eq!(foreign, if generated_parent_first { 3 } else { 1 });
            let before = fs::read(directory.target())?;
            let values = [RowValue::Long(4), RowValue::Long(4), RowValue::Long(4)];
            let result = if insert {
                crate::insert_row(directory.target(), b"Node", &values, &mut budget()).map(|_| ())
            } else {
                let locator = {
                    let mut work = budget();
                    let mut db = DatabaseReader::open(directory.target(), &mut work)?;
                    db.rows(&d, &mut work)?
                        .next_row()?
                        .ok_or("self row")?
                        .locator()
                };
                crate::update_row(
                    directory.target(),
                    crate::RowUpdate {
                        table: b"Node",
                        row: locator,
                        values: &[RowValue::Long(1), RowValue::Long(4), RowValue::Long(4)],
                    },
                    &mut budget(),
                )
            };
            if generated_parent_first {
                result?;
                let mut db = DatabaseReader::open(directory.target(), &mut budget())?;
                assert_eq!(
                    db.validate(TextCodePage::Windows1252, &mut budget())?
                        .relationships_with_verified_keys,
                    2
                );
            } else {
                assert!(matches!(
                    result,
                    Err(crate::WriteError::RelationshipConstraint { value: 4, .. })
                ));
                assert_eq!(fs::read(directory.target())?, before);
            }
        }
    }
    Ok(())
}
