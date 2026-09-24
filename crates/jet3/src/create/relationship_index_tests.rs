use super::api_relationship_graph_tests::*;
use crate::WriteError;
use crate::testkit::create_spec;
use crate::testkit::{index, table};
use crate::{
    ColumnRef, ColumnSpec, ColumnType, IndexColumnSpec, IndexKind, IndexNullPolicy, IndexSpec,
    RelationshipField, RelationshipSpec, RowValue, TableSpec,
    create::{api::*, composer::ComposeError},
};
use std::fs;

fn index_count_refused(result: Result<(), WriteError>) -> bool {
    matches!(
        result,
        Err(WriteError::Compose(ComposeError::Schema(
            crate::TableSchemaPlanError::UnobservedIndexCount {
                count: 33,
                observed: 32
            }
        )))
    )
}
fn one_to_one() -> RelationshipSpec<'static> {
    RelationshipSpec {
        unique: true,
        ..relation(b"One", 0, 1, 1)
    }
}

#[test]
fn graph_selects_later_unique_parent_and_preserves_declared_foreign_indexes() -> TestResult {
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"Key", ColumnType::Long),
    ];
    let id_fields = [IndexColumnSpec::ascending(0)];
    let key_fields = [IndexColumnSpec::ascending(1)];
    let descending = [IndexColumnSpec::descending(1)];
    let row = |id, key| [RowValue::Long(id), RowValue::Long(key)];
    let (first, second, child_row) = (row(100, 1), row(200, 2), row(10, 1));
    let edge = RelationshipSpec {
        fields: &[RelationshipField {
            parent: ColumnRef::Ordinal(1),
            child: ColumnRef::Ordinal(1),
        }],
        ..relation(b"Relation", 0, 1, 1)
    };
    for parent_kind in [IndexKind::Primary, IndexKind::Unique] {
        let parent_indexes = [
            index(b"ById", &id_fields, IndexKind::Ordinary),
            index(b"ParentKey", &key_fields, parent_kind),
        ];
        for mode in 0..5 {
            let primary = index(b"ById", &id_fields, IndexKind::Primary);
            let foreign = index(
                b"ExistingForeign",
                if mode == 3 { &descending } else { &key_fields },
                match mode {
                    2 => IndexKind::Unique,
                    4 => IndexKind::Ordinary.with_null_policy(IndexNullPolicy::Required),
                    _ => IndexKind::Ordinary,
                },
            );
            let child_indexes = if mode == 0 {
                [foreign, primary]
            } else {
                [primary, foreign]
            };
            let requests = [
                TableRows {
                    table: table(b"Parent", &columns, &parent_indexes),
                    rows: &[&first, &second],
                },
                TableRows {
                    table: table(b"Child", &columns, &child_indexes),
                    rows: &[&child_row],
                },
            ];
            let directory = TempDir::new("create")?;
            let path = directory.target();
            create_spec(&path, &spec(&requests, &[edge]))?;
            let parent = definition(&path, b"Parent")?;
            let child = definition(&path, b"Child")?;
            let foreign_alias = child.relationships().next().ok_or("child relationship")?;
            let parent_alias = parent.relationships().next();
            assert_eq!(parent_alias.ok_or("parent alias")?.physical_index(), 1);
            assert_eq!(foreign_alias.physical_index(), mode.min(2));
            assert_eq!(child.physical_indexes().len(), if mode < 2 { 2 } else { 3 });
            assert_eq!(child.indexes().len(), 3);
            assert!(
                child
                    .indexes()
                    .iter()
                    .any(|index| index.name().raw_bytes() == foreign.name)
            );
            crate::insert_row(&path, b"Child", &row(11, 2), &mut budget())?;
            let before = fs::read(&path)?;
            assert!(matches!(
                crate::insert_row(&path, b"Child", &row(12, 99), &mut budget()),
                Err(WriteError::RelationshipConstraint { value: 99, .. })
            ));
            let request = crate::RowDelete {
                table: b"Parent",
                row: locate(&path, b"Parent", 100)?,
            };
            assert!(matches!(
                crate::delete_row(&path, request, &mut budget()),
                Err(WriteError::RelationshipConstraint { value: 1, .. })
            ));
            assert_eq!(fs::read(&path)?, before);
            assert_eq!(verified(&path)?, 1);
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
            .map(|(n, name)| {
                let fields = if reuse && n == 1 { &fk } else { &id };
                index(name, fields, IndexKind::Ordinary)
            })
            .collect::<Vec<_>>();
        for count in [31, 32] {
            let directory = TempDir::new("create")?;
            let child = TableSpec {
                indexes: &indexes[..count],
                ..TABLES[1]
            };
            let requests = [TableRows::empty(TABLES[0]), TableRows::empty(child)];
            let result = create_spec(
                directory.target(),
                &spec(&requests, &[relation(b"Relation", 0, 1, 1)]),
            );
            if count == 32 {
                assert!(index_count_refused(result));
                assert!(directory.is_empty()?);
            } else {
                result?;
                let definition = definition(&directory.target(), child.name)?;
                assert_eq!(definition.indexes().len(), 32);
                let physical = definition.physical_indexes().len();
                assert_eq!(physical, if reuse { 31 } else { 32 });
            }
        }
    }
    Ok(())
}

