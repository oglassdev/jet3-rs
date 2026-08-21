use super::{
    AllocationTraversalError, MapPageWalker, UnsupportedTraversalStep, VisitedPages,
    locate_allocation_map,
};
use crate::{
    AllocationMap, ByteCount, DatabasePageError, DatabaseReader, Error, JET3_PAGE_SIZE,
    PageGeometry, PageKind, PageNumber, ReadLimits, ResourceBudget, ResourceLimitKind,
    ResourceLimits, SliceSource, decode_allocation_map,
};

type TestResult = Result<(), Box<dyn std::error::Error>>;

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const EXTENDED_TAG: u8 = 0x05;
const DATA_TAG: u8 = 0x01;

fn limits() -> ResourceLimits {
    ResourceLimits::new(ReadLimits::default())
}

fn budget() -> ResourceBudget {
    ResourceBudget::new(limits())
}

/// Page zero carries the generic signature; every other page gets `tags[n]`
/// at byte zero and its page number as the first bitmap byte so reached
/// pages are distinguishable.
fn database_bytes(tags: &[u8]) -> Vec<u8> {
    let mut bytes = vec![0_u8; (tags.len() + 1) * PAGE_BYTES];
    bytes[4..19].copy_from_slice(b"Standard Jet DB");
    for (offset, tag) in tags.iter().enumerate() {
        let start = (offset + 1) * PAGE_BYTES;
        bytes[start] = *tag;
        bytes[start + 4] = u8::try_from(offset + 1).unwrap_or(u8::MAX);
    }
    bytes
}

fn indirect_record(references: &[u32]) -> Vec<u8> {
    let mut record = vec![0x01];
    for reference in references {
        record.extend_from_slice(&reference.to_le_bytes());
    }
    record
}

fn open<'a>(
    bytes: &'a [u8],
    budget: &mut ResourceBudget,
) -> Result<DatabaseReader<SliceSource<'a>>, Box<dyn std::error::Error>> {
    let source = SliceSource::new(bytes, budget.read_budget())?;
    Ok(DatabaseReader::from_source(source, budget)?)
}

fn walker<'r>(
    record: &'r [u8],
    geometry: PageGeometry,
    budget: &mut ResourceBudget,
) -> Result<MapPageWalker<'r>, Box<dyn std::error::Error>> {
    let AllocationMap::Indirect(map) = decode_allocation_map(record, budget)? else {
        return Err("expected an indirect record".into());
    };
    Ok(MapPageWalker::new(map, geometry, budget)?)
}

#[test]
fn map_location_is_unsupported_and_charges_one_work_unit() -> TestResult {
    let bytes = database_bytes(&[]);
    let mut resources = budget();
    let database = open(&bytes, &mut resources)?;
    let before = resources.total_work_units();
    assert_eq!(
        locate_allocation_map(&database, &mut resources).err(),
        Some(AllocationTraversalError::Unsupported(
            UnsupportedTraversalStep::MapLocation
        ))
    );
    assert_eq!(resources.total_work_units(), before + 1);
    Ok(())
}

#[test]
fn follows_distinct_references_with_exact_accounting() -> TestResult {
    let bytes = database_bytes(&[EXTENDED_TAG, EXTENDED_TAG, EXTENDED_TAG]);
    let record = indirect_record(&[3, 1]);
    let mut resources = budget();
    let mut database = open(&bytes, &mut resources)?;
    let visits = resources.page_visits();
    let mut walk = walker(&record, database.geometry(), &mut resources)?;
    assert_eq!(resources.allocation_bytes(), ByteCount::new(1));

    let mut page = [0_u8; PAGE_BYTES];
    let mut reached = walk
        .next_map_page(&mut database, &mut page, &mut resources)?
        .ok_or("expected page 3")?;
    assert_eq!(reached.page(), PageNumber::new(3));
    assert_eq!(reached.reference_index(), 0);
    assert_eq!(reached.relative_bits().next_bit(&mut resources)?, Some(0));
    assert_eq!(reached.relative_bits().next_bit(&mut resources)?, Some(1));
    assert_eq!(
        reached.absolute_page(0),
        Err(AllocationTraversalError::Unsupported(
            UnsupportedTraversalStep::ExtendedPageBase
        ))
    );
    assert_eq!(walk.depth(), 1);

    let reached = walk
        .next_map_page(&mut database, &mut page, &mut resources)?
        .ok_or("expected page 1")?;
    assert_eq!(reached.page(), PageNumber::new(1));
    assert_eq!(reached.reference_index(), 1);
    assert!(
        walk.next_map_page(&mut database, &mut page, &mut resources)?
            .is_none()
    );
    assert_eq!(walk.depth(), 2);
    assert_eq!(resources.page_visits(), visits + 2);
    Ok(())
}

