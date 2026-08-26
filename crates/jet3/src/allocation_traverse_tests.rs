use super::{
    AllocationTraversalError, PageChainWalker, ReachedMapPage, UnsupportedTraversalStep,
    VisitedPages, follow_map_page_reference, locate_allocation_map,
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
fn unsupported_steps_are_structured_and_charge_one_work_unit() -> TestResult {
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
    for reference in [0, 1, u32::MAX] {
        assert_eq!(
            follow_map_page_reference(reference, &mut resources),
            Err(AllocationTraversalError::Unsupported(
                UnsupportedTraversalStep::PointerFollowing
            ))
        );
    }
    assert_eq!(resources.total_work_units(), before + 4);
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
    let mut reached = ReachedMapPage::new(reached)?;
    assert_eq!(reached.page(), PageNumber::new(3));
    assert_eq!(reached.relative_bits().next_bit(&mut resources)?, Some(0));
    assert_eq!(reached.relative_bits().next_bit(&mut resources)?, Some(1));
    assert_eq!(
        reached.absolute_page(0),
        Err(AllocationTraversalError::Unsupported(
            UnsupportedTraversalStep::ExtendedPageBase
        ))
    );
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
fn visited_set_is_charged_before_allocation_and_fails_closed() -> TestResult {
    let bytes = database_bytes(&[EXTENDED_TAG; 9]);
    let mut resources = ResourceBudget::new(limits().with_max_allocation_bytes(ByteCount::new(1)));
    let database = open(&bytes, &mut resources)?;
    assert_eq!(database.geometry().page_count(), 10);
    assert!(matches!(
        PageChainWalker::new(database.geometry(), &mut resources),
        Err(AllocationTraversalError::Resource(
            Error::ResourceLimitExceeded {
                kind: ResourceLimitKind::AllocationBytes,
                ..
            }
        ))
    ));
    assert_eq!(resources.allocation_bytes(), ByteCount::new(0));

    let mut resources = ResourceBudget::new(limits().with_max_allocation_bytes(ByteCount::new(2)));
    let database = open(&bytes, &mut resources)?;
    PageChainWalker::new(database.geometry(), &mut resources)?;
    assert_eq!(resources.allocation_bytes(), ByteCount::new(2));
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
