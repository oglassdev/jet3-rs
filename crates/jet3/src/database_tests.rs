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

#[test]
fn inputs_shorter_than_the_signature_offset_are_rejected_before_any_read() -> TestResult {
    for length in [0_usize, 1, 2, SIGNATURE_START - 1] {
        let bytes = vec![0_u8; length];
        let mut operation = budget(PAGE_BYTES as u64, PAGE_BYTES as u64, u64::MAX);
        let source = source(&bytes, &mut operation)?;

        assert_eq!(
            DatabaseReader::from_source(source, &mut operation).err(),
            Some(DatabaseOpenError::Candidate(CandidateError::Signature(
                HeaderError::Read(Error::OffsetOutOfBounds {
                    offset: ByteOffset::new(SIGNATURE_START as u64),
                    input_len: ByteCount::new(length as u64),
                })
            )))
        );
        assert_eq!(operation.read_budget().total_read(), ByteCount::new(0));
        assert_eq!(operation.page_visits(), 0);
        assert_eq!(operation.total_work_units(), 0);
    }
    Ok(())
}

#[test]
fn an_input_of_exactly_the_signature_window_fails_geometry_after_recognition() -> TestResult {
    let complete = candidate_bytes(1, b"Standard Jet DB");
    let bytes = &complete[..SIGNATURE_END];
    let mut operation = budget(PAGE_BYTES as u64, PAGE_BYTES as u64, u64::MAX);
    let source = source(bytes, &mut operation)?;

    assert_eq!(
        DatabaseReader::from_source(source, &mut operation).err(),
        Some(DatabaseOpenError::Candidate(CandidateError::Geometry(
            Error::PartialPage {
                input_len: ByteCount::new(SIGNATURE_END as u64),
                page_size: JET3_PAGE_SIZE,
                trailing: ByteCount::new(SIGNATURE_END as u64),
            }
        )))
    );
    assert_eq!(operation.read_budget().total_read(), ByteCount::new(15));
    assert_eq!(operation.page_visits(), 0);
    assert_eq!(operation.total_work_units(), 0);
    Ok(())
}

#[test]
fn cumulative_read_ceiling_stops_page_zero_before_any_page_work() -> TestResult {
    let bytes = candidate_bytes(1, b"Standard Jet DB");
    let maximum = (PAGE_BYTES + 15 - 1) as u64;
    let mut operation = budget(PAGE_BYTES as u64, PAGE_BYTES as u64, maximum);
    let source = source(&bytes, &mut operation)?;

    assert_eq!(
        DatabaseReader::from_source(source, &mut operation).err(),
        Some(DatabaseOpenError::Header(DatabaseHeaderPageError::Read(
            Error::LimitExceeded {
                kind: LimitKind::TotalReadBytes,
                requested: ByteCount::new((PAGE_BYTES + 15) as u64),
                maximum: ByteCount::new(maximum),
            }
        )))
    );
    assert_eq!(operation.read_budget().total_read(), ByteCount::new(15));
    assert_eq!(operation.page_visits(), 0);
    assert_eq!(operation.total_work_units(), 0);
    Ok(())
}

#[test]
fn aggregate_work_ceiling_stops_page_zero_at_the_page_visit_charge() -> TestResult {
    let bytes = candidate_bytes(1, b"Standard Jet DB");
    let mut operation = ResourceBudget::new(
        limits(PAGE_BYTES as u64, PAGE_BYTES as u64, u64::MAX).with_max_total_work_units(0),
    );
    let source = source(&bytes, &mut operation)?;

    assert_eq!(
        DatabaseReader::from_source(source, &mut operation).err(),
        Some(DatabaseOpenError::Header(DatabaseHeaderPageError::Read(
            Error::ResourceLimitExceeded {
                kind: ResourceLimitKind::TotalWorkUnits,
                requested: 1,
                maximum: 0,
            }
        )))
    );
    assert_eq!(operation.read_budget().total_read(), ByteCount::new(15));
    assert_eq!(operation.page_visits(), 0);
    assert_eq!(operation.total_work_units(), 0);
    Ok(())
}

#[test]
fn opening_continues_an_already_charged_operation_budget() -> TestResult {
    let bytes = candidate_bytes(1, b"Standard Jet DB");
    let prior = 32_u64;
    let expected = prior + (PAGE_BYTES + 15) as u64;

    let mut operation = budget(PAGE_BYTES as u64, PAGE_BYTES as u64, expected);
    operation
        .read_budget()
        .charge_read_attempt(ByteCount::new(prior))?;
    operation.charge_page_visits(1)?;
    let opened_source = source(&bytes, &mut operation)?;
    let database = DatabaseReader::from_source(opened_source, &mut operation)?;

    assert_eq!(database.signature_kind(), JetFileKind::Standard);
    assert_eq!(
        operation.read_budget().total_read(),
        ByteCount::new(expected)
    );
    assert_eq!(operation.page_visits(), 2);
    assert_eq!(operation.total_work_units(), 2);

    let mut exhausted = budget(PAGE_BYTES as u64, PAGE_BYTES as u64, expected - 1);
    exhausted
        .read_budget()
        .charge_read_attempt(ByteCount::new(prior))?;
    let exhausted_source = source(&bytes, &mut exhausted)?;

    assert_eq!(
        DatabaseReader::from_source(exhausted_source, &mut exhausted).err(),
        Some(DatabaseOpenError::Header(DatabaseHeaderPageError::Read(
            Error::LimitExceeded {
                kind: LimitKind::TotalReadBytes,
                requested: ByteCount::new(expected),
                maximum: ByteCount::new(expected - 1),
            }
        )))
    );
    assert_eq!(
        exhausted.read_budget().total_read(),
        ByteCount::new(prior + 15)
    );
    assert_eq!(exhausted.page_visits(), 0);
    assert_eq!(exhausted.total_work_units(), 0);
    Ok(())
}

