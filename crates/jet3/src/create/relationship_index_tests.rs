use super::api_relationship_graph_tests::*;
use crate::{
    ColumnRef, ColumnSpec, ColumnType, DatabaseReader, IndexColumnSpec, IndexKind, IndexSpec,
    RelationshipField, RelationshipSpec, RowValue, TableSpec, TextCodePage,
    create::{api::*, composer::ComposeError},
};
use std::fs;

#[test]
fn graph_selects_later_unique_parent_and_preserves_declared_foreign_indexes() -> TestResult {
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"Key", ColumnType::Long),
    ];
    let id_fields = [IndexColumnSpec::ascending(0)];
    let key_fields = [IndexColumnSpec::ascending(1)];
    let descending = [IndexColumnSpec::descending(1)];
    for parent_kind in [IndexKind::Primary, IndexKind::Unique] {
        let parent_indexes = [
            IndexSpec {
                name: b"ById",
                fields: &id_fields,
                kind: IndexKind::Ordinary,
            },
            IndexSpec {
                name: b"ParentKey",
                fields: &key_fields,
                kind: parent_kind,
            },
        ];
        for mode in 0..5 {
            let primary = IndexSpec {
                name: b"ById",
                fields: &id_fields,
                kind: IndexKind::Primary,
            };
            let foreign = IndexSpec {
                name: b"ExistingForeign",
                fields: if mode == 3 { &descending } else { &key_fields },
                kind: match mode {
                    2 => IndexKind::Unique,
                    4 => IndexKind::Ordinary.with_null_policy(crate::IndexNullPolicy::Required),
                    _ => IndexKind::Ordinary,
                },
            };
            let child_indexes = if mode == 0 {
                [foreign, primary]
            } else {
                [primary, foreign]
            };
            let parent = TableSpec {
                validation: crate::TableValidation::NONE,
                name: b"Parent",
                columns: &columns,
                indexes: &parent_indexes,
            };
            let child = TableSpec {
                validation: crate::TableValidation::NONE,
                name: b"Child",
                columns: &columns,
                indexes: &child_indexes,
            };
            let parent_rows: &[&[RowValue<'_>]] = &[
                &[RowValue::Long(100), RowValue::Long(1)],
                &[RowValue::Long(200), RowValue::Long(2)],
            ];
            let child_rows: &[&[RowValue<'_>]] = &[&[RowValue::Long(10), RowValue::Long(1)]];
            let requests = [
                TableRows {
                    table: parent,
                    rows: parent_rows,
                },
                TableRows {
                    table: child,
                    rows: child_rows,
                },
            ];
            let mut edge = relation(b"Relation", 0, 1, 1);
            edge.fields = &[RelationshipField {
                parent: ColumnRef::Ordinal(1),
                child: ColumnRef::Ordinal(1),
            }];
            let directory = Directory::new()?;
            create_database(
                directory.target(),
                &DatabaseSpec {
                    tables: &requests,
                    relationships: &[edge],
                    ..DatabaseSpec::default()
                },
                &mut budget(),
            )?;
            let parent_locator = {
                let mut work = budget();
                let mut db = DatabaseReader::open(directory.target(), &mut work)?;
                let pd =
                    crate::write::update::indexed_writable_table(&mut db, parent.name, &mut work)?;
                let cd =
                    crate::write::update::indexed_writable_table(&mut db, child.name, &mut work)?;
                let pr = pd.relationships().next().ok_or("parent relationship")?;
                let cr = cd.relationships().next().ok_or("child relationship")?;
                assert_eq!(pr.physical_index(), 1);
                assert_eq!(cr.physical_index(), if mode < 2 { mode } else { 2 });
                assert_eq!(cd.physical_indexes().len(), if mode < 2 { 2 } else { 3 });
                assert_eq!(cd.indexes().len(), 3);
                assert!(
                    cd.indexes()
                        .iter()
                        .any(|index| index.name().raw_bytes() == foreign.name)
                );
                let mut rows = db.rows(&pd, &mut work)?;
                rows.next_row()?.ok_or("parent row")?.locator()
            };
            crate::insert_row(
                directory.target(),
                child.name,
                &[RowValue::Long(11), RowValue::Long(2)],
                &mut budget(),
            )?;
            let before = fs::read(directory.target())?;
            assert!(matches!(
                crate::insert_row(
                    directory.target(),
                    child.name,
                    &[RowValue::Long(12), RowValue::Long(99)],
                    &mut budget()
                ),
                Err(crate::UpdateError::RelationshipConstraint { value: 99, .. })
            ));
            assert!(matches!(
                crate::delete_row(
                    directory.target(),
                    crate::RowDelete {
                        table: parent.name,
                        row: parent_locator
                    },
                    &mut budget()
                ),
                Err(crate::UpdateError::RelationshipConstraint { value: 1, .. })
            ));
            assert_eq!(fs::read(directory.target())?, before);
            let mut db = DatabaseReader::open(directory.target(), &mut budget())?;
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
fn relationship_alias_consumes_a_logical_index_slot_when_reusing_a_tree() -> TestResult {
    let names = (0..32)
        .map(|n| format!("Index{n:02}").into_bytes())
        .collect::<Vec<_>>();
    let id = [IndexColumnSpec::ascending(0)];
    let fk = [IndexColumnSpec::ascending(1)];
    for reuse in [false, true] {
        let indexes = names
            .iter()
            .enumerate()
            .map(|(n, name)| IndexSpec {
                name,
                fields: if reuse && n == 1 { &fk } else { &id },
                kind: IndexKind::Ordinary,
            })
            .collect::<Vec<_>>();
        for count in [31, 32] {
            let directory = Directory::new()?;
            let child = TableSpec {
                indexes: &indexes[..count],
                ..TABLES[1]
            };
            let result = create_database(
                directory.target(),
                &DatabaseSpec {
                    tables: &[TableRows::empty(TABLES[0]), TableRows::empty(child)],
                    relationships: &[relation(b"Relation", 0, 1, 1)],
                    ..DatabaseSpec::default()
                },
                &mut budget(),
            );
            if count == 32 {
                assert!(matches!(
                    result,
                    Err(CreateDatabaseError::Compose(ComposeError::Schema(
                        crate::TableSchemaPlanError::UnobservedIndexCount {
                            count: 33,
                            observed: 32
                        }
                    )))
                ));
                assert!(!directory.target().exists());
            } else {
                result?;
                let mut work = budget();
                let mut db = DatabaseReader::open(directory.target(), &mut work)?;
                let definition =
                    crate::write::update::indexed_writable_table(&mut db, child.name, &mut work)?;
                assert_eq!(definition.indexes().len(), 32);
                assert_eq!(
                    definition.physical_indexes().len(),
                    if reuse { 31 } else { 32 }
                );
            }
        }
    }
    Ok(())
}

#[test]
fn graph_parent_selection_follows_logical_name_order_before_primary_status() -> TestResult {
    for primary_first in [false, true] {
        let unique = IndexSpec {
            name: b"AUnique",
            kind: IndexKind::Unique,
            ..INDEXES[0]
        };
        let primary = IndexSpec {
            name: b"ZPrimary",
            ..INDEXES[0]
        };
        let indexes = if primary_first {
            [primary, unique]
        } else {
            [unique, primary]
        };
        let parent = TableSpec {
            indexes: &indexes,
            ..TABLES[0]
        };
        let directory = Directory::new()?;
        create_database(
            directory.target(),
            &DatabaseSpec {
                tables: &[TableRows::empty(parent), TableRows::empty(TABLES[1])],
                relationships: &[relation(b"Relation", 0, 1, 1)],
                ..DatabaseSpec::default()
            },
            &mut budget(),
        )?;
        let mut work = budget();
        let mut db = DatabaseReader::open(directory.target(), &mut work)?;
        let definition =
            crate::write::update::indexed_writable_table(&mut db, parent.name, &mut work)?;
        assert_eq!(
            definition
                .relationships()
                .next()
                .ok_or("parent relationship")?
                .physical_index(),
            u16::from(primary_first)
        );
    }
    Ok(())
}

#[test]
fn graph_nullable_unique_parent_allows_duplicate_nulls_and_null_foreign_keys() -> TestResult {
    let indexes = [IndexSpec {
        kind: IndexKind::Unique,
        ..INDEXES[0]
    }];
    let parent = TableSpec {
        indexes: &indexes,
        ..TABLES[0]
    };
    let null: &[RowValue<'_>] = &[
        RowValue::Null,
        RowValue::Null,
        RowValue::Null,
        RowValue::Null,
    ];
    let keyed: &[RowValue<'_>] = &[
        RowValue::Long(1),
        RowValue::Null,
        RowValue::Null,
        RowValue::Null,
    ];
    let directory = Directory::new()?;
    create_database(
        directory.target(),
        &DatabaseSpec {
            tables: &[
                TableRows {
                    table: parent,
                    rows: &[keyed, null, null],
                },
                TableRows {
                    table: TABLES[1],
                    rows: &[keyed],
                },
            ],
            relationships: &[relation(b"Relation", 0, 1, 1)],
            ..DatabaseSpec::default()
        },
        &mut budget(),
    )?;
    crate::insert_row(
        directory.target(),
        TABLES[1].name,
        &[
            RowValue::Long(2),
            RowValue::Long(1),
            RowValue::Null,
            RowValue::Null,
        ],
        &mut budget(),
    )?;
    let before = fs::read(directory.target())?;
    assert!(matches!(
        crate::insert_row(
            directory.target(),
            TABLES[1].name,
            &[
                RowValue::Long(3),
                RowValue::Long(99),
                RowValue::Null,
                RowValue::Null
            ],
            &mut budget()
        ),
        Err(crate::UpdateError::RelationshipConstraint { value: 99, .. })
    ));
    assert_eq!(fs::read(directory.target())?, before);
    let mut db = DatabaseReader::open(directory.target(), &mut budget())?;
    assert_eq!(
        db.validate(TextCodePage::Windows1252, &mut budget())?
            .relationships_with_verified_keys,
        1
    );
    Ok(())
}

#[test]
fn graph_parent_hidden_names_cross_the_native_nibble_boundary() -> TestResult {
    let names = (0..32)
        .map(|n| format!("Index{n:02}").into_bytes())
        .collect::<Vec<_>>();
    let fields = [IndexColumnSpec::ascending(0)];
    let indexes = names
        .iter()
        .map(|name| IndexSpec {
            name,
            fields: &fields,
            kind: IndexKind::Unique,
        })
        .collect::<Vec<_>>();
    for (count, hidden) in [
        (15, ".rP"),
        (16, ".rAB"),
        (24, ".rIB"),
        (25, ".rJB"),
        (26, ".rKB"),
        (31, ".rPB"),
        (32, ""),
    ] {
        let directory = Directory::new()?;
        let parent = TableSpec {
            indexes: &indexes[..count],
            ..TABLES[0]
        };
        let result = create_database(
            directory.target(),
            &DatabaseSpec {
                tables: &[TableRows::empty(parent), TableRows::empty(TABLES[1])],
                relationships: &[relation(b"Relation", 0, 1, 1)],
                ..DatabaseSpec::default()
            },
            &mut budget(),
        );
        if count == 32 {
            assert!(matches!(
                result,
                Err(CreateDatabaseError::Compose(ComposeError::Schema(
                    crate::TableSchemaPlanError::UnobservedIndexCount {
                        count: 33,
                        observed: 32
                    }
                )))
            ));
            assert!(!directory.target().exists());
        } else {
            result?;
            let mut work = budget();
            let mut db = DatabaseReader::open(directory.target(), &mut work)?;
            let definition =
                crate::write::update::indexed_writable_table(&mut db, parent.name, &mut work)?;
            let relation = definition
                .relationships()
                .next()
                .ok_or("parent relationship")?;
            assert_eq!(relation.name().raw_bytes(), hidden.as_bytes());
            assert_eq!(relation.raw_selector() as usize, count);
            assert_eq!(definition.indexes().len(), count + 1);
        }
    }
    Ok(())
}

#[test]
fn one_to_one_graph_selects_only_unique_include_null_child_indexes() -> TestResult {
    for (kind, descending, reused) in [
        (IndexKind::Unique, false, true),
        (IndexKind::Unique, true, false),
        (IndexKind::Ordinary, false, false),
        (IndexKind::Primary, false, false),
        (
            IndexKind::Unique.with_null_policy(crate::IndexNullPolicy::Required),
            false,
            false,
        ),
        (
            IndexKind::Unique.with_null_policy(crate::IndexNullPolicy::IgnoreAllNull),
            false,
            false,
        ),
    ] {
        let directory = Directory::new()?;
        let fields = [if descending {
            IndexColumnSpec::descending(1)
        } else {
            IndexColumnSpec::ascending(1)
        }];
        let indexes = [IndexSpec {
            name: b"Existing",
            fields: &fields,
            kind,
        }];
        let child = TableSpec {
            indexes: &indexes,
            ..TABLES[1]
        };
        let edge = RelationshipSpec {
            unique: true,
            ..relation(b"One", 0, 1, 1)
        };
        create_database(
            directory.target(),
            &DatabaseSpec {
                tables: &[TableRows::empty(TABLES[0]), TableRows::empty(child)],
                relationships: &[edge],
                ..DatabaseSpec::default()
            },
            &mut budget(),
        )?;
        let mut b = budget();
        let mut db = DatabaseReader::open(directory.target(), &mut b)?;
        let table = crate::write::update::indexed_writable_table(&mut db, child.name, &mut b)?;
        assert_eq!(table.physical_indexes().len(), if reused { 1 } else { 2 });
        let foreign = table.relationships().next().ok_or("foreign")?;
        assert_eq!(foreign.physical_index(), u16::from(!reused));
        assert_eq!(
            table.physical_indexes()[usize::from(foreign.physical_index())].raw_flags(),
            1
        );
        assert!(db.relationship_catalog(&mut b)?[0].one_to_one());
        assert_eq!(
            db.validate(TextCodePage::Windows1252, &mut b)?
                .relationships_with_verified_keys,
            1
        );
    }
    Ok(())
}

#[test]
fn one_to_one_and_ordinary_graph_edges_keep_distinct_child_trees() -> TestResult {
    let directory = Directory::new()?;
    let edges = [
        relation(b"Many", 0, 2, 1),
        RelationshipSpec {
            unique: true,
            ..relation(b"One", 1, 2, 1)
        },
    ];
    create_database(
        directory.target(),
        &DatabaseSpec {
            tables: &TABLES.map(TableRows::empty),
            relationships: &edges,
            ..DatabaseSpec::default()
        },
        &mut budget(),
    )?;
    let mut b = budget();
    let mut db = DatabaseReader::open(directory.target(), &mut b)?;
    let table = crate::write::update::indexed_writable_table(&mut db, TABLES[2].name, &mut b)?;
    assert_eq!(
        table
            .physical_indexes()
            .iter()
            .map(|i| i.raw_flags())
            .collect::<Vec<_>>(),
        [9, 0, 1]
    );
    assert_eq!(
        db.validate(TextCodePage::Windows1252, &mut b)?
            .relationships_with_verified_keys,
        2
    );
    Ok(())
}

#[test]
fn one_to_one_creation_refuses_duplicate_children_before_publication() -> TestResult {
    let directory = Directory::new()?;
    let requests = [
        TableRows {
            table: TABLES[0],
            rows: &[&[
                RowValue::Long(1),
                RowValue::Null,
                RowValue::Null,
                RowValue::Null,
            ]],
        },
        TableRows {
            table: TABLES[1],
            rows: &[
                &[
                    RowValue::Long(1),
                    RowValue::Long(1),
                    RowValue::Null,
                    RowValue::Null,
                ],
                &[
                    RowValue::Long(2),
                    RowValue::Long(1),
                    RowValue::Null,
                    RowValue::Null,
                ],
            ],
        },
    ];
    let edge = RelationshipSpec {
        unique: true,
        ..relation(b"One", 0, 1, 1)
    };
    assert!(
        create_database(
            directory.target(),
            &DatabaseSpec {
                tables: &requests,
                relationships: &[edge],
                ..DatabaseSpec::default()
            },
            &mut budget()
        )
        .is_err()
    );
    assert!(!directory.target().exists());
    Ok(())
}

#[test]
fn generated_one_to_one_index_records_initial_child_row_count() -> TestResult {
    let directory = Directory::new()?;
    let parent_rows: &[&[RowValue<'_>]] = &[
        &[
            RowValue::Long(1),
            RowValue::Null,
            RowValue::Null,
            RowValue::Null,
        ],
        &[
            RowValue::Long(2),
            RowValue::Null,
            RowValue::Null,
            RowValue::Null,
        ],
    ];
    let child_rows: &[&[RowValue<'_>]] = &[
        &[
            RowValue::Long(1),
            RowValue::Long(1),
            RowValue::Null,
            RowValue::Null,
        ],
        &[
            RowValue::Long(2),
            RowValue::Long(2),
            RowValue::Null,
            RowValue::Null,
        ],
        &[
            RowValue::Long(3),
            RowValue::Null,
            RowValue::Null,
            RowValue::Null,
        ],
    ];
    let edge = RelationshipSpec {
        unique: true,
        ..relation(b"One", 0, 1, 1)
    };
    create_database(
        directory.target(),
        &DatabaseSpec {
            tables: &[
                TableRows {
                    table: TABLES[0],
                    rows: parent_rows,
                },
                TableRows {
                    table: TABLES[1],
                    rows: child_rows,
                },
            ],
            relationships: &[edge],
            ..DatabaseSpec::default()
        },
        &mut budget(),
    )?;
    let mut work = budget();
    let mut db = DatabaseReader::open(directory.target(), &mut work)?;
    let child = crate::write::update::indexed_writable_table(&mut db, TABLES[1].name, &mut work)?;
    let foreign = child.relationships().next().ok_or("foreign relationship")?;
    assert_eq!(
        child.physical_indexes()[usize::from(foreign.physical_index())].sourced_prefix(),
        &[3, 0, 0, 0, 3, 0, 0, 0]
    );
    Ok(())
}
