use super::api_relationship_graph_tests::*;
use crate::{
    DatabaseReader, PageNumber, RelationshipSpec, ResourceBudget, ResourceLimits, RowValue,
    TableSpec, TextCodePage,
    create::{
        api::*,
        api_relationship_graph::{
            create_database_with_relationships, create_database_with_relationships_and_rows, *,
        },
        composer::{ComposeError, GraphImage, compose_relationship_graph},
    },
};
use std::collections::BTreeSet;
use std::fs;

#[test]
fn graph_candidate_rejects_uninterpreted_generated_relationship_metadata() -> TestResult {
    let directory = Directory::new()?;
    let relationships = [relation(b"Link", 0, 1, 1)];
    let requests = TABLES.map(|table| TableRows { table, rows: &[] });
    create_database_with_relationships(directory.target(), &TABLES, &relationships, &mut budget())?;
    let GraphImage { image, tables } =
        compose_relationship_graph(&requests, &relationships, &mut budget())?;
    let mut pages = image.into_pages();
    let mut work = budget();
    let mut db = DatabaseReader::open(directory.target(), &mut work)?;
    let central = db.table_definition(PageNumber::new(5), &mut work)?;
    let (locator, field_offset) = {
        let mut rows = db.rows(&central, &mut work)?;
        let row = rows.next_row()?.ok_or("relationship row")?;
        (
            row.storage_locator(),
            row.present_fixed_field_range(crate::ColumnOrdinal::new(1))
                .ok_or("relationship flags")?
                .start,
        )
    };
    let mut bytes = [0; crate::PAGE_BYTES];
    db.read_raw_page(locator.page(), &mut bytes, &mut work)?;
    drop(db);
    let directory_view = crate::row::directory::RowDirectory::validate(
        locator.page(),
        central.root(),
        &bytes,
        &mut work,
    )?;
    let offset = directory_view.entry(&bytes, locator.slot())?.range().start + field_offset;
    bytes[offset..offset + 4].copy_from_slice(&0x4000_i32.to_le_bytes());
    pages
        .iter_mut()
        .find(|page| page.number() == locator.page())
        .ok_or("planned relationship page")?
        .replace_image(crate::PageImage::from_bytes(bytes));
    let mut output = fs::File::create(directory.target())?;
    write_pages(&mut output, &pages)?;
    drop(output);
    let mut db = DatabaseReader::open(directory.target(), &mut budget())?;
    let report = db.validate(TextCodePage::Windows1252, &mut budget())?;
    assert_eq!(report.uninterpreted_relationship_rows, 1);
    assert!(!report.relationship_inventory_checked);
    assert!(matches!(
        check_graph(
            &directory.target(),
            &requests,
            &relationships,
            &tables,
            &pages,
            &mut budget()
        ),
        Err(CandidateCheckError::Mismatch {
            detail: "relationship graph complete validation"
        })
    ));
    Ok(())
}

#[test]
fn relationship_catalog_spans_pages_and_index_branches_with_complete_locators() -> TestResult {
    let directory = Directory::new()?;
    let names = (0..33).map(|n| format!("T{n:02}")).collect::<Vec<_>>();
    let relation_names = (0..32)
        .map(|n| format!("R{n:02}{}", "x".repeat(60)))
        .collect::<Vec<_>>();
    let tables = names
        .iter()
        .map(|name| TableSpec {
            name: name.as_bytes(),
            ..TABLES[0]
        })
        .collect::<Vec<_>>();
    let relationships = relation_names
        .iter()
        .enumerate()
        .map(|(n, name)| RelationshipSpec {
            cascade_updates: false,
            cascade_deletes: false,
            name: name.as_bytes(),
            ..relation(b"", n, n + 1, 1)
        })
        .collect::<Vec<_>>();
    let mut work = budget();
    create_database_with_relationships(directory.target(), &tables, &relationships, &mut work)?;
    let mut database = DatabaseReader::open(directory.target(), &mut budget())?;
    let report = database.validate(TextCodePage::Windows1252, &mut budget())?;
    assert_eq!(report.relationships_with_verified_keys, 32);
    let definition = database.table_definition(PageNumber::new(5), &mut budget())?;
    assert_eq!(definition.row_count(), 32);
    let mut locators = BTreeSet::new();
    {
        let mut row_budget = budget();
        let mut rows = database.rows(&definition, &mut row_budget)?;
        while let Some(row) = rows.next_row()? {
            locators.insert((row.locator().page().get(), row.locator().slot()));
        }
    }
    assert_eq!(locators.len(), 32);
    assert!(
        locators
            .iter()
            .map(|row| row.0)
            .collect::<BTreeSet<_>>()
            .len()
            > 1
    );
    for ordinal in 0..3 {
        let tree = database.index_tree(&definition, ordinal, &mut budget())?;
        assert_eq!(
            tree.entries()
                .iter()
                .map(|entry| (entry.row().page().get(), entry.row().slot()))
                .collect::<BTreeSet<_>>(),
            locators
        );
        assert_eq!(
            definition.physical_indexes()[ordinal as usize].distinct_key_count(),
            32
        );
        if ordinal == 0 {
            assert!(tree.nodes().len() > 1);
        }
    }
    let refused = directory.0.join("limited.mdb");
    let mut limited = ResourceBudget::new(
        ResourceLimits::default().with_max_total_work_units(work.total_work_units() - 1),
    );
    assert!(
        create_database_with_relationships(&refused, &tables, &relationships, &mut limited)
            .is_err()
    );
    assert!(!refused.exists());
    assert_eq!(fs::read_dir(&directory.0)?.count(), 1);
    Ok(())
}

