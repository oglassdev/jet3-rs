use super::{CatalogError, CatalogObjectClass, CatalogObjectKind};
use crate::{
    ByteCount, CatalogRecordError, DatabaseReader, Error, JET3_PAGE_SIZE, PageKind, PageNumber,
    ReadLimits, ResourceBudget, ResourceLimitKind, ResourceLimits, SliceSource,
};
use std::error::Error as _;

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;

fn record(id: u32, kind: u16, flags: u32, name: &[u8]) -> Vec<u8> {
    let mut row = vec![0_u8; 31 + name.len() + 6];
    row[0] = 17;
    row[1..5].copy_from_slice(&id.to_le_bytes());
    row[9..11].copy_from_slice(&kind.to_le_bytes());
    row[27..31].copy_from_slice(&flags.to_le_bytes());
    row[31..31 + name.len()].copy_from_slice(name);
    let length = row.len();
    row[length - 6] = u8::try_from(31 + name.len()).unwrap_or_default();
    row[length - 5] = 31;
    row[length - 4] = 11;
    row[length - 3] = 0xff;
    row
}

fn write_rows(page: &mut [u8], rows: &[Vec<u8>]) {
    page[0] = 1;
    page[8..10].copy_from_slice(&u16::try_from(rows.len()).unwrap_or_default().to_le_bytes());
    let mut start = PAGE_BYTES;
    for (index, row) in rows.iter().enumerate() {
        start -= row.len();
        page[10 + 2 * index..12 + 2 * index]
            .copy_from_slice(&u16::try_from(start).unwrap_or_default().to_le_bytes());
        page[start..start + row.len()].copy_from_slice(row);
    }
}

fn write_tdef(page: &mut [u8], owned_row: u8) {
    page[0] = 2;
    page[35..39].copy_from_slice(&[owned_row, 2, 0, 0]);
    page[39..43].copy_from_slice(&[3, 2, 0, 0]);
}

fn database_bytes(self_name: &[u8], user_id: u32, user_kind: u16, duplicate_root: bool) -> Vec<u8> {
    let page_count = if duplicate_root { 6 } else { 5 };
    let mut bytes = vec![0_u8; page_count * PAGE_BYTES];
    bytes[4..19].copy_from_slice(b"Standard Jet DB");
    bytes[0x41] = 0x4e;
    bytes[0x42..0x50].copy_from_slice(&[
        0x86, 0xfb, 0xec, 0x37, 0x5d, 0x44, 0x9c, 0xfa, 0xc6, 0x5e, 0x28, 0xe6, 0x13, 0xb6,
    ]);
    write_tdef(&mut bytes[PAGE_BYTES..2 * PAGE_BYTES], 0);
    write_tdef(&mut bytes[4 * PAGE_BYTES..5 * PAGE_BYTES], 2);

    let mut maps = vec![
        vec![0, 0, 0, 0, 0, 1 << 3],
        vec![0, 0, 0, 0, 0],
        vec![0, 0, 0, 0, 0, if duplicate_root { 1 << 5 } else { 0 }],
        vec![0, 0, 0, 0, 0],
    ];
    write_rows(&mut bytes[2 * PAGE_BYTES..3 * PAGE_BYTES], &maps);
    maps.clear();

    let catalog_rows = vec![
        record(1, 1, 0x8000_0000, self_name),
        record(user_id, user_kind, 0, b"Caf\xe9_Euro\x80"),
    ];
    write_rows(&mut bytes[3 * PAGE_BYTES..4 * PAGE_BYTES], &catalog_rows);
    if duplicate_root {
        write_rows(
            &mut bytes[5 * PAGE_BYTES..6 * PAGE_BYTES],
            &[record(4, 1, 0x8000_0000, b"MSysObjects")],
        );
    }
    bytes
}

fn operation(bytes: &[u8]) -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::new(ReadLimits::new(
        ByteCount::new(bytes.len() as u64),
        JET3_PAGE_SIZE,
        ByteCount::new(u64::MAX),
    )))
}

fn open<'a>(
    bytes: &'a [u8],
    resources: &mut ResourceBudget,
) -> Result<DatabaseReader<SliceSource<'a>>, Box<dyn std::error::Error>> {
    let source = SliceSource::new(bytes, resources.read_budget())?;
    Ok(DatabaseReader::from_source(source, resources)?)
}

