use super::api_relationship::*;
use crate::WriteError;
use crate::testkit::create_spec;
use crate::testkit::{index, table};
use crate::{
    ColumnRef, ColumnSpec, ColumnType, IndexColumnSpec, IndexKind, IndexSpec, RelationshipField,
    RelationshipSpec, ResourceBudget, ResourceLimits, TableRef, TableSpec,
    create::{
        check::*,
        composer::{ComposeError, compose_relationship},
    },
};
use std::fs;

pub(super) use crate::testkit::TempDir;
pub(super) use crate::testkit::TestResult;
pub(super) use crate::testkit::budget;
pub(super) fn schema(two: bool) -> ([TableSpec<'static>; 2], RelationshipSpec<'static>) {
    const PARENT_COLUMNS: &[ColumnSpec<'static>] = &[
        ColumnSpec::new(b"Code2", ColumnType::Long),
        ColumnSpec::new(b"Key1", ColumnType::Long),
    ];
    const CHILD_COLUMNS: &[ColumnSpec<'static>] = &[
        ColumnSpec::new(
            b"Label3",
            ColumnType::Text {
                max_len: crate::definition::column_writer::nz(8),
            },
        ),
        ColumnSpec::new(b"Account4", ColumnType::Long),
    ];
    const INDEXES: &[IndexSpec<'static>] = &[
        index(
            b"Primary9",
            &[IndexColumnSpec {
                column: ColumnRef::Name(b"Key1"),
                direction: crate::IndexDirection::Ascending,
            }],
            IndexKind::Primary,
        ),
        index(
            b"Unique8",
            &[IndexColumnSpec {
                column: ColumnRef::Name(b"Code2"),
                direction: crate::IndexDirection::Ascending,
            }],
            IndexKind::Unique,
        ),
    ];
    (
        [
            table(
                b"Accounts7",
                PARENT_COLUMNS,
                &INDEXES[..if two { 2 } else { 1 }],
            ),
            table(b"Events9", CHILD_COLUMNS, &[]),
        ],
        RelationshipSpec {
            unique: false,
            enforce: true,
            join: crate::RelationshipJoin::Inner,
            cascade_updates: false,
            cascade_deletes: false,
            name: b"Account7Events9",
            parent: TableRef::Name(b"Accounts7"),
            child: TableRef::Ordinal(1),
            fields: &[RelationshipField {
                parent: ColumnRef::Name(b"Key1"),
                child: ColumnRef::Ordinal(1),
            }],
        },
    )
}

#[test]
fn public_relationship_creation_publishes_both_index_shapes() -> TestResult {
    for two in [false, true] {
        let directory = TempDir::new("create")?;
        let (tables, spec) = schema(two);
        create_spec(
            directory.target(),
            &crate::DatabaseSpec {
                tables: &tables.map(crate::TableRows::empty),
                relationships: std::slice::from_ref(&spec),
                relationship_layout: crate::RelationshipLayout::SingleLong,
            },
        )?;
        let pages = compose_relationship(&tables, &spec, &mut budget())?.into_pages();
        check_relationship_image(&directory.target(), &tables, &spec, &pages, &mut budget())?;
        assert_eq!(fs::read_dir(&*directory)?.count(), 1);
    }
    Ok(())
}

#[test]
fn unsupported_references_and_schema_leave_no_destination() -> TestResult {
    let directory = TempDir::new("create")?;
    let (mut tables, mut spec) = schema(false);
    for reference in [TableRef::Ordinal(2), TableRef::Name(b"accounts7")] {
        spec.parent = reference;
        assert!(matches!(
            create_spec(
                directory.target(),
                &crate::DatabaseSpec {
                    tables: &tables.map(crate::TableRows::empty),
                    relationships: std::slice::from_ref(&spec),
                    relationship_layout: crate::RelationshipLayout::SingleLong
                }
            ),
            Err(WriteError::Compose(
                ComposeError::UnsupportedRelationship { .. }
            ))
        ));
    }
    spec.parent = TableRef::Ordinal(0);
    spec.unique = true;
    assert!(
        create_spec(
            directory.target(),
            &crate::DatabaseSpec {
                tables: &tables.map(crate::TableRows::empty),
                relationships: std::slice::from_ref(&spec),
                relationship_layout: crate::RelationshipLayout::SingleLong
            }
        )
        .is_err()
    );
    spec.unique = false;
    tables[1].indexes = tables[0].indexes;
    assert!(
        create_spec(
            directory.target(),
            &crate::DatabaseSpec {
                tables: &tables.map(crate::TableRows::empty),
                relationships: std::slice::from_ref(&spec),
                relationship_layout: crate::RelationshipLayout::SingleLong
            }
        )
        .is_err()
    );
    assert!(directory.is_empty()?);
    Ok(())
}

#[test]
fn existing_destination_and_exhausted_budget_are_preserved() -> TestResult {
    let directory = TempDir::new("create")?;
    let (tables, spec) = schema(false);
    let mut limited = ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(0));
    assert!(matches!(
        crate::create_database(
            directory.target(),
            &crate::DatabaseSpec {
                tables: &tables.map(crate::TableRows::empty),
                relationships: std::slice::from_ref(&spec),
                relationship_layout: crate::RelationshipLayout::SingleLong
            },
            &mut limited
        ),
        Err(WriteError::Compose(_))
    ));
    assert!(directory.is_empty()?);
    let mut composition = budget();
    let plan = compose_relationship(&tables, &spec, &mut composition)?;
    let work_before_check =
        composition.total_work_units() + plan.pages().len() as u64 * crate::PAGE_BYTES as u64;
    let mut check_limited =
        ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(work_before_check));
    assert!(matches!(
        crate::create_database(
            directory.target(),
            &crate::DatabaseSpec {
                tables: &tables.map(crate::TableRows::empty),
                relationships: std::slice::from_ref(&spec),
                relationship_layout: crate::RelationshipLayout::SingleLong
            },
            &mut check_limited
        ),
        Err(WriteError::CreatePublish(_))
    ));
    assert!(directory.is_empty()?);
    fs::write(directory.target(), b"keep me")?;
    assert!(matches!(
        create_spec(
            directory.target(),
            &crate::DatabaseSpec {
                tables: &tables.map(crate::TableRows::empty),
                relationships: std::slice::from_ref(&spec),
                relationship_layout: crate::RelationshipLayout::SingleLong
            }
        ),
        Err(WriteError::CreatePublish(_))
    ));
    assert_eq!(fs::read(directory.target())?, b"keep me");
    assert_eq!(fs::read_dir(&*directory)?.count(), 1);
    Ok(())
}

