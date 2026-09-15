use super::*;
use crate::{RelationshipValidationError, ValidationError, ValidationReport};

fn validate(fixture: &Fixture) -> Result<ValidationReport> {
    let before = fs::read(fixture.path())?;
    let mut work = budget();
    let mut db = DatabaseReader::open(fixture.path(), &mut work)?;
    let report = db.validate(TextCodePage::Windows1252, &mut work)?;
    assert_eq!(fs::read(fixture.path())?, before);
    Ok(report)
}

fn metadata_offset(fixture: &Fixture, row: usize, column: u16) -> Result<usize> {
    let mut work = budget();
    let mut db = DatabaseReader::open(fixture.path(), &mut work)?;
    let table = definition(&mut db, b"MSysRelationships", &mut work)?;
    let mut rows = db.rows(&table, &mut work)?;
    for _ in 0..row {
        rows.next_row()?.ok_or("central row absent")?;
    }
    let row = rows.next_row()?.ok_or("central row absent")?;
    let offset = row
        .present_fixed_field_range(ColumnOrdinal::new(column))
        .ok_or("central field absent")?
        .start;
    let locator = row.storage_locator();
    drop(rows);
    let mut raw = [0; PAGE_BYTES];
    db.read_raw_page(locator.page(), &mut raw, &mut work)?;
    let directory = crate::row_directory::RowDirectory::validate(
        locator.page(),
        table.root(),
        &raw,
        &mut work,
    )?;
    Ok(locator.page().get() as usize * PAGE_BYTES
        + directory.entry(&raw, locator.slot())?.range().start
        + offset)
}

fn relation_error(fixture: &Fixture) -> Result<RelationshipValidationError> {
    let before = fs::read(fixture.path())?;
    let mut work = budget();
    let mut db = DatabaseReader::open(fixture.path(), &mut work)?;
    let error = db
        .validate(TextCodePage::Windows1252, &mut work)
        .err()
        .ok_or("expected error")?;
    assert_eq!(fs::read(fixture.path())?, before);
    match error {
        ValidationError::Relationships(error) => Ok(error),
        other => Err(format!("unexpected error: {other:?}").into()),
    }
}

#[test]
fn validation_checks_self_and_multiple_relationships_and_nullable_keys() -> Result {
    let fixture = fixture(&[
        Edge {
            name: b"SelfRelation",
            parent: 0,
            child: 0,
            column: 1,
        },
        Edge {
            name: b"OtherRelation",
            parent: 0,
            child: 1,
            column: 1,
        },
    ])?;
    let report = validate(&fixture)?;
    assert_eq!(report.relationship_catalog_rows, 2);
    assert_eq!(report.relationships_with_verified_keys, 2);
    assert_eq!(report.uninterpreted_relationship_rows, 0);
    assert!(report.relationship_inventory_checked);
    Ok(())
}

#[test]
fn unsupported_rows_do_not_hide_malformed_known_reciprocals_or_duplicate_names() -> Result {
    let first = Edge {
        name: b"FirstRelation",
        parent: 0,
        child: 1,
        column: 1,
    };
    let second = Edge {
        name: b"SecondRelation",
        parent: 0,
        child: 2,
        column: 1,
    };
    let fixture = fixture(&[first, second])?;
    let mut bytes = fs::read(fixture.path())?;
    // Unknown central flags retain the first constraint outside semantic coverage.
    let flag = metadata_offset(&fixture, 0, 1)?;
    bytes[flag..flag + 4].copy_from_slice(&0x4000_i32.to_le_bytes());
    fs::write(fixture.path(), &bytes)?;
    let report = validate(&fixture)?;
    assert_eq!(report.relationships_with_verified_keys, 1);
    assert_eq!(report.uninterpreted_relationship_rows, 1);
    assert!(!report.relationship_inventory_checked);
    let mut work = budget();
    let mut db = DatabaseReader::open(fixture.path(), &mut work)?;
    let table = definition(&mut db, NAMES[2], &mut work)?;
    let record = table
        .relationships()
        .next()
        .ok_or("relation absent")?
        .raw_record();
    let start = table.root().get() as usize * PAGE_BYTES;
    let offset = bytes[start..start + PAGE_BYTES]
        .windows(20)
        .position(|bytes| bytes == record)
        .ok_or("record absent")?
        + start;
    // EXP-0268: the child selector must cross-match its parent reciprocal.
    bytes[offset + 9] ^= 16;
    drop(db);
    fs::write(fixture.path(), &bytes)?;
    assert!(matches!(
        relation_error(&fixture)?,
        RelationshipValidationError::Metadata(_)
    ));

    let duplicate = super::fixture(&[first, first])?;
    let mut bytes = fs::read(duplicate.path())?;
    let flag = metadata_offset(&duplicate, 1, 1)?;
    bytes[flag..flag + 4].copy_from_slice(&0x4000_i32.to_le_bytes());
    fs::write(duplicate.path(), bytes)?;
    assert_eq!(
        relation_error(&duplicate)?,
        RelationshipValidationError::Metadata("duplicate relationship catalog name")
    );
    Ok(())
}

