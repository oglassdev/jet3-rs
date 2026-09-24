use super::page_append_plan::{
    AppendPageError, AppendPagePlan, ExistingPageError, plan_existing_page, plan_existing_pages,
};
use crate::{
    ByteCount, InlineUsageMapEncoder, PageImage, PageKind, PageNumber, UsageMapWriteError,
};

use crate::testkit::TestResult;

use crate::testkit::budget;

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
    assert_eq!(
        plan_existing_page(
            PageNumber::new(20),
            PageImage::new(PageKind::TableDefinition),
        ),
        Err(ExistingPageError::OutsideEmptyDatabase {
            page: PageNumber::new(20),
        })
    );
    Ok(())
}

#[test]
fn exact_existing_batch_pairs_all_slots_without_a_failure_state() {
    let images: [PageImage; 20] = std::array::from_fn(|index| {
        let mut bytes = [0x5a; crate::PAGE_BYTES];
        bytes[11] = index as u8;
        PageImage::from_bytes(bytes)
    });

    let planned = plan_existing_pages(images.clone());
    assert_eq!(planned.len(), 20);
    for (index, (page, image)) in planned.zip(images).enumerate() {
        assert_eq!(page.number(), PageNumber::new(index as u64));
        assert_eq!(page.image(), &image);
    }
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
fn append_rejections_leave_the_plan_and_global_map_unchanged() -> TestResult {
    for (page_count, bitmap_bytes, expected) in [
        (
            20,
            4,
            AppendPageError::PageAlreadyInUse {
                page: PageNumber::new(20),
            },
        ),
        (
            20,
            2,
            AppendPageError::GlobalMap(UsageMapWriteError::PageOutOfMap {
                page: PageNumber::new(20),
                first: PageNumber::new(0),
                page_count: 16,
            }),
        ),
        (
            u64::MAX,
            1,
            AppendPageError::PageCountOverflow {
                page_count: u64::MAX,
            },
        ),
    ] {
        let mut plan = AppendPagePlan { page_count };
        let mut map = global_map(bitmap_bytes)?;
        let map_before = map.clone();
        assert_eq!(
            plan.append(PageImage::new(PageKind::Data), &mut map),
            Err(expected)
        );
        assert_eq!(plan.page_count(), page_count);
        assert_eq!(map, map_before);
    }
    Ok(())
}
