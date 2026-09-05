use super::*;
use crate::{RowLocator, create_database_with_relationship_rows};

fn parent_rows() -> &'static [&'static [RowValue<'static>]] {
    &[
        &[RowValue::Long(9), RowValue::Long(1)],
        &[RowValue::Long(8), RowValue::Long(2)],
        &[RowValue::Long(7), RowValue::Long(3)],
    ]
}

fn map_bit(
    bytes: &[u8],
    page: u64,
    row: u8,
    target: u64,
) -> Result<bool, Box<dyn std::error::Error>> {
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

#[test]
fn duplicate_child_keys_keep_payload_locators_maps_and_distinct_counts() -> TestResult {
    let directory = Directory::new()?;
    let (mut tables, relation) = schema(false);
    let child_columns = [
        ColumnSpec::new(
            b"Label3",
            ColumnType::Text {
                max_len: crate::column_definition_writer::nz(255),
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
    let requests = [
        TableRows {
            table: tables[0],
            rows: parent_rows(),
        },
        TableRows {
            table: tables[1],
            rows: &child_rows,
        },
    ];
    create_database_with_relationship_rows(
        directory.target(),
        &requests,
        &relation,
        &mut budget(),
    )?;
    let bytes = fs::read(directory.target())?;
    assert_eq!(bytes.len(), 32 * crate::PAGE_BYTES);
    assert_eq!(
        &bytes[25 * crate::PAGE_BYTES + 12..25 * crate::PAGE_BYTES + 16],
        &20_u32.to_le_bytes()
    );
    assert_eq!(
        &bytes[25 * crate::PAGE_BYTES + 47..25 * crate::PAGE_BYTES + 51],
        &3_u32.to_le_bytes()
    );
    for page in 27..30 {
        assert!(map_bit(&bytes, 26, 0, page)?);
        assert!(map_bit(&bytes, 26, 1, page)?);
    }
    assert!(map_bit(&bytes, 26, 2, 31)?);
    assert!(!map_bit(&bytes, 26, 0, 31)?);
    let mut operation = budget();
    let mut database = DatabaseReader::open(directory.target(), &mut operation)?;
    let child = database.table_definition(PageNumber::new(25), &mut operation)?;
    let foreign = database.index_tree(&child, 0, &mut operation)?;
    let mut positions = (0..20).collect::<Vec<_>>();
    positions.sort_unstable_by_key(|position| (position % 3, *position));
    assert_eq!(
        foreign
            .entries()
            .iter()
            .map(|entry| entry.row())
            .collect::<Vec<_>>(),
        positions
            .iter()
            .map(|position| RowLocator::new(
                PageNumber::new(27 + position / 7),
                (position % 7) as u8
            ))
            .collect::<Vec<_>>()
    );
    drop(database);
    let pages = compose_relationship_with_rows(&requests, &relation, &mut budget())?.into_pages();
    for offset in [
        25 * crate::PAGE_BYTES + 47,
        26 * crate::PAGE_BYTES + 5,
        27 * crate::PAGE_BYTES + 2047,
        31 * crate::PAGE_BYTES + 252,
    ] {
        let mut changed = bytes.clone();
        changed[offset] ^= 1;
        fs::write(directory.target(), changed)?;
        assert!(
            check_relationship_contents(
                &directory.target(),
                &tables,
                &relation,
                &pages,
                Some(&requests),
                &mut budget()
            )
            .is_err()
        );
    }
    Ok(())
}

#[test]
fn orphan_null_duplicate_and_unsupported_parent_shapes_are_refused() -> TestResult {
    let directory = Directory::new()?;
    let (tables, relation) = schema(false);
    type Rows<'a> = &'a [&'a [RowValue<'a>]];
    let cases: &[(Rows<'_>, Rows<'_>, &str)] = &[
        (
            parent_rows(),
            &[&[RowValue::Text(b"a"), RowValue::Long(99)]],
            "orphan",
        ),
        (
            parent_rows(),
            &[&[RowValue::Text(b"a"), RowValue::Null]],
            "null",
        ),
        (
            &[
                &[RowValue::Long(9), RowValue::Long(1)],
                &[RowValue::Long(8), RowValue::Long(1)],
            ],
            &[],
            "duplicate",
        ),
        (&[&[RowValue::Long(9), RowValue::Null]], &[], "null"),
        (&[], &[&[RowValue::Text(b"a"), RowValue::Long(1)]], "orphan"),
    ];
    for &(parent, child, expected) in cases {
        let requests = [
            TableRows {
                table: tables[0],
                rows: parent,
            },
            TableRows {
                table: tables[1],
                rows: child,
            },
        ];
        let error = create_database_with_relationship_rows(
            directory.target(),
            &requests,
            &relation,
            &mut budget(),
        )
        .err()
        .ok_or("unexpected success")?;
        assert!(matches!(
            (expected, error),
            (
                "orphan",
                CreateDatabaseError::Compose(ComposeError::OrphanInitialRelationshipKey { .. })
            ) | (
                "null",
                CreateDatabaseError::Compose(ComposeError::NullInitialIndexKey { .. })
            ) | (
                "duplicate",
                CreateDatabaseError::Compose(ComposeError::DuplicateInitialIndexKey { .. })
            )
        ));
    }
    let (two, relation) = schema(true);
    assert!(matches!(
        create_database_with_relationship_rows(
            directory.target(),
            &[
                TableRows {
                    table: two[0],
                    rows: &[]
                },
                TableRows {
                    table: two[1],
                    rows: &[]
                }
            ],
            &relation,
            &mut budget()
        ),
        Err(CreateDatabaseError::Compose(
            ComposeError::UnsupportedRelationship { .. }
        ))
    ));
    assert!(directory.empty()?);
    Ok(())
}

#[test]
fn foreign_branch_growth_and_publication_budget_preserve_destination() -> TestResult {
    let directory = Directory::new()?;
    let (tables, relation) = schema(false);
    let value = [RowValue::Text(b"a"), RowValue::Long(1)];
    let child_rows = vec![value.as_slice(); 201];
    let requests = [
        TableRows {
            table: tables[0],
            rows: parent_rows(),
        },
        TableRows {
            table: tables[1],
            rows: &child_rows[..200],
        },
    ];
    let mut composition = budget();
    let plan = compose_relationship_with_rows(&requests, &relation, &mut composition)?;
    let mut limited = ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(
        composition.total_work_units() + plan.pages().len() as u64 * crate::PAGE_BYTES as u64,
    ));
    assert!(matches!(
        create_database_with_relationship_rows(
            directory.target(),
            &requests,
            &relation,
            &mut limited
        ),
        Err(CreateDatabaseError::Publish(_))
    ));
    assert!(directory.empty()?);
    create_database_with_relationship_rows(
        directory.target(),
        &requests,
        &relation,
        &mut budget(),
    )?;
    let expanded = [
        requests[0],
        TableRows {
            rows: &child_rows,
            ..requests[1]
        },
    ];
    let directory = Directory::new()?;
    create_database_with_relationship_rows(
        directory.target(),
        &expanded,
        &relation,
        &mut budget(),
    )?;
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
        create_database_with_relationship_rows(
            directory.target(),
            &expanded,
            &relation,
            &mut limited
        )
        .is_err()
    );
    assert_eq!(fs::read(directory.target())?, original);
    assert_eq!(fs::read_dir(&directory.0)?.count(), 1);
    Ok(())
}

#[test]
fn empty_and_unreferenced_parent_rows_are_valid_inputs() -> TestResult {
    let (tables, relation) = schema(false);
    for parent in [&[], parent_rows()] {
        let directory = Directory::new()?;
        create_database_with_relationship_rows(
            directory.target(),
            &[
                TableRows {
                    table: tables[0],
                    rows: parent,
                },
                TableRows {
                    table: tables[1],
                    rows: &[],
                },
            ],
            &relation,
            &mut budget(),
        )?;
    }
    Ok(())
}
