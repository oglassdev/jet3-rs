//! EXP-0290 ordered composite fields and partial-null key matching.
use super::api_relationship_graph_tests::*;
use crate::WriteError;
use crate::{
    ColumnRef, ColumnSpec, ColumnType, DatabaseReader, IndexColumnSpec, IndexDirection, IndexKind,
    IndexSpec, RelationshipField, RelationshipSpec, RowValue, TableRef, TableSpec, TextCodePage,
    create::{api::*, composer::ComposeError},
};
use std::fs;
use std::path::Path;

const PAIR: &[IndexColumnSpec<'static>] = &[
    IndexColumnSpec {
        column: ColumnRef::Ordinal(1),
        direction: IndexDirection::Ascending,
    },
    IndexColumnSpec {
        column: ColumnRef::Ordinal(2),
        direction: IndexDirection::Ascending,
    },
];
const FIELDS: &[RelationshipField<'static>] = &[
    RelationshipField {
        parent: ColumnRef::Ordinal(1),
        child: ColumnRef::Ordinal(1),
    },
    RelationshipField {
        parent: ColumnRef::Ordinal(2),
        child: ColumnRef::Ordinal(2),
    },
];
const PARENT_INDEXES: &[IndexSpec<'static>] = &[
    INDEXES[0],
    IndexSpec {
        name: b"Pair",
        fields: PAIR,
        kind: IndexKind::Unique,
    },
];
fn schema() -> [TableSpec<'static>; 2] {
    [
        TableSpec {
            indexes: PARENT_INDEXES,
            ..TABLES[0]
        },
        TABLES[1],
    ]
}
fn edge() -> RelationshipSpec<'static> {
    RelationshipSpec {
        unique: false,
        enforce: true,
        join: crate::RelationshipJoin::Inner,
        cascade_updates: false,
        cascade_deletes: false,
        name: b"Pair",
        parent: TableRef::Ordinal(0),
        child: TableRef::Ordinal(1),
        fields: FIELDS,
    }
}

#[test]
fn composite_relationship_creation_checks_full_tuple_and_catalog_inventory() -> TestResult {
    let directory = Directory::new()?;
    let tables = schema();
    let parent = [
        &[
            RowValue::Long(1),
            RowValue::Long(11),
            RowValue::Long(111),
            RowValue::Null,
        ][..],
        &[
            RowValue::Long(2),
            RowValue::Long(11),
            RowValue::Null,
            RowValue::Null,
        ][..],
    ];
    let child = [
        &[
            RowValue::Long(10),
            RowValue::Long(11),
            RowValue::Long(111),
            RowValue::Memo(b"first"),
        ][..],
        &[
            RowValue::Long(11),
            RowValue::Long(11),
            RowValue::Null,
            RowValue::Null,
        ][..],
    ];
    let requests = [
        TableRows {
            table: tables[0],
            rows: &parent,
        },
        TableRows {
            table: tables[1],
            rows: &child,
        },
    ];
    create_database(
        directory.target(),
        &DatabaseSpec {
            tables: &requests,
            relationships: &[edge()],
            ..DatabaseSpec::default()
        },
        &mut budget(),
    )?;
    let mut database = DatabaseReader::open(directory.target(), &mut budget())?;
    let report = database.validate(TextCodePage::Windows1252, &mut budget())?;
    assert_eq!(report.relationship_catalog_rows, 2);
    assert_eq!(report.relationships_with_verified_keys, 1);
    assert!(report.relationship_inventory_checked);
    for key in [RowValue::Long(999), RowValue::Null] {
        let orphan = [&[RowValue::Long(12), key, RowValue::Long(111), RowValue::Null][..]];
        let missing = directory.0.join(if matches!(key, RowValue::Null) {
            "partial.mdb"
        } else {
            "orphan.mdb"
        });
        let requests = [
            requests[0],
            TableRows {
                table: tables[1],
                rows: &orphan,
            },
        ];
        assert!(matches!(
            create_database(
                &missing,
                &DatabaseSpec {
                    tables: &requests,
                    relationships: &[edge()],
                    ..DatabaseSpec::default()
                },
                &mut budget()
            ),
            Err(WriteError::Compose(
                ComposeError::OrphanInitialScalarRelationshipKey { row: 0 }
            ))
        ));
        assert!(!missing.exists());
    }
    Ok(())
}

#[test]
fn composite_relationship_requires_aligned_unique_fields_and_distinct_components() -> TestResult {
    let directory = Directory::new()?;
    let tables = schema();
    for fields in [
        &[FIELDS[1], FIELDS[0]][..],
        &[FIELDS[0], FIELDS[0]][..],
        &[][..],
        &[FIELDS[0]; 11][..],
    ] {
        let invalid = RelationshipSpec { fields, ..edge() };
        assert!(matches!(
            create_database(
                directory.target(),
                &DatabaseSpec {
                    tables: &tables.map(TableRows::empty),
                    relationships: &[invalid],
                    ..DatabaseSpec::default()
                },
                &mut budget()
            ),
            Err(WriteError::Compose(
                ComposeError::UnsupportedRelationship { .. }
            ))
        ));
        assert!(!directory.target().exists());
    }
    Ok(())
}

