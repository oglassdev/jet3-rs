use super::{relationship::*, tests::inline_map_bit};
use crate::testkit::TestResult;
use crate::testkit::budget;
use crate::testkit::table;
use crate::{
    ColumnOrdinal, ColumnRef, DatabaseReader, IndexColumnSpec, IndexKind, IndexSpec, PAGE_BYTES,
    RelationshipField, RelationshipSpec, ResourceLimits, SliceSource, TableRef,
    create::composer::*, definition::column_writer::nz,
};

const RENAMED_PARENT_COLUMNS: [ColumnSpec<'static>; 2] = [
    ColumnSpec::new(b"Code2", ColumnType::Long),
    ColumnSpec::new(b"Key1", ColumnType::Long),
];
const RENAMED_CHILD_COLUMNS: [ColumnSpec<'static>; 2] = [
    ColumnSpec::new(b"Label3", ColumnType::Text { max_len: nz(8) }),
    ColumnSpec::new(b"Account4", ColumnType::Long),
];

fn image(plan: &WholeFileImagePlan) -> Vec<u8> {
    plan.pages()
        .iter()
        .flat_map(|page| page.image().as_bytes().iter().copied())
        .collect()
}
fn bytes() -> Result<Vec<u8>, ComposeError> {
    Ok(image(&compose_parent_child(&mut budget())?))
}
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
            table(
                parent_name,
                &RENAMED_PARENT_COLUMNS,
                &indexes[..if two { 2 } else { 1 }],
            ),
            table(child_name, &RENAMED_CHILD_COLUMNS, &[]),
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
fn reciprocal_records_and_system_rows_match_the_first_observation() -> TestResult {
    let bytes = bytes()?;
    assert_eq!(bytes.len(), 29 * PAGE_BYTES);
    let mut budget = budget();
    let mut database =
        DatabaseReader::from_source(SliceSource::new(&bytes, budget.read_budget())?, &mut budget)?;
    for (root, name, selector, ordinal, other) in [
        (20, b".rC".as_slice(), 2, 0, 25),
        (25, b"ParentChild".as_slice(), 0, 2, 20),
    ] {
        let definition = database.table_definition(PageNumber::new(root), &mut budget)?;
        let relations = definition.relationships().collect::<Vec<_>>();
        assert_eq!(relations.len(), 1);
        let relation = relations[0];
        assert_eq!(relation.name().raw_bytes(), name);
        assert_eq!(relation.physical_index(), 0);
        assert_eq!(relation.raw_selector(), selector);
        assert_eq!(relation.raw_relation_ordinal(), ordinal);
        assert_eq!(relation.related_table(), PageNumber::new(other));
        assert_eq!(relation.raw_context(), [0, 0]);
        for index in 0..definition.physical_indexes().len() {
            assert!(
                database
                    .index_tree(&definition, index as u16, &mut budget)?
                    .entries()
                    .is_empty()
            );
        }
    }
    let relations = database.table_definition(PageNumber::new(5), &mut budget)?;
    let mut cursor = database.rows(&relations, &mut budget)?;
    let row = cursor.next_row()?.ok_or("missing relationship row")?;
    for (ordinal, value) in [
        (0, b"ParentChild".as_slice()),
        (4, b"Child"),
        (5, b"ParentId"),
        (6, b"Parent"),
        (7, b"Id"),
    ] {
        assert_eq!(
            row.field(ColumnOrdinal::new(ordinal))
                .and_then(|field| field.raw_bytes()),
            Some(value)
        );
    }
    for (ordinal, value) in [(1, 0_i32), (2, 1), (3, 0)] {
        assert_eq!(
            row.field(ColumnOrdinal::new(ordinal))
                .and_then(|field| field.raw_bytes()),
            Some(value.to_le_bytes().as_slice())
        );
    }
    assert!(cursor.next_row()?.is_none());
    Ok(())
}

