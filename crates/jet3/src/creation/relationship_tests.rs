use super::*;
use crate::{
    ColumnRef, ColumnSpec, IndexColumnSpec, IndexSpec, RelationshipColumn, ResourceLimits, TableRef,
};
use std::fs;
use std::sync::atomic::{AtomicU64, Ordering};

type TestResult = Result<(), Box<dyn std::error::Error>>;
static NEXT: AtomicU64 = AtomicU64::new(0);
struct Directory(std::path::PathBuf);
impl Directory {
    fn new() -> Result<Self, io::Error> {
        let path = std::env::temp_dir().join(format!(
            "jet3-create-relation-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path)?;
        Ok(Self(path))
    }
    fn target(&self) -> std::path::PathBuf {
        self.0.join("created.mdb")
    }
    fn empty(&self) -> Result<bool, io::Error> {
        Ok(fs::read_dir(&self.0)?.next().is_none())
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
fn schema(two: bool) -> ([TableSpec<'static>; 2], RelationshipSpec<'static>) {
    const PARENT_COLUMNS: &[ColumnSpec<'static>] = &[
        ColumnSpec::new(b"Code2", ColumnType::Long),
        ColumnSpec::new(b"Key1", ColumnType::Long),
    ];
    const CHILD_COLUMNS: &[ColumnSpec<'static>] = &[
        ColumnSpec::new(
            b"Label3",
            ColumnType::Text {
                max_len: crate::column_definition_writer::nz(8),
            },
        ),
        ColumnSpec::new(b"Account4", ColumnType::Long),
    ];
    const INDEXES: &[IndexSpec<'static>] = &[
        IndexSpec {
            name: b"Primary9",
            fields: &[IndexColumnSpec {
                column: ColumnRef::Name(b"Key1"),
                direction: crate::IndexDirection::Ascending,
            }],
            kind: IndexKind::Primary,
        },
        IndexSpec {
            name: b"Unique8",
            fields: &[IndexColumnSpec {
                column: ColumnRef::Name(b"Code2"),
                direction: crate::IndexDirection::Ascending,
            }],
            kind: IndexKind::Unique,
        },
    ];
    (
        [
            TableSpec {
                name: b"Accounts7",
                columns: PARENT_COLUMNS,
                indexes: &INDEXES[..if two { 2 } else { 1 }],
            },
            TableSpec {
                name: b"Events9",
                columns: CHILD_COLUMNS,
                indexes: &[],
            },
        ],
        RelationshipSpec {
            name: b"Account7Events9",
            parent: RelationshipColumn {
                table: TableRef::Name(b"Accounts7"),
                column: ColumnRef::Name(b"Key1"),
            },
            child: RelationshipColumn {
                table: TableRef::Ordinal(1),
                column: ColumnRef::Ordinal(1),
            },
        },
    )
}

#[test]
fn public_relationship_creation_publishes_both_index_shapes() -> TestResult {
    for two in [false, true] {
        let directory = Directory::new()?;
        let (tables, spec) = schema(two);
        crate::create_database_with_relationship(
            directory.target(),
            &tables,
            &spec,
            &mut budget(),
        )?;
        let pages = compose_relationship(&tables, &spec, &mut budget())?.into_pages();
        check_relationship_candidate(&directory.target(), &tables, &spec, &pages, &mut budget())?;
        assert_eq!(fs::read_dir(&directory.0)?.count(), 1);
    }
    Ok(())
}

#[test]
fn unsupported_references_and_schema_leave_no_destination() -> TestResult {
    let directory = Directory::new()?;
    let (mut tables, mut spec) = schema(false);
    for reference in [TableRef::Ordinal(2), TableRef::Name(b"accounts7")] {
        spec.parent.table = reference;
        assert!(matches!(
            crate::create_database_with_relationship(
                directory.target(),
                &tables,
                &spec,
                &mut budget()
            ),
            Err(CreateDatabaseError::Compose(
                ComposeError::UnsupportedRelationship { .. }
            ))
        ));
    }
    spec.parent.table = TableRef::Ordinal(0);
    tables[1].indexes = tables[0].indexes;
    assert!(
        crate::create_database_with_relationship(directory.target(), &tables, &spec, &mut budget())
            .is_err()
    );
    assert!(directory.empty()?);
    Ok(())
}

#[test]
fn existing_destination_and_exhausted_budget_are_preserved() -> TestResult {
    let directory = Directory::new()?;
    let (tables, spec) = schema(false);
    let mut limited = ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(0));
    assert!(matches!(
        crate::create_database_with_relationship(directory.target(), &tables, &spec, &mut limited),
        Err(CreateDatabaseError::Compose(_))
    ));
    assert!(directory.empty()?);
    let mut composition = budget();
    let plan = compose_relationship(&tables, &spec, &mut composition)?;
    let work_before_check =
        composition.total_work_units() + plan.pages().len() as u64 * crate::PAGE_BYTES as u64;
    let mut check_limited =
        ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(work_before_check));
    assert!(matches!(
        crate::create_database_with_relationship(
            directory.target(),
            &tables,
            &spec,
            &mut check_limited
        ),
        Err(CreateDatabaseError::Publish(_))
    ));
    assert!(directory.empty()?);
    fs::write(directory.target(), b"keep me")?;
    assert!(matches!(
        crate::create_database_with_relationship(directory.target(), &tables, &spec, &mut budget()),
        Err(CreateDatabaseError::Publish(_))
    ));
    assert_eq!(fs::read(directory.target())?, b"keep me");
    assert_eq!(fs::read_dir(&directory.0)?.count(), 1);
    Ok(())
}

#[test]
fn corrupted_written_page_and_wrong_endpoint_fail_publication_check() -> TestResult {
    let directory = Directory::new()?;
    let (tables, mut spec) = schema(false);
    let pages = compose_relationship(&tables, &spec, &mut budget())?.into_pages();
    crate::create_database_with_relationship(directory.target(), &tables, &spec, &mut budget())?;
    spec.child.column = ColumnRef::Ordinal(0);
    assert!(matches!(
        check_relationship_candidate(&directory.target(), &tables, &spec, &pages, &mut budget()),
        Err(CandidateCheckError::Mismatch {
            detail: "relationship endpoint"
        })
    ));
    let mut bytes = fs::read(directory.target())?;
    let last = bytes.last_mut().ok_or("empty file")?;
    *last ^= 1;
    fs::write(directory.target(), bytes)?;
    assert!(matches!(
        check_relationship_candidate(&directory.target(), &tables, &spec, &pages, &mut budget()),
        Err(CandidateCheckError::Mismatch {
            detail: "relationship written page"
        })
    ));
    Ok(())
}

#[path = "relationship_rows_tests.rs"]
mod initial_rows;
