use super::{
    AllocationTraversalError, PageChainWalker, ReachedMapPage, VisitedPages,
    follow_map_page_reference,
};
use crate::{
    ByteCount, DatabasePageError, DatabaseReader, Error, JET3_PAGE_SIZE, PageGeometry, PageKind,
    PageNumber, ReadLimits, ResourceBudget, ResourceLimitKind, ResourceLimits, SliceSource,
};

type TestResult = Result<(), Box<dyn std::error::Error>>;

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const EXTENDED_TAG: u8 = 0x05;
const DATA_TAG: u8 = 0x01;
const BITMAP: PageKind = PageKind::ExtendedUsageBitmap;

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
    bytes[0x41] = 0x4e;
    bytes[0x42..0x50].copy_from_slice(&[
        0x86, 0xfb, 0xec, 0x37, 0x5d, 0x44, 0x9c, 0xfa, 0xc6, 0x5e, 0x28, 0xe6, 0x13, 0xb6,
    ]);
    for (offset, tag) in tags.iter().enumerate() {
        let start = (offset + 1) * PAGE_BYTES;
        bytes[start] = *tag;
        bytes[start + 4] = u8::try_from(offset + 1).unwrap_or(u8::MAX);
    }
    bytes
}

fn open<'a>(
    bytes: &'a [u8],
    budget: &mut ResourceBudget,
) -> Result<DatabaseReader<SliceSource<'a>>, Box<dyn std::error::Error>> {
    let source = SliceSource::new(bytes, budget.read_budget())?;
    Ok(DatabaseReader::from_source(source, budget)?)
}

#[test]
fn raw_references_decode_null_and_direct_pages_with_exact_work() -> TestResult {
    let mut resources = budget();
    let before = resources.total_work_units();
    assert_eq!(follow_map_page_reference(0, &mut resources)?, None);
    assert_eq!(
        follow_map_page_reference(1, &mut resources)?,
        Some(PageNumber::new(1))
    );
    assert_eq!(
        follow_map_page_reference(u32::MAX, &mut resources)?,
        Some(PageNumber::new(u64::from(u32::MAX)))
    );
    assert_eq!(resources.total_work_units(), before + 3);
    Ok(())
}

#[test]
fn follows_distinct_pages_with_exact_accounting() -> TestResult {
    let bytes = database_bytes(&[EXTENDED_TAG, EXTENDED_TAG, EXTENDED_TAG]);
    let mut resources = budget();
    let mut database = open(&bytes, &mut resources)?;
    let visits = resources.page_visits();
    let work = resources.total_work_units();
    let mut walk = PageChainWalker::new(database.geometry(), &mut resources)?;
    assert_eq!(resources.allocation_bytes(), ByteCount::new(1));

    let mut page = [0_u8; PAGE_BYTES];
    let reached = walk.follow(
        PageNumber::new(3),
        BITMAP,
        &mut database,
        &mut page,
        &mut resources,
    )?;
    let mut reached = ReachedMapPage::new(1, reached)?;
    assert_eq!(reached.page(), PageNumber::new(3));
    assert_eq!(reached.relative_bits().next_bit(&mut resources)?, Some(0));
    assert_eq!(reached.relative_bits().next_bit(&mut resources)?, Some(1));
    assert_eq!(reached.slot(), 1);
    assert_eq!(reached.absolute_page(0)?, PageNumber::new(16_352));
    assert_eq!(walk.depth(), 1);
    assert!(walk.followed(PageNumber::new(3)));
    assert!(!walk.followed(PageNumber::new(1)));

    walk.follow(
        PageNumber::new(1),
        BITMAP,
        &mut database,
        &mut page,
        &mut resources,
    )?;
    assert_eq!(walk.depth(), 2);
    assert_eq!(resources.page_visits(), visits + 2);
    // One visited byte, two explicit steps, two page visits, two
    // classifications, and two inspected bits each charge aggregate work.
    assert_eq!(resources.total_work_units(), work + 1 + 2 + 2 + 2 + 2);
    Ok(())
}

#[test]
fn repeated_page_is_charged_then_rejected_without_changing_the_walker() -> TestResult {
    let bytes = database_bytes(&[EXTENDED_TAG, EXTENDED_TAG]);
    let mut resources = budget();
    let mut database = open(&bytes, &mut resources)?;
    let mut walk = PageChainWalker::new(database.geometry(), &mut resources)?;
    let mut page = [0_u8; PAGE_BYTES];
    let two = PageNumber::new(2);
    walk.follow(two, BITMAP, &mut database, &mut page, &mut resources)?;
    let visits = resources.page_visits();
    assert_eq!(
        walk.follow(two, BITMAP, &mut database, &mut page, &mut resources)
            .err(),
        Some(AllocationTraversalError::RepeatedPage { page: two })
    );
    assert_eq!(resources.page_visits(), visits + 1);
    assert_eq!(walk.depth(), 1);
    walk.follow(
        PageNumber::new(1),
        BITMAP,
        &mut database,
        &mut page,
        &mut resources,
    )?;
    assert_eq!(walk.depth(), 2);
    Ok(())
}

