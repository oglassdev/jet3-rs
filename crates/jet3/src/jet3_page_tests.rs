use std::cell::Cell;
use std::io;
use std::rc::Rc;

use super::Jet3PageReader;
use crate::{
    ByteCount, ByteOffset, Error, JET3_PAGE_SIZE, LimitKind, PageNumber, ReadAt, ReadBudget,
    ReadLimits, ResourceBudget, ResourceLimitKind, ResourceLimits,
};

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;

#[derive(Debug, Clone, Copy)]
enum ReadBehavior {
    Exact,
    Short,
    Fault,
}

#[derive(Debug)]
struct TestSource {
    bytes: Vec<u8>,
    behavior: ReadBehavior,
    reads: u64,
}

#[derive(Debug)]
struct LengthSpySource {
    length: ByteCount,
    reads: Rc<Cell<u64>>,
}

impl ReadAt for LengthSpySource {
    fn len(&self) -> ByteCount {
        self.length
    }

    fn read_exact_at(
        &mut self,
        _offset: ByteOffset,
        _destination: &mut [u8],
        _budget: &mut ReadBudget,
    ) -> Result<(), Error> {
        self.reads.set(self.reads.get().saturating_add(1));
        Err(Error::Arithmetic {
            operation: "unexpected read during page-reader construction",
        })
    }
}

impl TestSource {
    fn exact(bytes: Vec<u8>) -> Self {
        Self {
            bytes,
            behavior: ReadBehavior::Exact,
            reads: 0,
        }
    }

    fn with_behavior(bytes: Vec<u8>, behavior: ReadBehavior) -> Self {
        Self {
            bytes,
            behavior,
            reads: 0,
        }
    }
}

impl ReadAt for TestSource {
    fn len(&self) -> ByteCount {
        ByteCount::new(self.bytes.len() as u64)
    }

    fn read_exact_at(
        &mut self,
        offset: ByteOffset,
        destination: &mut [u8],
        budget: &mut ReadBudget,
    ) -> Result<(), Error> {
        let count = ByteCount::from_usize(destination.len())?;
        budget.charge_read_attempt(count)?;
        self.reads = self.reads.checked_add(1).ok_or(Error::Arithmetic {
            operation: "count test-source reads",
        })?;

        match self.behavior {
            ReadBehavior::Exact => {
                let start = offset.to_usize()?;
                let end = start
                    .checked_add(destination.len())
                    .ok_or(Error::Arithmetic {
                        operation: "compute test-source read end",
                    })?;
                let source = self.bytes.get(start..end).ok_or(Error::UnexpectedEnd {
                    offset,
                    needed: count,
                    available: ByteCount::new(self.bytes.len().saturating_sub(start) as u64),
                })?;
                destination.copy_from_slice(source);
                Ok(())
            }
            ReadBehavior::Short => {
                let actual_len = destination.len().checked_sub(1).ok_or(Error::Arithmetic {
                    operation: "compute test-source short-read length",
                })?;
                let prefix = destination.get_mut(..actual_len).ok_or(Error::Arithmetic {
                    operation: "select test-source short-read prefix",
                })?;
                prefix.fill(0xcc);
                Err(Error::ShortRead {
                    offset,
                    needed: count,
                    actual: ByteCount::from_usize(actual_len)?,
                })
            }
            ReadBehavior::Fault => Err(Error::Io {
                operation: "read test source",
                kind: io::ErrorKind::Other,
            }),
        }
    }
}

fn limits(single_read: u64, total_read: u64, page_visits: u64, total_work: u64) -> ResourceLimits {
    ResourceLimits::new(ReadLimits::new(
        ByteCount::new(u64::MAX),
        ByteCount::new(single_read),
        ByteCount::new(total_read),
    ))
    .with_max_page_visits(page_visits)
    .with_max_total_work_units(total_work)
}

fn budget(single_read: u64, total_read: u64, page_visits: u64, total_work: u64) -> ResourceBudget {
    ResourceBudget::new(limits(single_read, total_read, page_visits, total_work))
}

fn permissive_budget() -> ResourceBudget {
    budget(u64::MAX, u64::MAX, u64::MAX, u64::MAX)
}