#[test]
fn relationship_capacity_is_per_table_and_counts_both_self_sides() -> TestResult {
    let names = (0..33).map(|n| format!("T{n:02}")).collect::<Vec<_>>();
    let relation_names = (0..32).map(|n| format!("R{n:02}")).collect::<Vec<_>>();
    let tables = names
        .iter()
        .map(|name| TableSpec {
            name: name.as_bytes(),
            ..TABLES[0]
        })
        .collect::<Vec<_>>();
    for self_references in [false, true] {
        let relationships = relation_names
            .iter()
            .enumerate()
            .map(|(n, name)| RelationshipSpec {
                cascade_updates: false,
                cascade_deletes: false,
                name: name.as_bytes(),
                ..relation(b"", 0, if self_references { 0 } else { n + 1 }, 1)
            })
            .collect::<Vec<_>>();
        let accepted = if self_references { 15 } else { 31 };
        for count in [accepted, accepted + 1] {
            let directory = Directory::new()?;
            let result = create_database_with_relationships(
                directory.target(),
                &tables,
                &relationships[..count],
                &mut budget(),
            );
            if count == accepted {
                result?;
                let mut work = budget();
                let mut db = DatabaseReader::open(directory.target(), &mut work)?;
                let parent = crate::write::update::indexed_writable_table(
                    &mut db,
                    tables[0].name,
                    &mut work,
                )?;
                assert_eq!(
                    parent.indexes().len(),
                    if self_references { 31 } else { 32 }
                );
                assert_eq!(
                    db.validate(TextCodePage::Windows1252, &mut work)?
                        .relationships_with_verified_keys,
                    count as u64
                );
            } else {
                assert!(matches!(
                    result,
                    Err(CreateDatabaseError::Compose(ComposeError::Schema(
                        crate::TableSchemaPlanError::UnobservedIndexCount {
                            count: 33,
                            observed: 32
                        }
                    )))
                ));
                assert!(!directory.target().exists());
            }
        }
    }
    Ok(())
}

#[test]
fn third_shared_parent_constraint_is_enforced_on_mutation() -> TestResult {
    let directory = Directory::new()?;
    let parent_rows: &[&[RowValue<'_>]] = &[
        &[
            RowValue::Long(1),
            RowValue::Null,
            RowValue::Null,
            RowValue::Null,
        ],
        &[
            RowValue::Long(2),
            RowValue::Null,
            RowValue::Null,
            RowValue::Null,
        ],
    ];
    let child = TableSpec {
        name: b"Child",
        ..TABLES[0]
    };
    let requests = [
        TableRows {
            table: TABLES[0],
            rows: parent_rows,
        },
        TableRows {
            table: TABLES[1],
            rows: parent_rows,
        },
        TableRows {
            table: TABLES[2],
            rows: &parent_rows[..1],
        },
        TableRows {
            table: child,
            rows: &[],
        },
    ];
    let relationships = [
        relation(b"First", 0, 3, 1),
        relation(b"Second", 1, 3, 1),
        relation(b"Third", 2, 3, 1),
    ];
    create_database_with_relationships_and_rows(
        directory.target(),
        &requests,
        &relationships,
        &mut budget(),
    )?;
    crate::insert_row(
        directory.target(),
        child.name,
        &[
            RowValue::Long(10),
            RowValue::Long(1),
            RowValue::Null,
            RowValue::Null,
        ],
        &mut budget(),
    )?;
    let before = fs::read(directory.target())?;
    assert!(matches!(
        crate::insert_row(
            directory.target(),
            child.name,
            &[
                RowValue::Long(11),
                RowValue::Long(2),
                RowValue::Null,
                RowValue::Null
            ],
            &mut budget()
        ),
        Err(crate::UpdateError::RelationshipConstraint { value: 2, .. })
    ));
    assert_eq!(fs::read(directory.target())?, before);
    let mut work = budget();
    let mut db = DatabaseReader::open(directory.target(), &mut work)?;
    assert_eq!(
        db.validate(TextCodePage::Windows1252, &mut work)?
            .relationships_with_verified_keys,
        3
    );
    Ok(())
}
