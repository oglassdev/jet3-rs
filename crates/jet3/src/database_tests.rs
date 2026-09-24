use crate::{
    ByteCount, ByteOffset, CandidateError, DatabaseFormatError, DatabaseHeaderPageError,
    DatabaseProtection, DatabaseVersion, Error, HeaderError, JET3_PAGE_SIZE, JetFileKind,
    LimitKind, PAGE_BYTES, PageClassificationError, PageKind, PageNumber, ReadAt, ReadBudget,
    ReadLimits, ResourceBudget, ResourceLimitKind, ResourceLimits, SliceSource,
};
use std::fs;
use std::io;

use super::database::{DatabaseOpenError, DatabasePageError, DatabaseReader};

const SIGNATURE_START: usize = 4;
const SIGNATURE_END: usize = 19;
const PAGE: u64 = PAGE_BYTES as u64;

use crate::testkit::{TempDir, TestResult};

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
    crate::testkit::write_jet3_header_fields(&mut bytes);
    bytes
}

fn source<'a>(bytes: &'a [u8], budget: &mut ResourceBudget) -> Result<SliceSource<'a>, Error> {
    SliceSource::new(bytes, budget.read_budget())
}

fn assert_charged(operation: &mut ResourceBudget, read: u64, pages: u64, label: &str) {
    assert_eq!(
        operation.read_budget().total_read(),
        ByteCount::new(read),
        "{label}"
    );
    assert_eq!(operation.page_visits(), pages, "{label}");
    assert_eq!(operation.total_work_units(), pages, "{label}");
}

#[test]
fn opens_minimum_one_page_candidate_with_exact_accounting() -> TestResult {
    let bytes = candidate_bytes(1, b"Standard Jet DB");
    let mut operation = budget(PAGE, PAGE, PAGE + 15);
    let source = source(&bytes, &mut operation)?;

    let database = DatabaseReader::from_source(source, &mut operation)?;

    assert_eq!(database.signature_kind(), JetFileKind::Standard);
    assert_eq!(database.format().version(), DatabaseVersion::Jet3);
    assert_eq!(
        database.format().protection(),
        DatabaseProtection::UnencryptedWithoutPassword
    );
    assert_eq!(database.geometry().page_count(), 1);
    assert_eq!(database.header().raw_bytes(), bytes.as_slice());
    assert_charged(&mut operation, PAGE + 15, 1, "open");
    assert_eq!(operation.allocation_bytes(), ByteCount::new(0));
    Ok(())
}

#[test]
fn unsupported_version_and_protection_states_fail_closed() -> TestResult {
    for (offset, value, expected) in [
        (
            0x14,
            0x01,
            DatabaseFormatError::UnsupportedVersion { observed: 0x01 },
        ),
        (
            0x41,
            0xee,
            DatabaseFormatError::EncryptedOrUnsupported { observed: 0xee },
        ),
        (0x42, 0x00, DatabaseFormatError::PasswordedOrUnsupported),
    ] {
        let mut bytes = candidate_bytes(1, b"Standard Jet DB");
        bytes[offset] = value;
        let mut operation = budget(PAGE, PAGE, u64::MAX);
        let source = source(&bytes, &mut operation)?;
        assert_eq!(
            DatabaseReader::from_source(source, &mut operation).err(),
            Some(DatabaseOpenError::Format(expected))
        );
    }
    Ok(())
}

#[test]
fn candidate_failures_stop_before_any_page_visit() -> TestResult {
    let signed = candidate_bytes(1, b"Standard Jet DB");
    let partial = |length: usize| {
        CandidateError::Geometry(Error::PartialPage {
            input_len: ByteCount::new(length as u64),
            page_size: JET3_PAGE_SIZE,
            trailing: ByteCount::new(length as u64),
        })
    };
    let cases = [
        (
            vec![0; SIGNATURE_START - 1],
            CandidateError::Signature(HeaderError::Read(Error::OffsetOutOfBounds {
                offset: ByteOffset::new(SIGNATURE_START as u64),
                input_len: ByteCount::new(3),
            })),
            0,
        ),
        (
            vec![0; SIGNATURE_END - 1],
            CandidateError::Signature(HeaderError::Read(Error::UnexpectedEnd {
                offset: ByteOffset::new(4),
                needed: ByteCount::new(15),
                available: ByteCount::new(14),
            })),
            0,
        ),
        (
            candidate_bytes(1, b"Not a Jet file!"),
            CandidateError::Signature(HeaderError::UnknownSignature {
                observed: *b"Not a Jet file!",
            }),
            15,
        ),
        (signed[..SIGNATURE_END].to_vec(), partial(SIGNATURE_END), 15),
        (
            signed[..PAGE_BYTES - 1].to_vec(),
            partial(PAGE_BYTES - 1),
            15,
        ),
    ];
    for (bytes, expected, read) in cases {
        let label = format!("{} bytes", bytes.len());
        let mut operation = budget(PAGE, PAGE, u64::MAX);
        let source = source(&bytes, &mut operation)?;
        assert_eq!(
            DatabaseReader::from_source(source, &mut operation).err(),
            Some(DatabaseOpenError::Candidate(expected)),
            "{label}"
        );
        assert_charged(&mut operation, read, 0, &label);
    }
    Ok(())
}

