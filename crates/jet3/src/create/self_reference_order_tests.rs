//! EXP-0286 self-reference boundaries and physical index update order.
use super::descending_parent_tests::*;
use crate::testkit::create_spec;
use crate::testkit::{index, table};
use crate::{
    ColumnRef, ColumnSpec, ColumnType, IndexColumnSpec, IndexKind, IndexSpec, RelationshipField,
    RelationshipSide, RelationshipSpec, RowValue, WriteError,
    create::{TableRows, api_relationship_graph_tests::*},
};
use std::fs;
use std::path::Path;

const NODE_COLUMNS: &[ColumnSpec<'static>] = &[
    ColumnSpec::new(b"Id", ColumnType::Long),
    ColumnSpec::new(b"ParentKey", ColumnType::Long),
    ColumnSpec::new(b"ParentId", ColumnType::Long),
];
fn self_link(name: &'static [u8]) -> RelationshipSpec<'static> {
    RelationshipSpec {
        name,
        fields: &[RelationshipField {
            parent: ColumnRef::Ordinal(1),
            child: ColumnRef::Ordinal(2),
        }],
        ..edge(0)
    }
}
/// A self-related `Node` table keyed by a descending unique `ParentKey`.
fn node(path: &Path, rows: &[&[RowValue<'_>]]) -> TestResult {
    let indexes = [
        index(b"ById", ID, IndexKind::Primary),
        index(b"Key", DESC, IndexKind::Unique),
    ];
    let requests = [TableRows {
        table: table(b"Node", NODE_COLUMNS, &indexes),
        rows,
    }];
    create_spec(path, &spec(&requests, &[self_link(b"Relation")]))?;
    Ok(())
}
fn replace(path: &Path, values: &[RowValue<'_>]) -> Result<(), WriteError> {
    let row = locate(path, b"Node", 1).map_err(|_| WriteError::NotFound("test row"))?;
    let request = crate::RowUpdate {
        table: b"Node",
        row,
        values,
    };
    crate::update_row(path, request, &mut budget())
}

#[test]
fn self_reference_key_changes_require_existing_parent_keys() -> TestResult {
    use RowValue::{Long, Null};
    let replacement = [Long(1), Long(4), Long(4)];
    for initial in [
        None,
        Some([Long(1), Null, Null]),
        Some([Long(1), Long(1), Null]),
        Some([Long(1), Long(1), Long(1)]),
    ] {
        let directory = TempDir::new("create")?;
        let path = directory.target();
        let rows: Vec<&[RowValue<'_>]> =
            initial.as_ref().map(|r| r.as_slice()).into_iter().collect();
        node(&path, &rows)?;
        let before = fs::read(&path)?;
        let result = if initial.is_some() {
            replace(&path, &replacement)
        } else {
            crate::insert_row(&path, b"Node", &replacement, &mut budget()).map(|_| ())
        };
        assert!(matches!(
            result,
            Err(WriteError::RelationshipConstraint { value: 4, .. })
        ));
        assert_eq!(fs::read(&path)?, before);
    }

    // The only null parent key is still referenced by its own null child key.
    let directory = TempDir::new("create")?;
    let path = directory.target();
    node(&path, &[&[Long(1), Null, Null]])?;
    let before = fs::read(&path)?;
    assert!(matches!(
        replace(&path, &[Long(1), Long(4), Null]),
        Err(WriteError::NullRelationshipConstraint { .. })
    ));
    assert_eq!(fs::read(&path)?, before);
    let row = locate(&path, b"Node", 1)?;
    crate::delete_row(
        &path,
        crate::RowDelete {
            table: b"Node",
            row,
        },
        &mut budget(),
    )?;
    assert_eq!(definition(&path, b"Node")?.row_count(), 0);

    let directory = TempDir::new("create")?;
    let path = directory.target();
    node(
        &path,
        &[&[Long(1), Null, Null], &[Long(2), Long(1), Long(1)]],
    )?;
    replace(&path, &[Long(1), Long(4), Long(1)])?;
    assert_eq!(verified(&path)?, 1);
    Ok(())
}

#[test]
fn self_key_checks_follow_physical_order_for_generated_and_declared_parents() -> TestResult {
    let foreign_fields = [IndexColumnSpec::ascending(ColumnRef::Ordinal(2))];
    let generated: [IndexSpec<'_>; 2] = [
        index(b"ById", ID, IndexKind::Primary),
        index(b"Descending", DESC, IndexKind::Unique),
    ];
    let declared = [
        index(b"ById", ID, IndexKind::Primary),
        index(b"Child", &foreign_fields, IndexKind::Ordinary),
        index(b"Ascending", ASC, IndexKind::Unique),
    ];
    for generated_parent_first in [false, true] {
        let indexes: &[IndexSpec<'_>] = if generated_parent_first {
            &generated
        } else {
            &declared
        };
        let external = RelationshipSpec {
            name: b"External",
            ..edge(1)
        };
        let relations = [external, self_link(b"SelfLink")];
        for insert in [false, true] {
            let directory = TempDir::new("create")?;
            let path = directory.target();
            let row = [RowValue::Long(1), RowValue::Long(1), RowValue::Long(1)];
            let requests = [
                TableRows {
                    table: table(b"Node", NODE_COLUMNS, indexes),
                    rows: if insert { &[] } else { &[&row] },
                },
                TableRows::empty(table(b"Child", PAIR_COLUMNS, &[])),
            ];
            let edges = &relations[usize::from(!generated_parent_first)..];
            create_spec(&path, &spec(&requests, edges))?;
            let d = definition(&path, b"Node")?;
            let foreign = d
                .relationships()
                .find(|r| r.side() == RelationshipSide::ForeignTable)
                .ok_or("self foreign index")?;
            let parent = d
                .relationships()
                .find(|r| {
                    r.side() == RelationshipSide::PrimaryTable && r.related_table() == d.root()
                })
                .ok_or("self parent index")?;
            assert_eq!(parent.physical_index(), 2);
            assert_eq!(
                foreign.physical_index(),
                if generated_parent_first { 3 } else { 1 }
            );
            let before = fs::read(&path)?;
            let result = if insert {
                let values = [RowValue::Long(4), RowValue::Long(4), RowValue::Long(4)];
                crate::insert_row(&path, b"Node", &values, &mut budget()).map(|_| ())
            } else {
                replace(
                    &path,
                    &[RowValue::Long(1), RowValue::Long(4), RowValue::Long(4)],
                )
            };
            if generated_parent_first {
                result?;
                assert_eq!(verified(&path)?, 2);
            } else {
                assert!(matches!(
                    result,
                    Err(WriteError::RelationshipConstraint { value: 4, .. })
                ));
                assert_eq!(fs::read(&path)?, before);
            }
        }
    }
    Ok(())
}