#[test]
fn invalid_reference_is_structured_and_not_read() -> TestResult {
    let bytes = database_bytes(&[EXTENDED_TAG]);
    let mut resources = budget();
    let mut database = open(&bytes, &mut resources)?;
    let mut walk = PageChainWalker::new(database.geometry(), &mut resources)?;
    let mut page = [0_u8; PAGE_BYTES];
    let visits = resources.page_visits();
    assert_eq!(
        walk.follow(
            PageNumber::new(2),
            BITMAP,
            &mut database,
            &mut page,
            &mut resources
        )
        .err(),
        Some(AllocationTraversalError::InvalidReference {
            page: PageNumber::new(2),
            source: Error::PageOutOfBounds {
                page: 2,
                page_count: 2,
            },
        })
    );
    assert_eq!(resources.page_visits(), visits);
    assert_eq!(walk.depth(), 0);
    Ok(())
}

#[test]
fn unexpected_kind_is_rejected_losslessly_and_not_marked() -> TestResult {
    let bytes = database_bytes(&[DATA_TAG, 0x7f]);
    let mut resources = budget();
    let mut database = open(&bytes, &mut resources)?;
    let mut walk = PageChainWalker::new(database.geometry(), &mut resources)?;
    let mut page = [0_u8; PAGE_BYTES];
    for (number, actual) in [(1, PageKind::Data), (2, PageKind::Unknown(0x7f))] {
        let number = PageNumber::new(number);
        assert_eq!(
            walk.follow(number, BITMAP, &mut database, &mut page, &mut resources)
                .err(),
            Some(AllocationTraversalError::UnexpectedPageKind {
                page: number,
                expected: BITMAP,
                actual,
            })
        );
        assert!(!walk.followed(number));
    }
    assert_eq!(walk.depth(), 0);
    walk.follow(
        PageNumber::new(1),
        PageKind::Data,
        &mut database,
        &mut page,
        &mut resources,
    )?;
    assert_eq!(walk.depth(), 1);
    Ok(())
}

#[test]
fn chain_depth_limit_is_exact_and_checked_before_any_read() -> TestResult {
    let bytes = database_bytes(&[EXTENDED_TAG, EXTENDED_TAG]);
    let mut resources = ResourceBudget::new(limits().with_max_chain_depth(1));
    let mut database = open(&bytes, &mut resources)?;
    let mut walk = PageChainWalker::new(database.geometry(), &mut resources)?;
    let mut page = [0_u8; PAGE_BYTES];
    walk.follow(
        PageNumber::new(1),
        BITMAP,
        &mut database,
        &mut page,
        &mut resources,
    )?;
    let visits = resources.page_visits();
    assert!(matches!(
        walk.follow(
            PageNumber::new(2),
            BITMAP,
            &mut database,
            &mut page,
            &mut resources
        ),
        Err(AllocationTraversalError::Resource(
            Error::ResourceLimitExceeded {
                kind: ResourceLimitKind::ChainDepth,
                ..
            }
        ))
    ));
    assert_eq!(resources.page_visits(), visits);
    assert_eq!(walk.depth(), 1);
    Ok(())
}

#[test]
fn visited_set_inline_boundary_is_exact_and_precharged() -> TestResult {
    let inline_geometry =
        PageGeometry::new(ByteCount::new(256 * JET3_PAGE_SIZE.get()), JET3_PAGE_SIZE)?;
    let mut resources = ResourceBudget::new(limits().with_max_allocation_bytes(ByteCount::new(32)));
    let mut inline = VisitedPages::new(inline_geometry, &mut resources)?;
    assert!(!inline.insert(PageNumber::new(255))?);
    assert_eq!(resources.allocation_bytes(), ByteCount::new(32));

    let heap_geometry =
        PageGeometry::new(ByteCount::new(257 * JET3_PAGE_SIZE.get()), JET3_PAGE_SIZE)?;
    let mut resources = ResourceBudget::new(limits().with_max_allocation_bytes(ByteCount::new(32)));
    assert!(matches!(
        VisitedPages::new(heap_geometry, &mut resources),
        Err(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::AllocationBytes,
            ..
        })
    ));
    assert_eq!(resources.allocation_bytes(), ByteCount::new(0));

    let mut resources = ResourceBudget::new(limits().with_max_allocation_bytes(ByteCount::new(33)));
    let mut heap = VisitedPages::new(heap_geometry, &mut resources)?;
    assert!(!heap.insert(PageNumber::new(256))?);
    assert_eq!(resources.allocation_bytes(), ByteCount::new(33));
    Ok(())
}