#[test]
fn opened_reader_reads_and_streams_every_page_and_returns_its_source() -> TestResult {
    let mut bytes = candidate_bytes(2, b"Standard Jet DB");
    bytes[PAGE_BYTES..].fill(0x5c);
    let expected_reads = 4 * PAGE + 15;
    let mut operation = budget(bytes.len() as u64, PAGE, expected_reads);
    let source = source(&bytes, &mut operation)?;
    let mut database = DatabaseReader::from_source(source, &mut operation)?;
    assert_eq!(database.geometry().page_count(), 2);

    let mut page = [0_u8; PAGE_BYTES];
    database.read_raw_page(PageNumber::new(1), &mut page, &mut operation)?;
    assert!(page.iter().all(|byte| *byte == 0x5c));
    {
        let mut cursor = database.raw_pages();
        let first = cursor
            .next_page(&mut operation)?
            .ok_or("captured page zero must be yielded")?;
        assert_eq!(first.number(), PageNumber::new(0));
        assert_eq!(first.bytes().as_slice(), &bytes[..PAGE_BYTES]);
        let second = cursor
            .next_page(&mut operation)?
            .ok_or("captured page one must be yielded")?;
        assert_eq!(second.number(), PageNumber::new(1));
        assert!(second.bytes().iter().all(|byte| *byte == 0x5c));
        assert!(cursor.next_page(&mut operation)?.is_none());
        assert_eq!(cursor.pages_read(), 2);
    }

    assert_charged(&mut operation, expected_reads, 4, "streamed");
    assert_eq!(
        database.into_source().len(),
        ByteCount::new(bytes.len() as u64)
    );
    Ok(())
}

#[test]
fn opening_continues_an_already_charged_operation_budget() -> TestResult {
    let bytes = candidate_bytes(1, b"Standard Jet DB");
    let prior = 32_u64;
    let expected = prior + PAGE + 15;

    let mut operation = budget(PAGE, PAGE, expected);
    operation
        .read_budget()
        .charge_read_attempt(ByteCount::new(prior))?;
    operation.charge_page_visits(1)?;
    let opened_source = source(&bytes, &mut operation)?;
    let database = DatabaseReader::from_source(opened_source, &mut operation)?;

    assert_eq!(database.signature_kind(), JetFileKind::Standard);
    assert_charged(&mut operation, expected, 2, "continued");

    let mut exhausted = budget(PAGE, PAGE, expected - 1);
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
    assert_charged(&mut exhausted, prior + 15, 0, "exhausted");
    Ok(())
}

#[derive(Debug)]
struct TestSource {
    bytes: Vec<u8>,
    full_page_signature: Option<[u8; 15]>,
    page_fault: Option<Error>,
}