fn patterned_pages(count: usize) -> Vec<u8> {
    let mut bytes = vec![0_u8; count * PAGE_BYTES];
    for (index, byte) in bytes.iter_mut().enumerate() {
        *byte = (index / PAGE_BYTES) as u8;
    }
    bytes
}

#[test]
fn constructor_accepts_empty_and_aligned_sources_without_reading() -> Result<(), Error> {
    let empty = Jet3PageReader::new(TestSource::exact(Vec::new()))?;
    assert_eq!(empty.geometry().source_len(), ByteCount::new(0));
    assert_eq!(empty.geometry().page_size(), JET3_PAGE_SIZE);
    assert_eq!(empty.geometry().page_count(), 0);
    assert_eq!(empty.source().reads, 0);

    let aligned = Jet3PageReader::new(TestSource::exact(patterned_pages(3)))?;
    assert_eq!(
        aligned.geometry().source_len(),
        ByteCount::new((3 * PAGE_BYTES) as u64)
    );
    assert_eq!(aligned.geometry().page_size(), JET3_PAGE_SIZE);
    assert_eq!(aligned.geometry().page_count(), 3);
    assert_eq!(aligned.source().reads, 0);
    Ok(())
}

#[test]
fn constructor_rejects_partial_page_without_reading() {
    let reads = Rc::new(Cell::new(0));
    let result = Jet3PageReader::new(LengthSpySource {
        length: ByteCount::new((PAGE_BYTES + 1) as u64),
        reads: Rc::clone(&reads),
    });
    assert!(matches!(
        result,
        Err(Error::PartialPage {
            input_len,
            page_size: JET3_PAGE_SIZE,
            trailing
        }) if input_len == ByteCount::new((PAGE_BYTES + 1) as u64)
            && trailing == ByteCount::new(1)
    ));
    assert_eq!(reads.get(), 0);
}

#[test]
fn reads_first_and_last_complete_pages() -> Result<(), Error> {
    let mut reader = Jet3PageReader::new(TestSource::exact(patterned_pages(3)))?;
    let mut destination = [0_u8; PAGE_BYTES];
    let mut operation = permissive_budget();

    reader.read_page(PageNumber::new(0), &mut destination, &mut operation)?;
    assert!(destination.iter().all(|byte| *byte == 0));
    reader.read_page(PageNumber::new(2), &mut destination, &mut operation)?;
    assert!(destination.iter().all(|byte| *byte == 2));
    assert_eq!(reader.source().reads, 2);
    assert_eq!(
        operation.read_budget().total_read(),
        ByteCount::new((2 * PAGE_BYTES) as u64)
    );
    assert_eq!(operation.page_visits(), 2);
    assert_eq!(operation.total_work_units(), 2);
    Ok(())
}

#[test]
fn rejects_page_at_count_and_one_above_without_charging() -> Result<(), Error> {
    let mut reader = Jet3PageReader::new(TestSource::exact(patterned_pages(2)))?;
    let mut destination = [0_u8; PAGE_BYTES];
    let mut operation = permissive_budget();

    for page in [PageNumber::new(2), PageNumber::new(3)] {
        assert_eq!(
            reader.read_page(page, &mut destination, &mut operation),
            Err(Error::PageOutOfBounds {
                page: page.get(),
                page_count: 2,
            })
        );
    }
    assert_eq!(reader.source().reads, 0);
    assert_eq!(operation.read_budget().total_read(), ByteCount::new(0));
    assert_eq!(operation.page_visits(), 0);
    assert_eq!(operation.total_work_units(), 0);
    Ok(())
}

#[test]
fn empty_source_rejects_page_zero_without_charging() -> Result<(), Error> {
    let mut reader = Jet3PageReader::new(TestSource::exact(Vec::new()))?;
    let mut destination = [0_u8; PAGE_BYTES];
    let mut operation = permissive_budget();

    assert_eq!(
        reader.read_page(PageNumber::new(0), &mut destination, &mut operation),
        Err(Error::PageOutOfBounds {
            page: 0,
            page_count: 0,
        })
    );
    assert_uncharged(&mut operation);
    assert_eq!(reader.source().reads, 0);
    Ok(())
}