#[test]
fn opened_reader_streams_every_page_and_returns_its_owned_source() -> TestResult {
    let mut bytes = candidate_bytes(2, b"Standard Jet DB");
    bytes[PAGE_BYTES..].fill(0x5c);
    let expected_reads = (3 * PAGE_BYTES + 15) as u64;
    let mut operation = budget(bytes.len() as u64, PAGE_BYTES as u64, expected_reads);
    let source = source(&bytes, &mut operation)?;
    let mut database = DatabaseReader::from_source(source, &mut operation)?;
    {
        let mut cursor = database.raw_pages();
        let first = cursor
            .next_page(&mut operation)?
            .ok_or("captured page zero must be yielded")?;
        assert_eq!(first.number(), crate::PageNumber::new(0));
        assert_eq!(first.bytes().as_slice(), &bytes[..PAGE_BYTES]);
        let second = cursor
            .next_page(&mut operation)?
            .ok_or("captured page one must be yielded")?;
        assert_eq!(second.number(), crate::PageNumber::new(1));
        assert!(second.bytes().iter().all(|byte| *byte == 0x5c));
        assert!(cursor.next_page(&mut operation)?.is_none());
        assert_eq!(cursor.pages_read(), 2);
    }

    assert_eq!(operation.page_visits(), 3);
    assert_eq!(
        operation.read_budget().total_read(),
        ByteCount::new(expected_reads)
    );
    assert_eq!(
        database.into_source().len(),
        ByteCount::new(bytes.len() as u64)
    );
    Ok(())
}

#[derive(Debug)]
struct FaultingPageSource {
    captured_len: ByteCount,
    fault: Error,
}

impl ReadAt for FaultingPageSource {
    fn len(&self) -> ByteCount {
        self.captured_len
    }

    fn read_exact_at(
        &mut self,
        offset: ByteOffset,
        destination: &mut [u8],
        budget: &mut ReadBudget,
    ) -> Result<(), Error> {
        budget.charge_read_attempt(ByteCount::from_usize(destination.len())?)?;
        let request = (offset.get(), destination.len());
        if request == (SIGNATURE_START as u64, 15) {
            destination.copy_from_slice(b"Standard Jet DB");
            return Ok(());
        }
        if request == (0, PAGE_BYTES) {
            return Err(self.fault.clone());
        }
        Err(Error::Io {
            operation: "unexpected database test source request",
            kind: io::ErrorKind::InvalidInput,
        })
    }
}

#[test]
fn page_zero_source_faults_stay_structured_and_remain_charged() {
    for fault in [
        Error::ShortRead {
            offset: ByteOffset::new(0),
            needed: JET3_PAGE_SIZE,
            actual: ByteCount::new(0),
        },
        Error::Io {
            operation: "read faulting database test source",
            kind: io::ErrorKind::Other,
        },
    ] {
        let source = FaultingPageSource {
            captured_len: ByteCount::new(PAGE_BYTES as u64),
            fault: fault.clone(),
        };
        let mut operation = budget(PAGE_BYTES as u64, PAGE_BYTES as u64, u64::MAX);

        assert_eq!(
            DatabaseReader::from_source(source, &mut operation).err(),
            Some(DatabaseOpenError::Header(DatabaseHeaderPageError::Read(
                fault
            )))
        );
        assert_eq!(
            operation.read_budget().total_read(),
            ByteCount::new((PAGE_BYTES + 15) as u64)
        );
        assert_eq!(operation.page_visits(), 1);
        assert_eq!(operation.total_work_units(), 1);
    }
}

#[test]
fn display_and_error_sources_preserve_open_stage_context() {
    let observed = *b"Not a Jet file!";
    let opened = DatabaseOpenError::Source(Error::Io {
        operation: "open input file",
        kind: io::ErrorKind::NotFound,
    });
    let candidate =
        DatabaseOpenError::Candidate(CandidateError::Signature(HeaderError::UnknownSignature {
            observed,
        }));
    let header = DatabaseOpenError::Header(DatabaseHeaderPageError::Signature(
        HeaderError::UnknownSignature { observed },
    ));
    let changed = DatabaseOpenError::SignatureChanged {
        initial: JetFileKind::Standard,
        header: JetFileKind::System,
    };

    assert!(
        opened
            .to_string()
            .starts_with("database source open failed")
    );
    assert!(
        candidate
            .to_string()
            .starts_with("database candidate inspection failed")
    );
    assert!(
        header
            .to_string()
            .starts_with("database header validation failed")
    );
    let changed_text = changed.to_string();
    assert!(changed_text.starts_with("database signature changed while opening"));
    assert!(changed_text.contains("Standard") && changed_text.contains("System"));

    assert!(opened.source().is_some());
    assert!(candidate.source().is_some());
    assert!(header.source().is_some());
    assert!(changed.source().is_none());
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