#[test]
fn relationship_catalog_and_aces_match_recorded_ids_and_permissions() -> TestResult {
    let bytes = bytes()?;
    let mut budget = budget();
    let mut database =
        DatabaseReader::from_source(SliceSource::new(&bytes, budget.read_budget())?, &mut budget)?;
    let objects = database.table_definition(PageNumber::new(2), &mut budget)?;
    for index in 0..2 {
        assert_eq!(
            database
                .index_tree(&objects, index, &mut budget)?
                .entries()
                .len(),
            11
        );
    }
    let mut cursor = database.rows(&objects, &mut budget)?;
    let mut matches = 0;
    while let Some(row) = cursor.next_row()? {
        if row
            .field(ColumnOrdinal::new(2))
            .and_then(|field| field.raw_bytes())
            == Some(b"ParentChild".as_slice())
        {
            matches += 1;
            for (ordinal, wanted) in [
                (0, i32::MIN.to_le_bytes()),
                (1, 0x0f000003_i32.to_le_bytes()),
                (7, 0_i32.to_le_bytes()),
            ] {
                assert_eq!(
                    row.field(ColumnOrdinal::new(ordinal))
                        .and_then(|field| field.raw_bytes()),
                    Some(wanted.as_slice())
                );
            }
            assert_eq!(
                row.field(ColumnOrdinal::new(3))
                    .and_then(|field| field.raw_bytes()),
                Some(8_i16.to_le_bytes().as_slice())
            );
        }
    }
    assert_eq!(matches, 1);
    drop(cursor);
    let aces = database.table_definition(PageNumber::new(3), &mut budget)?;
    assert_eq!(
        database.index_tree(&aces, 0, &mut budget)?.entries().len(),
        22
    );
    let mut cursor = database.rows(&aces, &mut budget)?;
    let mut permissions = Vec::new();
    while let Some(row) = cursor.next_row()? {
        if row
            .field(ColumnOrdinal::new(0))
            .and_then(|field| field.raw_bytes())
            == Some(i32::MIN.to_le_bytes().as_slice())
        {
            permissions.push((
                row.field(ColumnOrdinal::new(1))
                    .and_then(|field| field.raw_bytes())
                    .ok_or("missing SID")?
                    .to_vec(),
                row.field(ColumnOrdinal::new(2))
                    .and_then(|field| field.raw_bytes())
                    .ok_or("missing ACM")?
                    .to_vec(),
            ));
        }
    }
    assert_eq!(
        permissions,
        [
            (vec![3, 1], 983294_i32.to_le_bytes().to_vec()),
            (vec![2, 1], 1048575_i32.to_le_bytes().to_vec())
        ]
    );
    Ok(())
}

#[test]
fn index_locators_maps_and_unrelated_generated_pages_are_preserved() -> TestResult {
    let bytes = bytes()?;
    let base = compose_database(&TABLES, &mut budget())?;
    for page in [4, 6, 7, 8, 10, 14, 21, 22, 23, 24] {
        assert_eq!(
            &bytes[page * PAGE_BYTES..(page + 1) * PAGE_BYTES],
            base.pages()[page].image().as_bytes()
        );
    }
    for (map, row, owned) in [(12, 8, 28), (12, 9, 28), (26, 2, 27)] {
        assert!(inline_map_bit(&bytes, map, row, owned)?);
    }
    for page in 0..29 {
        assert!(!inline_map_bit(&bytes, 1, 0, page)?);
    }
    let mut budget = budget();
    let mut database =
        DatabaseReader::from_source(SliceSource::new(&bytes, budget.read_budget())?, &mut budget)?;
    let definition = database.table_definition(PageNumber::new(5), &mut budget)?;
    for (ordinal, wanted) in [
        b"\x7f\x73\x60\x75\x66\x70\x77\x62\x69\x6a\x6d\x64\x00".as_slice(),
        b"\x7f\x62\x69\x6a\x6d\x64\x00",
        b"\x7f\x73\x60\x75\x66\x70\x77\x00",
    ]
    .into_iter()
    .enumerate()
    {
        let tree = database.index_tree(&definition, ordinal as u16, &mut budget)?;
        assert_eq!(tree.entries().len(), 1);
        assert_eq!(tree.entries()[0].key().raw_bytes(), wanted);
        assert_eq!(tree.entries()[0].row().page(), PageNumber::new(28));
        assert_eq!(tree.entries()[0].row().slot(), 0);
    }
    Ok(())
}

