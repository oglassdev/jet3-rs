//! EXP-0286 generated ascending parent trees and retained mutation counters.
use super::api_relationship_graph_tests::*;
use crate::WriteError;
use crate::testkit::create_spec;
use crate::testkit::{index, table};
use crate::{
    ColumnRef, ColumnSpec, ColumnType, DatabaseReader, IndexColumnSpec, IndexDirection, IndexKind,
    IndexNullPolicy, RelationshipField, RelationshipSide, RelationshipSpec, RowValue,
    create::{api::*, composer::ComposeError},
};
use std::fs;

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
    RelationshipSpec {
        fields: &[RelationshipField {
            parent: ColumnRef::Ordinal(1),
            child: ColumnRef::Ordinal(1),
        }],
        ..relation(b"Relation", 0, child, 1)
    }
}
fn pair(id: i32, key: Option<i32>) -> [RowValue<'static>; 2] {
    [
        RowValue::Long(id),
        key.map_or(RowValue::Null, RowValue::Long),
    ]
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
        let nullable = kind.null_policy() == IndexNullPolicy::Include;
        let key = |id| {
            if nullable {
                RowValue::Null
            } else {
                RowValue::Long(id)
            }
        };
        let rows = [
            [RowValue::Long(1), RowValue::Long(1), RowValue::Long(1)],
            [RowValue::Long(2), key(2), RowValue::Long(1)],
            [RowValue::Long(3), key(3), RowValue::Long(1)],
        ];
        let rows: &[&[RowValue<'_>]] = &[&rows[0], &rows[1], &rows[2]];
        let indexes = [
            index(b"ById", ID, IndexKind::Ordinary),
            index(b"Descending", DESC, kind),
        ];
        let child_indexes = [index(b"ById", ID, IndexKind::Primary)];
        let requests = [
            TableRows {
                table: table(b"Parent", &columns, &indexes),
                rows,
            },
            TableRows {
                table: table(b"Child", &columns, &child_indexes),
                rows: &rows[..1],
            },
        ];
        for self_reference in [false, true] {
            let directory = TempDir::new("create")?;
            let path = directory.target();
            let mut relationship = edge(usize::from(!self_reference));
            if self_reference {
                relationship.fields = &[RelationshipField {
                    parent: ColumnRef::Ordinal(1),
                    child: ColumnRef::Ordinal(2),
                }];
            }
            let tables = &requests[..if self_reference { 1 } else { 2 }];
            create_spec(&path, &spec(tables, &[relationship]))?;
            let d = definition(&path, b"Parent")?;
            let expected = if self_reference { 3 } else { 2 };
            assert_eq!(d.physical_indexes().len(), expected + 1);
            assert_eq!(d.indexes().len(), expected + 1);
            let generated = &d.physical_indexes()[expected];
            assert_eq!(generated.raw_flags(), if nullable { 1 } else { 9 });
            assert_eq!(generated.fields()[0].direction(), IndexDirection::Ascending);
            assert_eq!(
                d.physical_indexes()[1].fields()[0].direction(),
                IndexDirection::Descending
            );
            let alias = d
                .relationships()
                .find(|r| r.side() == RelationshipSide::PrimaryTable)
                .ok_or("parent alias")?;
            assert_eq!(usize::from(alias.physical_index()), expected);
            let mut db = DatabaseReader::open(&path, &mut budget())?;
            let tree = db.index_tree(&d, expected as u16, &mut budget())?;
            assert_eq!(tree.entries().len(), 3);
            assert_eq!(verified(&path)?, 1);
        }
    }
    Ok(())
}

#[test]
fn generated_parent_is_shared_and_declared_ascending_parent_is_preferred() -> TestResult {
    for with_ascending in [false, true] {
        let indexes = [
            index(b"Descending", DESC, IndexKind::Unique),
            index(b"Ascending", ASC, IndexKind::Unique),
        ];
        let tables = [
            table(
                b"Parent",
                PAIR_COLUMNS,
                &indexes[..if with_ascending { 2 } else { 1 }],
            ),
            table(b"ChildA", PAIR_COLUMNS, &[]),
            table(b"ChildB", PAIR_COLUMNS, &[]),
        ];
        let second = RelationshipSpec {
            name: b"Second",
            ..edge(2)
        };
        let directory = TempDir::new("create")?;
        create_spec(
            directory.target(),
            &spec(&tables.map(TableRows::empty), &[edge(1), second]),
        )?;
        let d = definition(&directory.target(), b"Parent")?;
        assert_eq!(d.physical_indexes().len(), 2);
        assert_eq!(d.indexes().len(), if with_ascending { 4 } else { 3 });
        assert!(d.relationships().all(|r| r.physical_index() == 1));
    }
    // The generated tree copies the policy of the logically first eligible index.
    let nullable = index(b"ANullableDesc", DESC, IndexKind::Unique);
    let required = index(b"ZRequiredPrimary", DESC, IndexKind::Primary);
    for indexes in [[nullable, required], [required, nullable]] {
        let tables = [
            table(b"Parent", PAIR_COLUMNS, &indexes),
            table(b"Child", PAIR_COLUMNS, &[]),
        ];
        let directory = TempDir::new("create")?;
        create_spec(
            directory.target(),
            &spec(&tables.map(TableRows::empty), &[edge(1)]),
        )?;
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
fn generated_parent_capacity_and_hidden_names_remain_bounded() -> TestResult {
    let names = (0..32)
        .map(|i| format!("Index{i:02}").into_bytes())
        .collect::<Vec<_>>();
    let indexes = names
        .iter()
        .map(|name| index(name, DESC, IndexKind::Unique))
        .collect::<Vec<_>>();
    let child = TableRows::empty(table(b"Child", PAIR_COLUMNS, &[]));
    for count in [31, 32] {
        let parent = TableRows::empty(table(b"Parent", PAIR_COLUMNS, &indexes[..count]));
        let directory = TempDir::new("create")?;
        let result = create_spec(directory.target(), &spec(&[parent, child], &[edge(1)]));
        if count == 31 {
            result?;
            let d = definition(&directory.target(), b"Parent")?;
            assert_eq!(d.physical_indexes().len(), 32);
            assert_eq!(d.indexes().len(), 32);
        } else {
            assert!(matches!(
                result,
                Err(WriteError::Compose(ComposeError::Schema(
                    crate::TableSchemaPlanError::UnobservedIndexCount { .. }
                )))
            ));
            assert!(directory.is_empty()?);
        }
    }
    let hidden = [index(b".rB", DESC, IndexKind::Unique)];
    let parent = TableRows::empty(table(b"Parent", PAIR_COLUMNS, &hidden));
    let directory = TempDir::new("create")?;
    assert!(create_spec(directory.target(), &spec(&[parent, child], &[edge(1)])).is_err());
    assert!(directory.is_empty()?);
    Ok(())
}

#[test]
fn generated_parent_assignments_and_deletes_clamp_only_its_retained_counters() -> TestResult {
    let indexes = [
        index(b"ById", ID, IndexKind::Primary),
        index(b"Descending", DESC, IndexKind::Unique),
    ];
    let rows = [pair(1, Some(1)), pair(2, Some(2)), pair(3, Some(3))];
    let rows: &[&[RowValue<'_>]] = &[&rows[0], &rows[1], &rows[2]];
    let directory = TempDir::new("create")?;
    let path = directory.target();
    let requests = [
        TableRows {
            table: table(b"Parent", PAIR_COLUMNS, &indexes),
            rows,
        },
        TableRows {
            table: table(b"Child", PAIR_COLUMNS, &indexes[..1]),
            rows: &rows[..1],
        },
    ];
    create_spec(&path, &spec(&requests, &[edge(1)]))?;
    let d = definition(&path, b"Parent")?;
    let first = locate(&path, b"Parent", 1)?;
    let second = locate(&path, b"Parent", 2)?;
    let third = locate(&path, b"Parent", 3)?;
    // Native rows-before-relation history: two initial keys, then a third insertion.
    let mut bytes = fs::read(&path)?;
    let offset = d.root().get() as usize * crate::PAGE_BYTES + 43 + 2 * 8;
    bytes[offset..offset + 4].copy_from_slice(&2u32.to_le_bytes());
    fs::write(&path, bytes)?;
    crate::update_field(
        &path,
        crate::FieldUpdate {
            table: b"Parent",
            row: third,
            column: crate::ColumnOrdinal::new(1),
            value: RowValue::Long(30),
        },
        &mut budget(),
    )?;
    let after = definition(&path, b"Parent")?;
    assert_eq!(
        after.physical_indexes()[2].sourced_prefix(),
        &[1, 0, 0, 0, 1, 0, 0, 0]
    );
    for index in &after.physical_indexes()[..2] {
        assert_eq!(index.sourced_prefix(), &[0, 0, 0, 0, 3, 0, 0, 0]);
    }
    let delete = |row| {
        let request = crate::RowDelete {
            table: b"Parent",
            row,
        };
        crate::delete_row(&path, request, &mut budget())
    };
    delete(second)?;
    let generated = |path| -> TestResult<Vec<u8>> {
        Ok(definition(path, b"Parent")?.physical_indexes()[2]
            .sourced_prefix()
            .to_vec())
    };
    assert_eq!(generated(&path)?, [0; 8]);
    let before = fs::read(&path)?;
    assert!(delete(first).is_err());
    assert_eq!(fs::read(&path)?, before);
    delete(third)?;
    assert_eq!(generated(&path)?, [0; 8]);
    Ok(())
}

#[test]
fn null_parent_mutations_require_no_remaining_null_children() -> TestResult {
    let parents = [pair(1, Some(1)), pair(2, None), pair(3, None)];
    let (first, second) = (pair(10, Some(1)), pair(11, None));
    let children: &[&[RowValue<'_>]] = &[&first, &second];
    for direction in [ASC, DESC] {
        let indexes = [
            index(b"ById", ID, IndexKind::Primary),
            index(b"Key", direction, IndexKind::Unique),
        ];
        for null_child in [false, true] {
            for delete in [false, true] {
                let directory = TempDir::new("create")?;
                let path = directory.target();
                let requests = [
                    TableRows {
                        table: table(b"Parent", PAIR_COLUMNS, &indexes),
                        rows: &[&parents[0], &parents[1], &parents[2]],
                    },
                    TableRows {
                        table: table(b"Child", PAIR_COLUMNS, &indexes[..1]),
                        rows: &children[..if null_child { 2 } else { 1 }],
                    },
                ];
                create_spec(&path, &spec(&requests, &[edge(1)]))?;
                let row = locate(&path, b"Parent", 2)?;
                let before = fs::read(&path)?;
                let result = if delete {
                    let request = crate::RowDelete {
                        table: b"Parent",
                        row,
                    };
                    crate::delete_row(&path, request, &mut budget())
                } else {
                    let values = pair(2, Some(22));
                    let request = crate::RowUpdate {
                        table: b"Parent",
                        row,
                        values: &values,
                    };
                    crate::update_row(&path, request, &mut budget())
                };
                if null_child {
                    assert!(matches!(
                        result,
                        Err(WriteError::NullRelationshipConstraint { .. })
                    ));
                    assert_eq!(fs::read(&path)?, before);
                } else {
                    result?;
                    assert_eq!(verified(&path)?, 1);
                }
            }
        }
    }
    Ok(())
}
