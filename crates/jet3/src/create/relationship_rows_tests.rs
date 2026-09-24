use super::api_relationship_tests::*;
use crate::WriteError;
use crate::testkit::create_spec;
use crate::{
    ColumnSpec, ColumnType, DatabaseReader, PageNumber, ResourceBudget, ResourceLimits, RowLocator,
    RowValue, TableRows,
    create::{
        api_relationship::*,
        composer::{ComposeError, compose_relationship_with_rows},
    },
    create_database,
};
use std::fs;

type Rows<'a> = &'a [&'a [RowValue<'a>]];
const PARENT_ROWS: Rows<'static> = &[
    &[RowValue::Long(9), RowValue::Long(1)],
    &[RowValue::Long(8), RowValue::Long(2)],
    &[RowValue::Long(7), RowValue::Long(3)],
];

fn map_bit(bytes: &[u8], page: u64, row: u8, target: u64) -> TestResult<bool> {
    let start = page as usize * crate::PAGE_BYTES;
    let raw = bytes[start..start + crate::PAGE_BYTES].try_into()?;
    let classified = crate::classify_page(PageNumber::new(page), raw, &mut budget())?;
    let record = crate::locate_usage_map(
        classified,
        crate::MapRowLocator::new(PageNumber::new(page), row),
        &mut budget(),
    )?;
    Ok(record.raw()[5 + target as usize / 8] & (1 << (target % 8)) != 0)
}
fn pair<'a>(
    tables: &[crate::TableSpec<'a>; 2],
    parent: Rows<'a>,
    child: Rows<'a>,
) -> [TableRows<'a>; 2] {
    [
        TableRows {
            table: tables[0],
            rows: parent,
        },
        TableRows {
            table: tables[1],
            rows: child,
        },
    ]
}

#[test]
fn duplicate_child_keys_keep_payload_locators_maps_and_distinct_counts() -> TestResult {
    let directory = TempDir::new("create")?;
    let (mut tables, relation) = schema(false);
    let child_columns = [
        ColumnSpec::new(
            b"Label3",
            ColumnType::Text {
                max_len: crate::definition::column_writer::nz(255),
            },
        ),
        tables[1].columns[1],
    ];
    tables[1].columns = &child_columns;
    let payloads = (0..20)
        .map(|position| [b'a' + position; 255])
        .collect::<Vec<_>>();
    let values = payloads
        .iter()
        .enumerate()
        .map(|(position, payload)| {
            [
                RowValue::Text(payload),
                RowValue::Long(1 + (position % 3) as i32),
            ]
        })
        .collect::<Vec<_>>();
    let child_rows = values.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    let requests = pair(&tables, PARENT_ROWS, &child_rows);
    create_spec(directory.target(), &single(&requests, &relation))?;
    let bytes = fs::read(directory.target())?;
    let page = |number: usize| number * crate::PAGE_BYTES;
    assert_eq!(bytes.len(), page(33));
    assert_eq!(&bytes[page(25) + 12..page(25) + 16], &20_u32.to_le_bytes());
    assert_eq!(&bytes[page(25) + 47..page(25) + 51], &3_u32.to_le_bytes());
    for target in 29..32 {
        assert!(map_bit(&bytes, 26, 0, target)?);
        assert!(map_bit(&bytes, 26, 1, target)?);
    }
    assert!(map_bit(&bytes, 26, 2, 28)?);
    assert!(!map_bit(&bytes, 26, 0, 28)?);
    let mut operation = budget();
    let mut database = DatabaseReader::open(directory.target(), &mut operation)?;
    let child = database.table_definition(PageNumber::new(25), &mut operation)?;
    let foreign = database.index_tree(&child, 0, &mut operation)?;
    let mut positions = (0..20).collect::<Vec<_>>();
    positions.sort_unstable_by_key(|position| (position % 3, *position));
    let locators = foreign.entries().iter().map(|entry| entry.row());
    assert_eq!(
        locators.collect::<Vec<_>>(),
        positions
            .iter()
            .map(|n| RowLocator::new(PageNumber::new(29 + n / 7), (n % 7) as u8))
            .collect::<Vec<_>>()
    );
    drop(database);
    let pages = compose_relationship_with_rows(&requests, &relation, &mut budget())?.into_pages();
    for offset in [page(25) + 47, page(26) + 5, page(29) + 2047, page(28) + 252] {
        let mut changed = bytes.clone();
        changed[offset] ^= 1;
        fs::write(directory.target(), changed)?;
        let result = check_relationship_contents(
            &directory.target(),
            &tables,
            &relation,
            &pages,
            Some(&requests),
            &mut budget(),
        );
        assert!(result.is_err(), "offset {offset}");
    }
    Ok(())
}