#[test]
fn discovers_root_and_streams_lossless_records() -> Result<(), Box<dyn std::error::Error>> {
    let bytes = database_bytes(b"MSysObjects", 4, 1, false);
    let mut resources = operation(&bytes);
    let mut database = open(&bytes, &mut resources)?;
    let mut catalog = database.catalog(&mut resources)?;
    assert_eq!(catalog.root().get(), 1);

    let system = catalog.next_record()?.ok_or("missing system record")?;
    assert_eq!(system.id().get(), 1);
    assert_eq!(system.kind(), CatalogObjectKind::Table);
    assert_eq!(system.class(), CatalogObjectClass::System);
    assert_eq!(system.name().decoded_ascii(), Some("MSysObjects"));
    assert_eq!(system.table_definition().map(|page| page.get()), Some(1));

    let user = catalog.next_record()?.ok_or("missing user record")?;
    assert_eq!(user.id().get(), 4);
    assert_eq!(user.class(), CatalogObjectClass::User);
    assert_eq!(user.name().raw_bytes(), b"Caf\xe9_Euro\x80");
    assert_eq!(user.table_definition().map(|page| page.get()), Some(4));
    assert!(catalog.next_record()?.is_none());
    let reads = catalog.owned.budget_mut().read_budget().total_read();
    let work = catalog.owned.budget_mut().total_work_units();
    assert!(catalog.next_record()?.is_none());
    assert_eq!(catalog.owned.budget_mut().read_budget().total_read(), reads);
    assert_eq!(catalog.owned.budget_mut().total_work_units(), work);
    Ok(())
}

#[test]
fn unknown_kinds_are_yielded_without_a_table_reference() -> Result<(), Box<dyn std::error::Error>> {
    let bytes = database_bytes(b"MSysObjects", 0xfeed_beef, 0x1234, false);
    let mut resources = operation(&bytes);
    let mut database = open(&bytes, &mut resources)?;
    let mut catalog = database.catalog(&mut resources)?;
    let _ = catalog.next_record()?.ok_or("missing system record")?;
    let unknown = catalog.next_record()?.ok_or("missing unknown record")?;
    assert_eq!(unknown.kind(), CatalogObjectKind::Unknown(0x1234));
    assert_eq!(unknown.kind().raw(), 0x1234);
    assert_eq!(unknown.table_definition(), None);
    Ok(())
}

#[test]
fn root_discovery_rejects_zero_and_multiple_matches() -> Result<(), Box<dyn std::error::Error>> {
    let bytes = database_bytes(b"NotTheCatalog", 4, 1, false);
    let mut resources = operation(&bytes);
    let mut database = open(&bytes, &mut resources)?;
    assert!(matches!(
        database.catalog(&mut resources),
        Err(CatalogError::RootNotFound)
    ));

    let bytes = database_bytes(b"MSysObjects", 4, 1, true);
    let mut resources = operation(&bytes);
    let mut database = open(&bytes, &mut resources)?;
    assert!(matches!(
        database.catalog(&mut resources),
        Err(CatalogError::DuplicateRoot { .. })
    ));

    let mut bytes = database_bytes(b"NotTheCatalog", 4, 1, false);
    bytes[PAGE_BYTES] = 0;
    bytes[4 * PAGE_BYTES] = 0;
    let page_count = u64::try_from(bytes.len() / PAGE_BYTES)?;
    let limits = ResourceLimits::new(ReadLimits::new(
        ByteCount::new(bytes.len() as u64),
        JET3_PAGE_SIZE,
        ByteCount::new(u64::MAX),
    ))
    .with_max_item_work(page_count);
    let mut exact = ResourceBudget::new(limits);
    let mut database = open(&bytes, &mut exact)?;
    let result = database.catalog(&mut exact);
    assert!(
        matches!(result, Err(CatalogError::RootNotFound)),
        "{result:?}"
    );
    assert_eq!(exact.item_work(), page_count);

    let limits = ResourceLimits::new(ReadLimits::new(
        ByteCount::new(bytes.len() as u64),
        JET3_PAGE_SIZE,
        ByteCount::new(u64::MAX),
    ))
    .with_max_item_work(page_count - 1);
    let mut one_below = ResourceBudget::new(limits);
    let mut database = open(&bytes, &mut one_below)?;
    assert!(matches!(
        database.catalog(&mut one_below),
        Err(CatalogError::Resource(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::ItemWork,
            requested,
            maximum,
        })) if requested == page_count && maximum == page_count - 1
    ));
    assert_eq!(one_below.item_work(), 0);
    Ok(())
}

