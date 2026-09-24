//! EXP-0286 generated ascending parent trees and retained mutation counters.
use super::api_relationship_graph_tests::*;
use crate::{
    ColumnRef, ColumnSpec, ColumnType, DatabaseReader, IndexColumnSpec, IndexDirection, IndexKind,
    IndexNullPolicy, IndexSpec, RelationshipField, RelationshipSide, RelationshipSpec, RowValue,
    TableSpec, TextCodePage,
    create::{
        api::*,
        api_relationship_graph::{
            create_database_with_relationships, create_database_with_relationships_and_rows,
        },
        composer::ComposeError,
    },
};
use std::fs;
use std::path::Path;

pub(super) const PAIR_COLUMNS: &[ColumnSpec<'static>] = &[
    ColumnSpec::new(b"Id", ColumnType::Long),
    ColumnSpec::new(b"Key", ColumnType::Long),
];
pub(super) const DESC: &[IndexColumnSpec<'static>] = &[IndexColumnSpec {
    column: ColumnRef::Ordinal(1),
    direction: IndexDirection::Descending,
}];
pub(super) const ASC: &[IndexColumnSpec<'static>] = &[IndexColumnSpec {
    column: ColumnRef::Ordinal(1),
    direction: IndexDirection::Ascending,
}];
pub(super) const ID: &[IndexColumnSpec<'static>] = &[IndexColumnSpec {
    column: ColumnRef::Ordinal(0),
    direction: IndexDirection::Ascending,
}];
pub(super) fn edge(child: usize) -> RelationshipSpec<'static> {
    let mut edge = relation(b"Relation", 0, child, 1);
    edge.fields = &[RelationshipField {
        parent: ColumnRef::Ordinal(1),
        child: ColumnRef::Ordinal(1),
    }];
    edge
}
pub(super) fn definition(
    path: &Path,
    name: &[u8],
) -> Result<crate::TableDefinition, Box<dyn std::error::Error>> {
    let mut work = budget();
    let mut db = DatabaseReader::open(path, &mut work)?;
    Ok(crate::write::update::indexed_writable_table(
        &mut db, name, &mut work,
    )?)
}

