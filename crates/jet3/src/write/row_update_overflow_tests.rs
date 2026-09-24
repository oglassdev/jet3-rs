use super::row_update_tests::*;
use crate::{
    ColumnOrdinal, DatabaseReader, PAGE_BYTES, PublishStage, ResourceBudget, ResourceLimits,
    RowLocator, RowValue, WriteError, write::row_update::*,
};
use std::error::Error as StdError;
use std::fs;

fn values<'a>(id: i32, text: &'a [u8], binary: &'a [u8]) -> [RowValue<'a>; 4] {
    [
        RowValue::Long(id),
        RowValue::Text(text),
        RowValue::Binary(binary),
        RowValue::Boolean(false),
    ]
}

fn storage(f: &Fixture, logical: RowLocator) -> Result<RowLocator, Box<dyn StdError>> {
    let mut work = budget();
    let mut db = DatabaseReader::open(f.path(), &mut work)?;
    let table = db.table_definition(f.root, &mut work)?;
    let graph =
        crate::row::mutation_graph::RowGraph::load(&mut db, &table, Some(logical), &mut work)?;
    if !table.indexes().is_empty() {
        crate::index::mutation::load(&mut db, &table, &mut work)?;
    }
    graph
        .selected
        .last()
        .copied()
        .ok_or("missing storage".into())
}

fn slot(bytes: &[u8], row: RowLocator) -> (u16, &[u8]) {
    let base = row.page().get() as usize * PAGE_BYTES;
    let offset = base + 10 + usize::from(row.slot()) * 2;
    let word = word(bytes, offset) as u16;
    let end = if row.slot() == 0 {
        PAGE_BYTES
    } else {
        super::row_update_tests::word(bytes, offset - 2) & 0x1fff
    };
    (
        word & 0xe000,
        &bytes[base + usize::from(word & 0x1fff)..base + end],
    )
}

#[test]
fn growth_fixed_key_edit_collapse_reuse_and_deletion_keep_logical_addresses() -> TestResult {
    let f = Fixture::with_index(67, true)?;
    let before = f.rows()?;
    let logical = f.locators[0];
    let large = values(0, &[b'X'; 255], &[0xa5; 255]);
    update_row(f.path(), f.request(0, &large), &mut budget())?;
    let hidden = storage(&f, logical)?;
    assert_ne!(hidden.page(), logical.page());
    let grown = fs::read(f.path())?;
    assert_eq!(
        slot(&grown, logical),
        (
            0x4000,
            crate::row::directory::overflow_pointer(hidden)?.as_slice()
        )
    );
    assert_eq!(slot(&grown, hidden).0, 0x8000);
    let equal = values(0, &[b'Y'; 255], &[0x5a; 255]);
    update_row(f.path(), f.request(0, &equal), &mut budget())?;
    assert_eq!(storage(&f, logical)?, hidden);
    crate::update_field(
        f.path(),
        crate::FieldUpdate {
            table: b"Rows",
            row: logical,
            column: ColumnOrdinal::new(0),
            value: RowValue::Long(900),
        },
        &mut budget(),
    )?;
    assert_eq!(storage(&f, logical)?, hidden);
    let short = values(900, b"x", b"y");
    update_row(f.path(), f.request(0, &short), &mut budget())?;
    assert_eq!(storage(&f, logical)?, logical);
    let collapsed = fs::read(f.path())?;
    assert_eq!(slot(&collapsed, logical).0, 0);
    assert_eq!(slot(&collapsed, hidden), (0xc000, &[][..]));
    assert_eq!(collapsed[hidden.page().get() as usize * PAGE_BYTES], 9);
    let large = values(900, &[b'Z'; 255], &[0xa5; 255]);
    update_row(f.path(), f.request(0, &large), &mut budget())?;
    assert_eq!(storage(&f, logical)?, hidden);
    assert_eq!(fs::metadata(f.path())?.len(), grown.len() as u64);
    let inserted = crate::insert_row(
        f.path(),
        b"Rows",
        &values(901, &[b'I'; 150], &[0x77; 150]),
        &mut budget(),
    )?;
    assert_eq!(inserted.page(), hidden.page());
    crate::delete_row(
        f.path(),
        crate::RowDelete {
            table: b"Rows",
            row: logical,
        },
        &mut budget(),
    )?;
    let remaining = f.rows()?;
    for row in &before[1..] {
        assert!(remaining.contains(row));
    }
    assert_eq!(remaining.len(), before.len());
    assert_eq!(storage(&f, inserted)?, inserted);
    let deleted = fs::read(f.path())?;
    assert_eq!(slot(&deleted, logical), (0xc000, &[][..]));
    assert_eq!(slot(&deleted, hidden), (0xc000, &[][..]));
    assert_eq!(deleted[hidden.page().get() as usize * PAGE_BYTES], 1);
    crate::delete_row(
        f.path(),
        crate::RowDelete {
            table: b"Rows",
            row: inserted,
        },
        &mut budget(),
    )?;
    assert_eq!(
        fs::read(f.path())?[hidden.page().get() as usize * PAGE_BYTES],
        9
    );
    Ok(())
}

