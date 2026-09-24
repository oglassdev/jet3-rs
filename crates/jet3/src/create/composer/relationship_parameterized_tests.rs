use super::relationship::*;
use crate::{
    ColumnRef, DatabaseReader, IndexColumnSpec, IndexKind, IndexSpec, PAGE_BYTES,
    RelationshipField, RelationshipSpec, ResourceLimits, SliceSource, TableRef,
    create::composer::*, definition::column_writer::nz,
};

type TestResult = Result<(), Box<dyn std::error::Error>>;
use crate::testkit::budget;

const RENAMED_PARENT_COLUMNS: [ColumnSpec<'static>; 2] = [
    ColumnSpec::new(b"Code2", ColumnType::Long),
    ColumnSpec::new(b"Key1", ColumnType::Long),
];
const RENAMED_CHILD_COLUMNS: [ColumnSpec<'static>; 2] = [
    ColumnSpec::new(b"Label3", ColumnType::Text { max_len: nz(8) }),
    ColumnSpec::new(b"Account4", ColumnType::Long),
];

fn renamed(two: bool) -> ([TableSpec<'static>; 2], RelationshipSpec<'static>) {
    let parent_name: &[u8] = if two { b"Owners2" } else { b"Accounts7" };
    let child_name: &[u8] = if two { b"Details4" } else { b"Events9" };
    let indexes: &[IndexSpec<'static>] = &[
        IndexSpec {
            name: b"Primary9",
            fields: &[IndexColumnSpec {
                column: ColumnRef::Ordinal(1),
                direction: IndexDirection::Ascending,
            }],
            kind: IndexKind::Primary,
        },
        IndexSpec {
            name: b"Unique8",
            fields: &[IndexColumnSpec {
                column: ColumnRef::Ordinal(0),
                direction: IndexDirection::Ascending,
            }],
            kind: IndexKind::Unique,
        },
    ];
    (
        [
            TableSpec {
                validation: crate::TableValidation::NONE,
                name: parent_name,
                columns: &RENAMED_PARENT_COLUMNS,
                indexes: &indexes[..if two { 2 } else { 1 }],
            },
            TableSpec {
                validation: crate::TableValidation::NONE,
                name: child_name,
                columns: &RENAMED_CHILD_COLUMNS,
                indexes: &[],
            },
        ],
        RelationshipSpec {
            unique: false,
            enforce: true,
            join: crate::RelationshipJoin::Inner,
            cascade_updates: false,
            cascade_deletes: false,
            name: if two {
                b"Owner2_Details4"
            } else {
                b"Account7Events9"
            },
            parent: TableRef::Name(parent_name),
            child: TableRef::Name(child_name),
            fields: &[RelationshipField {
                parent: ColumnRef::Name(b"Key1"),
                child: ColumnRef::Name(b"Account4"),
            }],
        },
    )
}

#[test]
fn caller_names_columns_and_both_selector_cases_reopen() -> TestResult {
    for two in [false, true] {
        let (tables, spec) = renamed(two);
        let plan = compose_relationship(&tables, &spec, &mut budget())?;
        let bytes = plan
            .pages()
            .iter()
            .flat_map(|page| page.image().as_bytes().iter().copied())
            .collect::<Vec<_>>();
        assert_eq!(bytes.len(), (if two { 30 } else { 29 }) * PAGE_BYTES);
        let mut budget = budget();
        let mut database = DatabaseReader::from_source(
            SliceSource::new(&bytes, budget.read_budget())?,
            &mut budget,
        )?;
        let parent = database.table_definition(PageNumber::new(20), &mut budget)?;
        let relation = parent
            .relationships()
            .next()
            .ok_or("missing parent relationship")?;
        assert_eq!(
            relation.name().raw_bytes(),
            if two { b".rC" } else { b".rB" }
        );
        assert_eq!(relation.raw_selector(), if two { 2 } else { 1 });
        assert_eq!(parent.physical_indexes()[0].fields()[0].column().get(), 1);
        let child = database.table_definition(relation.related_table(), &mut budget)?;
        assert_eq!(
            child
                .relationships()
                .next()
                .ok_or("missing child relationship")?
                .name()
                .raw_bytes(),
            spec.name
        );
        assert_eq!(child.physical_indexes()[0].fields()[0].column().get(), 1);
    }
    Ok(())
}

#[test]
fn missing_references_wrong_types_and_unsupported_indexes_are_refused() {
    let (tables, spec) = renamed(false);
    let mut wrong = spec;
    wrong.parent = TableRef::Ordinal(9);
    assert!(matches!(
        compose_relationship(&tables, &wrong, &mut budget()),
        Err(ComposeError::UnsupportedRelationship { .. })
    ));
    wrong = spec;
    wrong.fields = &[RelationshipField {
        parent: ColumnRef::Name(b"Key1"),
        child: ColumnRef::Name(b"Missing"),
    }];
    assert!(matches!(
        compose_relationship(&tables, &wrong, &mut budget()),
        Err(ComposeError::UnsupportedRelationship {
            detail: "child column reference"
        })
    ));
    wrong.fields = &[RelationshipField {
        parent: ColumnRef::Name(b"Key1"),
        child: ColumnRef::Ordinal(0),
    }];
    assert!(matches!(
        compose_relationship(&tables, &wrong, &mut budget()),
        Err(ComposeError::UnsupportedRelationship {
            detail: "relationship columns must both be Long"
        })
    ));
    wrong = spec;
    wrong.fields = &[RelationshipField {
        parent: ColumnRef::Ordinal(0),
        child: ColumnRef::Name(b"Account4"),
    }];
    assert!(matches!(
        compose_relationship(&tables, &wrong, &mut budget()),
        Err(ComposeError::UnsupportedRelationship { .. })
    ));
    let mut indexed_child = tables;
    indexed_child[1].indexes = &[IndexSpec {
        name: b"Extra",
        fields: &[IndexColumnSpec {
            column: ColumnRef::Ordinal(1),
            direction: IndexDirection::Ascending,
        }],
        kind: IndexKind::Ordinary,
    }];
    assert!(matches!(
        compose_relationship(&indexed_child, &spec, &mut budget()),
        Err(ComposeError::UnsupportedRelationship {
            detail: "child admits one separate ascending Long primary index"
        })
    ));
}

#[test]
fn name_collisions_and_unsupported_name_bytes_are_refused() {
    let (mut tables, mut spec) = renamed(false);
    spec.name = b"Link\x81";
    assert!(matches!(
        compose_relationship(&tables, &spec, &mut budget()),
        Err(ComposeError::NameKey(_))
    ));
    spec.name = b"";
    assert!(matches!(
        compose_relationship(&tables, &spec, &mut budget()),
        Err(ComposeError::NameKey(_))
    ));
    spec.name = b"Link";
    tables[1].name = b"ACCOUNTS7";
    spec.parent = TableRef::Ordinal(0);
    spec.child = TableRef::Ordinal(1);
    assert!(matches!(
        compose_relationship(&tables, &spec, &mut budget()),
        Err(ComposeError::DuplicateTableName { .. })
    ));
}

#[test]
fn long_value_columns_compose_and_allocation_exhaustion_is_refused() {
    let (mut tables, spec) = renamed(false);
    let mut limited =
        ResourceBudget::new(ResourceLimits::default().with_max_allocation_bytes(ByteCount::new(0)));
    assert!(compose_relationship(&tables, &spec, &mut limited).is_err());
    let columns = [
        ColumnSpec::new(b"Note", ColumnType::Memo),
        ColumnSpec::new(b"Account4", ColumnType::Long),
    ];
    tables[1].columns = &columns;
    assert!(compose_relationship(&tables, &spec, &mut budget()).is_ok());
}

#[test]
#[ignore = "exports two renamed candidates for separately preregistered validation"]
fn export_parameterized_relationship_candidates() -> TestResult {
    use std::io::Write;
    let root = std::path::PathBuf::from(
        std::env::var_os("JET3_RELATIONSHIP_CANDIDATE_DIR")
            .ok_or("JET3_RELATIONSHIP_CANDIDATE_DIR required")?,
    );
    for (two, name) in [
        (false, "relationship-one-index.mdb"),
        (true, "relationship-two-index.mdb"),
    ] {
        let (tables, spec) = renamed(two);
        let plan = compose_relationship(&tables, &spec, &mut budget())?;
        let mut file = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(root.join(name))?;
        for page in plan.pages() {
            file.write_all(page.image().as_bytes())?;
        }
    }
    Ok(())
}
