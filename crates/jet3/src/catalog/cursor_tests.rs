use super::cursor::CatalogError;
use crate::{
    ByteCount, CatalogObjectClass, CatalogObjectKind, CatalogRecord, DatabaseReader, Error,
    JET3_PAGE_SIZE, PAGE_BYTES, ReadLimits, ResourceBudget, ResourceLimitKind, ResourceLimits,
    RowDirectoryError, RowError, SliceSource,
};

use crate::testkit::TestResult;

pub(super) fn record(id: u32, kind: u16, flags: u32, name: &[u8]) -> Vec<u8> {
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

pub(super) fn write_rows(page: &mut [u8], rows: &[Vec<u8>]) {
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

pub(super) fn database_bytes(
    self_name: &[u8],
    user_id: u32,
    user_kind: u16,
    duplicate_root: bool,
) -> Vec<u8> {
    let page_count = if duplicate_root { 6 } else { 5 };
    let mut bytes = crate::testkit::database_image(page_count);
    write_tdef(&mut bytes[PAGE_BYTES..2 * PAGE_BYTES], 0);
    write_tdef(&mut bytes[4 * PAGE_BYTES..5 * PAGE_BYTES], 2);

    let maps = vec![
        vec![0, 0, 0, 0, 0, 1 << 3],
        vec![0, 0, 0, 0, 0],
        vec![0, 0, 0, 0, 0, if duplicate_root { 1 << 5 } else { 0 }],
        vec![0, 0, 0, 0, 0],
    ];
    write_rows(&mut bytes[2 * PAGE_BYTES..3 * PAGE_BYTES], &maps);

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

pub(super) fn operation(bytes: &[u8]) -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::new(ReadLimits::new(
        ByteCount::new(bytes.len() as u64),
        JET3_PAGE_SIZE,
        ByteCount::new(u64::MAX),
    )))
}

pub(super) fn open<'a>(
    bytes: &'a [u8],
    resources: &mut ResourceBudget,
) -> Result<DatabaseReader<SliceSource<'a>>, Box<dyn std::error::Error>> {
    let source = SliceSource::new(bytes, resources.read_budget())?;
    Ok(DatabaseReader::from_source(source, resources)?)
}

/// Streams past the self record and returns the next item; a failed cursor
/// must stay exhausted.
fn second_record(bytes: &[u8]) -> TestResult<Result<Option<CatalogRecord>, CatalogError>> {
    let mut resources = operation(bytes);
    let mut database = open(bytes, &mut resources)?;
    let mut catalog = database.catalog(&mut resources)?;
    catalog.next_record()?.ok_or("missing self record")?;
    let result = catalog.next_record();
    if result.is_err() {
        assert!(catalog.next_record()?.is_none());
    }
    Ok(result)
}

fn catalog_error(bytes: &[u8]) -> TestResult<CatalogError> {
    let mut resources = operation(bytes);
    let mut database = open(bytes, &mut resources)?;
    Ok(database
        .catalog(&mut resources)
        .err()
        .ok_or("catalog opened")?)
}

#[test]
fn discovers_root_and_streams_lossless_records() -> TestResult {
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

    let unknown = second_record(&database_bytes(b"MSysObjects", 0xfeed_beef, 0x1234, false))??
        .ok_or("missing unknown record")?;
    assert_eq!(unknown.kind(), CatalogObjectKind::Unknown(0x1234));
    assert_eq!(unknown.kind().raw(), 0x1234);
    assert_eq!(unknown.table_definition(), None);
    Ok(())
}