#[test]
fn self_relationship_creation_refuses_identical_keys_but_admits_partial_overlap() -> TestResult {
    let directory = Directory::new()?;
    let tables = schema();
    let scalar = [RelationshipField {
        parent: ColumnRef::Name(b"Id"),
        child: ColumnRef::Ordinal(0),
    }];
    for fields in [&scalar[..], FIELDS] {
        let relation = RelationshipSpec {
            unique: false,
            enforce: true,
            join: crate::RelationshipJoin::Inner,
            cascade_updates: false,
            cascade_deletes: false,
            name: b"SelfRelation",
            parent: TableRef::Name(b"Alpha"),
            child: TableRef::Ordinal(0),
            fields,
        };
        assert!(matches!(
            create_database(
                directory.target(),
                &DatabaseSpec {
                    tables: &tables[..1]
                        .iter()
                        .copied()
                        .map(TableRows::empty)
                        .collect::<Vec<_>>(),
                    relationships: &[relation],
                    ..DatabaseSpec::default()
                },
                &mut budget()
            ),
            Err(WriteError::Compose(
                ComposeError::UnsupportedRelationship { .. }
            ))
        ));
        assert!(!directory.target().exists());
    }
    let partial = [
        FIELDS[0],
        RelationshipField {
            parent: ColumnRef::Ordinal(2),
            child: ColumnRef::Name(b"Id"),
        },
    ];
    let swapped = [
        RelationshipField {
            parent: ColumnRef::Ordinal(1),
            child: ColumnRef::Ordinal(2),
        },
        RelationshipField {
            parent: ColumnRef::Ordinal(2),
            child: ColumnRef::Ordinal(1),
        },
    ];
    for fields in [&partial[..], &swapped[..]] {
        let directory = Directory::new()?;
        create_database(
            directory.target(),
            &DatabaseSpec {
                tables: &tables[..1]
                    .iter()
                    .copied()
                    .map(TableRows::empty)
                    .collect::<Vec<_>>(),
                relationships: &[RelationshipSpec {
                    unique: false,
                    enforce: true,
                    join: crate::RelationshipJoin::Inner,
                    cascade_updates: false,
                    cascade_deletes: false,
                    name: b"PartialSelf",
                    parent: TableRef::Ordinal(0),
                    child: TableRef::Name(b"Alpha"),
                    fields,
                }],
                ..DatabaseSpec::default()
            },
            &mut budget(),
        )?;
        let mut database = DatabaseReader::open(directory.target(), &mut budget())?;
        let report = database.validate(TextCodePage::Windows1252, &mut budget())?;
        assert_eq!(report.relationships_with_verified_keys, 1);
    }
    Ok(())
}

fn locate(
    path: &Path,
    table: &[u8],
    id: i32,
) -> Result<crate::RowLocator, Box<dyn std::error::Error>> {
    let mut work = budget();
    let mut db = DatabaseReader::open(path, &mut work)?;
    let definition = crate::write::update::indexed_writable_table(&mut db, table, &mut work)?;
    let mut rows = db.rows(&definition, &mut work)?;
    while let Some(mut row) = rows.next_row()? {
        if matches!(row.value(crate::ColumnOrdinal::new(0), TextCodePage::Windows1252)?.ok_or("missing Id")?.kind(), crate::ValueKind::Long(value) if *value == id)
        {
            return Ok(row.locator());
        }
    }
    Err("missing row".into())
}