#[test]
fn orphan_null_duplicate_and_unsupported_parent_shapes_are_refused() -> TestResult {
    let directory = TempDir::new("create")?;
    let (tables, relation) = schema(false);
    let orphan: Rows<'_> = &[&[RowValue::Text(b"a"), RowValue::Long(99)]];
    let referenced: Rows<'_> = &[&[RowValue::Text(b"a"), RowValue::Long(1)]];
    let duplicate: Rows<'_> = &[
        &[RowValue::Long(9), RowValue::Long(1)],
        &[RowValue::Long(8), RowValue::Long(1)],
    ];
    let null: Rows<'_> = &[&[RowValue::Long(9), RowValue::Null]];
    for (parent, child, expected) in [
        (PARENT_ROWS, orphan, "orphan"),
        (duplicate, &[][..], "duplicate"),
        (null, &[], "null"),
        (&[], referenced, "orphan"),
    ] {
        let result = create_spec(
            directory.target(),
            &single(&pair(&tables, parent, child), &relation),
        );
        let error = result.err().ok_or("unexpected success")?;
        assert!(
            matches!(
                (expected, &error),
                (
                    "orphan",
                    WriteError::Compose(ComposeError::OrphanInitialRelationshipKey { .. })
                ) | (
                    "null",
                    WriteError::Compose(ComposeError::NullInitialIndexKey { .. })
                ) | (
                    "duplicate",
                    WriteError::Compose(ComposeError::DuplicateInitialIndexKey { .. })
                )
            ),
            "{expected}: {error:?}"
        );
    }
    let (two, relation) = schema(true);
    assert!(matches!(
        create_spec(
            directory.target(),
            &single(&pair(&two, PARENT_ROWS, &[]), &relation)
        ),
        Err(WriteError::Compose(
            ComposeError::UnsupportedRelationship { .. }
        ))
    ));
    assert!(directory.is_empty()?);
    Ok(())
}

#[test]
fn foreign_branch_growth_and_publication_budget_preserve_destination() -> TestResult {
    let directory = TempDir::new("create")?;
    let (tables, relation) = schema(false);
    let value = [RowValue::Text(b"a"), RowValue::Long(1)];
    let child_rows = vec![value.as_slice(); 201];
    let requests = pair(&tables, PARENT_ROWS, &child_rows[..200]);
    let mut composition = budget();
    let plan = compose_relationship_with_rows(&requests, &relation, &mut composition)?;
    let mut limited = ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(
        composition.total_work_units() + plan.pages().len() as u64 * crate::PAGE_BYTES as u64,
    ));
    assert!(matches!(
        create_database(
            directory.target(),
            &single(&requests, &relation),
            &mut limited
        ),
        Err(WriteError::CreatePublish(_))
    ));
    assert!(directory.is_empty()?);
    // Empty and unreferenced parents are valid inputs.
    for parent in [&[][..], PARENT_ROWS] {
        let directory = TempDir::new("create")?;
        create_spec(
            directory.target(),
            &single(&pair(&tables, parent, &[]), &relation),
        )?;
    }
    let expanded = pair(&tables, PARENT_ROWS, &child_rows);
    create_spec(directory.target(), &single(&expanded, &relation))?;
    let original = fs::read(directory.target())?;
    let mut reader = DatabaseReader::open(directory.target(), &mut budget())?;
    let definition = reader.table_definition(PageNumber::new(25), &mut budget())?;
    let tree = reader.index_tree(&definition, 0, &mut budget())?;
    assert_eq!(tree.entries().len(), 201);
    assert_eq!(tree.nodes().len(), 3);
    for node in tree.nodes() {
        assert!(map_bit(&original, 26, 2, node.page().get())?);
        assert!(!map_bit(&original, 26, 0, node.page().get())?);
    }
    drop(reader);
    let mut limited = ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(1));
    assert!(
        create_database(
            directory.target(),
            &single(&expanded, &relation),
            &mut limited
        )
        .is_err()
    );
    assert_eq!(fs::read(directory.target())?, original);
    assert_eq!(directory.entries()?.len(), 1);
    Ok(())
}
