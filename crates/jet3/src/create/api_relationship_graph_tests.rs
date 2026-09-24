use super::api_relationship_graph::*;
use crate::WriteError;
use crate::testkit::{create, create_spec, validate_file};
use crate::testkit::{index, table};
use crate::{
    ColumnOrdinal, ColumnRef, ColumnSpec, ColumnType, DatabaseReader, IndexColumnSpec,
    IndexDirection, IndexKind, IndexSpec, PageNumber, RelationshipField, RelationshipSpec,
    ResourceBudget, ResourceLimits, RowLocator, RowValue, TableDefinition, TableRef, TableSpec,
    create::{
        api::*,
        check::*,
        composer::{ComposeError, GraphImage, compose_relationship_graph},
    },
};
use std::collections::BTreeSet;
use std::fs;
use std::path::Path;

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
pub(super) fn spec<'a>(
    tables: &'a [TableRows<'a>],
    relationships: &'a [RelationshipSpec<'a>],
) -> DatabaseSpec<'a> {
    DatabaseSpec {
        tables,
        relationships,
        ..DatabaseSpec::default()
    }
}
/// A `COLUMNS` row with a null `Body`.
pub(super) fn values(id: i32, one: Option<i32>, two: Option<i32>) -> [RowValue<'static>; 4] {
    let long = |value: Option<i32>| value.map_or(RowValue::Null, RowValue::Long);
    [RowValue::Long(id), long(one), long(two), RowValue::Null]
}
pub(super) fn definition(path: &Path, name: &[u8]) -> TestResult<TableDefinition> {
    let mut work = budget();
    let mut db = DatabaseReader::open(path, &mut work)?;
    Ok(crate::write::update::indexed_writable_table(
        &mut db, name, &mut work,
    )?)
}
/// The row whose first column is `Long(id)`.
pub(super) fn locate(path: &Path, table: &[u8], id: i32) -> TestResult<RowLocator> {
    let definition = definition(path, table)?;
    let mut work = budget();
    let mut db = DatabaseReader::open(path, &mut work)?;
    let mut rows = db.rows(&definition, &mut work)?;
    while let Some(mut row) = rows.next_row()? {
        if crate::row::scalar_values::read_column(&mut row, ColumnOrdinal::new(0))?
            == RowValue::Long(id)
        {
            return Ok(row.locator());
        }
    }
    Err("missing row".into())
}
pub(super) fn verified(path: &Path) -> TestResult<u64> {
    Ok(validate_file(path)?.relationships_with_verified_keys)
}

#[test]
fn graph_creation_handles_multiple_shared_chain_and_self_endpoints() -> TestResult {
    let payload = [b'x'; 4096];
    let mut first = values(1, None, None);
    first[3] = RowValue::Memo(&payload);
    let mut second = values(2, Some(1), Some(1));
    second[3] = RowValue::Memo(b"a");
    let third = values(3, Some(2), Some(2));
    let rows: &[&[RowValue<'_>]] = &[&first, &second, &third];
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
                let name: &[u8] = if i == 0 { b"RelationA" } else { b"RelationB" };
                relation(name, parent, child, column)
            })
            .collect();
        for populated in [false, true] {
            let directory = TempDir::new("create")?;
            let requests = TABLES.map(|table| TableRows {
                table,
                rows: if populated { rows } else { &[] },
            });
            create_spec(directory.target(), &spec(&requests, &relationships))?;
            assert_eq!(validate_file(directory.target())?.user_tables, 3);
            assert_eq!(directory.entries()?.len(), 1);
        }
    }
    Ok(())
}

