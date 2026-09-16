use super::*;
use crate::{
    ColumnRef, IndexColumnSpec, IndexKind, IndexSpec, RelationshipField, RelationshipSpec,
    TableRef, TableRows,
};

fn fixture() -> Result<Fixture, Box<dyn StdError>> {
    let fixture = simple()?;
    fs::remove_file(fixture.path())?;
    crate::create_database_with_relationship_rows(
        fixture.path(),
        &[
            TableRows {
                table: TableSpec {
                    name: b"Parent",
                    columns: &[
                        ColumnSpec::new(b"Id", ColumnType::Long),
                        ColumnSpec::new(b"Other", ColumnType::Long),
                    ],
                    indexes: &[IndexSpec {
                        name: b"ById",
                        fields: &[IndexColumnSpec::ascending(0)],
                        kind: IndexKind::Primary,
                    }],
                },
                rows: &[
                    &[RowValue::Long(1), RowValue::Long(11)],
                    &[RowValue::Long(2), RowValue::Long(22)],
                    &[RowValue::Long(3), RowValue::Long(33)],
                ],
            },
            TableRows {
                table: TableSpec {
                    name: b"Child",
                    columns: &[
                        ColumnSpec::new(b"Id", ColumnType::Long),
                        ColumnSpec::new(b"ParentId", ColumnType::Long),
                        ColumnSpec::new(b"Other", ColumnType::Long),
                    ],
                    indexes: &[],
                },
                rows: &[
                    &[RowValue::Long(10), RowValue::Long(1), RowValue::Long(7)],
                    &[RowValue::Long(11), RowValue::Long(1), RowValue::Long(8)],
                    &[RowValue::Long(12), RowValue::Long(2), RowValue::Long(9)],
                ],
            },
        ],
        &RelationshipSpec {
            name: b"ParentChild",
            parent: TableRef::Ordinal(0),
            child: TableRef::Ordinal(1),
            fields: &[RelationshipField {
                parent: ColumnRef::Ordinal(0),
                child: ColumnRef::Ordinal(1),
            }],
        },
        &mut budget(),
    )?;
    Ok(fixture)
}
fn definition(
    database: &mut DatabaseReader<FileSource>,
    name: &[u8],
    budget: &mut ResourceBudget,
) -> Result<crate::TableDefinition, Box<dyn StdError>> {
    let mut root = None;
    {
        let mut catalog = database.catalog(budget)?;
        while let Some(record) = catalog.next_record()? {
            if record.name().raw_bytes() == name {
                root = record.table_definition();
            }
        }
    }
    Ok(database.table_definition(root.ok_or("table absent")?, budget)?)
}
#[test]
fn catalog_and_reciprocal_indexes_resolve_both_relationship_sides() -> TestResult {
    let fixture = fixture()?;
    let mut work = budget();
    let mut database = DatabaseReader::open(fixture.path(), &mut work)?;
    let parent = definition(&mut database, b"Parent", &mut work)?;
    let child = definition(&mut database, b"Child", &mut work)?;
    for (name, target) in [(b"Parent".as_slice(), &parent), (b"Child", &child)] {
        let constraints =
            crate::relationship_catalog::load(&mut database, target, name, &mut work)?;
        assert_eq!(constraints.len(), 1);
        assert_eq!(constraints[0].parent.root(), parent.root());
        assert_eq!(constraints[0].child.root(), child.root());
        assert_eq!(constraints[0].parent_columns, [ColumnOrdinal::new(0)]);
        assert_eq!(constraints[0].child_columns, [ColumnOrdinal::new(1)]);
    }
    Ok(())
}

fn locator(fixture: &Fixture, name: &[u8], id: i32) -> Result<RowLocator, Box<dyn StdError>> {
    let mut work = budget();
    let mut database = DatabaseReader::open(fixture.path(), &mut work)?;
    let table = definition(&mut database, name, &mut work)?;
    let mut rows = database.rows(&table, &mut work)?;
    while let Some(mut row) = rows.next_row()? {
        if matches!(row.value(ColumnOrdinal::new(0), crate::TextCodePage::Windows1252)?.ok_or("Id absent")?.kind(), crate::ValueKind::Long(value) if *value == id)
        {
            return Ok(row.locator());
        }
    }
    Err("Id absent".into())
}

fn field(fixture: &Fixture, table: &[u8], id: i32, column: u8, value: i32) -> TestResult {
    update_field(
        fixture.path(),
        FieldUpdate {
            table,
            row: locator(fixture, table, id)?,
            column: ColumnOrdinal::new(u16::from(column)),
            value: RowValue::Long(value),
        },
        &mut budget(),
    )?;
    Ok(())
}

