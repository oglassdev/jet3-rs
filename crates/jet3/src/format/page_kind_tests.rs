use super::page_kind::{PageClassificationError, PageKind, classify_page};
use crate::{
    Error, PAGE_BYTES, PageNumber, ReadLimits, ResourceBudget, ResourceLimitKind, ResourceLimits,
};

use crate::testkit::TestResult;

fn budget(maximum_work: u64) -> ResourceBudget {
    ResourceBudget::new(
        ResourceLimits::new(ReadLimits::default()).with_max_total_work_units(maximum_work),
    )
}

#[test]
fn page_zero_tag_zero_is_the_only_database_definition_context() -> TestResult {
    let page = [0_u8; PAGE_BYTES];
    let mut operation = budget(1);

    let classified = classify_page(PageNumber::new(0), &page, &mut operation)?;

    assert_eq!(classified.number(), PageNumber::new(0));
    assert_eq!(classified.kind(), PageKind::DatabaseDefinition);
    assert_eq!(classified.raw_bytes(), &page);
    assert_eq!(operation.total_work_units(), 1);
    Ok(())
}

#[test]
fn nonzero_pages_map_documented_tags_and_keep_all_others_as_lossless_unknowns() -> TestResult {
    let mut operation = budget(256);
    for tag in u8::MIN..=u8::MAX {
        let expected = match tag {
            0x01 => PageKind::Data,
            0x02 => PageKind::TableDefinition,
            0x03 => PageKind::IntermediateIndex,
            0x04 => PageKind::LeafIndex,
            0x05 => PageKind::ExtendedUsageBitmap,
            other => PageKind::Unknown(other),
        };
        let mut page = [if tag % 2 == 0 { 0x00 } else { 0xFF }; PAGE_BYTES];
        page[0] = tag;
        let classified = classify_page(PageNumber::new(42), &page, &mut operation)?;
        assert_eq!(classified.kind(), expected, "tag {tag:#04x}");
        assert_eq!(classified.raw_bytes(), &page);
    }
    assert_eq!(operation.total_work_units(), 256);
    Ok(())
}

#[test]
fn documented_tags_in_the_wrong_page_context_are_unknown() -> TestResult {
    let page_zero_unknown_tags = [1_u8, 2, 3, 4, 5, 8, u8::MAX];
    let mut operation = budget(1 + page_zero_unknown_tags.len() as u64);
    let page = [0_u8; PAGE_BYTES];
    assert_eq!(
        classify_page(PageNumber::new(1), &page, &mut operation)?.kind(),
        PageKind::Unknown(0)
    );

    for tag in page_zero_unknown_tags {
        let mut page = [0_u8; PAGE_BYTES];
        page[0] = tag;
        assert_eq!(
            classify_page(PageNumber::new(0), &page, &mut operation)?.kind(),
            PageKind::Unknown(tag)
        );
    }
    Ok(())
}

#[test]
fn classification_work_limit_is_charged_exactly_once_and_rejects_atomically() -> TestResult {
    let mut page = [0_u8; PAGE_BYTES];
    page[0] = 0x01;
    let mut operation = budget(1);

    assert_eq!(
        classify_page(PageNumber::new(1), &page, &mut operation)?.kind(),
        PageKind::Data
    );
    assert_eq!(operation.total_work_units(), 1);
    assert_eq!(
        classify_page(PageNumber::new(1), &page, &mut operation),
        Err(PageClassificationError::Resource(
            Error::ResourceLimitExceeded {
                kind: ResourceLimitKind::TotalWorkUnits,
                requested: 2,
                maximum: 1,
            }
        ))
    );
    assert_eq!(operation.total_work_units(), 1);
    assert_eq!(operation.page_visits(), 0);
    assert_eq!(operation.read_budget().total_read().get(), 0);
    Ok(())
}