#[test]
fn descending_parents_generate_ascending_trees_with_the_same_null_policy() -> TestResult {
    let columns = [
        PAIR_COLUMNS[0],
        PAIR_COLUMNS[1],
        ColumnSpec::new(b"ForeignKey", ColumnType::Long),
    ];
    for kind in [
        IndexKind::Primary,
        IndexKind::Unique,
        IndexKind::Unique.with_null_policy(IndexNullPolicy::Required),
    ] {
        for self_reference in [false, true] {
            let indexes = [
                IndexSpec {
                    name: b"ById",
                    fields: ID,
                    kind: IndexKind::Ordinary,
                },
                IndexSpec {
                    name: b"Descending",
                    fields: DESC,
                    kind,
                },
            ];
            let parent = TableSpec {
                validation: crate::TableValidation::NONE,
                name: b"Parent",
                columns: &columns,
                indexes: &indexes,
            };
            let child_indexes = [IndexSpec {
                name: b"ById",
                fields: ID,
                kind: IndexKind::Primary,
            }];
            let child = TableSpec {
                validation: crate::TableValidation::NONE,
                name: b"Child",
                columns: &columns,
                indexes: &child_indexes,
            };
            let nullable = kind.null_policy() == IndexNullPolicy::Include;
            let rows: &[&[RowValue<'_>]] = &[
                &[RowValue::Long(1), RowValue::Long(1), RowValue::Long(1)],
                &[
                    RowValue::Long(2),
                    if nullable {
                        RowValue::Null
                    } else {
                        RowValue::Long(2)
                    },
                    RowValue::Long(1),
                ],
                &[
                    RowValue::Long(3),
                    if nullable {
                        RowValue::Null
                    } else {
                        RowValue::Long(3)
                    },
                    RowValue::Long(1),
                ],
            ];
            let requests = [
                TableRows {
                    table: parent,
                    rows,
                },
                TableRows {
                    table: child,
                    rows: &rows[..1],
                },
            ];
            let directory = Directory::new()?;
            let mut relationship = edge(usize::from(!self_reference));
            if self_reference {
                relationship.fields = &[RelationshipField {
                    parent: ColumnRef::Ordinal(1),
                    child: ColumnRef::Ordinal(2),
                }];
            }
            create_database_with_relationships_and_rows(
                directory.target(),
                &requests[..if self_reference { 1 } else { 2 }],
                &[relationship],
                &mut budget(),
            )?;
            let d = definition(&directory.target(), b"Parent")?;
            let expected = if self_reference { 3 } else { 2 };
            assert_eq!(d.physical_indexes().len(), expected + 1);
            assert_eq!(d.indexes().len(), expected + 1);
            let p = &d.physical_indexes()[expected];
            assert_eq!(p.raw_flags(), if nullable { 1 } else { 9 });
            assert_eq!(p.fields()[0].direction(), IndexDirection::Ascending);
            assert_eq!(
                d.physical_indexes()[1].fields()[0].direction(),
                IndexDirection::Descending
            );
            assert_eq!(
                d.relationships()
                    .find(|r| r.side() == RelationshipSide::PrimaryTable)
                    .ok_or("parent alias")?
                    .physical_index() as usize,
                expected
            );
            let mut db = DatabaseReader::open(directory.target(), &mut budget())?;
            assert_eq!(
                db.index_tree(&d, expected as u16, &mut budget())?
                    .entries()
                    .len(),
                3
            );
            assert_eq!(
                db.validate(TextCodePage::Windows1252, &mut budget())?
                    .relationships_with_verified_keys,
                1
            );
        }
    }
    Ok(())
}

#[test]
fn generated_parent_is_shared_and_declared_ascending_parent_is_preferred() -> TestResult {
    for with_ascending in [false, true] {
        let indexes = [
            IndexSpec {
                name: b"Descending",
                fields: DESC,
                kind: IndexKind::Unique,
            },
            IndexSpec {
                name: b"Ascending",
                fields: ASC,
                kind: IndexKind::Unique,
            },
        ];
        let parent = TableSpec {
            validation: crate::TableValidation::NONE,
            name: b"Parent",
            columns: PAIR_COLUMNS,
            indexes: &indexes[..if with_ascending { 2 } else { 1 }],
        };
        let tables = [
            parent,
            TableSpec {
                validation: crate::TableValidation::NONE,
                name: b"ChildA",
                columns: PAIR_COLUMNS,
                indexes: &[],
            },
            TableSpec {
                validation: crate::TableValidation::NONE,
                name: b"ChildB",
                columns: PAIR_COLUMNS,
                indexes: &[],
            },
        ];
        let mut second = edge(2);
        second.name = b"Second";
        let directory = Directory::new()?;
        create_database_with_relationships(
            directory.target(),
            &tables,
            &[edge(1), second],
            &mut budget(),
        )?;
        let d = definition(&directory.target(), b"Parent")?;
        assert_eq!(d.physical_indexes().len(), 2);
        assert_eq!(d.indexes().len(), if with_ascending { 4 } else { 3 });
        assert!(d.relationships().all(|r| r.physical_index() == 1));
    }
    Ok(())
}

#[test]
fn generated_parent_capacity_and_hidden_names_remain_bounded() -> TestResult {
    let names = (0..32)
        .map(|i| format!("Index{i:02}").into_bytes())
        .collect::<Vec<_>>();
    let indexes = names
        .iter()
        .map(|name| IndexSpec {
            name,
            fields: DESC,
            kind: IndexKind::Unique,
        })
        .collect::<Vec<_>>();
    let child = TableSpec {
        validation: crate::TableValidation::NONE,
        name: b"Child",
        columns: PAIR_COLUMNS,
        indexes: &[],
    };
    for count in [31, 32] {
        let parent = TableSpec {
            validation: crate::TableValidation::NONE,
            name: b"Parent",
            columns: PAIR_COLUMNS,
            indexes: &indexes[..count],
        };
        let directory = Directory::new()?;
        let result = create_database_with_relationships(
            directory.target(),
            &[parent, child],
            &[edge(1)],
            &mut budget(),
        );
        if count == 31 {
            result?;
            let d = definition(&directory.target(), b"Parent")?;
            assert_eq!(d.physical_indexes().len(), 32);
            assert_eq!(d.indexes().len(), 32);
        } else {
            assert!(matches!(
                result,
                Err(CreateDatabaseError::Compose(ComposeError::Schema(
                    crate::TableSchemaPlanError::UnobservedIndexCount { .. }
                )))
            ));
            assert!(!directory.target().exists());
        }
    }
    let hidden = [IndexSpec {
        name: b".rB",
        fields: DESC,
        kind: IndexKind::Unique,
    }];
    let parent = TableSpec {
        validation: crate::TableValidation::NONE,
        name: b"Parent",
        columns: PAIR_COLUMNS,
        indexes: &hidden,
    };
    let directory = Directory::new()?;
    assert!(
        create_database_with_relationships(
            directory.target(),
            &[parent, child],
            &[edge(1)],
            &mut budget()
        )
        .is_err()
    );
    assert!(!directory.target().exists());
    Ok(())
}

#[test]
fn generated_parent_assignments_and_deletes_clamp_only_its_retained_counters() -> TestResult {
    let indexes = [
        IndexSpec {
            name: b"ById",
            fields: ID,
            kind: IndexKind::Primary,
        },
        IndexSpec {
            name: b"Descending",
            fields: DESC,
            kind: IndexKind::Unique,
        },
    ];
    let tables = [
        TableSpec {
            validation: crate::TableValidation::NONE,
            name: b"Parent",
            columns: PAIR_COLUMNS,
            indexes: &indexes,
        },
        TableSpec {
            validation: crate::TableValidation::NONE,
            name: b"Child",
            columns: PAIR_COLUMNS,
            indexes: &indexes[..1],
        },
    ];
    let rows: &[&[RowValue<'_>]] = &[
        &[RowValue::Long(1), RowValue::Long(1)],
        &[RowValue::Long(2), RowValue::Long(2)],
        &[RowValue::Long(3), RowValue::Long(3)],
    ];
    let directory = Directory::new()?;
    create_database_with_relationships_and_rows(
        directory.target(),
        &[
            TableRows {
                table: tables[0],
                rows,
            },
            TableRows {
                table: tables[1],
                rows: &rows[..1],
            },
        ],
        &[edge(1)],
        &mut budget(),
    )?;
    let d = definition(&directory.target(), b"Parent")?;
    let locators = {
        let mut work = budget();
        let mut db = DatabaseReader::open(directory.target(), &mut work)?;
        let mut cursor = db.rows(&d, &mut work)?;
        let mut locators = Vec::new();
        while let Some(row) = cursor.next_row()? {
            locators.push(row.locator());
        }
        locators
    };
    // Native rows-before-relation history: two initial keys, then a third insertion.
    let mut bytes = fs::read(directory.target())?;
    let offset = d.root().get() as usize * crate::PAGE_BYTES + 43 + 2 * 8;
    bytes[offset..offset + 4].copy_from_slice(&2u32.to_le_bytes());
    fs::write(directory.target(), bytes)?;
    crate::update_field(
        directory.target(),
        crate::FieldUpdate {
            table: b"Parent",
            row: locators[2],
            column: crate::ColumnOrdinal::new(1),
            value: RowValue::Long(30),
        },
        &mut budget(),
    )?;
    let after = definition(&directory.target(), b"Parent")?;
    assert_eq!(
        after.physical_indexes()[2].sourced_prefix(),
        &[1, 0, 0, 0, 1, 0, 0, 0]
    );
    for index in &after.physical_indexes()[..2] {
        assert_eq!(index.sourced_prefix(), &[0, 0, 0, 0, 3, 0, 0, 0]);
    }
    crate::delete_row(
        directory.target(),
        crate::RowDelete {
            table: b"Parent",
            row: locators[1],
        },
        &mut budget(),
    )?;
    let after = definition(&directory.target(), b"Parent")?;
    assert_eq!(after.physical_indexes()[2].sourced_prefix(), &[0; 8]);
    let before = fs::read(directory.target())?;
    assert!(
        crate::delete_row(
            directory.target(),
            crate::RowDelete {
                table: b"Parent",
                row: locators[0]
            },
            &mut budget()
        )
        .is_err()
    );
    assert_eq!(fs::read(directory.target())?, before);
    crate::delete_row(
        directory.target(),
        crate::RowDelete {
            table: b"Parent",
            row: locators[2],
        },
        &mut budget(),
    )?;
    assert_eq!(
        definition(&directory.target(), b"Parent")?.physical_indexes()[2].sourced_prefix(),
        &[0; 8]
    );
    Ok(())
}

#[test]
fn descending_parent_source_uses_logical_name_order_before_primary_or_physical_order() -> TestResult
{
    let nullable = IndexSpec {
        name: b"ANullableDesc",
        fields: DESC,
        kind: IndexKind::Unique,
    };
    let required = IndexSpec {
        name: b"ZRequiredPrimary",
        fields: DESC,
        kind: IndexKind::Primary,
    };
    for indexes in [[nullable, required], [required, nullable]] {
        let tables = [
            TableSpec {
                validation: crate::TableValidation::NONE,
                name: b"Parent",
                columns: PAIR_COLUMNS,
                indexes: &indexes,
            },
            TableSpec {
                validation: crate::TableValidation::NONE,
                name: b"Child",
                columns: PAIR_COLUMNS,
                indexes: &[],
            },
        ];
        let directory = Directory::new()?;
        create_database_with_relationships(directory.target(), &tables, &[edge(1)], &mut budget())?;
        let d = definition(&directory.target(), b"Parent")?;
        assert_eq!(d.physical_indexes().len(), 3);
        assert_eq!(d.physical_indexes()[2].raw_flags(), 1);
        assert_eq!(
            d.physical_indexes()[2].fields()[0].direction(),
            IndexDirection::Ascending
        );
    }
    Ok(())
}

#[test]
fn null_parent_mutations_require_no_remaining_null_children() -> TestResult {
    for direction in [ASC, DESC] {
        for null_child in [false, true] {
            for delete in [false, true] {
                let indexes = [
                    IndexSpec {
                        name: b"ById",
                        fields: ID,
                        kind: IndexKind::Primary,
                    },
                    IndexSpec {
                        name: b"Key",
                        fields: direction,
                        kind: IndexKind::Unique,
                    },
                ];
                let tables = [
                    TableSpec {
                        validation: crate::TableValidation::NONE,
                        name: b"Parent",
                        columns: PAIR_COLUMNS,
                        indexes: &indexes,
                    },
                    TableSpec {
                        validation: crate::TableValidation::NONE,
                        name: b"Child",
                        columns: PAIR_COLUMNS,
                        indexes: &indexes[..1],
                    },
                ];
                let parents: &[&[RowValue<'_>]] = &[
                    &[RowValue::Long(1), RowValue::Long(1)],
                    &[RowValue::Long(2), RowValue::Null],
                    &[RowValue::Long(3), RowValue::Null],
                ];
                let children: &[&[RowValue<'_>]] = &[
                    &[RowValue::Long(10), RowValue::Long(1)],
                    &[RowValue::Long(11), RowValue::Null],
                ];
                let directory = Directory::new()?;
                create_database_with_relationships_and_rows(
                    directory.target(),
                    &[
                        TableRows {
                            table: tables[0],
                            rows: parents,
                        },
                        TableRows {
                            table: tables[1],
                            rows: &children[..if null_child { 2 } else { 1 }],
                        },
                    ],
                    &[edge(1)],
                    &mut budget(),
                )?;
                let d = definition(&directory.target(), b"Parent")?;
                let locator = {
                    let mut work = budget();
                    let mut db = DatabaseReader::open(directory.target(), &mut work)?;
                    let mut rows = db.rows(&d, &mut work)?;
                    rows.next_row()?.ok_or("first parent")?;
                    rows.next_row()?.ok_or("null parent")?.locator()
                };
                let before = fs::read(directory.target())?;
                let result = if delete {
                    crate::delete_row(
                        directory.target(),
                        crate::RowDelete {
                            table: b"Parent",
                            row: locator,
                        },
                        &mut budget(),
                    )
                } else {
                    crate::update_row(
                        directory.target(),
                        crate::RowUpdate {
                            table: b"Parent",
                            row: locator,
                            values: &[RowValue::Long(2), RowValue::Long(22)],
                        },
                        &mut budget(),
                    )
                };
                if null_child {
                    assert!(matches!(
                        result,
                        Err(crate::UpdateError::NullRelationshipConstraint { .. })
                    ));
                    assert_eq!(fs::read(directory.target())?, before);
                } else {
                    result?;
                    let mut db = DatabaseReader::open(directory.target(), &mut budget())?;
                    assert_eq!(
                        db.validate(TextCodePage::Windows1252, &mut budget())?
                            .relationships_with_verified_keys,
                        1
                    );
                }
            }
        }
    }
    Ok(())
}
