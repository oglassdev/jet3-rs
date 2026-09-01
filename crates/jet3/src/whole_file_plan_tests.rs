use super::{
    EMPTY_DATABASE_PAGE_COUNT, EXISTING_PAGE_COUNT, WholeFileImagePlan, WholeFilePlanError,
};
use crate::limits::ReadLimits;
use crate::page_append_plan::{AppendPageError, PlannedPage};
use crate::{
    ByteCount, Error, InlineUsageMapEncoder, PageImage, PageNumber, ResourceBudget,
    ResourceLimitKind, ResourceLimits, UsageMapWriteError,
};

type TestResult = Result<(), Box<dyn std::error::Error>>;

fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::new(ReadLimits::default()))
}

fn planned_page_storage(page_count: u64) -> u64 {
    (std::mem::size_of::<PlannedPage>() as u64) * page_count
}

fn global_map(bitmap_bytes: u64) -> Result<InlineUsageMapEncoder, UsageMapWriteError> {
    InlineUsageMapEncoder::new(
        PageNumber::new(0),
        ByteCount::new(bitmap_bytes),
        &mut budget(),
    )
}

fn existing_images() -> [PageImage; EXISTING_PAGE_COUNT] {
    std::array::from_fn(|index| {
        let mut bytes = [0xa5; crate::PAGE_BYTES];
        bytes[17] = index as u8;
        PageImage::from_bytes(bytes)
    })
}

#[test]
fn complete_plan_preserves_existing_and_appended_images_in_slot_order() -> TestResult {
    let existing = existing_images();
    let mut plan_budget = budget();
    let mut plan = WholeFileImagePlan::from_existing_pages(existing.clone(), &mut plan_budget)?;
    let mut map = global_map(3)?;
    map.set_page(PageNumber::new(20))?;
    map.set_page(PageNumber::new(21))?;

    let mut first_bytes = [0x11; crate::PAGE_BYTES];
    first_bytes[17] = 20;
    let first = PageImage::from_bytes(first_bytes);
    let mut second_bytes = [0x22; crate::PAGE_BYTES];
    second_bytes[17] = 21;
    let second = PageImage::from_bytes(second_bytes);

    assert_eq!(
        plan.append(first.clone(), &mut map, &mut plan_budget)?,
        PageNumber::new(20)
    );
    assert_eq!(
        plan.append(second.clone(), &mut map, &mut plan_budget)?,
        PageNumber::new(21)
    );
    assert_eq!(plan.page_count(), 22);
    assert_eq!(
        plan_budget.allocation_bytes(),
        ByteCount::new(planned_page_storage(22))
    );
    assert!(!map.is_set(PageNumber::new(20))?);
    assert!(!map.is_set(PageNumber::new(21))?);

    let pages = plan.into_pages();
    assert_eq!(pages.len(), 22);
    for (index, image) in existing.iter().enumerate() {
        assert_eq!(pages[index].number(), PageNumber::new(index as u64));
        assert_eq!(pages[index].image(), image);
    }
    assert_eq!(pages[20].image(), &first);
    assert_eq!(pages[21].image(), &second);
    Ok(())
}

#[test]
fn rejected_append_preserves_whole_file_plan_and_global_map() -> TestResult {
    let existing = existing_images();
    let mut plan_budget = budget();
    let mut plan = WholeFileImagePlan::from_existing_pages(existing.clone(), &mut plan_budget)?;
    let mut map = global_map(3)?;
    let map_before = map.clone();

    assert_eq!(
        plan.append(
            PageImage::from_bytes([0x33; crate::PAGE_BYTES]),
            &mut map,
            &mut plan_budget,
        ),
        Err(WholeFilePlanError::Append(
            AppendPageError::PageAlreadyInUse {
                page: PageNumber::new(20),
            }
        ))
    );
    assert_eq!(plan.page_count(), 20);
    assert_eq!(plan.pages().len(), 20);
    for (index, (planned, image)) in plan.pages().iter().zip(existing.iter()).enumerate() {
        assert_eq!(planned.number(), PageNumber::new(index as u64));
        assert_eq!(planned.image(), image);
    }
    assert_eq!(map, map_before);
    Ok(())
}

#[test]
fn constructor_budget_rejection_precedes_storage_reservation() {
    let requested = planned_page_storage(EMPTY_DATABASE_PAGE_COUNT);
    let maximum = requested - 1;
    let mut plan_budget = ResourceBudget::new(
        ResourceLimits::new(ReadLimits::default())
            .with_max_allocation_bytes(ByteCount::new(maximum)),
    );

    assert_eq!(
        WholeFileImagePlan::from_existing_pages(existing_images(), &mut plan_budget),
        Err(WholeFilePlanError::Resource(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::AllocationBytes,
            requested,
            maximum,
        }))
    );
    assert_eq!(plan_budget.allocation_bytes(), ByteCount::new(0));
}

#[test]
fn append_budget_rejection_preserves_plan_and_global_map() -> TestResult {
    let existing = existing_images();
    let initial_storage = planned_page_storage(EMPTY_DATABASE_PAGE_COUNT);
    let appended_storage = planned_page_storage(1);
    let mut plan_budget = ResourceBudget::new(
        ResourceLimits::new(ReadLimits::default())
            .with_max_allocation_bytes(ByteCount::new(initial_storage)),
    );
    let mut plan = WholeFileImagePlan::from_existing_pages(existing.clone(), &mut plan_budget)?;
    let mut map = global_map(3)?;
    map.set_page(PageNumber::new(20))?;
    let map_before = map.clone();

    assert_eq!(
        plan.append(
            PageImage::from_bytes([0x44; crate::PAGE_BYTES]),
            &mut map,
            &mut plan_budget,
        ),
        Err(WholeFilePlanError::Resource(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::AllocationBytes,
            requested: initial_storage + appended_storage,
            maximum: initial_storage,
        }))
    );
    assert_eq!(
        plan_budget.allocation_bytes(),
        ByteCount::new(initial_storage)
    );
    assert_eq!(plan.page_count(), EMPTY_DATABASE_PAGE_COUNT);
    assert_eq!(plan.pages().len(), EXISTING_PAGE_COUNT);
    for (index, (planned, image)) in plan.pages().iter().zip(existing.iter()).enumerate() {
        assert_eq!(planned.number(), PageNumber::new(index as u64));
        assert_eq!(planned.image(), image);
    }
    assert_eq!(map, map_before);
    Ok(())
}