#[test]
fn graph_creation_resolves_generated_parent_keys_and_refuses_before_publication() -> TestResult {
    let directory = TempDir::new("create")?;
    let mut columns = COLUMNS.to_vec();
    columns[0] = ColumnSpec::new(b"Id", ColumnType::AutoIncrement);
    let parent = TableSpec {
        columns: &columns,
        ..TABLES[0]
    };
    let mut generated = values(0, None, None);
    generated[0] = RowValue::AutoIncrement;
    let parent_rows: &[&[RowValue<'_>]] = &[&generated, &generated];
    let (first, second) = (values(10, Some(1), None), values(11, Some(2), None));
    let requests = [
        TableRows {
            table: parent,
            rows: parent_rows,
        },
        TableRows {
            table: TABLES[1],
            rows: &[&first, &second],
        },
    ];
    let edge = [relation(b"A", 0, 1, 1)];
    create_spec(directory.target(), &spec(&requests, &edge))?;

    let refused = directory.join("refused.mdb");
    let orphan = values(12, Some(3), None);
    let bad = [
        requests[0],
        TableRows {
            table: TABLES[1],
            rows: &[&orphan],
        },
    ];
    assert!(matches!(
        create_spec(&refused, &spec(&bad, &edge)),
        Err(WriteError::Compose(
            ComposeError::OrphanInitialRelationshipKey { row: 0, value: 3 }
        ))
    ));
    let self_orphan = values(1, Some(2), None);
    let requests = [TableRows {
        table: TABLES[0],
        rows: &[&self_orphan],
    }];
    assert!(matches!(
        create_spec(&refused, &spec(&requests, &[relation(b"Self", 0, 0, 1)])),
        Err(WriteError::Compose(
            ComposeError::OrphanInitialRelationshipKey { row: 0, value: 2 }
        ))
    ));
    let empty = TABLES.map(TableRows::empty);
    let duplicates = [relation(b"Same", 0, 1, 1), relation(b"same", 0, 1, 2)];
    assert!(matches!(
        create_spec(&refused, &spec(&empty, &duplicates)),
        Err(WriteError::Compose(
            ComposeError::UnsupportedRelationship { .. }
        ))
    ));
    let mut limited = ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(0));
    assert!(create_database(&refused, &spec(&empty, &edge), &mut limited).is_err());
    assert_eq!(directory.entries()?.len(), 1);
    Ok(())
}

#[test]
fn graph_creation_preserves_destination_and_empty_graph_matches_normal_creation() -> TestResult {
    let directory = TempDir::new("create")?;
    let other = directory.join("normal.mdb");
    let empty = TABLES.map(TableRows::empty);
    create_spec(directory.target(), &spec(&empty, &[]))?;
    create(&other, &empty)?;
    let original = fs::read(directory.target())?;
    assert_eq!(original, fs::read(other)?);
    assert!(matches!(
        create_spec(
            directory.target(),
            &spec(&empty, &[relation(b"A", 0, 1, 1)])
        ),
        Err(WriteError::CreatePublish(_))
    ));
    assert_eq!(fs::read(directory.target())?, original);
    Ok(())
}

#[test]
fn graph_candidate_check_rejects_mismatches_and_uninterpreted_metadata() -> TestResult {
    let directory = TempDir::new("create")?;
    let relationships = [relation(b"A", 0, 1, 1)];
    let requests = TABLES.map(TableRows::empty);
    let GraphImage { image, tables } =
        compose_relationship_graph(&requests, &relationships, &mut budget())?;
    let mut pages = image.into_pages();
    create_spec(directory.target(), &spec(&requests, &relationships))?;
    let check = |requests: &[TableRows<'_>], edges: &[RelationshipSpec<'_>], pages: &[_]| {
        check_graph(
            &directory.target(),
            requests,
            edges,
            &tables,
            pages,
            &mut budget(),
        )
    };
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
            check(&wrong, &relationships, &pages),
            Err(ImageCheckError::Mismatch { .. })
        ));
    }
    assert!(matches!(
        check(&requests, &[relation(b"A", 0, 1, 2)], &pages),
        Err(ImageCheckError::Mismatch { .. })
    ));

    // Unknown central flags leave the written relationship uninterpreted.
    let mut work = budget();
    let mut db = DatabaseReader::open(directory.target(), &mut work)?;
    let central = db.table_definition(PageNumber::new(5), &mut work)?;
    let (locator, field_offset) = {
        let mut rows = db.rows(&central, &mut work)?;
        let row = rows.next_row()?.ok_or("relationship row")?;
        let flags = row.present_fixed_field_range(ColumnOrdinal::new(1));
        (row.storage_locator(), flags.ok_or("flags")?.start)
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
    write_pages(&mut fs::File::create(directory.target())?, &pages)?;
    let report = validate_file(directory.target())?;
    assert_eq!(report.uninterpreted_relationship_rows, 1);
    assert!(!report.relationship_inventory_checked);
    assert!(matches!(
        check(&requests, &relationships, &pages),
        Err(ImageCheckError::Mismatch {
            detail: "relationship graph complete validation"
        })
    ));
    Ok(())
}

