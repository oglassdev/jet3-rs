use super::api_relationship_graph::*;
use crate::WriteError;
use crate::testkit::{create, create_spec};
use crate::testkit::{index, table};
use crate::{
    ColumnRef, ColumnSpec, ColumnType, DatabaseReader, IndexColumnSpec, IndexDirection, IndexKind,
    IndexSpec, RelationshipField, RelationshipSpec, ResourceBudget, ResourceLimits, RowValue,
    TableRef, TableSpec, TextCodePage,
    create::{
        api::*,
        check::*,
        composer::{ComposeError, GraphImage, compose_relationship_graph},
    },
};
use std::fs;

pub(super) use crate::testkit::TempDir;
pub(super) use crate::testkit::TestResult;
pub(super) use crate::testkit::budget;
pub(super) const COLUMNS: &[ColumnSpec<'static>] = &[
    ColumnSpec::new(b"Id", ColumnType::Long),
    ColumnSpec::new(b"One", ColumnType::Long),
    ColumnSpec::new(b"Two", ColumnType::Long),
    ColumnSpec::new(b"Body", ColumnType::Memo),
];
pub(super) const INDEXES: &[IndexSpec<'static>] = &[index(
    b"ById",
    &[IndexColumnSpec {
        column: ColumnRef::Ordinal(0),
        direction: IndexDirection::Ascending,
    }],
    IndexKind::Primary,
)];
pub(super) const TABLES: [TableSpec<'static>; 3] = [
    table(b"Alpha", COLUMNS, INDEXES),
    table(b"Bravo", COLUMNS, INDEXES),
    table(b"Charlie", COLUMNS, INDEXES),
];
pub(super) fn relation(
    name: &'static [u8],
    parent: usize,
    child: usize,
    column: u16,
) -> RelationshipSpec<'static> {
    RelationshipSpec {
        unique: false,
        enforce: true,
        join: crate::RelationshipJoin::Inner,
        cascade_updates: false,
        cascade_deletes: false,
        name,
        parent: TableRef::Ordinal(parent),
        child: TableRef::Ordinal(child),
        fields: match column {
            1 => &[RelationshipField {
                parent: ColumnRef::Ordinal(0),
                child: ColumnRef::Ordinal(1),
            }],
            2 => &[RelationshipField {
                parent: ColumnRef::Ordinal(0),
                child: ColumnRef::Ordinal(2),
            }],
            _ => &[],
        },
    }
}

#[test]
fn graph_creation_handles_multiple_shared_chain_and_self_endpoints() -> TestResult {
    let payload = [b'x'; 4096];
    let rows: &[&[RowValue<'_>]] = &[
        &[
            RowValue::Long(1),
            RowValue::Null,
            RowValue::Null,
            RowValue::Memo(&payload),
        ],
        &[
            RowValue::Long(2),
            RowValue::Long(1),
            RowValue::Long(1),
            RowValue::Memo(b"a"),
        ],
        &[
            RowValue::Long(3),
            RowValue::Long(2),
            RowValue::Long(2),
            RowValue::Null,
        ],
    ];
    for edges in [
        vec![(0, 1, 1), (0, 2, 1)],
        vec![(0, 1, 1), (0, 1, 2)],
        vec![(0, 2, 1), (1, 2, 2)],
        vec![(0, 1, 1), (1, 2, 1)],
        vec![(0, 0, 1)],
        vec![(0, 2, 1), (1, 2, 1)],
        vec![(2, 0, 1), (1, 0, 2)],
    ] {
        let relationships: Vec<_> = edges
            .iter()
            .enumerate()
            .map(|(i, &(parent, child, column))| {
                relation(
                    if i == 0 { b"RelationA" } else { b"RelationB" },
                    parent,
                    child,
                    column,
                )
            })
            .collect();
        for populated in [false, true] {
            let directory = TempDir::new("create")?;
            let requests: Vec<_> = TABLES
                .iter()
                .map(|&table| TableRows {
                    table,
                    rows: if populated { rows } else { &[] },
                })
                .collect();
            create_spec(
                directory.target(),
                &DatabaseSpec {
                    tables: &requests,
                    relationships: &relationships,
                    ..DatabaseSpec::default()
                },
            )?;
            let mut database = DatabaseReader::open(directory.target(), &mut budget())?;
            let report = database.validate(TextCodePage::Windows1252, &mut budget())?;
            assert_eq!(report.user_tables, 3);
            assert_eq!(fs::read_dir(&*directory)?.count(), 1);
        }
    }
    Ok(())
}

#[test]
fn graph_creation_rejects_orphans_and_duplicate_names_before_publication() -> TestResult {
    let directory = TempDir::new("create")?;
    let rows: &[&[RowValue<'_>]] = &[&[
        RowValue::Long(1),
        RowValue::Long(2),
        RowValue::Null,
        RowValue::Null,
    ]];
    let requests = [TableRows {
        table: TABLES[0],
        rows,
    }];
    assert!(matches!(
        create_spec(
            directory.target(),
            &DatabaseSpec {
                tables: &requests,
                relationships: &[relation(b"Self", 0, 0, 1)],
                ..DatabaseSpec::default()
            }
        ),
        Err(WriteError::Compose(
            ComposeError::OrphanInitialRelationshipKey { row: 0, value: 2 }
        ))
    ));
    assert!(matches!(
        create_spec(
            directory.target(),
            &DatabaseSpec {
                tables: &TABLES.map(TableRows::empty),
                relationships: &[relation(b"Same", 0, 1, 1), relation(b"same", 0, 1, 2)],
                ..DatabaseSpec::default()
            }
        ),
        Err(WriteError::Compose(
            ComposeError::UnsupportedRelationship { .. }
        ))
    ));
    let mut limited = ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(0));
    assert!(
        create_database(
            directory.target(),
            &DatabaseSpec {
                tables: &TABLES.map(TableRows::empty),
                relationships: &[relation(b"A", 0, 1, 1)],
                ..DatabaseSpec::default()
            },
            &mut limited
        )
        .is_err()
    );
    assert!(fs::read_dir(&*directory)?.next().is_none());
    Ok(())
}