#[test]
fn corrupted_written_page_and_wrong_endpoint_fail_publication_check() -> TestResult {
    let directory = TempDir::new("create")?;
    let (tables, mut spec) = schema(false);
    let pages = compose_relationship(&tables, &spec, &mut budget())?.into_pages();
    create_spec(
        directory.target(),
        &crate::DatabaseSpec {
            tables: &tables.map(crate::TableRows::empty),
            relationships: std::slice::from_ref(&spec),
            relationship_layout: crate::RelationshipLayout::SingleLong,
        },
    )?;
    spec.fields = &[RelationshipField {
        parent: ColumnRef::Ordinal(0),
        child: ColumnRef::Ordinal(0),
    }];
    assert!(matches!(
        check_relationship_image(&directory.target(), &tables, &spec, &pages, &mut budget()),
        Err(ImageCheckError::Mismatch {
            detail: "relationship endpoint"
        })
    ));
    let mut bytes = fs::read(directory.target())?;
    let last = bytes.last_mut().ok_or("empty file")?;
    *last ^= 1;
    fs::write(directory.target(), bytes)?;
    assert!(matches!(
        check_relationship_image(&directory.target(), &tables, &spec, &pages, &mut budget()),
        Err(ImageCheckError::Mismatch {
            detail: "relationship written page"
        })
    ));
    Ok(())
}