#[test]
fn root_discovery_rejects_zero_and_multiple_matches() -> TestResult {
    assert!(matches!(
        catalog_error(&database_bytes(b"NotTheCatalog", 4, 1, false))?,
        CatalogError::RootNotFound
    ));
    assert!(matches!(
        catalog_error(&database_bytes(b"MSysObjects", 4, 1, true))?,
        CatalogError::DuplicateRoot { .. }
    ));

    let mut bytes = database_bytes(b"NotTheCatalog", 4, 1, false);
    bytes[PAGE_BYTES] = 0;
    bytes[4 * PAGE_BYTES] = 0;
    let page_count = u64::try_from(bytes.len() / PAGE_BYTES)?;
    let limits = operation(&bytes).limits();
    let mut exact = ResourceBudget::new(limits.with_max_item_work(page_count));
    let mut database = open(&bytes, &mut exact)?;
    let result = database.catalog(&mut exact);
    assert!(
        matches!(result, Err(CatalogError::RootNotFound)),
        "{result:?}"
    );
    assert_eq!(exact.item_work(), page_count);

    let mut one_below = ResourceBudget::new(limits.with_max_item_work(page_count - 1));
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
fn discovery_skips_unadmitted_tag_two_candidates() -> TestResult {
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
fn duplicate_ids_and_bad_table_references_exhaust_the_cursor() -> TestResult {
    assert!(matches!(
        second_record(&database_bytes(b"MSysObjects", 1, 1, false))?,
        Err(CatalogError::DuplicateObjectId { .. })
    ));
    assert!(matches!(
        second_record(&database_bytes(b"MSysObjects", 5, 1, false))?,
        Err(CatalogError::InvalidTableDefinitionReference { .. })
    ));
    assert!(matches!(
        second_record(&database_bytes(b"MSysObjects", 3, 1, false))?,
        Err(CatalogError::UnexpectedTableDefinitionReference { .. })
    ));
    Ok(())
}

fn overflow_bytes(move_self: bool) -> Vec<u8> {
    let mut bytes = database_bytes(b"MSysObjects", 4, 1, false);
    bytes.resize(6 * PAGE_BYTES, 0);
    let moved = if move_self {
        record(1, 1, 0x8000_0000, b"MSysObjects")
    } else {
        record(4, 1, 0, b"MovedTable")
    };
    let rows = if move_self {
        vec![vec![0, 5, 0, 0], record(4, 1, 0, b"OrdinaryTable")]
    } else {
        vec![record(1, 1, 0x8000_0000, b"MSysObjects"), vec![0, 5, 0, 0]]
    };
    write_rows(&mut bytes[3 * PAGE_BYTES..4 * PAGE_BYTES], &rows);
    let slot = usize::from(!move_self);
    bytes[3 * PAGE_BYTES + 11 + 2 * slot] |= 0x40;
    write_rows(&mut bytes[5 * PAGE_BYTES..6 * PAGE_BYTES], &[moved]);
    bytes[5 * PAGE_BYTES + 11] |= 0x80;
    bytes[5 * PAGE_BYTES + 4..5 * PAGE_BYTES + 8].copy_from_slice(&1_u32.to_le_bytes());
    // Catalog owns the target page too; hidden storage must not be emitted twice.
    bytes[3 * PAGE_BYTES - 1] |= 1 << 5;
    bytes
}

#[test]
fn moved_catalog_record_and_moved_self_record_stream_once() -> Result<(), Box<dyn std::error::Error>>
{
    for move_self in [false, true] {
        let bytes = overflow_bytes(move_self);
        let mut budget = operation(&bytes);
        let mut database = open(&bytes, &mut budget)?;
        let mut catalog = database.catalog(&mut budget)?;
        assert_eq!(catalog.next_record()?.ok_or("missing self")?.id().get(), 1);
        let user = catalog.next_record()?.ok_or("missing user")?;
        assert_eq!(user.id().get(), 4);
        assert_eq!(
            user.name().decoded_ascii(),
            Some(if move_self {
                "OrdinaryTable"
            } else {
                "MovedTable"
            })
        );
        assert!(catalog.next_record()?.is_none());
    }
    Ok(())
}

#[test]
fn malformed_catalog_overflow_exhausts_cursor() -> TestResult {
    for case in 0..9 {
        let mut bytes = overflow_bytes(false);
        match case {
            0 => bytes[5 * PAGE_BYTES + 4] = 4,
            1 => bytes[5 * PAGE_BYTES + 11] &= !0x80,
            2 | 8 => {}
            3 => bytes[5 * PAGE_BYTES] = 3,
            4 => bytes[5 * PAGE_BYTES + 10..5 * PAGE_BYTES + 12]
                .copy_from_slice(&0xc800_u16.to_le_bytes()),
            5 | 6 => {
                write_rows(
                    &mut bytes[5 * PAGE_BYTES..6 * PAGE_BYTES],
                    &[vec![0, 5, 0, 0]],
                );
                bytes[5 * PAGE_BYTES + 11] |= 0xc0;
                if case == 6 {
                    bytes[6 * PAGE_BYTES - 4..6 * PAGE_BYTES].copy_from_slice(&[1, 3, 0, 0]);
                }
            }
            7 => bytes[3 * PAGE_BYTES + 12] += 1,
            _ => unreachable!(),
        }
        if case == 2 || case == 8 {
            let start = usize::from(
                u16::from_le_bytes([bytes[3 * PAGE_BYTES + 12], bytes[3 * PAGE_BYTES + 13]])
                    & 0x1fff,
            );
            bytes[3 * PAGE_BYTES + start + usize::from(case == 8)] = 9;
        }
        let mut budget = operation(&bytes);
        let mut database = open(&bytes, &mut budget)?;
        let mut catalog = database.catalog(&mut budget)?;
        catalog.next_record()?.ok_or("missing self")?;
        let error = catalog
            .next_record()
            .err()
            .ok_or("must reject overflow corruption")?;
        match (case, &error) {
            (
                0,
                CatalogError::Overflow(RowError::Directory(RowDirectoryError::UnexpectedOwner {
                    ..
                })),
            )
            | (1 | 4, CatalogError::Overflow(RowError::InvalidOverflowTarget { .. }))
            | (
                2,
                CatalogError::Overflow(RowError::Directory(RowDirectoryError::MissingRow {
                    ..
                })),
            )
            | (3, CatalogError::Overflow(RowError::UnexpectedOwnedPageKind { .. }))
            | (5, CatalogError::Overflow(RowError::SelfLink { .. }))
            | (7, CatalogError::InvalidOverflowPointerLength { .. })
            | (8, CatalogError::Overflow(RowError::Allocation(_)))
            | (6, CatalogError::Overflow(RowError::Cycle { .. })) => {}
            _ => return Err(format!("case {case}: {error:?}").into()),
        }
        let work = catalog.budget_mut().total_work_units();
        assert!(catalog.next_record()?.is_none());
        assert_eq!(catalog.budget_mut().total_work_units(), work);
    }
    Ok(())
}

#[test]
fn catalog_overflow_depth_limit_is_not_swallowed_during_discovery() -> TestResult {
    for maximum in [0, 1] {
        let bytes = overflow_bytes(true);
        let mut budget =
            ResourceBudget::new(operation(&bytes).limits().with_max_chain_depth(maximum));
        let mut database = open(&bytes, &mut budget)?;
        let result = database.catalog(&mut budget);
        if maximum == 0 {
            assert!(matches!(
                result,
                Err(CatalogError::Overflow(RowError::Resource(
                    Error::ResourceLimitExceeded {
                        kind: ResourceLimitKind::ChainDepth,
                        ..
                    }
                )))
            ));
        } else {
            assert!(result.is_ok());
        }
    }
    Ok(())
}

#[test]
fn catalog_source_slot_is_not_limited_by_the_target_locator_width() -> TestResult {
    for row in [255, 256, 1000] {
        let mut bytes = overflow_bytes(true);
        let mut rows = vec![Vec::new(); row];
        rows.push(vec![0, 5, 0, 0]);
        write_rows(&mut bytes[3 * PAGE_BYTES..4 * PAGE_BYTES], &rows);
        for slot in 0..row {
            bytes[3 * PAGE_BYTES + 11 + 2 * slot] |= 0xc0;
        }
        bytes[3 * PAGE_BYTES + 11 + 2 * row] |= 0x40;
        let mut budget = operation(&bytes);
        let mut database = open(&bytes, &mut budget)?;
        let mut catalog = database.catalog(&mut budget)?;
        assert_eq!(
            catalog
                .next_record()?
                .ok_or("missing moved self")?
                .id()
                .get(),
            1
        );
        assert!(catalog.next_record()?.is_none());
    }
    Ok(())
}
