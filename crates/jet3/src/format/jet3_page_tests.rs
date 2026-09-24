use crate::{
    ByteCount, ByteOffset, Error, JET3_PAGE_SIZE, LimitKind, PAGE_BYTES, PageNumber, ReadAt,
    ReadBudget, ReadLimits, ResourceBudget, ResourceLimitKind, ResourceLimits,
};
use std::cell::Cell;
use std::io;
use std::rc::Rc;

use super::jet3_page::Jet3PageReader;

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
fn exact_limits_read_first_and_last_complete_pages() -> Result<(), Error> {
    let mut reader = Jet3PageReader::new(TestSource::exact(patterned_pages(3)))?;
    let mut destination = [0_u8; PAGE_BYTES];
    let mut operation = budget(JET3_PAGE_SIZE.get(), 2 * JET3_PAGE_SIZE.get(), 2, 2);

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
fn rejected_first_reads_charge_nothing_and_skip_the_source() -> Result<(), Error> {
    let below = JET3_PAGE_SIZE.get() - 1;
    let out_of_bounds = |page, page_count| Error::PageOutOfBounds { page, page_count };
    let read_limit = |kind| Error::LimitExceeded {
        kind,
        requested: JET3_PAGE_SIZE,
        maximum: ByteCount::new(below),
    };
    let cases = [
        (
            "page at count",
            2,
            2,
            permissive_budget(),
            out_of_bounds(2, 2),
        ),
        (
            "page above count",
            2,
            3,
            permissive_budget(),
            out_of_bounds(3, 2),
        ),
        (
            "empty source",
            0,
            0,
            permissive_budget(),
            out_of_bounds(0, 0),
        ),
        (
            "read limit before page bounds",
            1,
            1,
            budget(below, u64::MAX, 1, 1),
            read_limit(LimitKind::SingleReadBytes),
        ),
        (
            "single read one below",
            1,
            0,
            budget(below, u64::MAX, 1, 1),
            read_limit(LimitKind::SingleReadBytes),
        ),
        (
            "total read one below",
            1,
            0,
            budget(u64::MAX, below, 1, 1),
            read_limit(LimitKind::TotalReadBytes),
        ),
    ];
    for (label, pages, page, mut operation, expected) in cases {
        let mut reader = Jet3PageReader::new(TestSource::exact(patterned_pages(pages)))?;
        let mut destination = [0_u8; PAGE_BYTES];
        assert_eq!(
            reader.read_page(PageNumber::new(page), &mut destination, &mut operation),
            Err(expected),
            "{label}"
        );
        assert_eq!(
            operation.read_budget().total_read(),
            ByteCount::new(0),
            "{label}"
        );
        assert_eq!(operation.page_visits(), 0, "{label}");
        assert_eq!(operation.total_work_units(), 0, "{label}");
        assert_eq!(reader.source().reads, 0, "{label}");
    }
    Ok(())
}

#[test]
fn cumulative_limits_reject_the_second_read_after_one_page() -> Result<(), Error> {
    let size = JET3_PAGE_SIZE.get();
    let cases = [
        (
            0,
            budget(size, size, u64::MAX, u64::MAX),
            Error::LimitExceeded {
                kind: LimitKind::TotalReadBytes,
                requested: ByteCount::new(2 * size),
                maximum: JET3_PAGE_SIZE,
            },
        ),
        (
            1,
            budget(u64::MAX, u64::MAX, 1, u64::MAX),
            Error::ResourceLimitExceeded {
                kind: ResourceLimitKind::PageVisits,
                requested: 2,
                maximum: 1,
            },
        ),
        (
            1,
            budget(u64::MAX, u64::MAX, u64::MAX, 1),
            Error::ResourceLimitExceeded {
                kind: ResourceLimitKind::TotalWorkUnits,
                requested: 2,
                maximum: 1,
            },
        ),
    ];
    for (second_page, mut operation, expected) in cases {
        let mut reader = Jet3PageReader::new(TestSource::exact(patterned_pages(2)))?;
        let mut destination = [0_u8; PAGE_BYTES];

        reader.read_page(PageNumber::new(0), &mut destination, &mut operation)?;
        assert_eq!(
            reader.read_page(
                PageNumber::new(second_page),
                &mut destination,
                &mut operation
            ),
            Err(expected)
        );
        assert_eq!(reader.source().reads, 1);
        assert_eq!(operation.read_budget().total_read(), JET3_PAGE_SIZE);
        assert_eq!(operation.page_visits(), 1);
        assert_eq!(operation.total_work_units(), 1);
    }
    Ok(())
}

#[test]
fn failed_source_reads_charge_attempted_bytes_and_page_visit() -> Result<(), Error> {
    for (behavior, expected) in [
        (
            ReadBehavior::Short,
            Error::ShortRead {
                offset: ByteOffset::new(0),
                needed: JET3_PAGE_SIZE,
                actual: ByteCount::new(JET3_PAGE_SIZE.get() - 1),
            },
        ),
        (
            ReadBehavior::Fault,
            Error::Io {
                operation: "read test source",
                kind: io::ErrorKind::Other,
            },
        ),
    ] {
        let source = TestSource::with_behavior(patterned_pages(1), behavior);
        let mut reader = Jet3PageReader::new(source)?;
        let mut destination = [0x5a_u8; PAGE_BYTES];
        let mut operation = permissive_budget();

        assert_eq!(
            reader.read_page(PageNumber::new(0), &mut destination, &mut operation),
            Err(expected)
        );
        assert_eq!(operation.read_budget().total_read(), JET3_PAGE_SIZE);
        assert_eq!(operation.page_visits(), 1);
        assert_eq!(operation.total_work_units(), 1);
        assert_eq!(reader.source().reads, 1);
        assert!(destination.iter().all(|byte| *byte == 0x5a));
    }
    Ok(())
}
