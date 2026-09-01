use super::{EXISTING_PAGE_COUNT, WholeFileImagePlan, WholeFilePlanError};
use crate::limits::ReadLimits;
use crate::page_append_plan::AppendPageError;
use crate::{
    ByteCount, InlineUsageMapEncoder, PageImage, PageNumber, ResourceBudget, ResourceLimits,
    UsageMapWriteError,
};

type TestResult = Result<(), Box<dyn std::error::Error>>;

fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::new(ReadLimits::default()))
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
    let mut plan = WholeFileImagePlan::from_existing_pages(existing.clone())?;
    let mut map = global_map(3)?;
    map.set_page(PageNumber::new(20))?;
    map.set_page(PageNumber::new(21))?;

    let mut first_bytes = [0x11; crate::PAGE_BYTES];
    first_bytes[17] = 20;
    let first = PageImage::from_bytes(first_bytes);
    let mut second_bytes = [0x22; crate::PAGE_BYTES];
    second_bytes[17] = 21;
    let second = PageImage::from_bytes(second_bytes);

    assert_eq!(plan.append(first.clone(), &mut map)?, PageNumber::new(20));
    assert_eq!(plan.append(second.clone(), &mut map)?, PageNumber::new(21));
    assert_eq!(plan.page_count(), 22);
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
    let mut plan = WholeFileImagePlan::from_existing_pages(existing_images())?;
    let plan_before = plan.clone();
    let mut map = global_map(3)?;
    let map_before = map.clone();

    assert_eq!(
        plan.append(PageImage::from_bytes([0x33; crate::PAGE_BYTES]), &mut map),
        Err(WholeFilePlanError::Append(
            AppendPageError::PageAlreadyInUse {
                page: PageNumber::new(20),
            }
        ))
    );
    assert_eq!(plan, plan_before);
    assert_eq!(map, map_before);
    Ok(())
}