#[test]
fn discovery_skips_unadmitted_tag_two_candidates() -> Result<(), Box<dyn std::error::Error>> {
    let mut bytes = database_bytes(b"MSysObjects", 4, 1, true);
    write_rows(
        &mut bytes[5 * PAGE_BYTES..6 * PAGE_BYTES],
        &[record(9, 1, 0x8000_0000, b"NotTheCatalog")],
    );
    bytes[5 * PAGE_BYTES] = 2;
    bytes[5 * PAGE_BYTES + 35..5 * PAGE_BYTES + 39].copy_from_slice(&[0, 0xff, 0xff, 0xff]);
    let mut resources = operation(&bytes);
    let mut database = open(&bytes, &mut resources)?;
    let catalog = database.catalog(&mut resources)?;
    assert_eq!(catalog.root().get(), 1);
    Ok(())
}

#[test]
fn duplicate_ids_exhaust_the_cursor() -> Result<(), Box<dyn std::error::Error>> {
    let bytes = database_bytes(b"MSysObjects", 1, 1, false);
    let mut resources = operation(&bytes);
    let mut database = open(&bytes, &mut resources)?;
    let mut catalog = database.catalog(&mut resources)?;
    let _ = catalog.next_record()?.ok_or("missing first record")?;
    assert!(matches!(
        catalog.next_record(),
        Err(CatalogError::DuplicateObjectId { .. })
    ));
    assert!(catalog.next_record()?.is_none());
    Ok(())
}

#[test]
fn bad_table_references_are_distinguished() -> Result<(), Box<dyn std::error::Error>> {
    let bytes = database_bytes(b"MSysObjects", 5, 1, false);
    let mut resources = operation(&bytes);
    let mut database = open(&bytes, &mut resources)?;
    let mut catalog = database.catalog(&mut resources)?;
    let _ = catalog.next_record()?.ok_or("missing first record")?;
    assert!(matches!(
        catalog.next_record(),
        Err(CatalogError::InvalidTableDefinitionReference { .. })
    ));

    let bytes = database_bytes(b"MSysObjects", 3, 1, false);
    let mut resources = operation(&bytes);
    let mut database = open(&bytes, &mut resources)?;
    let mut catalog = database.catalog(&mut resources)?;
    let _ = catalog.next_record()?.ok_or("missing first record")?;
    assert!(matches!(
        catalog.next_record(),
        Err(CatalogError::UnexpectedTableDefinitionReference { .. })
    ));
    Ok(())
}

#[test]
fn catalog_errors_expose_context_and_nested_sources() -> Result<(), Box<dyn std::error::Error>> {
    let bytes = database_bytes(b"MSysObjects", 4, 1, false);
    let mut resources = operation(&bytes);
    let mut database = open(&bytes, &mut resources)?;
    let id = database
        .catalog(&mut resources)?
        .next_record()?
        .ok_or("missing catalog record")?
        .id();

    let plain = [
        CatalogError::UnexpectedOwnedPageKind {
            page: PageNumber::new(3),
            actual: PageKind::LeafIndex,
        },
        CatalogError::RootNotFound,
        CatalogError::DuplicateRoot {
            first: PageNumber::new(1),
            duplicate: PageNumber::new(2),
        },
        CatalogError::DuplicateObjectId { id },
        CatalogError::UnexpectedTableDefinitionReference {
            id,
            page: PageNumber::new(3),
        },
    ];
    for error in plain {
        assert!(!error.to_string().is_empty());
        assert!(error.source().is_none());
    }

    let nested = [
        CatalogError::Record(CatalogRecordError::RecordTooShort {
            length: 1,
            minimum: 37,
        }),
        CatalogError::InvalidTableDefinitionReference {
            id,
            page: PageNumber::new(9),
            source: Error::Arithmetic {
                operation: "test invalid catalog reference",
            },
        },
        CatalogError::Resource(Error::Arithmetic {
            operation: "test catalog resource",
        }),
    ];
    for error in nested {
        assert!(!error.to_string().is_empty());
        assert!(error.source().is_some());
    }
    Ok(())
}
