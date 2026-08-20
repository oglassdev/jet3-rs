use super::{PageClassificationError, PageKind, classify_page};
use crate::{
    Error, JET3_PAGE_SIZE, PageNumber, ReadLimits, ResourceBudget, ResourceLimitKind,
    ResourceLimits,
};

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;

type TestResult = Result<(), Box<dyn std::error::Error>>;

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
fn nonzero_pages_map_each_documented_payload_tag_exactly() -> TestResult {
    let cases = [
        (0x01, PageKind::Data),
        (0x02, PageKind::TableDefinition),
        (0x03, PageKind::IntermediateIndex),
        (0x04, PageKind::LeafIndex),
        (0x05, PageKind::ExtendedUsageBitmap),
    ];
    let mut operation = budget(cases.len() as u64);

    for (tag, expected) in cases {
        let mut page = [0xA5_u8; PAGE_BYTES];
        page[0] = tag;
        assert_eq!(
            classify_page(PageNumber::new(1), &page, &mut operation)?.kind(),
            expected
        );
    }

    assert_eq!(operation.total_work_units(), cases.len() as u64);
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
fn tag_eight_and_all_other_unsupported_bytes_remain_lossless_unknowns() -> TestResult {
    let unsupported_count = u64::from(u8::MAX) + 1 - 5;
    let mut operation = budget(unsupported_count);

    for tag in u8::MIN..=u8::MAX {
        if (1..=5).contains(&tag) {
            continue;
        }
        let mut page = [0_u8; PAGE_BYTES];
        page[0] = tag;
        assert_eq!(
            classify_page(PageNumber::new(42), &page, &mut operation)?.kind(),
            PageKind::Unknown(tag)
        );
    }
    assert_eq!(operation.total_work_units(), unsupported_count);
    Ok(())
}

#[test]
fn bytes_after_byte_zero_are_preserved_but_do_not_affect_classification() -> TestResult {
    let mut first = [0_u8; PAGE_BYTES];
    first[0] = 0x02;
    let mut second = [0xFF_u8; PAGE_BYTES];
    second[0] = 0x02;
    let mut operation = budget(2);

    let first_view = classify_page(PageNumber::new(7), &first, &mut operation)?;
    let second_view = classify_page(PageNumber::new(7), &second, &mut operation)?;

    assert_eq!(first_view.kind(), PageKind::TableDefinition);
    assert_eq!(second_view.kind(), PageKind::TableDefinition);
    assert_eq!(first_view.raw_bytes(), &first);
    assert_eq!(second_view.raw_bytes(), &second);
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