fn replace(fixture: &Fixture, table: &[u8], id: i32, values: &[RowValue<'_>]) -> TestResult {
    crate::update_row(
        fixture.path(),
        crate::RowUpdate {
            table,
            row: locator(fixture, table, id)?,
            values,
        },
        &mut budget(),
    )?;
    Ok(())
}

fn delete(fixture: &Fixture, table: &[u8], id: i32) -> TestResult {
    crate::delete_row(
        fixture.path(),
        crate::RowDelete {
            table,
            row: locator(fixture, table, id)?,
        },
        &mut budget(),
    )?;
    Ok(())
}

fn rows(fixture: &Fixture, name: &[u8]) -> Result<Vec<Vec<Option<i32>>>, Box<dyn StdError>> {
    let mut work = budget();
    let mut database = DatabaseReader::open(fixture.path(), &mut work)?;
    let table = definition(&mut database, name, &mut work)?;
    crate::index_mutation::load(&mut database, &table, &mut work)?;
    let mut rows = database.rows(&table, &mut work)?;
    let mut result = Vec::new();
    while let Some(mut row) = rows.next_row()? {
        let mut values = Vec::new();
        for column in table.columns() {
            values.push(
                match row
                    .value(column.ordinal(), crate::TextCodePage::Windows1252)?
                    .ok_or("field absent")?
                    .kind()
                {
                    crate::ValueKind::Null => None,
                    crate::ValueKind::Long(value) => Some(*value),
                    _ => return Err("unexpected field".into()),
                },
            );
        }
        result.push(values);
    }
    result.sort();
    Ok(result)
}

#[test]
fn related_tables_accept_inserts_key_changes_nulls_and_ordered_deletes() -> TestResult {
    let fixture = fixture()?;
    crate::insert_row(
        fixture.path(),
        b"Parent",
        &[RowValue::Long(4), RowValue::Long(44)],
        &mut budget(),
    )?;
    crate::insert_row(
        fixture.path(),
        b"Child",
        &[RowValue::Long(13), RowValue::Long(4), RowValue::Long(9)],
        &mut budget(),
    )?;
    crate::insert_row(
        fixture.path(),
        b"Child",
        &[RowValue::Long(14), RowValue::Null, RowValue::Long(11)],
        &mut budget(),
    )?;
    field(&fixture, b"Child", 10, 1, 2)?;
    replace(
        &fixture,
        b"Child",
        11,
        &[RowValue::Long(11), RowValue::Null, RowValue::Long(8)],
    )?;
    delete(&fixture, b"Parent", 1)?;
    field(&fixture, b"Parent", 3, 0, 5)?;
    replace(
        &fixture,
        b"Child",
        12,
        &[RowValue::Long(12), RowValue::Long(4), RowValue::Long(10)],
    )?;
    delete(&fixture, b"Child", 10)?;
    delete(&fixture, b"Parent", 2)?;
    assert_eq!(
        rows(&fixture, b"Parent")?,
        vec![vec![Some(4), Some(44)], vec![Some(5), Some(33)]]
    );
    assert_eq!(
        rows(&fixture, b"Child")?,
        vec![
            vec![Some(11), None, Some(8)],
            vec![Some(12), Some(4), Some(10)],
            vec![Some(13), Some(4), Some(9)],
            vec![Some(14), None, Some(11)]
        ]
    );
    fixture.assert_only_original()
}

#[test]
fn orphan_and_referenced_parent_requests_preserve_the_complete_file() -> TestResult {
    let fixture = fixture()?;
    let before = fs::read(fixture.path())?;
    let child = locator(&fixture, b"Child", 12)?;
    let parent = locator(&fixture, b"Parent", 1)?;
    let results = [
        crate::insert_row(
            fixture.path(),
            b"Child",
            &[RowValue::Long(13), RowValue::Long(999), RowValue::Long(0)],
            &mut budget(),
        )
        .map(|_| ()),
        update_field(
            fixture.path(),
            FieldUpdate {
                table: b"Child",
                row: child,
                column: ColumnOrdinal::new(1),
                value: RowValue::Long(999),
            },
            &mut budget(),
        ),
        crate::update_row(
            fixture.path(),
            crate::RowUpdate {
                table: b"Child",
                row: child,
                values: &[RowValue::Long(12), RowValue::Long(999), RowValue::Long(0)],
            },
            &mut budget(),
        ),
        crate::delete_row(
            fixture.path(),
            crate::RowDelete {
                table: b"Parent",
                row: parent,
            },
            &mut budget(),
        ),
        update_field(
            fixture.path(),
            FieldUpdate {
                table: b"Parent",
                row: parent,
                column: ColumnOrdinal::new(0),
                value: RowValue::Long(99),
            },
            &mut budget(),
        ),
        crate::update_row(
            fixture.path(),
            crate::RowUpdate {
                table: b"Parent",
                row: parent,
                values: &[RowValue::Long(99), RowValue::Long(11)],
            },
            &mut budget(),
        ),
    ];
    for result in results {
        assert!(
            matches!(result, Err(UpdateError::RelationshipConstraint { .. })),
            "{result:?}"
        );
    }
    assert_eq!(fs::read(fixture.path())?, before);
    fixture.assert_only_original()
}

fn foreign_index(fixture: &Fixture) -> Result<([u32; 2], [u8; PAGE_BYTES]), Box<dyn StdError>> {
    let mut work = budget();
    let mut database = DatabaseReader::open(fixture.path(), &mut work)?;
    let child = definition(&mut database, b"Child", &mut work)?;
    let ordinal = child
        .relationships()
        .next()
        .ok_or("relation absent")?
        .physical_index();
    let index = &child.physical_indexes()[usize::from(ordinal)];
    let mut root = [0; PAGE_BYTES];
    database.read_raw_page(child.root(), &mut root, &mut work)?;
    let offset = 43 + usize::from(ordinal) * 8;
    let count = [
        u32::from_le_bytes(root[offset..offset + 4].try_into()?),
        u32::from_le_bytes(root[offset + 4..offset + 8].try_into()?),
    ];
    database.read_raw_page(index.root(), &mut root, &mut work)?;
    Ok((count, root))
}

fn set_first_word(fixture: &Fixture, first: u32) -> TestResult {
    let mut work = budget();
    let mut database = DatabaseReader::open(fixture.path(), &mut work)?;
    let child = definition(&mut database, b"Child", &mut work)?;
    let ordinal = child
        .relationships()
        .next()
        .ok_or("relation absent")?
        .physical_index();
    let offset = child.root().get() as usize * PAGE_BYTES + 43 + usize::from(ordinal) * 8;
    drop(database);
    let mut original = fs::read(fixture.path())?;
    original[offset..offset + 4].copy_from_slice(&first.to_le_bytes());
    fs::write(fixture.path(), original)?;
    Ok(())
}

#[test]
fn zero_first_word_retains_the_second_word_through_foreign_assignments() -> TestResult {
    let fixture = fixture()?;
    assert_eq!(foreign_index(&fixture)?.0, [0, 2]);
    field(&fixture, b"Child", 10, 1, 2)?; // Both the old and new keys remain present.
    assert_eq!(foreign_index(&fixture)?.0, [0, 2]);
    field(&fixture, b"Child", 10, 1, 3)?;
    assert_eq!(foreign_index(&fixture)?.0, [0, 2]);
    field(&fixture, b"Child", 10, 1, 1)?;
    assert_eq!(foreign_index(&fixture)?.0, [0, 2]);
    let before = foreign_index(&fixture)?;
    field(&fixture, b"Child", 10, 1, 1)?;
    replace(
        &fixture,
        b"Child",
        10,
        &[RowValue::Long(10), RowValue::Long(1), RowValue::Long(99)],
    )?;
    assert_eq!(foreign_index(&fixture)?, before);
    rows(&fixture, b"Child")?;
    fixture.assert_only_original()
}

#[test]
fn equal_foreign_assignments_clamp_the_second_word_to_the_decreased_first() -> TestResult {
    let fixture = fixture()?;
    set_first_word(&fixture, 3)?;
    let before = foreign_index(&fixture)?;
    field(&fixture, b"Child", 10, 2, 99)?;
    assert_eq!(foreign_index(&fixture)?, before);
    field(&fixture, b"Child", 10, 1, 1)?;
    assert_eq!(foreign_index(&fixture)?, ([2, 2], before.1));
    replace(
        &fixture,
        b"Child",
        10,
        &[RowValue::Long(10), RowValue::Long(1), RowValue::Long(100)],
    )?;
    assert_eq!(foreign_index(&fixture)?, ([1, 1], before.1));
    fixture.assert_only_original()
}

#[test]
fn deletion_caps_positive_foreign_state_and_retains_state_after_zero() -> TestResult {
    let fixture = fixture()?;
    set_first_word(&fixture, 3)?;
    delete(&fixture, b"Child", 10)?;
    assert_eq!(foreign_index(&fixture)?.0, [2, 2]);
    delete(&fixture, b"Child", 11)?;
    assert_eq!(foreign_index(&fixture)?.0, [1, 1]);
    crate::insert_row(
        fixture.path(),
        b"Child",
        &[RowValue::Long(14), RowValue::Long(3), RowValue::Long(14)],
        &mut budget(),
    )?;
    assert_eq!(foreign_index(&fixture)?.0, [1, 2]);
    delete(&fixture, b"Child", 14)?;
    assert_eq!(foreign_index(&fixture)?.0, [0, 0]);
    crate::insert_row(
        fixture.path(),
        b"Child",
        &[RowValue::Long(15), RowValue::Null, RowValue::Long(15)],
        &mut budget(),
    )?;
    assert_eq!(foreign_index(&fixture)?.0, [0, 1]);
    delete(&fixture, b"Child", 15)?;
    assert_eq!(foreign_index(&fixture)?.0, [0, 1]);
    rows(&fixture, b"Child")?;
    fixture.assert_only_original()
}

#[test]
fn damaged_reciprocal_metadata_and_stale_parent_index_are_refused() -> TestResult {
    let fixture = fixture()?;
    let before = fs::read(fixture.path())?;
    let row = locator(&fixture, b"Child", 12)?;
    let mut work = budget();
    let mut database = DatabaseReader::open(fixture.path(), &mut work)?;
    let parent = definition(&mut database, b"Parent", &mut work)?;
    let child = definition(&mut database, b"Child", &mut work)?;
    let relation = *parent
        .relationships()
        .next()
        .ok_or("parent relation absent")?
        .raw_record();
    let relation_start = parent.root().get() as usize * PAGE_BYTES;
    let relation_offset = before[relation_start..relation_start + PAGE_BYTES]
        .windows(20)
        .position(|record| record == relation)
        .ok_or("relation raw record absent")?
        + relation_start;
    let tree = database.index_tree(&parent, 0, &mut work)?;
    let key = tree
        .entries()
        .first()
        .ok_or("parent key absent")?
        .key()
        .raw_bytes();
    let index_start = parent.physical_indexes()[0].root().get() as usize * PAGE_BYTES;
    let key_offset = before[index_start..index_start + PAGE_BYTES]
        .windows(key.len())
        .position(|bytes| bytes == key)
        .ok_or("parent raw key absent")?
        + index_start;
    drop(database);
    for (offset, bit) in [
        (relation_offset + 9, 16),
        (relation_offset + 17, 1),
        (key_offset + 4, 1),
    ] {
        let mut damaged = before.clone();
        damaged[offset] ^= bit;
        fs::write(fixture.path(), &damaged)?;
        let result = update_field(
            fixture.path(),
            FieldUpdate {
                table: b"Child",
                row,
                column: child.columns()[2].ordinal(),
                value: RowValue::Long(88),
            },
            &mut budget(),
        );
        assert!(
            matches!(
                result,
                Err(UpdateError::Unsupported(_) | UpdateError::Mismatch(_))
            ),
            "{result:?}"
        );
        assert_eq!(fs::read(fixture.path())?, damaged);
    }
    fixture.assert_only_original()
}

#[test]
fn missing_catalog_and_parent_records_cannot_hide_an_incoming_relationship() -> TestResult {
    let fixture = fixture()?;
    let parent_row = locator(&fixture, b"Parent", 1)?;
    let mut work = budget();
    let mut database = DatabaseReader::open(fixture.path(), &mut work)?;
    let parent = definition(&mut database, b"Parent", &mut work)?;
    let catalog = definition(&mut database, b"MSysRelationships", &mut work)?;
    let row = database
        .rows(&catalog, &mut work)?
        .next_row()?
        .ok_or("catalog row absent")?
        .locator();
    let mut damaged = fs::read(fixture.path())?;
    let start = parent.root().get() as usize * PAGE_BYTES;
    let relation = parent
        .relationships()
        .next()
        .ok_or("parent relation absent")?;
    let offset = damaged[start..start + PAGE_BYTES]
        .windows(20)
        .position(|record| record == relation.raw_record())
        .ok_or("parent raw relation absent")?
        + start;
    let ordinary = parent
        .indexes()
        .iter()
        .find(|index| matches!(index.kind(), crate::IndexDefinitionKind::Primary))
        .ok_or("primary record absent")?;
    damaged[offset..offset + 20].copy_from_slice(ordinary.raw_record());
    damaged[offset + 19] = 0;
    // Hide the sole central row and make its advertised live count agree.
    damaged[row.page().get() as usize * PAGE_BYTES + 11 + usize::from(row.slot()) * 2] |= 0x80;
    damaged[catalog.root().get() as usize * PAGE_BYTES + 12
        ..catalog.root().get() as usize * PAGE_BYTES + 16]
        .fill(0);
    drop(database);
    fs::write(fixture.path(), &damaged)?;
    let result = update_field(
        fixture.path(),
        FieldUpdate {
            table: b"Parent",
            row: parent_row,
            column: parent.columns()[0].ordinal(),
            value: RowValue::Long(99),
        },
        &mut budget(),
    );
    assert!(
        matches!(
            result,
            Err(UpdateError::Mismatch(
                "unresolved incoming relationship record"
            ))
        ),
        "{result:?}"
    );
    assert_eq!(fs::read(fixture.path())?, damaged);
    fixture.assert_only_original()
}

#[test]
fn an_existing_orphan_is_not_silently_repaired_by_a_later_write() -> TestResult {
    let fixture = fixture()?;
    let selected = locator(&fixture, b"Child", 12)?;
    let mut work = budget();
    let mut database = DatabaseReader::open(fixture.path(), &mut work)?;
    let child = definition(&mut database, b"Child", &mut work)?;
    let offset = {
        let mut rows = database.rows(&child, &mut work)?;
        let mut offset = None;
        while let Some(row) = rows.next_row()? {
            if row.locator() == selected {
                offset = row
                    .present_fixed_field_range(ColumnOrdinal::new(1))
                    .map(|range| range.start);
            }
        }
        offset.ok_or("foreign field absent")?
    };
    let mut raw = [0; PAGE_BYTES];
    database.read_raw_page(selected.page(), &mut raw, &mut work)?;
    let directory = RowDirectory::validate(selected.page(), child.root(), &raw, &mut work)?;
    let start = directory.entry(&raw, selected.slot())?.range().start + offset;
    raw[start..start + 4].copy_from_slice(&4_i32.to_le_bytes());
    let mut indexes = crate::index_mutation::load(&mut database, &child, &mut work)?;
    indexes.replace(
        selected,
        &[RowValue::Long(12), RowValue::Long(4), RowValue::Long(9)],
        &mut work,
    )?;
    let mut edits = crate::page_edits::PageEdits::new(database.geometry().page_count());
    edits.set_image(
        &mut database,
        selected.page(),
        crate::PageImage::from_bytes(raw),
        &mut work,
    )?;
    indexes.stage(&mut database, &child, &mut edits, &mut work)?;
    edits.publish(&fixture.path(), database, &mut work, |_| {
        Ok::<(), std::convert::Infallible>(())
    })?;
    let damaged = fs::read(fixture.path())?;
    let mut database = DatabaseReader::open(fixture.path(), &mut work)?;
    crate::index_mutation::load(&mut database, &child, &mut work)?;
    drop(database);
    let result = update_field(
        fixture.path(),
        FieldUpdate {
            table: b"Child",
            row: selected,
            column: ColumnOrdinal::new(1),
            value: RowValue::Long(2),
        },
        &mut budget(),
    );
    assert!(
        matches!(
            result,
            Err(UpdateError::Mismatch("orphan relationship key"))
        ),
        "{result:?}"
    );
    assert_eq!(fs::read(fixture.path())?, damaged);
    fixture.assert_only_original()
}

#[test]
fn duplicate_reciprocal_relationship_records_are_refused() -> TestResult {
    let fixture = fixture()?;
    let selected = locator(&fixture, b"Child", 12)?;
    let mut work = budget();
    let mut database = DatabaseReader::open(fixture.path(), &mut work)?;
    let parent = definition(&mut database, b"Parent", &mut work)?;
    let primary = parent
        .indexes()
        .iter()
        .find(|index| matches!(index.kind(), crate::IndexDefinitionKind::Primary))
        .ok_or("primary absent")?;
    let relation = parent.relationships().next().ok_or("relation absent")?;
    let mut damaged = fs::read(fixture.path())?;
    let start = parent.root().get() as usize * PAGE_BYTES;
    let offset = damaged[start..start + PAGE_BYTES]
        .windows(20)
        .position(|record| record == primary.raw_record())
        .ok_or("primary raw record absent")?
        + start;
    damaged[offset..offset + 20].copy_from_slice(relation.raw_record());
    drop(database);
    fs::write(fixture.path(), &damaged)?;
    let result = update_field(
        fixture.path(),
        FieldUpdate {
            table: b"Child",
            row: selected,
            column: ColumnOrdinal::new(2),
            value: RowValue::Long(10),
        },
        &mut budget(),
    );
    assert!(
        matches!(
            result,
            Err(UpdateError::Mismatch(
                "ambiguous reciprocal relationship index"
            ))
        ),
        "{result:?}"
    );
    assert_eq!(fs::read(fixture.path())?, damaged);
    fixture.assert_only_original()
}
