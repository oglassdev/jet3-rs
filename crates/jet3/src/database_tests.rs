use std::error::Error as StdError;
use std::fs;
use std::io;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

use super::{DatabaseOpenError, DatabaseReader};
use crate::{
    ByteCount, ByteOffset, CandidateError, DatabaseHeaderPageError, Error, HeaderError,
    JET3_PAGE_SIZE, JetFileKind, LimitKind, ReadAt, ReadBudget, ReadLimits, ResourceBudget,
    ResourceLimitKind, ResourceLimits, SliceSource,
};

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const SIGNATURE_START: usize = 4;
const SIGNATURE_END: usize = 19;

type TestResult = Result<(), Box<dyn StdError>>;

fn limits(max_input: u64, max_single_read: u64, max_total_read: u64) -> ResourceLimits {
    ResourceLimits::new(ReadLimits::new(
        ByteCount::new(max_input),
        ByteCount::new(max_single_read),
        ByteCount::new(max_total_read),
    ))
}

fn budget(max_input: u64, max_single_read: u64, max_total_read: u64) -> ResourceBudget {
    ResourceBudget::new(limits(max_input, max_single_read, max_total_read))
}

fn candidate_bytes(page_count: usize, signature: &[u8; 15]) -> Vec<u8> {
    let mut bytes = vec![0_u8; page_count * PAGE_BYTES];
    bytes[SIGNATURE_START..SIGNATURE_END].copy_from_slice(signature);
    bytes
}

fn source<'a>(bytes: &'a [u8], budget: &mut ResourceBudget) -> Result<SliceSource<'a>, Error> {
    SliceSource::new(bytes, budget.read_budget())
}

#[test]
fn opens_minimum_one_page_candidate_with_exact_accounting() -> TestResult {
    let bytes = candidate_bytes(1, b"Standard Jet DB");
    let expected_reads = (PAGE_BYTES + 15) as u64;
    let mut operation = budget(PAGE_BYTES as u64, PAGE_BYTES as u64, expected_reads);
    let source = source(&bytes, &mut operation)?;

    let database = DatabaseReader::from_source(source, &mut operation)?;

    assert_eq!(database.signature_kind(), JetFileKind::Standard);
    assert_eq!(database.geometry().page_count(), 1);
    assert_eq!(database.header().raw_bytes(), bytes.as_slice());
    assert_eq!(
        operation.read_budget().total_read(),
        ByteCount::new(expected_reads)
    );
    assert_eq!(operation.page_visits(), 1);
    assert_eq!(operation.total_work_units(), 1);
    assert_eq!(operation.allocation_bytes(), ByteCount::new(0));
    Ok(())
}

#[test]
fn retains_geometry_and_supports_bounded_raw_reads_after_open() -> TestResult {
    let mut bytes = candidate_bytes(2, b"Standard Jet DB");
    bytes[PAGE_BYTES..].fill(0xA7);
    let mut operation = budget(
        bytes.len() as u64,
        PAGE_BYTES as u64,
        (2 * PAGE_BYTES + 15) as u64,
    );
    let source = source(&bytes, &mut operation)?;
    let mut database = DatabaseReader::from_source(source, &mut operation)?;
    let mut page = [0_u8; PAGE_BYTES];

    database.read_raw_page(crate::PageNumber::new(1), &mut page, &mut operation)?;

    assert_eq!(database.geometry().page_count(), 2);
    assert!(page.iter().all(|byte| *byte == 0xA7));
    assert_eq!(operation.page_visits(), 2);
    Ok(())
}

#[test]
fn partial_final_page_is_rejected_before_page_visit() -> TestResult {
    let bytes = candidate_bytes(1, b"Standard Jet DB");
    let mut bytes = bytes[..PAGE_BYTES - 1].to_vec();
    bytes[SIGNATURE_START..SIGNATURE_END].copy_from_slice(b"Standard Jet DB");
    let mut operation = budget(u64::MAX, PAGE_BYTES as u64, u64::MAX);
    let source = source(&bytes, &mut operation)?;

    assert_eq!(
        DatabaseReader::from_source(source, &mut operation).err(),
        Some(DatabaseOpenError::Candidate(CandidateError::Geometry(
            Error::PartialPage {
                input_len: ByteCount::new((PAGE_BYTES - 1) as u64),
                page_size: JET3_PAGE_SIZE,
                trailing: ByteCount::new((PAGE_BYTES - 1) as u64),
            }
        )))
    );
    assert_eq!(operation.read_budget().total_read(), ByteCount::new(15));
    assert_eq!(operation.page_visits(), 0);
    Ok(())
}