#[test]
fn caller_names_columns_and_both_selector_cases_reopen() -> TestResult {
    for two in [false, true] {
        let (tables, spec) = renamed(two);
        let bytes = image(&compose_relationship(&tables, &spec, &mut budget())?);
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
fn candidate_refuses_unsupported_endpoints_names_and_exhausted_budgets() {
    let (tables, spec) = renamed(false);
    let child = |name| RelationshipField {
        parent: ColumnRef::Name(b"Key1"),
        child: ColumnRef::Name(name),
    };
    for (wrong, detail) in [
        (
            RelationshipSpec {
                parent: TableRef::Ordinal(9),
                ..spec
            },
            None,
        ),
        (
            RelationshipSpec {
                fields: &[child(b"Missing")],
                ..spec
            },
            Some("child column reference"),
        ),
        (
            RelationshipSpec {
                fields: &[child(b"Label3")],
                ..spec
            },
            Some("relationship columns must both be Long"),
        ),
        (
            RelationshipSpec {
                fields: &[RelationshipField {
                    parent: ColumnRef::Ordinal(0),
                    child: ColumnRef::Name(b"Account4"),
                }],
                ..spec
            },
            None,
        ),
    ] {
        let result = compose_relationship(&tables, &wrong, &mut budget());
        assert!(
            matches!(
                result,
                Err(ComposeError::UnsupportedRelationship { detail: actual })
                    if detail.is_none_or(|detail| detail == actual)
            ),
            "{detail:?}"
        );
    }
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
    for name in [b"Link\x81".as_slice(), b""] {
        let named = RelationshipSpec { name, ..spec };
        assert!(matches!(
            compose_relationship(&tables, &named, &mut budget()),
            Err(ComposeError::NameKey(_))
        ));
    }
    assert!(matches!(
        relation_index_name(&[b'A'; 65], RELATION_DATA, &mut budget()),
        Err(ComposeError::NameKey(_))
    ));
    let mut duplicate = tables;
    duplicate[1].name = b"ACCOUNTS7";
    let by_ordinal = RelationshipSpec {
        name: b"Link",
        parent: TableRef::Ordinal(0),
        child: TableRef::Ordinal(1),
        ..spec
    };
    assert!(matches!(
        compose_relationship(&duplicate, &by_ordinal, &mut budget()),
        Err(ComposeError::DuplicateTableName { .. })
    ));
    let mut limited = ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(0));
    assert!(compose_parent_child(&mut limited).is_err());
    let mut limited =
        ResourceBudget::new(ResourceLimits::default().with_max_allocation_bytes(ByteCount::new(0)));
    assert!(compose_relationship(&tables, &spec, &mut limited).is_err());
}

#[test]
fn long_value_child_columns_compose() {
    let (mut tables, spec) = renamed(false);
    let columns = [
        ColumnSpec::new(b"Note", ColumnType::Memo),
        ColumnSpec::new(b"Account4", ColumnType::Long),
    ];
    tables[1].columns = &columns;
    assert!(compose_relationship(&tables, &spec, &mut budget()).is_ok());
}

#[test]
#[ignore = "exports exact private candidate for a separately preregistered DAO run"]
fn export_relationship_candidate() -> TestResult {
    use std::io::Write;
    let path = std::env::var_os("JET3_RELATIONSHIP_CANDIDATE")
        .ok_or("JET3_RELATIONSHIP_CANDIDATE is required")?;
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)?;
    file.write_all(&bytes()?)?;
    Ok(())
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