#[test]
fn zero_length_chain_yields_nothing() -> TestResult {
    let bytes = database_bytes(&[EXTENDED_TAG]);
    let record = indirect_record(&[]);
    let mut resources = budget();
    let mut database = open(&bytes, &mut resources)?;
    let mut walk = walker(&record, database.geometry(), &mut resources)?;
    let mut page = [0_u8; PAGE_BYTES];
    let visits = resources.page_visits();
    assert!(
        walk.next_map_page(&mut database, &mut page, &mut resources)?
            .is_none()
    );
    assert_eq!(walk.depth(), 0);
    assert_eq!(resources.page_visits(), visits);
    Ok(())
}

#[test]
fn repeated_reference_is_charged_then_rejected_as_cycle() -> TestResult {
    let bytes = database_bytes(&[EXTENDED_TAG, EXTENDED_TAG]);
    let record = indirect_record(&[2, 2, 1]);
    let mut resources = budget();
    let mut database = open(&bytes, &mut resources)?;
    let mut walk = walker(&record, database.geometry(), &mut resources)?;
    let mut page = [0_u8; PAGE_BYTES];
    walk.next_map_page(&mut database, &mut page, &mut resources)?;
    let visits = resources.page_visits();
    assert_eq!(
        walk.next_map_page(&mut database, &mut page, &mut resources)
            .err(),
        Some(AllocationTraversalError::RepeatedMapPage {
            page: PageNumber::new(2),
            index: 1,
        })
    );
    assert_eq!(resources.page_visits(), visits + 1);
    assert!(
        walk.next_map_page(&mut database, &mut page, &mut resources)?
            .is_none(),
        "a cycle exhausts the walker"
    );
    assert_eq!(walk.depth(), 1);
    Ok(())
}

#[test]
fn invalid_reference_is_structured_and_not_read() -> TestResult {
    let bytes = database_bytes(&[EXTENDED_TAG]);
    let record = indirect_record(&[2]);
    let mut resources = budget();
    let mut database = open(&bytes, &mut resources)?;
    let mut walk = walker(&record, database.geometry(), &mut resources)?;
    let mut page = [0_u8; PAGE_BYTES];
    let visits = resources.page_visits();
    assert_eq!(
        walk.next_map_page(&mut database, &mut page, &mut resources)
            .err(),
        Some(AllocationTraversalError::InvalidReference {
            index: 0,
            reference: 2,
            source: Error::PageOutOfBounds {
                page: 2,
                page_count: 2,
            },
        })
    );
    assert_eq!(resources.page_visits(), visits);
    Ok(())
}

#[test]
fn zero_reference_is_unsupported_rather_than_interpreted() -> TestResult {
    let bytes = database_bytes(&[EXTENDED_TAG]);
    let record = indirect_record(&[0]);
    let mut resources = budget();
    let mut database = open(&bytes, &mut resources)?;
    let mut walk = walker(&record, database.geometry(), &mut resources)?;
    let mut page = [0_u8; PAGE_BYTES];
    assert_eq!(
        walk.next_map_page(&mut database, &mut page, &mut resources)
            .err(),
        Some(AllocationTraversalError::Unsupported(
            UnsupportedTraversalStep::ZeroReference
        ))
    );
    Ok(())
}

#[test]
fn non_bitmap_page_is_rejected_losslessly() -> TestResult {
    let bytes = database_bytes(&[DATA_TAG, 0x7f]);
    let record = indirect_record(&[1]);
    let mut resources = budget();
    let mut database = open(&bytes, &mut resources)?;
    let mut walk = walker(&record, database.geometry(), &mut resources)?;
    let mut page = [0_u8; PAGE_BYTES];
    assert_eq!(
        walk.next_map_page(&mut database, &mut page, &mut resources)
            .err(),
        Some(AllocationTraversalError::ExpectedExtendedUsageBitmap {
            page: PageNumber::new(1),
            actual: PageKind::Data,
        })
    );

    let record = indirect_record(&[2]);
    let mut walk = walker(&record, database.geometry(), &mut resources)?;
    assert_eq!(
        walk.next_map_page(&mut database, &mut page, &mut resources)
            .err(),
        Some(AllocationTraversalError::ExpectedExtendedUsageBitmap {
            page: PageNumber::new(2),
            actual: PageKind::Unknown(0x7f),
        })
    );
    Ok(())
}