#[test]
fn complete_inventory_rejects_an_endpoint_hidden_from_the_central_catalog() -> Result {
    let fixture = fixture(&[Edge {
        name: b"OnlyRelation",
        parent: 0,
        child: 1,
        column: 1,
    }])?;
    let mut work = budget();
    let mut db = DatabaseReader::open(fixture.path(), &mut work)?;
    let central = definition(&mut db, b"MSysRelationships", &mut work)?;
    let locator = db
        .rows(&central, &mut work)?
        .next_row()?
        .ok_or("row absent")?
        .locator();
    let mut bytes = fs::read(fixture.path())?;
    // EXP-0060/0073: hide the central row and correct its advertised live count.
    bytes[locator.page().get() as usize * PAGE_BYTES + 11 + usize::from(locator.slot()) * 2] |=
        0x80;
    let count = central.root().get() as usize * PAGE_BYTES + 12;
    bytes[count..count + 4].fill(0);
    drop(db);
    fs::write(fixture.path(), bytes)?;
    assert_eq!(
        relation_error(&fixture)?,
        RelationshipValidationError::Metadata("unresolved relationship endpoint record")
    );
    Ok(())
}

#[test]
fn consistent_child_rows_and_indexes_still_reject_an_orphan_key() -> Result {
    let fixture = fixture(&[Edge {
        name: b"OnlyRelation",
        parent: 0,
        child: 1,
        column: 1,
    }])?;
    let selected = locator(&fixture, 1, 2)?;
    let mut work = budget();
    let mut db = DatabaseReader::open(fixture.path(), &mut work)?;
    let table = definition(&mut db, NAMES[1], &mut work)?;
    let offset = {
        let mut rows = db.rows(&table, &mut work)?;
        let mut offset = None;
        while let Some(row) = rows.next_row()? {
            if row.locator() == selected {
                offset = row
                    .present_fixed_field_range(ColumnOrdinal::new(1))
                    .map(|range| range.start);
            }
        }
        offset.ok_or("field absent")?
    };
    let mut raw = [0; PAGE_BYTES];
    db.read_raw_page(selected.page(), &mut raw, &mut work)?;
    let directory = crate::row_directory::RowDirectory::validate(
        selected.page(),
        table.root(),
        &raw,
        &mut work,
    )?;
    let start = directory.entry(&raw, selected.slot())?.range().start + offset;
    raw[start..start + 4].copy_from_slice(&99_i32.to_le_bytes());
    let mut indexes = crate::index_mutation::load(&mut db, &table, &mut work)?;
    indexes.replace(
        selected,
        &[RowValue::Long(2), RowValue::Long(99), RowValue::Long(1)],
        &mut work,
    )?;
    let mut edits = crate::page_edits::PageEdits::new(db.geometry().page_count());
    edits.set_image(
        &mut db,
        selected.page(),
        PageImage::from_bytes(raw),
        &mut work,
    )?;
    indexes.stage(&mut db, &table, &mut edits, &mut work)?;
    edits.publish(&fixture.path(), db, &mut work, |_| {
        Ok::<(), std::convert::Infallible>(())
    })?;
    assert!(matches!(
        relation_error(&fixture)?,
        RelationshipValidationError::Orphan { value: 99, .. }
    ));
    Ok(())
}

#[test]
fn relationship_checks_share_the_validation_resource_budget() -> Result {
    let fixture = fixture(&[Edge {
        name: b"OnlyRelation",
        parent: 0,
        child: 1,
        column: 1,
    }])?;
    let before = fs::read(fixture.path())?;
    let mut measured = budget();
    let mut db = DatabaseReader::open(fixture.path(), &mut measured)?;
    db.validate(TextCodePage::Windows1252, &mut measured)?;
    let mut limited = ResourceBudget::new(
        ResourceLimits::default().with_max_total_work_units(measured.total_work_units() - 1),
    );
    let mut db = DatabaseReader::open(fixture.path(), &mut limited)?;
    let error = db
        .validate(TextCodePage::Windows1252, &mut limited)
        .err()
        .ok_or("expected budget error")?;
    assert!(matches!(
        error,
        ValidationError::Relationships(RelationshipValidationError::Resource(
            crate::Error::ResourceLimitExceeded { .. }
        ))
    ));
    assert_eq!(fs::read(fixture.path())?, before);
    Ok(())
}
