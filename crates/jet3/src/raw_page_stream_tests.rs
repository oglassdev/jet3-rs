use super::RawPageCursor;
use crate::{
    ByteCount, Error, JET3_PAGE_SIZE, Jet3PageReader, LimitKind, PageNumber, ReadLimits,
    ResourceBudget, ResourceLimitKind, ResourceLimits, SliceSource, read_jet_signature,
};

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const SIGNATURE_BYTES: u64 = 15;

type TestResult = Result<(), Box<dyn std::error::Error>>;

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
fn yields_every_page_in_physical_order_and_then_stays_exhausted() -> TestResult {
    let bytes = page_bytes(3);
    let mut operation = budget(3 * JET3_PAGE_SIZE.get(), 3, 3);
    let mut pages = reader(&bytes, &mut operation)?;
    let mut cursor = RawPageCursor::new(&mut pages);

    for expected in 0_u64..3 {
        let page = cursor
            .next_page(&mut operation)?
            .ok_or("expected generated page")?;
        assert_eq!(page.number(), PageNumber::new(expected));
        assert!(page.bytes().iter().all(|byte| *byte == expected as u8));
    }

    assert_eq!(cursor.pages_read(), 3);
    assert_eq!(cursor.next_page_number(), None);
    let reads_before = operation.read_budget().total_read();
    let visits_before = operation.page_visits();
    assert!(cursor.next_page(&mut operation)?.is_none());
    assert!(cursor.next_page(&mut operation)?.is_none());
    assert_eq!(operation.read_budget().total_read(), reads_before);
    assert_eq!(operation.page_visits(), visits_before);
    Ok(())
}

#[test]
fn exact_limits_cover_the_complete_stream() -> TestResult {
    let bytes = page_bytes(2);
    let expected_read = 2 * JET3_PAGE_SIZE.get();
    let mut operation = budget(expected_read, 2, 2);
    let mut pages = reader(&bytes, &mut operation)?;
    let mut cursor = RawPageCursor::new(&mut pages);

    assert!(cursor.next_page(&mut operation)?.is_some());
    assert!(cursor.next_page(&mut operation)?.is_some());
    assert!(cursor.next_page(&mut operation)?.is_none());
    assert_eq!(
        operation.read_budget().total_read(),
        ByteCount::new(expected_read)
    );
    assert_eq!(operation.page_visits(), 2);
    assert_eq!(operation.total_work_units(), 2);
    Ok(())
}

#[test]
fn total_read_rejection_does_not_advance_the_cursor() -> TestResult {
    let bytes = page_bytes(1);
    let maximum = ByteCount::new(JET3_PAGE_SIZE.get() - 1);
    let mut operation = budget(maximum.get(), 1, 1);
    let mut pages = reader(&bytes, &mut operation)?;
    let mut cursor = RawPageCursor::new(&mut pages);

    assert_eq!(
        cursor.next_page(&mut operation),
        Err(Error::LimitExceeded {
            kind: LimitKind::TotalReadBytes,
            requested: JET3_PAGE_SIZE,
            maximum,
        })
    );
    assert_eq!(cursor.pages_read(), 0);
    assert_eq!(cursor.next_page_number(), Some(PageNumber::new(0)));
    assert_eq!(operation.read_budget().total_read(), ByteCount::new(0));
    assert_eq!(operation.page_visits(), 0);
    assert_eq!(operation.total_work_units(), 0);
    Ok(())
}

#[test]
fn page_visit_rejection_does_not_advance_or_read() -> TestResult {
    let bytes = page_bytes(1);
    let mut operation = budget(JET3_PAGE_SIZE.get(), 0, 1);
    let mut pages = reader(&bytes, &mut operation)?;
    let mut cursor = RawPageCursor::new(&mut pages);

    assert_eq!(
        cursor.next_page(&mut operation),
        Err(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::PageVisits,
            requested: 1,
            maximum: 0,
        })
    );
    assert_eq!(cursor.pages_read(), 0);
    assert_eq!(cursor.next_page_number(), Some(PageNumber::new(0)));
    assert_eq!(operation.read_budget().total_read(), ByteCount::new(0));
    assert_eq!(operation.page_visits(), 0);
    assert_eq!(operation.total_work_units(), 0);
    Ok(())
}

#[test]
fn total_work_rejection_does_not_advance_or_read() -> TestResult {
    let bytes = page_bytes(1);
    let mut operation = budget(JET3_PAGE_SIZE.get(), 1, 0);
    let mut pages = reader(&bytes, &mut operation)?;
    let mut cursor = RawPageCursor::new(&mut pages);

    assert_eq!(
        cursor.next_page(&mut operation),
        Err(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::TotalWorkUnits,
            requested: 1,
            maximum: 0,
        })
    );
    assert_eq!(cursor.pages_read(), 0);
    assert_eq!(cursor.next_page_number(), Some(PageNumber::new(0)));
    assert_eq!(operation.read_budget().total_read(), ByteCount::new(0));
    assert_eq!(operation.page_visits(), 0);
    assert_eq!(operation.total_work_units(), 0);
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

#[test]
fn cursor_creation_uses_no_charged_or_input_sized_allocation() -> TestResult {
    let bytes = page_bytes(2);
    let mut operation = budget(u64::MAX, u64::MAX, u64::MAX);
    let mut pages = reader(&bytes, &mut operation)?;
    let cursor = RawPageCursor::new(&mut pages);

    assert_eq!(cursor.pages_read(), 0);
    assert_eq!(cursor.next_page_number(), Some(PageNumber::new(0)));
    assert_eq!(operation.read_budget().total_read(), ByteCount::new(0));
    assert_eq!(operation.allocation_bytes(), ByteCount::new(0));
    assert_eq!(operation.page_visits(), 0);
    Ok(())
}

#[test]
fn stream_accounting_can_follow_a_prior_signature_read() -> TestResult {
    let mut bytes = page_bytes(1);
    bytes[4..19].copy_from_slice(b"Standard Jet DB");
    let total_read = SIGNATURE_BYTES + JET3_PAGE_SIZE.get();
    let mut operation = budget(total_read, 1, 1);
    let mut source = SliceSource::new(&bytes, operation.read_budget())?;
    read_jet_signature(&mut source, operation.read_budget())?;
    let mut pages = Jet3PageReader::new(source)?;
    let mut cursor = RawPageCursor::new(&mut pages);

    assert!(cursor.next_page(&mut operation)?.is_some());
    assert_eq!(
        operation.read_budget().total_read(),
        ByteCount::new(total_read)
    );
    assert_eq!(operation.page_visits(), 1);
    assert_eq!(operation.total_work_units(), 1);
    Ok(())
}