#[test]
fn unknown_signature_is_a_bounded_structured_failure() -> TestResult {
    let observed = *b"Not a Jet file!";
    let bytes = candidate_bytes(1, &observed);
    let mut operation = budget(PAGE_BYTES as u64, PAGE_BYTES as u64, u64::MAX);
    let source = source(&bytes, &mut operation)?;

    assert_eq!(
        DatabaseReader::from_source(source, &mut operation).err(),
        Some(DatabaseOpenError::Candidate(CandidateError::Signature(
            HeaderError::UnknownSignature { observed }
        )))
    );
    assert_eq!(operation.read_budget().total_read(), ByteCount::new(15));
    assert_eq!(operation.page_visits(), 0);
    Ok(())
}

#[test]
fn truncated_signature_window_is_rejected_without_read_attempt() -> TestResult {
    let bytes = [0_u8; SIGNATURE_END - 1];
    let mut operation = budget(PAGE_BYTES as u64, PAGE_BYTES as u64, u64::MAX);
    let source = source(&bytes, &mut operation)?;

    assert_eq!(
        DatabaseReader::from_source(source, &mut operation).err(),
        Some(DatabaseOpenError::Candidate(CandidateError::Signature(
            HeaderError::Read(Error::UnexpectedEnd {
                offset: ByteOffset::new(4),
                needed: ByteCount::new(15),
                available: ByteCount::new(14),
            })
        )))
    );
    assert_eq!(operation.read_budget().total_read(), ByteCount::new(0));
    assert_eq!(operation.page_visits(), 0);
    Ok(())
}

#[derive(Debug)]
struct CountingSource {
    bytes: Vec<u8>,
    captured_len: ByteCount,
    reads: usize,
    full_page_signature: Option<[u8; 15]>,
}

impl ReadAt for CountingSource {
    fn len(&self) -> ByteCount {
        self.captured_len
    }

    fn read_exact_at(
        &mut self,
        offset: ByteOffset,
        destination: &mut [u8],
        budget: &mut ReadBudget,
    ) -> Result<(), Error> {
        let count = ByteCount::from_usize(destination.len())?;
        budget.charge_read_attempt(count)?;
        self.reads += 1;
        let start = offset.to_usize()?;
        let end = start
            .checked_add(destination.len())
            .ok_or(Error::Arithmetic {
                operation: "advance database test source",
            })?;
        destination.copy_from_slice(self.bytes.get(start..end).ok_or(Error::ShortRead {
            offset,
            needed: count,
            actual: ByteCount::new(0),
        })?);
        if let (PAGE_BYTES, Some(signature)) = (destination.len(), self.full_page_signature) {
            destination[SIGNATURE_START..SIGNATURE_END].copy_from_slice(&signature);
        }
        Ok(())
    }
}

#[test]
fn input_policy_rejection_precedes_source_access() {
    let bytes = candidate_bytes(1, b"Standard Jet DB");
    let source = CountingSource {
        bytes,
        captured_len: ByteCount::new(PAGE_BYTES as u64),
        reads: 0,
        full_page_signature: None,
    };
    let maximum = (PAGE_BYTES - 1) as u64;
    let mut operation = budget(maximum, PAGE_BYTES as u64, u64::MAX);

    assert_eq!(
        DatabaseReader::from_source(source, &mut operation).err(),
        Some(DatabaseOpenError::Candidate(CandidateError::Input(
            Error::LimitExceeded {
                kind: LimitKind::InputBytes,
                requested: ByteCount::new(PAGE_BYTES as u64),
                maximum: ByteCount::new(maximum),
            }
        )))
    );
    assert_eq!(operation.read_budget().total_read(), ByteCount::new(0));
    assert_eq!(operation.page_visits(), 0);
}

#[test]
fn header_read_limit_rejection_does_not_visit_or_access_page_zero() {
    let source = CountingSource {
        bytes: candidate_bytes(1, b"Standard Jet DB"),
        captured_len: ByteCount::new(PAGE_BYTES as u64),
        reads: 0,
        full_page_signature: None,
    };
    let maximum = (PAGE_BYTES - 1) as u64;
    let mut operation = budget(PAGE_BYTES as u64, maximum, u64::MAX);

    assert_eq!(
        DatabaseReader::from_source(source, &mut operation).err(),
        Some(DatabaseOpenError::Header(DatabaseHeaderPageError::Read(
            Error::LimitExceeded {
                kind: LimitKind::SingleReadBytes,
                requested: JET3_PAGE_SIZE,
                maximum: ByteCount::new(maximum),
            }
        )))
    );
    assert_eq!(operation.read_budget().total_read(), ByteCount::new(15));
    assert_eq!(operation.page_visits(), 0);
}