#[test]
fn insufficient_read_budget_precedes_invalid_page_reference() -> Result<(), Error> {
    let mut reader = Jet3PageReader::new(TestSource::exact(patterned_pages(1)))?;
    let mut destination = [0_u8; PAGE_BYTES];
    let maximum = ByteCount::new(JET3_PAGE_SIZE.get() - 1);
    let mut operation = budget(maximum.get(), u64::MAX, 1, 1);

    assert_eq!(
        reader.read_page(PageNumber::new(1), &mut destination, &mut operation),
        Err(Error::LimitExceeded {
            kind: LimitKind::SingleReadBytes,
            requested: JET3_PAGE_SIZE,
            maximum,
        })
    );
    assert_uncharged(&mut operation);
    assert_eq!(reader.source().reads, 0);
    Ok(())
}

#[test]
fn accepts_exact_single_and_total_read_limits() -> Result<(), Error> {
    let mut reader = Jet3PageReader::new(TestSource::exact(patterned_pages(1)))?;
    let mut destination = [0_u8; PAGE_BYTES];
    let mut operation = budget(JET3_PAGE_SIZE.get(), JET3_PAGE_SIZE.get(), 1, 1);

    reader.read_page(PageNumber::new(0), &mut destination, &mut operation)?;
    assert_eq!(operation.read_budget().total_read(), JET3_PAGE_SIZE);
    assert_eq!(operation.page_visits(), 1);
    Ok(())
}

#[test]
fn rejects_one_below_single_read_limit_without_charging() -> Result<(), Error> {
    let mut reader = Jet3PageReader::new(TestSource::exact(patterned_pages(1)))?;
    let mut destination = [0_u8; PAGE_BYTES];
    let maximum = ByteCount::new(JET3_PAGE_SIZE.get() - 1);
    let mut operation = budget(maximum.get(), u64::MAX, 1, 1);

    assert_eq!(
        reader.read_page(PageNumber::new(0), &mut destination, &mut operation),
        Err(Error::LimitExceeded {
            kind: LimitKind::SingleReadBytes,
            requested: JET3_PAGE_SIZE,
            maximum,
        })
    );
    assert_uncharged(&mut operation);
    assert_eq!(reader.source().reads, 0);
    Ok(())
}

#[test]
fn rejects_one_below_total_read_limit_without_charging() -> Result<(), Error> {
    let mut reader = Jet3PageReader::new(TestSource::exact(patterned_pages(1)))?;
    let mut destination = [0_u8; PAGE_BYTES];
    let maximum = ByteCount::new(JET3_PAGE_SIZE.get() - 1);
    let mut operation = budget(u64::MAX, maximum.get(), 1, 1);

    assert_eq!(
        reader.read_page(PageNumber::new(0), &mut destination, &mut operation),
        Err(Error::LimitExceeded {
            kind: LimitKind::TotalReadBytes,
            requested: JET3_PAGE_SIZE,
            maximum,
        })
    );
    assert_uncharged(&mut operation);
    assert_eq!(reader.source().reads, 0);
    Ok(())
}

#[test]
fn repeated_page_read_exhausts_cumulative_limit_without_second_visit() -> Result<(), Error> {
    let mut reader = Jet3PageReader::new(TestSource::exact(patterned_pages(1)))?;
    let mut destination = [0_u8; PAGE_BYTES];
    let mut operation = budget(
        JET3_PAGE_SIZE.get(),
        JET3_PAGE_SIZE.get(),
        u64::MAX,
        u64::MAX,
    );

    reader.read_page(PageNumber::new(0), &mut destination, &mut operation)?;
    assert_eq!(
        reader.read_page(PageNumber::new(0), &mut destination, &mut operation),
        Err(Error::LimitExceeded {
            kind: LimitKind::TotalReadBytes,
            requested: ByteCount::new(2 * JET3_PAGE_SIZE.get()),
            maximum: JET3_PAGE_SIZE,
        })
    );
    assert_eq!(reader.source().reads, 1);
    assert_eq!(operation.read_budget().total_read(), JET3_PAGE_SIZE);
    assert_eq!(operation.page_visits(), 1);
    assert_eq!(operation.total_work_units(), 1);
    Ok(())
}