#[test]
fn graph_creation_preserves_destination_and_empty_graph_matches_normal_creation() -> TestResult {
    let directory = TempDir::new("create")?;
    let other = directory.join("normal.mdb");
    create_spec(
        directory.target(),
        &DatabaseSpec {
            tables: &TABLES.map(TableRows::empty),
            relationships: &[],
            ..DatabaseSpec::default()
        },
    )?;
    create(&other, &TABLES.map(crate::TableRows::empty))?;
    let original = fs::read(directory.target())?;
    assert_eq!(original, fs::read(other)?);
    assert!(matches!(
        create_spec(
            directory.target(),
            &DatabaseSpec {
                tables: &TABLES.map(TableRows::empty),
                relationships: &[relation(b"A", 0, 1, 1)],
                ..DatabaseSpec::default()
            }
        ),
        Err(WriteError::CreatePublish(_))
    ));
    assert_eq!(fs::read(directory.target())?, original);
    Ok(())
}

#[test]
fn graph_candidate_check_rejects_changed_index_names_flags_and_endpoint_columns() -> TestResult {
    let directory = TempDir::new("create")?;
    let relationship = relation(b"A", 0, 1, 1);
    let requests = TABLES.map(|table| TableRows { table, rows: &[] });
    let GraphImage { image, tables } =
        compose_relationship_graph(&requests, &[relationship], &mut budget())?;
    let pages = image.into_pages();
    create_spec(
        directory.target(),
        &DatabaseSpec {
            tables: &TABLES.map(TableRows::empty),
            relationships: &[relationship],
            ..DatabaseSpec::default()
        },
    )?;
    for changed in [
        IndexSpec {
            name: b"Wrong",
            ..INDEXES[0]
        },
        IndexSpec {
            kind: IndexKind::Unique,
            ..INDEXES[0]
        },
    ] {
        let indexes = [changed];
        let mut wrong = requests;
        wrong[0].table.indexes = &indexes;
        assert!(matches!(
            check_graph(
                &directory.target(),
                &wrong,
                &[relationship],
                &tables,
                &pages,
                &mut budget()
            ),
            Err(ImageCheckError::Mismatch { .. })
        ));
    }
    let wrong = relation(b"A", 0, 1, 2);
    assert!(matches!(
        check_graph(
            &directory.target(),
            &requests,
            &[wrong],
            &tables,
            &pages,
            &mut budget()
        ),
        Err(ImageCheckError::Mismatch { .. })
    ));
    Ok(())
}

#[test]
fn graph_creation_resolves_generated_parent_keys_before_foreign_checks() -> TestResult {
    let directory = TempDir::new("create")?;
    let mut columns = COLUMNS.to_vec();
    columns[0] = ColumnSpec::new(b"Id", ColumnType::AutoIncrement);
    let parent = TableSpec {
        columns: &columns,
        ..TABLES[0]
    };
    let parent_rows: &[&[RowValue<'_>]] = &[
        &[
            RowValue::AutoIncrement,
            RowValue::Null,
            RowValue::Null,
            RowValue::Null,
        ],
        &[
            RowValue::AutoIncrement,
            RowValue::Null,
            RowValue::Null,
            RowValue::Null,
        ],
    ];
    let child_rows: &[&[RowValue<'_>]] = &[
        &[
            RowValue::Long(10),
            RowValue::Long(1),
            RowValue::Null,
            RowValue::Null,
        ],
        &[
            RowValue::Long(11),
            RowValue::Long(2),
            RowValue::Null,
            RowValue::Null,
        ],
    ];
    let requests = [
        TableRows {
            table: parent,
            rows: parent_rows,
        },
        TableRows {
            table: TABLES[1],
            rows: child_rows,
        },
    ];
    create_spec(
        directory.target(),
        &DatabaseSpec {
            tables: &requests,
            relationships: &[relation(b"A", 0, 1, 1)],
            ..DatabaseSpec::default()
        },
    )?;
    let bad_rows: &[&[RowValue<'_>]] = &[&[
        RowValue::Long(12),
        RowValue::Long(3),
        RowValue::Null,
        RowValue::Null,
    ]];
    let bad = [
        requests[0],
        TableRows {
            table: TABLES[1],
            rows: bad_rows,
        },
    ];
    let missing = directory.join("orphan.mdb");
    assert!(matches!(
        create_spec(
            &missing,
            &DatabaseSpec {
                tables: &bad,
                relationships: &[relation(b"A", 0, 1, 1)],
                ..DatabaseSpec::default()
            }
        ),
        Err(WriteError::Compose(
            ComposeError::OrphanInitialRelationshipKey { row: 0, value: 3 }
        ))
    ));
    assert!(!missing.exists());
    Ok(())
}
