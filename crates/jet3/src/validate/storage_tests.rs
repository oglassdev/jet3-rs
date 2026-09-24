use super::tests::*;
use crate::{MapRowLocator, PAGE_BYTES, PageNumber, StorageValidationError, validate::*};

pub(super) fn set_map_bit(
    bytes: &mut [u8],
    locator: MapRowLocator,
    page: PageNumber,
    set: bool,
) -> TestResult {
    let mut resources = budget();
    let mut database = open(bytes, &mut resources)?;
    let bits = crate::alloc::mutation_map::MapBits::load(&mut database, locator, &mut resources)?;
    let span = bits
        .spans
        .iter()
        .find(|span| {
            page.get() >= span.first && (page.get() - span.first) / 8 < span.bytes.len() as u64
        })
        .ok_or("fixture map does not represent page")?;
    let bit = page.get() - span.first;
    let offset = page_start(span.page) + span.offset + (bit / 8) as usize;
    let mask = 1 << (bit % 8);
    if set {
        bytes[offset] |= mask;
    } else {
        bytes[offset] &= !mask;
    }
    Ok(())
}

fn storage_error(bytes: &[u8]) -> TestResult<StorageValidationError> {
    match validate(bytes)? {
        Err(ValidationError::Storage(source))
        | Err(ValidationError::Table {
            source: TableValidationError::Storage(source),
            ..
        }) => Ok(source),
        other => Err(format!("expected storage error, got {other:?}").into()),
    }
}

#[test]
fn globally_free_definitions_and_index_pages_are_rejected() -> TestResult {
    let original = fixture()?;
    let items = definition(&original, b"Items")?;
    for page in [items.root(), items.physical_indexes()[0].root()] {
        let mut changed = original.clone();
        set_map_bit(
            &mut changed,
            crate::alloc::mutation_map_write::global_locator(),
            page,
            true,
        )?;
        assert!(
            matches!(storage_error(&changed)?, StorageValidationError::Page {
            page: actual, detail: "owned or metadata page is globally free",
        } if actual == page)
        );
    }
    Ok(())
}

#[test]
fn maps_cannot_alias_or_advertise_unowned_space() -> TestResult {
    let original = fixture()?;
    let items = definition(&original, b"Items")?;
    let mut changed = original.clone();
    // EXP-0057: adjacent owned/available row locators in the definition.
    let root = page_start(items.root());
    let owned: [u8; 4] = changed[root + 35..root + 39].try_into()?;
    changed[root + 39..root + 43].copy_from_slice(&owned);
    assert!(matches!(
        storage_error(&changed)?,
        StorageValidationError::Page {
            detail: "allocation map shared between roles",
            ..
        }
    ));
    let mut changed = original;
    set_map_bit(&mut changed, items.maps().available(), items.root(), true)?;
    assert!(matches!(
        storage_error(&changed)?,
        StorageValidationError::Page {
            detail: "available page is not owned",
            ..
        }
    ));
    Ok(())
}

#[test]
fn index_nodes_must_be_owned_and_index_reservations_cannot_claim_other_data() -> TestResult {
    let original = fixture()?;
    let items = definition(&original, b"Items")?;
    let index = &items.physical_indexes()[0];
    let locator = MapRowLocator::new(index.usage_map().page(), index.usage_map().row());
    let mut changed = original.clone();
    set_map_bit(&mut changed, locator, index.root(), false)?;
    assert!(matches!(
        storage_error(&changed)?,
        StorageValidationError::Page {
            detail: "index node is not owned by its index",
            ..
        }
    ));
    let notes = definition(&original, b"Notes")?;
    let mut resources = budget();
    let mut database = open(&original, &mut resources)?;
    let note_page = database
        .owned_pages(notes.root(), &mut resources)?
        .next_page()?
        .ok_or("Notes page")?;
    let mut changed = original;
    set_map_bit(&mut changed, locator, note_page, true)?;
    assert!(matches!(
        storage_error(&changed)?,
        StorageValidationError::Page {
            detail: "page is owned by incompatible roles",
            ..
        }
    ));
    Ok(())
}

fn payload_references(bytes: &[u8]) -> TestResult<Vec<(RowLocator, crate::LongValueReference)>> {
    let notes = definition(bytes, b"Notes")?;
    let mut resources = budget();
    let mut database = open(bytes, &mut resources)?;
    let mut rows = database.rows(&notes, &mut resources)?;
    let mut result = Vec::new();
    while let Some(mut row) = rows.next_row()? {
        let locator = row.locator();
        let value = row
            .value(notes.columns()[1].ordinal(), TextCodePage::Windows1252)?
            .ok_or("Memo")?;
        if let ValueKind::LongValue(LongValue::External(reference)) = value.kind() {
            result.push((locator, *reference));
        }
    }
    Ok(result)
}

#[test]
fn payload_references_require_column_ownership_and_cannot_share_fragments() -> TestResult {
    let original = fixture()?;
    let notes = definition(&original, b"Notes")?;
    let refs = payload_references(&original)?;
    let mut changed = original.clone();
    set_map_bit(
        &mut changed,
        notes.long_value_maps()[0].owned(),
        refs[0].1.target().page(),
        false,
    )?;
    assert!(matches!(
        storage_error(&changed)?,
        StorageValidationError::Fragment {
            detail: "fragment page is not owned by a payload column",
            ..
        }
    ));
    let mut changed = original;
    let descriptor = refs[1].1.raw_header();
    let matches: Vec<_> = changed
        .windows(descriptor.len())
        .enumerate()
        .filter_map(|(offset, bytes)| (bytes == descriptor).then_some(offset))
        .collect();
    assert_eq!(matches.len(), 1);
    changed[matches[0]..matches[0] + descriptor.len()].copy_from_slice(&refs[0].1.raw_header());
    assert!(matches!(
        storage_error(&changed)?,
        StorageValidationError::Fragment {
            detail: "payload fragment has multiple references",
            ..
        }
    ));
    Ok(())
}

#[test]
fn unreachable_payload_fragments_and_hidden_rows_are_rejected() -> TestResult {
    let original = fixture()?;
    let notes = definition(&original, b"Notes")?;
    let refs = payload_references(&original)?;
    let mut changed = original.clone();
    let row = refs[0].0;
    let offset = page_start(row.page());
    let mut resources = budget();
    let image: &[u8; PAGE_BYTES] = changed[offset..offset + PAGE_BYTES].try_into()?;
    let directory = crate::row::directory::RowDirectory::validate(
        row.page(),
        notes.root(),
        image,
        &mut resources,
    )?;
    let entry = directory.entry(image, row.slot())?;
    // EXP-0060: clear Memo's presence bit while retaining its old descriptor bytes.
    changed[offset + entry.range().end - 1] &= !2;
    assert!(matches!(
        storage_error(&changed)?,
        StorageValidationError::Page {
            detail: "unreferenced live payload fragment",
            ..
        }
    ));
    let mut changed = original;
    // EXP-0060: hide the first ordinary inline-Memo row, with no logical link to it.
    let slot = offset + 10;
    let raw = u16::from_le_bytes(changed[slot..slot + 2].try_into()?);
    changed[slot..slot + 2].copy_from_slice(&(raw | 0x8000).to_le_bytes());
    let root = page_start(notes.root());
    changed[root + 12..root + 16].copy_from_slice(&2_u32.to_le_bytes());
    assert!(matches!(
        storage_error(&changed)?,
        StorageValidationError::MapInvariant {
            detail: "unreferenced hidden row storage",
            ..
        }
    ));
    Ok(())
}
