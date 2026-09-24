use super::raw_page_stream::RawPageCursor;
use crate::{
    ByteCount, Error, JET3_PAGE_SIZE, Jet3PageReader, LimitKind, PAGE_BYTES, PageNumber,
    ReadLimits, ResourceBudget, ResourceLimitKind, ResourceLimits, SliceSource,
};

use crate::testkit::TestResult;

fn page_bytes(page_count: usize) -> Vec<u8> {
    let mut bytes = vec![0_u8; page_count * PAGE_BYTES];
    for (index, byte) in bytes.iter_mut().enumerate() {
        *byte = ((index / PAGE_BYTES) % 251) as u8;
    }
    bytes
}

fn budget(total_read: u64, page_visits: u64, total_work: u64) -> ResourceBudget {
    ResourceBudget::new(
        ResourceLimits::new(ReadLimits::new(
            ByteCount::new(u64::MAX),
            JET3_PAGE_SIZE,
            ByteCount::new(total_read),
        ))
        .with_max_page_visits(page_visits)
        .with_max_total_work_units(total_work),
    )
}

fn reader<'a>(
    bytes: &'a [u8],
    operation: &mut ResourceBudget,
) -> Result<Jet3PageReader<SliceSource<'a>>, Error> {
    let source = SliceSource::new(bytes, operation.read_budget())?;
    Jet3PageReader::new(source)
}

#[test]
fn exact_limits_yield_every_page_in_order_and_then_stay_exhausted() -> TestResult {
    let bytes = page_bytes(3);
    let mut operation = budget(3 * JET3_PAGE_SIZE.get(), 3, 3);
    let mut pages = reader(&bytes, &mut operation)?;
    let mut cursor = RawPageCursor::new(&mut pages);
    assert_eq!(cursor.next_page_number(), Some(PageNumber::new(0)));
    assert_eq!(operation.allocation_bytes(), ByteCount::new(0));

    for expected in 0_u64..3 {
        let page = cursor
            .next_page(&mut operation)?
            .ok_or("expected generated page")?;
        assert_eq!(page.number(), PageNumber::new(expected));
        assert!(page.bytes().iter().all(|byte| *byte == expected as u8));
    }

    assert_eq!(cursor.pages_read(), 3);
    assert_eq!(cursor.next_page_number(), None);
    assert!(cursor.next_page(&mut operation)?.is_none());
    assert!(cursor.next_page(&mut operation)?.is_none());
    assert_eq!(
        operation.read_budget().total_read(),
        ByteCount::new(3 * JET3_PAGE_SIZE.get())
    );
    assert_eq!(operation.page_visits(), 3);
    assert_eq!(operation.total_work_units(), 3);
    assert_eq!(operation.allocation_bytes(), ByteCount::new(0));
    Ok(())
}

#[test]
fn limit_rejection_does_not_advance_or_read() -> TestResult {
    let bytes = page_bytes(1);
    let page = JET3_PAGE_SIZE.get();
    let cases = [
        (
            budget(page - 1, 1, 1),
            Error::LimitExceeded {
                kind: LimitKind::TotalReadBytes,
                requested: JET3_PAGE_SIZE,
                maximum: ByteCount::new(page - 1),
            },
        ),
        (
            budget(page, 0, 1),
            Error::ResourceLimitExceeded {
                kind: ResourceLimitKind::PageVisits,
                requested: 1,
                maximum: 0,
            },
        ),
        (
            budget(page, 1, 0),
            Error::ResourceLimitExceeded {
                kind: ResourceLimitKind::TotalWorkUnits,
                requested: 1,
                maximum: 0,
            },
        ),
    ];
    for (mut operation, expected) in cases {
        let mut pages = reader(&bytes, &mut operation)?;
        let mut cursor = RawPageCursor::new(&mut pages);

        assert_eq!(cursor.next_page(&mut operation), Err(expected));
        assert_eq!(cursor.pages_read(), 0);
        assert_eq!(cursor.next_page_number(), Some(PageNumber::new(0)));
        assert_eq!(operation.read_budget().total_read(), ByteCount::new(0));
        assert_eq!(operation.page_visits(), 0);
        assert_eq!(operation.total_work_units(), 0);
    }
    Ok(())
}

#[test]
fn dropping_early_performs_no_future_page_work() -> TestResult {
    let bytes = page_bytes(3);
    let mut operation = budget(u64::MAX, u64::MAX, u64::MAX);
    let mut pages = reader(&bytes, &mut operation)?;
    {
        let mut cursor = RawPageCursor::new(&mut pages);
        let first = cursor
            .next_page(&mut operation)?
            .ok_or("expected first page")?;
        assert_eq!(first.number(), PageNumber::new(0));
    }

    assert_eq!(operation.read_budget().total_read(), JET3_PAGE_SIZE);
    assert_eq!(operation.page_visits(), 1);
    assert_eq!(operation.total_work_units(), 1);
    Ok(())
}