#[test]
fn page_visit_limit_accepts_exact_and_rejects_one_over() -> Result<(), Error> {
    let mut reader = Jet3PageReader::new(TestSource::exact(patterned_pages(2)))?;
    let mut destination = [0_u8; PAGE_BYTES];
    let mut operation = budget(u64::MAX, u64::MAX, 1, u64::MAX);

    reader.read_page(PageNumber::new(0), &mut destination, &mut operation)?;
    assert_eq!(
        reader.read_page(PageNumber::new(1), &mut destination, &mut operation),
        Err(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::PageVisits,
            requested: 2,
            maximum: 1,
        })
    );
    assert_eq!(reader.source().reads, 1);
    assert_eq!(operation.read_budget().total_read(), JET3_PAGE_SIZE);
    assert_eq!(operation.page_visits(), 1);
    assert_eq!(operation.total_work_units(), 1);
    Ok(())
}

#[test]
fn total_work_limit_accepts_exact_and_rejects_one_over() -> Result<(), Error> {
    let mut reader = Jet3PageReader::new(TestSource::exact(patterned_pages(2)))?;
    let mut destination = [0_u8; PAGE_BYTES];
    let mut operation = budget(u64::MAX, u64::MAX, u64::MAX, 1);

    reader.read_page(PageNumber::new(0), &mut destination, &mut operation)?;
    assert_eq!(
        reader.read_page(PageNumber::new(1), &mut destination, &mut operation),
        Err(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::TotalWorkUnits,
            requested: 2,
            maximum: 1,
        })
    );
    assert_eq!(reader.source().reads, 1);
    assert_eq!(operation.read_budget().total_read(), JET3_PAGE_SIZE);
    assert_eq!(operation.page_visits(), 1);
    assert_eq!(operation.total_work_units(), 1);
    Ok(())
}

#[test]
fn short_read_charges_attempted_bytes_and_page_visit() -> Result<(), Error> {
    let source = TestSource::with_behavior(patterned_pages(1), ReadBehavior::Short);
    let mut reader = Jet3PageReader::new(source)?;
    let mut destination = [0x5a_u8; PAGE_BYTES];
    let mut operation = permissive_budget();

    assert_eq!(
        reader.read_page(PageNumber::new(0), &mut destination, &mut operation),
        Err(Error::ShortRead {
            offset: ByteOffset::new(0),
            needed: JET3_PAGE_SIZE,
            actual: ByteCount::new(JET3_PAGE_SIZE.get() - 1),
        })
    );
    assert_failed_read_charged(&mut operation);
    assert_eq!(reader.source().reads, 1);
    assert!(destination.iter().all(|byte| *byte == 0x5a));
    Ok(())
}

#[test]
fn io_fault_charges_attempted_bytes_and_page_visit() -> Result<(), Error> {
    let source = TestSource::with_behavior(patterned_pages(1), ReadBehavior::Fault);
    let mut reader = Jet3PageReader::new(source)?;
    let mut destination = [0xa5_u8; PAGE_BYTES];
    let mut operation = permissive_budget();

    assert_eq!(
        reader.read_page(PageNumber::new(0), &mut destination, &mut operation),
        Err(Error::Io {
            operation: "read test source",
            kind: io::ErrorKind::Other,
        })
    );
    assert_failed_read_charged(&mut operation);
    assert_eq!(reader.source().reads, 1);
    assert!(destination.iter().all(|byte| *byte == 0xa5));
    Ok(())
}

#[test]
fn into_inner_returns_owned_source() -> Result<(), Error> {
    let source = TestSource::exact(patterned_pages(1));
    let reader = Jet3PageReader::new(source)?;
    let extracted = reader.into_inner();
    assert_eq!(extracted.bytes.len(), PAGE_BYTES);
    assert_eq!(extracted.reads, 0);
    Ok(())
}

fn assert_uncharged(operation: &mut ResourceBudget) {
    assert_eq!(operation.read_budget().total_read(), ByteCount::new(0));
    assert_eq!(operation.page_visits(), 0);
    assert_eq!(operation.total_work_units(), 0);
}

fn assert_failed_read_charged(operation: &mut ResourceBudget) {
    assert_eq!(operation.read_budget().total_read(), JET3_PAGE_SIZE);
    assert_eq!(operation.page_visits(), 1);
    assert_eq!(operation.total_work_units(), 1);
}
