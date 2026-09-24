//! EXP-0290 ordered composite fields and partial-null key matching.
use super::api_relationship_graph_tests::*;
use crate::WriteError;
use crate::testkit::{create_spec, validate_file};
use crate::testkit::{index, table};
use crate::{
    ColumnOrdinal, ColumnRef, ColumnSpec, ColumnType, IndexColumnSpec, IndexDirection, IndexKind,
    IndexSpec, RelationshipField, RelationshipSpec, RowValue, TableRef, TableSpec,
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
const PARENT_INDEXES: &[IndexSpec<'static>] =
    &[INDEXES[0], index(b"Pair", PAIR, IndexKind::Unique)];
const SCHEMA: [TableSpec<'static>; 2] = [
    TableSpec {
        indexes: PARENT_INDEXES,
        ..TABLES[0]
    },
    TABLES[1],
];
fn edge() -> RelationshipSpec<'static> {
    RelationshipSpec {
        name: b"Pair",
        fields: FIELDS,
        ..relation(b"", 0, 1, 0)
    }
}
fn composite(
    path: &Path,
    parents: &[&[RowValue<'_>]],
    children: &[&[RowValue<'_>]],
) -> Result<(), WriteError> {
    let requests = [
        TableRows {
            table: SCHEMA[0],
            rows: parents,
        },
        TableRows {
            table: SCHEMA[1],
            rows: children,
        },
    ];
    create_spec(path, &spec(&requests, &[edge()]))
}
fn relationship_error(result: Result<(), WriteError>) -> bool {
    matches!(
        result,
        Err(WriteError::RelationshipConstraint { .. }
            | WriteError::ScalarRelationshipConstraint { .. }
            | WriteError::NullRelationshipConstraint { .. })
    )
}

#[test]
fn composite_relationship_creation_checks_full_tuple_and_catalog_inventory() -> TestResult {
    let directory = TempDir::new("create")?;
    let parents = [values(1, Some(11), Some(111)), values(2, Some(11), None)];
    let mut child = values(10, Some(11), Some(111));
    child[3] = RowValue::Memo(b"first");
    let partial = values(11, Some(11), None);
    composite(
        &directory.target(),
        &[&parents[0], &parents[1]],
        &[&child, &partial],
    )?;
    let report = validate_file(directory.target())?;
    assert_eq!(report.relationship_catalog_rows, 2);
    assert_eq!(report.relationships_with_verified_keys, 1);
    assert!(report.relationship_inventory_checked);
    for (name, first) in [("orphan.mdb", Some(999)), ("partial.mdb", None)] {
        let refused = directory.join(name);
        let orphan = values(12, first, Some(111));
        let result = composite(&refused, &[&parents[0], &parents[1]], &[&orphan]);
        assert!(matches!(
            result,
            Err(WriteError::Compose(
                ComposeError::OrphanInitialScalarRelationshipKey { row: 0 }
            ))
        ));
        assert!(!refused.exists());
    }
    Ok(())
}

#[test]
fn composite_relationship_refuses_misaligned_duplicate_or_identical_self_fields() -> TestResult {
    let directory = TempDir::new("create")?;
    let empty = SCHEMA.map(TableRows::empty);
    for fields in [
        &[FIELDS[1], FIELDS[0]][..],
        &[FIELDS[0], FIELDS[0]][..],
        &[][..],
        &[FIELDS[0]; 11][..],
    ] {
        let invalid = RelationshipSpec { fields, ..edge() };
        assert!(matches!(
            create_spec(directory.target(), &spec(&empty, &[invalid])),
            Err(WriteError::Compose(
                ComposeError::UnsupportedRelationship { .. }
            ))
        ));
    }
    let scalar = [RelationshipField {
        parent: ColumnRef::Name(b"Id"),
        child: ColumnRef::Ordinal(0),
    }];
    let self_edge = RelationshipSpec {
        name: b"SelfRelation",
        parent: TableRef::Name(b"Alpha"),
        child: TableRef::Ordinal(0),
        ..edge()
    };
    for fields in [&scalar[..], FIELDS] {
        let relation = RelationshipSpec {
            fields,
            ..self_edge
        };
        assert!(matches!(
            create_spec(directory.target(), &spec(&empty[..1], &[relation])),
            Err(WriteError::Compose(
                ComposeError::UnsupportedRelationship { .. }
            ))
        ));
    }
    assert!(directory.is_empty()?);
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
        let directory = TempDir::new("create")?;
        let relation = RelationshipSpec {
            parent: TableRef::Ordinal(0),
            child: TableRef::Name(b"Alpha"),
            fields,
            ..self_edge
        };
        create_spec(directory.target(), &spec(&empty[..1], &[relation]))?;
        assert_eq!(verified(&directory.target())?, 1);
    }
    Ok(())
}

#[test]
fn referenced_parent_payload_field_edit_does_not_assign_its_composite_key() -> TestResult {
    let directory = TempDir::new("create")?;
    let path = directory.target();
    let mut columns = COLUMNS.to_vec();
    columns[3] = ColumnSpec::new(
        b"Body",
        ColumnType::Text {
            max_len: std::num::NonZeroU8::new(255).ok_or("width")?,
        },
    );
    let parent_table = TableSpec {
        columns: &columns,
        ..SCHEMA[0]
    };
    let mut parent = values(1, Some(11), Some(111));
    parent[3] = RowValue::Text(b"before");
    let child = values(10, Some(11), Some(111));
    let requests = [
        TableRows {
            table: parent_table,
            rows: &[&parent],
        },
        TableRows {
            table: SCHEMA[1],
            rows: &[&child],
        },
    ];
    create_spec(&path, &spec(&requests, &[edge()]))?;
    let row = locate(&path, b"Alpha", 1)?;
    let original = fs::read(&path)?;
    let mut replacement = parent;
    replacement[3] = RowValue::Text(&[b'x'; 255]);
    let request = crate::RowUpdate {
        table: b"Alpha",
        row,
        values: &replacement,
    };
    assert!(matches!(
        crate::update_row(&path, request, &mut budget()),
        Err(WriteError::ScalarRelationshipConstraint { .. })
    ));
    assert_eq!(fs::read(&path)?, original);
    crate::update_field(
        &path,
        crate::FieldUpdate {
            table: b"Alpha",
            row,
            column: ColumnOrdinal::new(3),
            value: replacement[3],
        },
        &mut budget(),
    )?;
    let mut work = budget();
    let mut db = crate::DatabaseReader::open(&path, &mut work)?;
    let table = definition(&path, b"Alpha")?;
    {
        let mut rows = db.rows(&table, &mut work)?;
        let mut actual = rows.next_row()?.ok_or("row")?;
        for (ordinal, expected) in replacement.iter().enumerate() {
            let column = ColumnOrdinal::new(ordinal as u16);
            let value = crate::row::scalar_values::read_column(&mut actual, column)?;
            assert_eq!(value, *expected);
        }
    }
    validate_file(&path)?;
    Ok(())
}

#[test]
fn composite_mutations_protect_assigned_parent_rows_and_admit_all_null_children() -> TestResult {
    let directory = TempDir::new("create")?;
    let path = directory.target();
    let parents = [
        values(1, Some(11), Some(111)),
        values(2, Some(11), None),
        values(3, Some(11), None),
        values(4, None, None),
    ];
    let children = [
        values(10, Some(11), Some(111)),
        values(11, Some(11), None),
        values(12, None, None),
    ];
    composite(
        &path,
        &[&parents[0], &parents[1], &parents[2], &parents[3]],
        &[&children[0], &children[1], &children[2]],
    )?;
    let original = fs::read(&path)?;
    let delete = |table: &[u8], id| -> TestResult {
        let row = locate(&path, table, id)?;
        crate::delete_row(&path, crate::RowDelete { table, row }, &mut budget())?;
        Ok(())
    };
    assert!(delete(b"Alpha", 2).is_err());
    assert_eq!(fs::read(&path)?, original);
    for (id, value) in [(1, 11), (2, 999)] {
        let request = crate::FieldUpdate {
            table: b"Alpha",
            row: locate(&path, b"Alpha", id)?,
            column: ColumnOrdinal::new(1),
            value: RowValue::Long(value),
        };
        assert!(matches!(
            crate::update_field(&path, request, &mut budget()),
            Err(WriteError::ScalarRelationshipConstraint { .. })
        ));
        assert_eq!(fs::read(&path)?, original);
    }
    assert!(matches!(
        crate::insert_row(&path, b"Bravo", &values(13, Some(999), None), &mut budget()),
        Err(WriteError::ScalarRelationshipConstraint { .. })
    ));
    assert_eq!(fs::read(&path)?, original);
    assert!(delete(b"Alpha", 4).is_err());
    assert_eq!(fs::read(&path)?, original);
    delete(b"Bravo", 12)?;
    delete(b"Alpha", 4)?;
    crate::insert_row(&path, b"Bravo", &values(13, None, None), &mut budget())?;
    validate_file(&path)?;
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
            index(b"ParentKey", &PAIR[..arity], IndexKind::Unique),
        ];
        let table = table(b"Alpha", &columns, &indexes);
        let relation = RelationshipSpec {
            name: b"SelfRelation",
            fields: &fields[..arity],
            ..relation(b"", 0, 0, 0)
        };
        for null_key in [false, true] {
            let key = |value| {
                if null_key {
                    RowValue::Null
                } else {
                    RowValue::Long(value)
                }
            };
            let (first, second) = (key(11), key(111));
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
                let directory = TempDir::new("create")?;
                let path = directory.target();
                let requests = [TableRows {
                    table,
                    rows: &rows[..if external_child { 2 } else { 1 }],
                }];
                create_spec(&path, &spec(&requests, &[relation]))?;
                let row = locate(&path, b"Alpha", 1)?;
                let before = fs::read(&path)?;
                let assign = crate::FieldUpdate {
                    table: b"Alpha",
                    row,
                    column: ColumnOrdinal::new(1),
                    value: first,
                };
                assert!(relationship_error(crate::update_field(
                    &path,
                    assign,
                    &mut budget()
                )));
                assert_eq!(fs::read(&path)?, before);
                let replace = |values: &[RowValue<'_>]| {
                    let request = crate::RowUpdate {
                        table: b"Alpha",
                        row,
                        values,
                    };
                    crate::update_row(&path, request, &mut budget())
                };
                if external_child {
                    assert!(relationship_error(replace(&selected)));
                    assert_eq!(fs::read(&path)?, before);
                } else {
                    replace(&selected)?;
                    assert_eq!(verified(&path)?, 1);
                    let before = fs::read(&path)?;
                    let mut orphan = selected;
                    orphan[3] = RowValue::Long(999);
                    assert!(matches!(
                        replace(&orphan),
                        Err(WriteError::RelationshipConstraint { .. }
                            | WriteError::ScalarRelationshipConstraint { .. })
                    ));
                    assert_eq!(fs::read(&path)?, before);
                }
            }
        }
    }
    Ok(())
}