#[test]
fn full_hidden_page_relocates_directly_and_preserves_ordinary_neighbors() -> TestResult {
    let f = Fixture::with_index(67, true)?;
    let logical = f.locators[0];
    update_row(
        f.path(),
        f.request(0, &values(0, &[b'X'; 200], &[1; 159])),
        &mut budget(),
    )?;
    let old_storage = storage(&f, logical)?;
    let mut inserted = Vec::new();
    for id in 100..104 {
        inserted.push(crate::insert_row(
            f.path(),
            b"Rows",
            &values(id, &[b'I'; 200], &[2; 200]),
            &mut budget(),
        )?);
    }
    assert!(inserted.iter().all(|row| row.page() == old_storage.page()));
    let before = f.rows()?;
    update_row(
        f.path(),
        f.request(0, &values(0, &[b'G'; 255], &[3; 255])),
        &mut budget(),
    )?;
    let new_storage = storage(&f, logical)?;
    assert_ne!(old_storage, new_storage);
    let raw = fs::read(f.path())?;
    assert_eq!(
        slot(&raw, logical),
        (
            0x4000,
            crate::row::directory::overflow_pointer(new_storage)?.as_slice()
        )
    );
    assert_eq!(slot(&raw, old_storage), (0xc000, &[][..]));
    let after = f.rows()?;
    for row in before.iter().filter(|row| row.0 != logical) {
        assert!(after.contains(row));
    }
    Ok(())
}

#[test]
fn bad_overflow_graphs_preserve_the_whole_source_for_every_mutation() -> TestResult {
    let f = Fixture::new(67)?;
    let large = values(0, &[b'X'; 255], &[1; 255]);
    update_row(f.path(), f.request(0, &large), &mut budget())?;
    let first = f.locators[0];
    let first_storage = storage(&f, first)?;
    update_row(
        f.path(),
        f.request(1, &values(1, &[b'Y'; 255], &[2; 255])),
        &mut budget(),
    )?;
    let good = fs::read(f.path())?;
    let second = f.locators[1];
    let logical_base = first.page().get() as usize * PAGE_BYTES;
    let storage_base = first_storage.page().get() as usize * PAGE_BYTES;
    let second_start = word(&good, logical_base + 10 + usize::from(second.slot()) * 2) & 0x1fff;
    for defect in 0..6 {
        let mut bad = good.clone();
        match defect {
            0 => bad[logical_base + second_start..logical_base + second_start + 4]
                .copy_from_slice(&crate::row::directory::overflow_pointer(first_storage)?),
            1 => bad[storage_base + 4] ^= 1,
            2 => bad[logical_base + 11] ^= 0x80,
            3 => bad[logical_base + 10] ^= 1,
            4 => bad[storage_base + 11 + usize::from(first_storage.slot()) * 2] &= 0x7f,
            _ => bad[logical_base + second_start..logical_base + second_start + 4]
                .copy_from_slice(&crate::row::directory::overflow_pointer(first)?),
        }
        fs::write(f.path(), &bad)?;
        assert!(update_row(f.path(), f.request(0, &large), &mut budget()).is_err());
        assert_eq!(fs::read(f.path())?, bad);
        assert!(
            crate::delete_row(
                f.path(),
                crate::RowDelete {
                    table: b"Rows",
                    row: first
                },
                &mut budget()
            )
            .is_err()
        );
        assert_eq!(fs::read(f.path())?, bad);
        assert!(crate::insert_row(f.path(), b"Rows", &large, &mut budget()).is_err());
        assert_eq!(fs::read(f.path())?, bad);
        assert!(
            crate::update_field(
                f.path(),
                crate::FieldUpdate {
                    table: b"Rows",
                    row: first,
                    column: ColumnOrdinal::new(0),
                    value: RowValue::Long(7),
                },
                &mut budget()
            )
            .is_err()
        );
        assert_eq!(fs::read(f.path())?, bad);
    }
    fs::write(f.path(), &good)?;
    let mut limited = ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(1));
    assert!(update_row(f.path(), f.request(0, &large), &mut limited).is_err());
    assert_eq!(fs::read(f.path())?, good);
    Ok(())
}

