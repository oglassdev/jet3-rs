use super::{AppendPageError, AppendPagePlan, ExistingPageError, plan_existing_page};
use crate::limits::ReadLimits;
use crate::{
    ByteCount, InlineUsageMapEncoder, PageImage, PageKind, PageNumber, ResourceBudget,
    ResourceLimits, UsageMapWriteError,
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

fn free_pages(
    map: &mut InlineUsageMapEncoder,
    pages: impl IntoIterator<Item = u64>,
) -> Result<(), UsageMapWriteError> {
    for page in pages {
        map.set_page(PageNumber::new(page))?;
    }
    Ok(())
}

#[test]
fn existing_page_boundaries_preserve_complete_images() -> TestResult {
    for page in [0, 19] {
        let mut bytes = [0xab; crate::PAGE_BYTES];
        bytes[0] = page as u8;
        bytes[crate::PAGE_BYTES - 1] = 0x11;
        let image = PageImage::from_bytes(bytes);

        let (number, planned_image) =
            plan_existing_page(PageNumber::new(page), image.clone())?.into_parts();
        assert_eq!(number, PageNumber::new(page));
        assert_eq!(planned_image, image);
        assert_eq!(planned_image.as_bytes(), &bytes);
    }
    Ok(())
}

#[test]
fn existing_page_rejects_the_first_append_slot() {
    assert_eq!(
        plan_existing_page(
            PageNumber::new(20),
            PageImage::new(PageKind::TableDefinition),
        ),
        Err(ExistingPageError::OutsideEmptyDatabase {
            page: PageNumber::new(20),
        })
    );
}

#[test]
fn q2_append_sequence_numbers_complete_images_and_marks_global_in_use() -> TestResult {
    let mut plan = AppendPagePlan::after_empty_database();
    let mut map = global_map(4)?;
    free_pages(&mut map, 20..=23)?;

    let kinds = [
        PageKind::TableDefinition,
        PageKind::Data,
        PageKind::Data,
        PageKind::Data,
    ];
    for (expected, kind) in (20..=23).zip(kinds) {
        let image = PageImage::new(kind);
        let planned = plan.append(image.clone(), &mut map)?;
        assert_eq!(planned.number(), PageNumber::new(expected));
        assert_eq!(planned.image(), &image);
        assert_eq!(planned.image().tag(), image.tag());
        assert!(!map.is_set(PageNumber::new(expected))?);
    }
    assert_eq!(plan.page_count(), 24);
    Ok(())
}

#[test]
fn append_preserves_arbitrary_complete_page_bytes() -> TestResult {
    let mut plan = AppendPagePlan::after_empty_database();
    let mut map = global_map(4)?;
    free_pages(&mut map, [20])?;
    let mut bytes = [0xab; crate::PAGE_BYTES];
    bytes[0] = 0xfe;
    bytes[crate::PAGE_BYTES - 1] = 0x11;
    let image = PageImage::from_bytes(bytes);

    let (number, planned_image) = plan.append(image.clone(), &mut map)?.into_parts();
    assert_eq!(number, PageNumber::new(20));
    assert_eq!(planned_image, image);
    assert_eq!(planned_image.as_bytes(), &bytes);
    Ok(())
}

#[test]
fn already_in_use_rejection_is_atomic() -> TestResult {
    let mut plan = AppendPagePlan::after_empty_database();
    let mut map = global_map(4)?;
    let map_before = map.clone();

    assert_eq!(
        plan.append(PageImage::new(PageKind::Data), &mut map),
        Err(AppendPageError::PageAlreadyInUse {
            page: PageNumber::new(20),
        })
    );
    assert_eq!(plan.page_count(), 20);
    assert_eq!(map, map_before);
    Ok(())
}

#[test]
fn out_of_map_rejection_is_atomic() -> TestResult {
    let mut plan = AppendPagePlan::after_empty_database();
    let mut map = global_map(2)?;
    let map_before = map.clone();

    assert_eq!(
        plan.append(PageImage::new(PageKind::Data), &mut map),
        Err(AppendPageError::GlobalMap(
            UsageMapWriteError::PageOutOfMap {
                page: PageNumber::new(20),
                first: PageNumber::new(0),
                page_count: 16,
            }
        ))
    );
    assert_eq!(plan.page_count(), 20);
    assert_eq!(map, map_before);
    Ok(())
}

#[test]
fn page_count_overflow_is_atomic() -> TestResult {
    let mut plan = AppendPagePlan {
        page_count: u64::MAX,
    };
    let mut map = global_map(1)?;
    let map_before = map.clone();

    assert_eq!(
        plan.append(PageImage::new(PageKind::Data), &mut map),
        Err(AppendPageError::PageCountOverflow {
            page_count: u64::MAX,
        })
    );
    assert_eq!(plan.page_count(), u64::MAX);
    assert_eq!(map, map_before);
    Ok(())
}