impl TestSource {
    fn new(full_page_signature: Option<[u8; 15]>, page_fault: Option<Error>) -> Self {
        Self {
            bytes: candidate_bytes(1, b"Standard Jet DB"),
            full_page_signature,
            page_fault,
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
        if let (PAGE_BYTES, Some(fault)) = (destination.len(), &self.page_fault) {
            return Err(fault.clone());
        }
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
fn resource_limits_reject_before_page_zero_is_visited() {
    let below = PAGE - 1;
    let cases = [
        (
            limits(below, PAGE, u64::MAX),
            DatabaseOpenError::Candidate(CandidateError::Input(Error::LimitExceeded {
                kind: LimitKind::InputBytes,
                requested: JET3_PAGE_SIZE,
                maximum: ByteCount::new(below),
            })),
            0,
        ),
        (
            limits(PAGE, below, u64::MAX),
            DatabaseOpenError::Header(DatabaseHeaderPageError::Read(Error::LimitExceeded {
                kind: LimitKind::SingleReadBytes,
                requested: JET3_PAGE_SIZE,
                maximum: ByteCount::new(below),
            })),
            15,
        ),
        (
            limits(PAGE, PAGE, u64::MAX).with_max_page_visits(0),
            DatabaseOpenError::Header(DatabaseHeaderPageError::Read(
                Error::ResourceLimitExceeded {
                    kind: ResourceLimitKind::PageVisits,
                    requested: 1,
                    maximum: 0,
                },
            )),
            15,
        ),
        (
            limits(PAGE, PAGE, u64::MAX).with_max_total_work_units(0),
            DatabaseOpenError::Header(DatabaseHeaderPageError::Read(
                Error::ResourceLimitExceeded {
                    kind: ResourceLimitKind::TotalWorkUnits,
                    requested: 1,
                    maximum: 0,
                },
            )),
            15,
        ),
    ];
    for (policy, expected, read) in cases {
        let label = format!("{expected:?}");
        let mut operation = ResourceBudget::new(policy);
        assert_eq!(
            DatabaseReader::from_source(TestSource::new(None, None), &mut operation).err(),
            Some(expected),
            "{label}"
        );
        assert_charged(&mut operation, read, 0, &label);
    }
}

#[test]
fn page_zero_faults_and_signature_changes_stay_structured_and_charged() {
    let io_fault = Error::Io {
        operation: "read faulting database test source",
        kind: io::ErrorKind::Other,
    };
    let short_fault = Error::ShortRead {
        offset: ByteOffset::new(0),
        needed: JET3_PAGE_SIZE,
        actual: ByteCount::new(0),
    };
    let cases = [
        (
            TestSource::new(None, Some(short_fault.clone())),
            DatabaseOpenError::Header(DatabaseHeaderPageError::Read(short_fault)),
        ),
        (
            TestSource::new(None, Some(io_fault.clone())),
            DatabaseOpenError::Header(DatabaseHeaderPageError::Read(io_fault)),
        ),
        (
            TestSource::new(Some(*b"Not a Jet file!"), None),
            DatabaseOpenError::Header(DatabaseHeaderPageError::Signature(
                HeaderError::UnknownSignature {
                    observed: *b"Not a Jet file!",
                },
            )),
        ),
        (
            TestSource::new(Some(*b"Jet System DB x"), None),
            DatabaseOpenError::SignatureChanged {
                initial: JetFileKind::Standard,
                header: JetFileKind::System,
            },
        ),
    ];
    for (source, expected) in cases {
        let label = format!("{expected:?}");
        let mut operation = budget(PAGE, PAGE, u64::MAX);
        assert_eq!(
            DatabaseReader::from_source(source, &mut operation).err(),
            Some(expected),
            "{label}"
        );
        assert_charged(&mut operation, PAGE + 15, 1, &label);
    }
}

#[test]
fn opens_a_file_path_and_reports_a_missing_file_as_a_source_error() -> TestResult {
    let directory = TempDir::new("database-open")?;
    let path = directory.target();
    let mut operation = budget(PAGE, PAGE, u64::MAX);

    assert_eq!(
        DatabaseReader::open(&path, &mut operation).err(),
        Some(DatabaseOpenError::Source(Error::Io {
            operation: "open input file",
            kind: io::ErrorKind::NotFound,
        }))
    );
    assert_charged(&mut operation, 0, 0, "missing");

    let bytes = candidate_bytes(1, b"Standard Jet DB");
    fs::write(&path, &bytes)?;
    let database = DatabaseReader::open(&path, &mut operation)?;
    assert_eq!(database.source().len(), ByteCount::new(PAGE));
    assert_eq!(database.header().raw_bytes(), bytes.as_slice());
    Ok(())
}

fn two_page_database(
    bytes: &[u8],
    work: u64,
) -> TestResult<(DatabaseReader<SliceSource<'_>>, ResourceBudget)> {
    let mut operation = ResourceBudget::new(
        limits(bytes.len() as u64, PAGE, 2 * PAGE + 15)
            .with_max_page_visits(2)
            .with_max_total_work_units(work),
    );
    let opened = source(bytes, &mut operation)?;
    let database = DatabaseReader::from_source(opened, &mut operation)?;
    Ok((database, operation))
}

#[test]
fn classified_page_read_charges_classification_after_a_successful_read() -> TestResult {
    let mut bytes = candidate_bytes(2, b"Standard Jet DB");
    bytes[PAGE_BYTES] = 0x03;
    bytes[PAGE_BYTES + 1..].fill(0xA7);
    let mut page = [0_u8; PAGE_BYTES];

    let (mut database, mut operation) = two_page_database(&bytes, 3)?;
    let classified =
        database.read_classified_page(PageNumber::new(1), &mut page, &mut operation)?;
    assert_eq!(classified.number(), PageNumber::new(1));
    assert_eq!(classified.kind(), PageKind::IntermediateIndex);
    assert_eq!(classified.raw_bytes(), &bytes[PAGE_BYTES..]);
    assert_eq!(operation.total_work_units(), 3);

    let (mut database, mut operation) = two_page_database(&bytes, 2)?;
    assert_eq!(
        database.read_classified_page(PageNumber::new(1), &mut page, &mut operation),
        Err(DatabasePageError::Classification(
            PageClassificationError::Resource(Error::ResourceLimitExceeded {
                kind: ResourceLimitKind::TotalWorkUnits,
                requested: 3,
                maximum: 2,
            })
        ))
    );
    assert_eq!(page.as_slice(), &bytes[PAGE_BYTES..]);
    assert_charged(&mut operation, 2 * PAGE + 15, 2, "classification rejected");

    let mut page = [0xA5_u8; PAGE_BYTES];
    let (mut database, mut operation) = two_page_database(&bytes, 3)?;
    assert_eq!(
        database.read_classified_page(PageNumber::new(2), &mut page, &mut operation),
        Err(DatabasePageError::Read(Error::PageOutOfBounds {
            page: 2,
            page_count: 2,
        }))
    );
    assert_eq!(page, [0xA5_u8; PAGE_BYTES]);
    assert_charged(&mut operation, PAGE + 15, 1, "read rejected");
    Ok(())
}