#[test]
fn logical_and_hidden_slots_on_the_same_page_compose_without_losing_neighbors() -> TestResult {
    for deleting in [false, true] {
        let f = Fixture::with_index(3, true)?;
        let initial = f.rows()?;
        let logical = f.locators[0];
        let mut bytes = fs::read(f.path())?;
        let raw = slot(&bytes, logical).1.to_vec();
        let base = logical.page().get() as usize * PAGE_BYTES;
        let source = bytes[base..base + PAGE_BYTES].try_into()?;
        let (appended, hidden_slot) = crate::row::data_page::DataPageEditor::open(
            logical.page(),
            f.root,
            &source,
            &mut budget(),
        )?
        .append(
            &raw,
            Some(crate::row::directory::RowSlot::Storage),
            &mut budget(),
        )?
        .ok_or("hidden append capacity")?;
        let hidden = RowLocator::new(logical.page(), hidden_slot);
        let linked = crate::row::data_page::DataPageEditor::open(
            logical.page(),
            f.root,
            appended.as_bytes(),
            &mut budget(),
        )?
        .replace(
            logical.slot(),
            &crate::row::directory::overflow_pointer(hidden)?,
            Some(crate::row::directory::RowSlot::Link),
            &mut budget(),
        )?
        .ok_or("logical link capacity")?;
        bytes[base..base + PAGE_BYTES].copy_from_slice(linked.as_bytes());
        fs::write(f.path(), &bytes)?;
        assert_eq!(storage(&f, logical)?, hidden);
        if deleting {
            crate::delete_row(
                f.path(),
                crate::RowDelete {
                    table: b"Rows",
                    row: logical,
                },
                &mut budget(),
            )?;
        } else {
            update_row(
                f.path(),
                f.request(0, &values(0, b"x", b"y")),
                &mut budget(),
            )?;
            assert_eq!(storage(&f, logical)?, logical);
        }
        let after = f.rows()?;
        for row in &initial[1..] {
            assert!(after.contains(row));
        }
        let bytes = fs::read(f.path())?;
        assert_eq!(slot(&bytes, hidden), (0xc000, &[][..]));
    }
    Ok(())
}

