use super::super::tests::inline_map_bit;
use super::*;
use crate::{ColumnOrdinal, DatabaseReader, ResourceLimits, SliceSource};

type TestResult = Result<(), Box<dyn std::error::Error>>;
fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}
fn bytes() -> Result<Vec<u8>, ComposeError> {
    Ok(compose_parent_child(&mut budget())?
        .pages()
        .iter()
        .flat_map(|page| page.image().as_bytes().iter().copied())
        .collect())
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
    for (map, row, owned) in [(12, 8, 27), (12, 9, 27), (26, 2, 28)] {
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
        assert_eq!(tree.entries()[0].row().page(), PageNumber::new(27));
        assert_eq!(tree.entries()[0].row().slot(), 0);
    }
    Ok(())
}

#[test]
fn candidate_budget_exhaustion_and_oversized_key_are_structured() {
    let mut limited = ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(0));
    assert!(compose_parent_child(&mut limited).is_err());
    assert!(matches!(
        relation_index_name(
            &[b'A'; INDEX_KEY_CAPACITY + 1],
            RELATION_DATA,
            &mut budget()
        ),
        Err(ComposeError::NameKey(_))
    ));
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