#[test]
fn chain_depth_limit_is_exact() -> TestResult {
    let bytes = database_bytes(&[EXTENDED_TAG, EXTENDED_TAG]);
    let record = indirect_record(&[1, 2]);
    let mut resources = ResourceBudget::new(limits().with_max_chain_depth(1));
    let mut database = open(&bytes, &mut resources)?;
    let mut walk = walker(&record, database.geometry(), &mut resources)?;
    let mut page = [0_u8; PAGE_BYTES];
    assert!(
        walk.next_map_page(&mut database, &mut page, &mut resources)?
            .is_some()
    );
    let visits = resources.page_visits();
    assert!(matches!(
        walk.next_map_page(&mut database, &mut page, &mut resources),
        Err(AllocationTraversalError::Resource(
            Error::ResourceLimitExceeded {
                kind: ResourceLimitKind::ChainDepth,
                ..
            }
        ))
    ));
    assert_eq!(
        resources.page_visits(),
        visits,
        "depth is checked before any read"
    );
    Ok(())
}

#[test]
fn visited_set_is_charged_before_allocation_and_fails_closed() -> TestResult {
    let bytes = database_bytes(&[EXTENDED_TAG; 9]);
    let record = indirect_record(&[1]);
    let mut resources = ResourceBudget::new(limits().with_max_allocation_bytes(ByteCount::new(1)));
    let database = open(&bytes, &mut resources)?;
    assert_eq!(database.geometry().page_count(), 10);
    assert!(matches!(
        walker(&record, database.geometry(), &mut resources),
        Err(error) if error.downcast_ref::<AllocationTraversalError>().is_some_and(|error| matches!(
            error,
            AllocationTraversalError::Resource(Error::ResourceLimitExceeded {
                kind: ResourceLimitKind::AllocationBytes,
                ..
            })
        ))
    ));
    assert_eq!(resources.allocation_bytes(), ByteCount::new(0));

    let mut resources = ResourceBudget::new(limits().with_max_allocation_bytes(ByteCount::new(2)));
    let database = open(&bytes, &mut resources)?;
    walker(&record, database.geometry(), &mut resources)?;
    assert_eq!(resources.allocation_bytes(), ByteCount::new(2));
    Ok(())
}

#[test]
fn page_visit_exhaustion_is_a_retryable_resource_error() -> TestResult {
    let bytes = database_bytes(&[EXTENDED_TAG]);
    let record = indirect_record(&[1]);
    let mut resources = ResourceBudget::new(limits().with_max_page_visits(1));
    let mut database = open(&bytes, &mut resources)?;
    let mut walk = walker(&record, database.geometry(), &mut resources)?;
    let mut page = [0_u8; PAGE_BYTES];
    assert!(matches!(
        walk.next_map_page(&mut database, &mut page, &mut resources),
        Err(AllocationTraversalError::Page(DatabasePageError::Read(
            Error::ResourceLimitExceeded {
                kind: ResourceLimitKind::PageVisits,
                ..
            }
        )))
    ));
    assert_eq!(walk.depth(), 0);
    Ok(())
}

#[test]
fn visited_pages_rejects_out_of_range_without_growing() -> TestResult {
    let geometry = PageGeometry::new(ByteCount::new(3 * JET3_PAGE_SIZE.get()), JET3_PAGE_SIZE)?;
    let mut resources = budget();
    let mut visited = VisitedPages::new(geometry, &mut resources)?;
    assert!(!visited.contains(PageNumber::new(2)));
    assert!(!visited.insert(PageNumber::new(2))?);
    assert!(visited.insert(PageNumber::new(2))?);
    assert!(visited.contains(PageNumber::new(2)));
    assert!(!visited.contains(PageNumber::new(3)));
    assert_eq!(
        visited.insert(PageNumber::new(3)),
        Err(Error::PageOutOfBounds {
            page: 3,
            page_count: 3,
        })
    );
    Ok(())
}