#[test]
fn resource_rejection_leaves_the_walker_retryable() -> TestResult {
    let bytes = database_bytes(&[EXTENDED_TAG]);
    let mut resources = ResourceBudget::new(limits().with_max_page_visits(1));
    let mut database = open(&bytes, &mut resources)?;
    let mut walk = PageChainWalker::new(database.geometry(), &mut resources)?;
    let mut page = [0_u8; PAGE_BYTES];
    let one = PageNumber::new(1);
    assert!(matches!(
        walk.follow(one, BITMAP, &mut database, &mut page, &mut resources),
        Err(AllocationTraversalError::Page(DatabasePageError::Read(
            Error::ResourceLimitExceeded {
                kind: ResourceLimitKind::PageVisits,
                ..
            }
        )))
    ));
    assert_eq!(walk.depth(), 0);
    assert!(!walk.followed(one));

    let mut retry = ResourceBudget::new(limits());
    let mut database = open(&bytes, &mut retry)?;
    walk.follow(one, BITMAP, &mut database, &mut page, &mut retry)?;
    assert_eq!(walk.depth(), 1);
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

fn owned_page_database(page_count: usize, record: &[u8], map_pages: &[usize]) -> Vec<u8> {
    assert!(page_count >= 3);
    assert!(record.len() + 5 < PAGE_BYTES - 14);
    let mut bytes = vec![0_u8; page_count * PAGE_BYTES];
    bytes[4..19].copy_from_slice(b"Standard Jet DB");
    bytes[0x41] = 0x4e;
    bytes[0x42..0x50].copy_from_slice(&[
        0x86, 0xfb, 0xec, 0x37, 0x5d, 0x44, 0x9c, 0xfa, 0xc6, 0x5e, 0x28, 0xe6, 0x13, 0xb6,
    ]);

    let table = PAGE_BYTES;
    bytes[table] = 2;
    bytes[table + 35..table + 39].copy_from_slice(&[0, 2, 0, 0]);
    bytes[table + 39..table + 43].copy_from_slice(&[1, 2, 0, 0]);

    let data = 2 * PAGE_BYTES;
    bytes[data] = 1;
    bytes[data + 8..data + 10].copy_from_slice(&2_u16.to_le_bytes());
    let owned_start = PAGE_BYTES - record.len();
    let available_start = owned_start - 5;
    bytes[data + 10..data + 12]
        .copy_from_slice(&u16::try_from(owned_start).unwrap_or_default().to_le_bytes());
    bytes[data + 12..data + 14].copy_from_slice(
        &u16::try_from(available_start)
            .unwrap_or_default()
            .to_le_bytes(),
    );
    bytes[data + owned_start..data + PAGE_BYTES].copy_from_slice(record);
    bytes[data + available_start..data + owned_start].copy_from_slice(&[0, 0, 0, 0, 0]);
    for &page in map_pages {
        bytes[page * PAGE_BYTES] = EXTENDED_TAG;
    }
    bytes
}

fn indirect_record(references: &[u32]) -> Vec<u8> {
    let mut record = vec![1];
    for reference in references {
        record.extend_from_slice(&reference.to_le_bytes());
    }
    record
}

#[test]
fn extended_base_accepts_last_bit_and_rejects_one_over_and_overflow() -> TestResult {
    let bytes = database_bytes(&[EXTENDED_TAG]);
    let mut resources = budget();
    let mut database = open(&bytes, &mut resources)?;
    let mut raw = [0_u8; PAGE_BYTES];
    let classified = database.read_classified_page(PageNumber::new(1), &mut raw, &mut resources)?;
    let reached = ReachedMapPage::new(1, classified)?;
    assert_eq!(reached.absolute_page(16_351)?, PageNumber::new(32_703));
    assert_eq!(
        reached.absolute_page(16_352),
        Err(AllocationTraversalError::RelativeBitOutOfRange {
            slot: 1,
            bit_index: 16_352,
        })
    );
    let reached = ReachedMapPage::new(u64::MAX, classified)?;
    assert_eq!(
        reached.absolute_page(0),
        Err(AllocationTraversalError::ExtendedPageOverflow {
            slot: u64::MAX,
            bit_index: 0,
        })
    );
    Ok(())
}

#[test]
fn owned_inline_map_accepts_bit_1023_and_rejects_bit_1024() -> TestResult {
    let mut exact_record = vec![0, 0, 0, 0, 0];
    exact_record.resize(5 + 128, 0);
    exact_record[5 + 127] = 0x80;
    let bytes = owned_page_database(1024, &exact_record, &[]);
    let mut resources = budget();
    let mut database = open(&bytes, &mut resources)?;
    let mut pages = database.owned_pages(PageNumber::new(1), &mut resources)?;
    assert_eq!(pages.next_page()?, Some(PageNumber::new(1023)));
    assert_eq!(pages.next_page()?, None);

    let mut one_over_record = vec![0, 0, 0, 0, 0];
    one_over_record.resize(5 + 129, 0);
    one_over_record[5 + 128] = 1;
    let bytes = owned_page_database(1024, &one_over_record, &[]);
    let mut resources = budget();
    let mut database = open(&bytes, &mut resources)?;
    let mut pages = database.owned_pages(PageNumber::new(1), &mut resources)?;
    assert_eq!(
        pages.next_page(),
        Err(AllocationTraversalError::InvalidReference {
            page: PageNumber::new(1024),
            source: Error::PageOutOfBounds {
                page: 1024,
                page_count: 1024,
            },
        })
    );
    Ok(())
}

#[test]
fn owned_indirect_map_follows_each_page_once_with_exact_visits() -> TestResult {
    let record = indirect_record(&[3, 4]);
    let mut bytes = owned_page_database(5, &record, &[3, 4]);
    bytes[3 * PAGE_BYTES + 4] = 1 << 3;
    let mut resources = budget();
    let mut database = open(&bytes, &mut resources)?;
    let visits = resources.page_visits();
    let allocation = resources.allocation_bytes();
    let mut pages = database.owned_pages(PageNumber::new(1), &mut resources)?;
    assert_eq!(pages.next_page()?, Some(PageNumber::new(3)));
    assert_eq!(pages.next_page()?, None);
    drop(pages);
    assert_eq!(resources.page_visits(), visits + 4);
    assert_eq!(
        resources.allocation_bytes(),
        ByteCount::new(allocation.get() + 1)
    );
    Ok(())
}

#[test]
fn zero_slots_end_without_following_a_page() -> TestResult {
    let record = indirect_record(&[0, 0]);
    let bytes = owned_page_database(5, &record, &[]);
    let mut resources = budget();
    let mut database = open(&bytes, &mut resources)?;
    let visits = resources.page_visits();
    let mut pages = database.owned_pages(PageNumber::new(1), &mut resources)?;
    assert_eq!(pages.next_page()?, None);
    drop(pages);
    assert_eq!(resources.page_visits(), visits + 2);
    Ok(())
}

#[test]
fn self_reference_is_rejected_before_following() -> TestResult {
    let record = indirect_record(&[2]);
    let bytes = owned_page_database(5, &record, &[]);
    let mut resources = budget();
    let mut database = open(&bytes, &mut resources)?;
    let mut pages = database.owned_pages(PageNumber::new(1), &mut resources)?;
    assert_eq!(
        pages.next_page(),
        Err(AllocationTraversalError::SelfReference {
            record_page: PageNumber::new(2),
        })
    );
    Ok(())
}

#[test]
fn duplicate_reference_is_a_cycle() -> TestResult {
    let record = indirect_record(&[3, 3]);
    let bytes = owned_page_database(5, &record, &[3]);
    let mut resources = budget();
    let mut database = open(&bytes, &mut resources)?;
    let mut pages = database.owned_pages(PageNumber::new(1), &mut resources)?;
    assert_eq!(
        pages.next_page(),
        Err(AllocationTraversalError::RepeatedPage {
            page: PageNumber::new(3),
        })
    );
    Ok(())
}

#[test]
fn nonzero_after_zero_slot_is_rejected_without_a_read() -> TestResult {
    let record = indirect_record(&[0, 3]);
    let bytes = owned_page_database(5, &record, &[3]);
    let mut resources = budget();
    let mut database = open(&bytes, &mut resources)?;
    let mut pages = database.owned_pages(PageNumber::new(1), &mut resources)?;
    assert_eq!(
        pages.next_page(),
        Err(AllocationTraversalError::NonzeroAfterNullSlot {
            slot: 1,
            page: PageNumber::new(3),
        })
    );
    Ok(())
}

#[test]
fn reference_beyond_captured_input_is_rejected_before_read() -> TestResult {
    let record = indirect_record(&[5]);
    let bytes = owned_page_database(5, &record, &[]);
    let mut resources = budget();
    let mut database = open(&bytes, &mut resources)?;
    let mut pages = database.owned_pages(PageNumber::new(1), &mut resources)?;
    assert_eq!(
        pages.next_page(),
        Err(AllocationTraversalError::InvalidReference {
            page: PageNumber::new(5),
            source: Error::PageOutOfBounds {
                page: 5,
                page_count: 5,
            },
        })
    );
    Ok(())
}
