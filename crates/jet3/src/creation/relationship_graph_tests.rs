use super::*;
use crate::{
    ColumnRef, ColumnSpec, IndexColumnSpec, IndexDirection, IndexSpec, RelationshipColumn,
    ResourceLimits,
};
use std::fs;
use std::sync::atomic::{AtomicU64, Ordering};

type TestResult = Result<(), Box<dyn std::error::Error>>;
static NEXT: AtomicU64 = AtomicU64::new(0);
struct Directory(std::path::PathBuf);
impl Directory {
    fn new() -> Result<Self, io::Error> {
        let path = std::env::temp_dir().join(format!(
            "jet3-create-graph-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path)?;
        Ok(Self(path))
    }
    fn target(&self) -> std::path::PathBuf {
        self.0.join("created.mdb")
    }
}
impl Drop for Directory {
    fn drop(&mut self) {
        let _result = fs::remove_dir_all(&self.0);
    }
}
fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}
const COLUMNS: &[ColumnSpec<'static>] = &[
    ColumnSpec::new(b"Id", ColumnType::Long),
    ColumnSpec::new(b"One", ColumnType::Long),
    ColumnSpec::new(b"Two", ColumnType::Long),
    ColumnSpec::new(b"Body", ColumnType::Memo),
];
const INDEXES: &[IndexSpec<'static>] = &[IndexSpec {
    name: b"ById",
    fields: &[IndexColumnSpec {
        column: ColumnRef::Ordinal(0),
        direction: IndexDirection::Ascending,
    }],
    kind: IndexKind::Primary,
}];
const TABLES: [TableSpec<'static>; 3] = [
    TableSpec {
        name: b"Alpha",
        columns: COLUMNS,
        indexes: INDEXES,
    },
    TableSpec {
        name: b"Bravo",
        columns: COLUMNS,
        indexes: INDEXES,
    },
    TableSpec {
        name: b"Charlie",
        columns: COLUMNS,
        indexes: INDEXES,
    },
];
fn relation(
    name: &'static [u8],
    parent: usize,
    child: usize,
    column: u16,
) -> RelationshipSpec<'static> {
    RelationshipSpec {
        name,
        parent: RelationshipColumn {
            table: TableRef::Ordinal(parent),
            column: ColumnRef::Ordinal(0),
        },
        child: RelationshipColumn {
            table: TableRef::Ordinal(child),
            column: ColumnRef::Ordinal(column),
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
            let directory = Directory::new()?;
            let requests: Vec<_> = TABLES
                .iter()
                .map(|&table| TableRows {
                    table,
                    rows: if populated { rows } else { &[] },
                })
                .collect();
            create_database_with_relationships_and_rows(
                directory.target(),
                &requests,
                &relationships,
                &mut budget(),
            )?;
            let mut database = DatabaseReader::open(directory.target(), &mut budget())?;
            let report = database.validate(TextCodePage::Windows1252, &mut budget())?;
            assert_eq!(report.user_tables, 3);
            assert_eq!(fs::read_dir(&directory.0)?.count(), 1);
        }
    }
    Ok(())
}

#[test]
fn graph_creation_rejects_orphans_duplicate_names_and_excess_relations_before_publication()
-> TestResult {
    let directory = Directory::new()?;
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
        create_database_with_relationships_and_rows(
            directory.target(),
            &requests,
            &[relation(b"Self", 0, 0, 1)],
            &mut budget()
        ),
        Err(CreateDatabaseError::Compose(
            ComposeError::OrphanInitialRelationshipKey { row: 0, value: 2 }
        ))
    ));
    for relationships in [
        vec![relation(b"Same", 0, 1, 1), relation(b"same", 0, 1, 2)],
        vec![
            relation(b"A", 0, 1, 1),
            relation(b"B", 0, 2, 1),
            relation(b"C", 0, 0, 1),
        ],
    ] {
        assert!(matches!(
            create_database_with_relationships(
                directory.target(),
                &TABLES,
                &relationships,
                &mut budget()
            ),
            Err(CreateDatabaseError::Compose(
                ComposeError::UnsupportedRelationship { .. }
            ))
        ));
    }
    let mut limited = ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(0));
    assert!(
        create_database_with_relationships(
            directory.target(),
            &TABLES,
            &[relation(b"A", 0, 1, 1)],
            &mut limited
        )
        .is_err()
    );
    assert!(fs::read_dir(&directory.0)?.next().is_none());
    Ok(())
}

#[test]
fn graph_creation_preserves_destination_and_empty_graph_matches_normal_creation() -> TestResult {
    let directory = Directory::new()?;
    let other = directory.0.join("normal.mdb");
    create_database_with_relationships(directory.target(), &TABLES, &[], &mut budget())?;
    crate::create_database(&other, &TABLES, &mut budget())?;
    let original = fs::read(directory.target())?;
    assert_eq!(original, fs::read(other)?);
    assert!(matches!(
        create_database_with_relationships(
            directory.target(),
            &TABLES,
            &[relation(b"A", 0, 1, 1)],
            &mut budget()
        ),
        Err(CreateDatabaseError::Publish(_))
    ));
    assert_eq!(fs::read(directory.target())?, original);
    Ok(())
}

#[test]
fn graph_candidate_check_rejects_changed_index_names_flags_and_endpoint_columns() -> TestResult {
    let directory = Directory::new()?;
    let relationship = relation(b"A", 0, 1, 1);
    let requests = TABLES.map(|table| TableRows { table, rows: &[] });
    let GraphImage { image, tables } =
        compose_relationship_graph(&requests, &[relationship], &mut budget())?;
    let pages = image.into_pages();
    create_database_with_relationships(
        directory.target(),
        &TABLES,
        &[relationship],
        &mut budget(),
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
            Err(CandidateCheckError::Mismatch { .. })
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
        Err(CandidateCheckError::Mismatch { .. })
    ));
    Ok(())
}

#[test]
fn graph_creation_resolves_generated_parent_keys_before_foreign_checks() -> TestResult {
    let directory = Directory::new()?;
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
    create_database_with_relationships_and_rows(
        directory.target(),
        &requests,
        &[relation(b"A", 0, 1, 1)],
        &mut budget(),
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
    let missing = directory.0.join("orphan.mdb");
    assert!(matches!(
        create_database_with_relationships_and_rows(
            &missing,
            &bad,
            &[relation(b"A", 0, 1, 1)],
            &mut budget()
        ),
        Err(CreateDatabaseError::Compose(
            ComposeError::OrphanInitialRelationshipKey { row: 0, value: 3 }
        ))
    ));
    assert!(!missing.exists());
    Ok(())
}