#[test]
fn overflow_allocation_failures_preserve_the_source_and_remove_private_files() -> TestResult {
    let f = Fixture::with_index(67, true)?;
    let before = fs::read(f.path())?;
    let duplicate = values(1, &[b'X'; 255], &[0x11; 255]);
    assert!(matches!(
        update_row(f.path(), f.request(0, &duplicate), &mut budget()),
        Err(WriteError::Unsupported("duplicate unique key"))
    ));
    assert_eq!(fs::read(f.path())?, before);
    let large = values(0, &[b'X'; 255], &[0x11; 255]);
    let result = update_with_hook(
        &f.path(),
        f.request(0, &large),
        &mut budget(),
        |stage| -> Result<(), std::io::Error> {
            if stage == PublishStage::Validation {
                for entry in fs::read_dir(&f.dir)? {
                    let path = entry?.path();
                    if path != f.path() {
                        let mut bytes = fs::read(&path)?;
                        let end = bytes.len() - 1;
                        bytes[end] ^= 1;
                        fs::write(path, bytes)?;
                    }
                }
            }
            Ok(())
        },
    );
    assert!(
        matches!(result, Err(WriteError::Publish(error)) if error.stage() == PublishStage::Validation)
    );
    assert_eq!(fs::read(f.path())?, before);
    assert_eq!(fs::read_dir(&f.dir)?.count(), 1);
    Ok(())
}

#[test]
fn valid_multi_hop_chains_are_refused_without_changing_the_image() -> TestResult {
    let f = Fixture::new(3)?;
    let [logical, middle, terminal] = f.locators.as_slice() else {
        return Err("three-row fixture".into());
    };
    let (logical, middle, terminal) = (*logical, *middle, *terminal);
    let mut bytes = fs::read(f.path())?;
    let base = logical.page().get() as usize * PAGE_BYTES;
    let body = slot(&bytes, terminal).1.to_vec();
    let mut page = crate::PageImage::from_bytes(bytes[base..base + PAGE_BYTES].try_into()?);
    for (locator, raw, kind) in [
        (terminal, body, crate::row::directory::RowSlot::Storage),
        (
            middle,
            crate::row::directory::overflow_pointer(terminal)?.to_vec(),
            crate::row::directory::RowSlot::StorageLink,
        ),
        (
            logical,
            crate::row::directory::overflow_pointer(middle)?.to_vec(),
            crate::row::directory::RowSlot::Link,
        ),
    ] {
        page = crate::row::data_page::DataPageEditor::open(
            logical.page(),
            f.root,
            page.as_bytes(),
            &mut budget(),
        )?
        .replace(locator.slot(), &raw, Some(kind), &mut budget())?
        .ok_or("multi-hop fixture capacity")?;
    }
    bytes[base..base + PAGE_BYTES].copy_from_slice(page.as_bytes());
    let root = f.root.get() as usize * PAGE_BYTES;
    let mut definition = crate::PageImage::from_bytes(bytes[root..root + PAGE_BYTES].try_into()?);
    for count in [3, 2] {
        definition = crate::row::data_page::count_table_row(
            definition.as_bytes(),
            count,
            false,
            &mut budget(),
        )?;
    }
    bytes[root..root + PAGE_BYTES].copy_from_slice(definition.as_bytes());
    fs::write(f.path(), &bytes)?;
    let mut work = budget();
    let mut db = DatabaseReader::open(f.path(), &mut work)?;
    let table = db.table_definition(f.root, &mut work)?;
    let graph =
        crate::row::mutation_graph::RowGraph::load(&mut db, &table, Some(logical), &mut work)?;
    assert_eq!(graph.selected, [logical, middle, terminal]);
    drop(db);
    for action in 0..3 {
        let result = match action {
            0 => update_row(
                f.path(),
                f.request(0, &values(2, b"x", b"y")),
                &mut budget(),
            ),
            1 => crate::update_field(
                f.path(),
                crate::FieldUpdate {
                    table: b"Rows",
                    row: logical,
                    column: ColumnOrdinal::new(0),
                    value: RowValue::Long(7),
                },
                &mut budget(),
            ),
            _ => crate::delete_row(
                f.path(),
                crate::RowDelete {
                    table: b"Rows",
                    row: logical,
                },
                &mut budget(),
            ),
        };
        assert!(matches!(
            result,
            Err(WriteError::Unsupported(
                "mutation of multi-hop overflow chain"
            ))
        ));
        assert_eq!(fs::read(f.path())?, bytes);
    }
    Ok(())
}