#[test]
fn page_visit_limit_rejection_precedes_complete_page_source_access() {
    let source = CountingSource {
        bytes: candidate_bytes(1, b"Standard Jet DB"),
        captured_len: ByteCount::new(PAGE_BYTES as u64),
        reads: 0,
        full_page_signature: None,
    };
    let mut operation = ResourceBudget::new(
        limits(PAGE_BYTES as u64, PAGE_BYTES as u64, u64::MAX).with_max_page_visits(0),
    );

    assert_eq!(
        DatabaseReader::from_source(source, &mut operation).err(),
        Some(DatabaseOpenError::Header(DatabaseHeaderPageError::Read(
            Error::ResourceLimitExceeded {
                kind: ResourceLimitKind::PageVisits,
                requested: 1,
                maximum: 0,
            }
        )))
    );
    assert_eq!(operation.read_budget().total_read(), ByteCount::new(15));
    assert_eq!(operation.page_visits(), 0);
}

#[test]
fn complete_page_revalidates_signature_after_initial_window() {
    let observed = *b"Not a Jet file!";
    let source = CountingSource {
        bytes: candidate_bytes(1, b"Standard Jet DB"),
        captured_len: ByteCount::new(PAGE_BYTES as u64),
        reads: 0,
        full_page_signature: Some(*b"Not a Jet file!"),
    };
    let mut operation = budget(PAGE_BYTES as u64, PAGE_BYTES as u64, u64::MAX);

    assert_eq!(
        DatabaseReader::from_source(source, &mut operation).err(),
        Some(DatabaseOpenError::Header(
            DatabaseHeaderPageError::Signature(HeaderError::UnknownSignature { observed })
        ))
    );
    assert_eq!(operation.page_visits(), 1);
    assert_eq!(
        operation.read_budget().total_read(),
        ByteCount::new((PAGE_BYTES + 15) as u64)
    );
}

#[test]
fn changing_to_another_recognized_signature_is_rejected() {
    let source = CountingSource {
        bytes: candidate_bytes(1, b"Standard Jet DB"),
        captured_len: ByteCount::new(PAGE_BYTES as u64),
        reads: 0,
        full_page_signature: Some(*b"Jet System DB x"),
    };
    let mut operation = budget(PAGE_BYTES as u64, PAGE_BYTES as u64, u64::MAX);

    assert_eq!(
        DatabaseReader::from_source(source, &mut operation).err(),
        Some(DatabaseOpenError::SignatureChanged {
            initial: JetFileKind::Standard,
            header: JetFileKind::System,
        })
    );
    assert_eq!(operation.page_visits(), 1);
    assert_eq!(
        operation.read_budget().total_read(),
        ByteCount::new((PAGE_BYTES + 15) as u64)
    );
}

static TEMP_COUNTER: AtomicU64 = AtomicU64::new(0);

fn temporary_path() -> PathBuf {
    let serial = TEMP_COUNTER.fetch_add(1, Ordering::Relaxed);
    std::env::temp_dir().join(format!(
        "jet3-database-open-{}-{serial}.mdb",
        std::process::id()
    ))
}

#[test]
fn opens_a_file_path_and_returns_its_bounded_source() -> TestResult {
    let path = temporary_path();
    let bytes = candidate_bytes(1, b"Standard Jet DB");
    fs::write(&path, &bytes)?;
    let result = (|| -> TestResult {
        let mut operation = budget(PAGE_BYTES as u64, PAGE_BYTES as u64, u64::MAX);
        let database = DatabaseReader::open(&path, &mut operation)?;
        assert_eq!(database.source().len(), ByteCount::new(PAGE_BYTES as u64));
        assert_eq!(database.header().raw_bytes(), bytes.as_slice());
        Ok(())
    })();
    let _ = fs::remove_file(path);
    result
}

#[test]
fn missing_file_returns_structured_source_error() {
    let path = temporary_path();
    let mut operation = budget(PAGE_BYTES as u64, PAGE_BYTES as u64, u64::MAX);

    assert_eq!(
        DatabaseReader::open(&path, &mut operation).err(),
        Some(DatabaseOpenError::Source(Error::Io {
            operation: "open input file",
            kind: io::ErrorKind::NotFound,
        }))
    );
    assert_eq!(operation.read_budget().total_read(), ByteCount::new(0));
    assert_eq!(operation.page_visits(), 0);
}