#[test]
fn referenced_parent_payload_field_edit_does_not_assign_its_composite_key() -> TestResult {
    let directory = Directory::new()?;
    let path = directory.target();
    let mut columns = COLUMNS.to_vec();
    columns[3] = ColumnSpec::new(
        b"Body",
        ColumnType::Text {
            max_len: std::num::NonZeroU8::new(255).ok_or("width")?,
        },
    );
    let mut tables = schema();
    tables[0].columns = &columns;
    let parent = [
        RowValue::Long(1),
        RowValue::Long(11),
        RowValue::Long(111),
        RowValue::Text(b"before"),
    ];
    let child = [
        RowValue::Long(10),
        RowValue::Long(11),
        RowValue::Long(111),
        RowValue::Null,
    ];
    create_database(
        &path,
        &DatabaseSpec {
            tables: &[
                TableRows {
                    table: tables[0],
                    rows: &[&parent],
                },
                TableRows {
                    table: tables[1],
                    rows: &[&child],
                },
            ],
            relationships: &[edge()],
            ..DatabaseSpec::default()
        },
        &mut budget(),
    )?;
    let row = locate(&path, b"Alpha", 1)?;
    let original = fs::read(&path)?;
    let mut replacement = parent;
    replacement[3] = RowValue::Text(&[b'x'; 255]);
    assert!(matches!(
        crate::update_row(
            &path,
            crate::RowUpdate {
                table: b"Alpha",
                row,
                values: &replacement,
            },
            &mut budget()
        ),
        Err(crate::WriteError::ScalarRelationshipConstraint { .. })
    ));
    assert_eq!(fs::read(&path)?, original);
    crate::update_field(
        &path,
        crate::FieldUpdate {
            table: b"Alpha",
            row,
            column: crate::ColumnOrdinal::new(3),
            value: replacement[3],
        },
        &mut budget(),
    )?;
    let mut work = budget();
    let mut db = DatabaseReader::open(&path, &mut work)?;
    let table = crate::write::update::indexed_writable_table(&mut db, b"Alpha", &mut work)?;
    {
        let mut rows = db.rows(&table, &mut work)?;
        let mut actual = rows.next_row()?.ok_or("row")?;
        for (ordinal, expected) in replacement.iter().enumerate() {
            assert_eq!(
                crate::row::scalar_values::read_column(
                    &mut actual,
                    crate::ColumnOrdinal::new(ordinal as u16)
                )?,
                *expected
            );
        }
    }
    db.validate(TextCodePage::Windows1252, &mut work)?;
    Ok(())
}

#[test]
fn composite_mutations_protect_assigned_parent_rows_and_admit_all_null_children() -> TestResult {
    let directory = Directory::new()?;
    let path = directory.target();
    let tables = schema();
    let parent = [
        &[
            RowValue::Long(1),
            RowValue::Long(11),
            RowValue::Long(111),
            RowValue::Null,
        ][..],
        &[
            RowValue::Long(2),
            RowValue::Long(11),
            RowValue::Null,
            RowValue::Null,
        ][..],
        &[
            RowValue::Long(3),
            RowValue::Long(11),
            RowValue::Null,
            RowValue::Null,
        ][..],
        &[
            RowValue::Long(4),
            RowValue::Null,
            RowValue::Null,
            RowValue::Null,
        ][..],
    ];
    let child = [
        &[
            RowValue::Long(10),
            RowValue::Long(11),
            RowValue::Long(111),
            RowValue::Null,
        ][..],
        &[
            RowValue::Long(11),
            RowValue::Long(11),
            RowValue::Null,
            RowValue::Null,
        ][..],
        &[
            RowValue::Long(12),
            RowValue::Null,
            RowValue::Null,
            RowValue::Null,
        ][..],
    ];
    create_database(
        &path,
        &DatabaseSpec {
            tables: &[
                TableRows {
                    table: tables[0],
                    rows: &parent,
                },
                TableRows {
                    table: tables[1],
                    rows: &child,
                },
            ],
            relationships: &[edge()],
            ..DatabaseSpec::default()
        },
        &mut budget(),
    )?;
    let original = fs::read(&path)?;
    let delete = |table: &[u8], id| -> TestResult {
        crate::delete_row(
            &path,
            crate::RowDelete {
                table,
                row: locate(&path, table, id)?,
            },
            &mut budget(),
        )?;
        Ok(())
    };
    assert!(delete(b"Alpha", 2).is_err());
    assert_eq!(fs::read(&path)?, original);
    for (id, value) in [(1, RowValue::Long(11)), (2, RowValue::Long(999))] {
        let error = crate::update_field(
            &path,
            crate::FieldUpdate {
                table: b"Alpha",
                row: locate(&path, b"Alpha", id)?,
                column: crate::ColumnOrdinal::new(1),
                value,
            },
            &mut budget(),
        )
        .err()
        .ok_or("referenced parent assignment accepted")?;
        assert!(matches!(
            error,
            crate::WriteError::ScalarRelationshipConstraint { .. }
        ));
        assert_eq!(fs::read(&path)?, original);
    }
    let error = crate::insert_row(
        &path,
        b"Bravo",
        &[
            RowValue::Long(13),
            RowValue::Long(999),
            RowValue::Null,
            RowValue::Null,
        ],
        &mut budget(),
    )
    .err()
    .ok_or("partial orphan accepted")?;
    assert!(matches!(
        error,
        crate::WriteError::ScalarRelationshipConstraint { .. }
    ));
    assert_eq!(fs::read(&path)?, original);
    assert!(delete(b"Alpha", 4).is_err());
    assert_eq!(fs::read(&path)?, original);
    delete(b"Bravo", 12)?;
    delete(b"Alpha", 4)?;
    crate::insert_row(
        &path,
        b"Bravo",
        &[
            RowValue::Long(13),
            RowValue::Null,
            RowValue::Null,
            RowValue::Null,
        ],
        &mut budget(),
    )?;
    let mut db = DatabaseReader::open(&path, &mut budget())?;
    db.validate(TextCodePage::Windows1252, &mut budget())?;
    Ok(())
}