#[test]
fn relationship_catalog_spans_pages_and_index_branches_with_complete_locators() -> TestResult {
    let directory = TempDir::new("create")?;
    let names = (0..33).map(|n| format!("T{n:02}")).collect::<Vec<_>>();
    let relation_names = (0..32)
        .map(|n| format!("R{n:02}{}", "x".repeat(60)))
        .collect::<Vec<_>>();
    let tables = names
        .iter()
        .map(|name| {
            TableRows::empty(TableSpec {
                name: name.as_bytes(),
                ..TABLES[0]
            })
        })
        .collect::<Vec<_>>();
    let relationships = relation_names
        .iter()
        .enumerate()
        .map(|(n, name)| RelationshipSpec {
            name: name.as_bytes(),
            ..relation(b"", n, n + 1, 1)
        })
        .collect::<Vec<_>>();
    let mut work = budget();
    create_database(
        directory.target(),
        &spec(&tables, &relationships),
        &mut work,
    )?;
    assert_eq!(verified(&directory.target())?, 32);
    let mut database = DatabaseReader::open(directory.target(), &mut budget())?;
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
    let refused = directory.join("limited.mdb");
    let mut limited = ResourceBudget::new(
        ResourceLimits::default().with_max_total_work_units(work.total_work_units() - 1),
    );
    assert!(create_database(&refused, &spec(&tables, &relationships), &mut limited).is_err());
    assert_eq!(directory.entries()?.len(), 1);
    Ok(())
}

#[test]
fn relationship_capacity_is_per_table_and_counts_both_self_sides() -> TestResult {
    let names = (0..33).map(|n| format!("T{n:02}")).collect::<Vec<_>>();
    let relation_names = (0..32).map(|n| format!("R{n:02}")).collect::<Vec<_>>();
    let tables = names
        .iter()
        .map(|name| {
            TableRows::empty(TableSpec {
                name: name.as_bytes(),
                ..TABLES[0]
            })
        })
        .collect::<Vec<_>>();
    for self_references in [false, true] {
        let relationships = relation_names
            .iter()
            .enumerate()
            .map(|(n, name)| RelationshipSpec {
                name: name.as_bytes(),
                ..relation(b"", 0, if self_references { 0 } else { n + 1 }, 1)
            })
            .collect::<Vec<_>>();
        let accepted = if self_references { 15 } else { 31 };
        for count in [accepted, accepted + 1] {
            let directory = TempDir::new("create")?;
            let result = create_spec(directory.target(), &spec(&tables, &relationships[..count]));
            if count == accepted {
                result?;
                let parent = definition(&directory.target(), b"T00")?;
                assert_eq!(
                    parent.indexes().len(),
                    if self_references { 31 } else { 32 }
                );
                assert_eq!(verified(&directory.target())?, count as u64);
            } else {
                assert!(matches!(
                    result,
                    Err(WriteError::Compose(ComposeError::Schema(
                        crate::TableSchemaPlanError::UnobservedIndexCount {
                            count: 33,
                            observed: 32
                        }
                    )))
                ));
                assert!(directory.is_empty()?);
            }
        }
    }
    Ok(())
}

#[test]
fn third_shared_parent_constraint_is_enforced_on_mutation() -> TestResult {
    let directory = TempDir::new("create")?;
    let (first, second) = (values(1, None, None), values(2, None, None));
    let parent_rows: &[&[RowValue<'_>]] = &[&first, &second];
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
        TableRows::empty(child),
    ];
    let relationships = [
        relation(b"First", 0, 3, 1),
        relation(b"Second", 1, 3, 1),
        relation(b"Third", 2, 3, 1),
    ];
    create_spec(directory.target(), &spec(&requests, &relationships))?;
    let insert = |id, key| {
        crate::insert_row(
            directory.target(),
            child.name,
            &values(id, Some(key), None),
            &mut budget(),
        )
    };
    insert(10, 1)?;
    let before = fs::read(directory.target())?;
    assert!(matches!(
        insert(11, 2),
        Err(WriteError::RelationshipConstraint { value: 2, .. })
    ));
    assert_eq!(fs::read(directory.target())?, before);
    assert_eq!(verified(&directory.target())?, 3);
    Ok(())
}