#[test]
fn graph_parent_selection_follows_logical_name_order_before_primary_status() -> TestResult {
    let unique = IndexSpec {
        name: b"AUnique",
        kind: IndexKind::Unique,
        ..INDEXES[0]
    };
    let primary = IndexSpec {
        name: b"ZPrimary",
        ..INDEXES[0]
    };
    for primary_first in [false, true] {
        let indexes = if primary_first {
            [primary, unique]
        } else {
            [unique, primary]
        };
        let parent = TableSpec {
            indexes: &indexes,
            ..TABLES[0]
        };
        let directory = TempDir::new("create")?;
        let requests = [TableRows::empty(parent), TableRows::empty(TABLES[1])];
        create_spec(
            directory.target(),
            &spec(&requests, &[relation(b"Relation", 0, 1, 1)]),
        )?;
        let definition = definition(&directory.target(), parent.name)?;
        let alias = definition.relationships().next().ok_or("parent alias")?;
        assert_eq!(alias.physical_index(), u16::from(primary_first));
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
    let null = [RowValue::Null; 4];
    let keyed = values(1, None, None);
    let directory = TempDir::new("create")?;
    let path = directory.target();
    let requests = [
        TableRows {
            table: parent,
            rows: &[&keyed, &null, &null],
        },
        TableRows {
            table: TABLES[1],
            rows: &[&keyed],
        },
    ];
    create_spec(&path, &spec(&requests, &[relation(b"Relation", 0, 1, 1)]))?;
    let insert = |id, key| {
        let values = values(id, Some(key), None);
        crate::insert_row(&path, TABLES[1].name, &values, &mut budget())
    };
    insert(2, 1)?;
    let before = fs::read(&path)?;
    assert!(matches!(
        insert(3, 99),
        Err(WriteError::RelationshipConstraint { value: 99, .. })
    ));
    assert_eq!(fs::read(&path)?, before);
    assert_eq!(verified(&path)?, 1);
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
        .map(|name| index(name, &fields, IndexKind::Unique))
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
        let directory = TempDir::new("create")?;
        let parent = TableSpec {
            indexes: &indexes[..count],
            ..TABLES[0]
        };
        let requests = [TableRows::empty(parent), TableRows::empty(TABLES[1])];
        let result = create_spec(
            directory.target(),
            &spec(&requests, &[relation(b"Relation", 0, 1, 1)]),
        );
        if count == 32 {
            assert!(index_count_refused(result));
            assert!(directory.is_empty()?);
        } else {
            result?;
            let definition = definition(&directory.target(), parent.name)?;
            let alias = definition.relationships().next().ok_or("parent alias")?;
            assert_eq!(alias.name().raw_bytes(), hidden.as_bytes());
            assert_eq!(alias.raw_selector() as usize, count);
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
            IndexKind::Unique.with_null_policy(IndexNullPolicy::Required),
            false,
            false,
        ),
        (
            IndexKind::Unique.with_null_policy(IndexNullPolicy::IgnoreAllNull),
            false,
            false,
        ),
    ] {
        let directory = TempDir::new("create")?;
        let path = directory.target();
        let fields = [if descending {
            IndexColumnSpec::descending(1)
        } else {
            IndexColumnSpec::ascending(1)
        }];
        let indexes = [index(b"Existing", &fields, kind)];
        let child = TableSpec {
            indexes: &indexes,
            ..TABLES[1]
        };
        let requests = [TableRows::empty(TABLES[0]), TableRows::empty(child)];
        create_spec(&path, &spec(&requests, &[one_to_one()]))?;
        let table = definition(&path, child.name)?;
        assert_eq!(table.physical_indexes().len(), if reused { 1 } else { 2 });
        let foreign = table.relationships().next().ok_or("foreign")?;
        assert_eq!(foreign.physical_index(), u16::from(!reused));
        let tree = &table.physical_indexes()[usize::from(foreign.physical_index())];
        assert_eq!(tree.raw_flags(), 1);
        let mut work = budget();
        let mut db = crate::DatabaseReader::open(&path, &mut work)?;
        assert!(db.relationship_catalog(&mut work)?[0].one_to_one());
        assert_eq!(verified(&path)?, 1);
    }

    // Ordinary and one-to-one edges on the same child column keep separate trees.
    let directory = TempDir::new("create")?;
    let edges = [
        relation(b"Many", 0, 2, 1),
        RelationshipSpec {
            unique: true,
            ..relation(b"One", 1, 2, 1)
        },
    ];
    create_spec(
        directory.target(),
        &spec(&TABLES.map(TableRows::empty), &edges),
    )?;
    let table = definition(&directory.target(), TABLES[2].name)?;
    let flags = table.physical_indexes().iter().map(|i| i.raw_flags());
    assert_eq!(flags.collect::<Vec<_>>(), [9, 0, 1]);
    assert_eq!(verified(&directory.target())?, 2);
    Ok(())
}

#[test]
fn one_to_one_creation_refuses_duplicate_children_and_records_child_row_count() -> TestResult {
    let directory = TempDir::new("create")?;
    let path = directory.target();
    let parents = [values(1, None, None), values(2, None, None)];
    let children = [
        values(1, Some(1), None),
        values(2, Some(2), None),
        values(3, None, None),
        values(4, Some(1), None),
    ];
    let parent_rows: [&[RowValue<'_>]; 2] = [&parents[0], &parents[1]];
    let child_rows: [&[RowValue<'_>]; 4] = [&children[0], &children[1], &children[2], &children[3]];
    let requests = |count| {
        [
            TableRows {
                table: TABLES[0],
                rows: &parent_rows,
            },
            TableRows {
                table: TABLES[1],
                rows: &child_rows[..count],
            },
        ]
    };
    assert!(create_spec(&path, &spec(&requests(4), &[one_to_one()])).is_err());
    assert!(directory.is_empty()?);
    create_spec(&path, &spec(&requests(3), &[one_to_one()]))?;
    let child = definition(&path, TABLES[1].name)?;
    let foreign = child.relationships().next().ok_or("foreign relationship")?;
    assert_eq!(
        child.physical_indexes()[usize::from(foreign.physical_index())].sourced_prefix(),
        &[3, 0, 0, 0, 3, 0, 0, 0]
    );
    Ok(())
}