#[test]
fn full_self_replacement_excludes_only_its_own_child_from_parent_guards() -> TestResult {
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"K1", ColumnType::Long),
        ColumnSpec::new(b"K2", ColumnType::Long),
        ColumnSpec::new(b"F1", ColumnType::Long),
        ColumnSpec::new(b"F2", ColumnType::Long),
    ];
    let fields = [
        RelationshipField {
            parent: ColumnRef::Ordinal(1),
            child: ColumnRef::Ordinal(3),
        },
        RelationshipField {
            parent: ColumnRef::Ordinal(2),
            child: ColumnRef::Ordinal(4),
        },
    ];
    for arity in [1, 2] {
        let indexes = [
            INDEXES[0],
            IndexSpec {
                name: b"ParentKey",
                fields: &PAIR[..arity],
                kind: IndexKind::Unique,
            },
        ];
        let table = TableSpec {
            validation: crate::TableValidation::NONE,
            name: b"Alpha",
            columns: &columns,
            indexes: &indexes,
        };
        for null_key in [false, true] {
            let first = if null_key {
                RowValue::Null
            } else {
                RowValue::Long(11)
            };
            let second = if null_key {
                RowValue::Null
            } else {
                RowValue::Long(111)
            };
            let selected = [RowValue::Long(1), first, second, first, second];
            let external = [
                RowValue::Long(2),
                RowValue::Long(22),
                RowValue::Long(222),
                first,
                second,
            ];
            let rows = [&selected[..], &external[..]];
            for external_child in [false, true] {
                let directory = Directory::new()?;
                let path = directory.target();
                create_database(
                    &path,
                    &DatabaseSpec {
                        tables: &[TableRows {
                            table,
                            rows: &rows[..if external_child { 2 } else { 1 }],
                        }],
                        relationships: &[RelationshipSpec {
                            unique: false,
                            enforce: true,
                            join: crate::RelationshipJoin::Inner,
                            cascade_updates: false,
                            cascade_deletes: false,
                            name: b"SelfRelation",
                            parent: TableRef::Ordinal(0),
                            child: TableRef::Ordinal(0),
                            fields: &fields[..arity],
                        }],
                        ..DatabaseSpec::default()
                    },
                    &mut budget(),
                )?;
                let row = locate(&path, b"Alpha", 1)?;
                let before = fs::read(&path)?;
                let error = crate::update_field(
                    &path,
                    crate::FieldUpdate {
                        table: b"Alpha",
                        row,
                        column: crate::ColumnOrdinal::new(1),
                        value: first,
                    },
                    &mut budget(),
                )
                .err()
                .ok_or("equal parent field assignment accepted")?;
                assert!(matches!(
                    error,
                    crate::WriteError::RelationshipConstraint { .. }
                        | crate::WriteError::ScalarRelationshipConstraint { .. }
                        | crate::WriteError::NullRelationshipConstraint { .. }
                ));
                assert_eq!(fs::read(&path)?, before);
                let result = crate::update_row(
                    &path,
                    crate::RowUpdate {
                        table: b"Alpha",
                        row,
                        values: &selected,
                    },
                    &mut budget(),
                );
                if external_child {
                    assert!(matches!(
                        result,
                        Err(crate::WriteError::RelationshipConstraint { .. }
                            | crate::WriteError::ScalarRelationshipConstraint { .. }
                            | crate::WriteError::NullRelationshipConstraint { .. })
                    ));
                    assert_eq!(fs::read(&path)?, before);
                } else {
                    result?;
                    let mut db = DatabaseReader::open(&path, &mut budget())?;
                    assert_eq!(
                        db.validate(TextCodePage::Windows1252, &mut budget())?
                            .relationships_with_verified_keys,
                        1
                    );
                    drop(db);
                    let before = fs::read(&path)?;
                    let mut orphan = selected;
                    orphan[3] = RowValue::Long(999);
                    assert!(matches!(
                        crate::update_row(
                            &path,
                            crate::RowUpdate {
                                table: b"Alpha",
                                row,
                                values: &orphan
                            },
                            &mut budget(),
                        ),
                        Err(crate::WriteError::RelationshipConstraint { .. }
                            | crate::WriteError::ScalarRelationshipConstraint { .. })
                    ));
                    assert_eq!(fs::read(&path)?, before);
                }
            }
        }
    }
    Ok(())
}
