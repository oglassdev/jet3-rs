use std::cell::RefCell;
use std::error::Error as StdError;
use std::io;
use std::rc::Rc;

use super::{CandidateError, RawJet3Candidate};
use crate::{
    ByteCount, ByteOffset, Error, HeaderError, JET3_PAGE_SIZE, JetFileKind, LimitKind, PageNumber,
    ReadAt, ReadBudget, ReadLimits, ResourceBudget, ResourceLimits, SliceSource,
};

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;

fn candidate_bytes(page_count: usize) -> Vec<u8> {
    candidate_bytes_with(page_count, b"Standard Jet DB")
}

fn candidate_bytes_with(page_count: usize, signature: &[u8; 15]) -> Vec<u8> {
    let mut bytes = vec![0_u8; page_count * PAGE_BYTES];
    bytes[4..19].copy_from_slice(signature);
    for (index, byte) in bytes.iter_mut().enumerate().skip(19) {
        *byte = (index % 251) as u8;
    }
    bytes
}

fn budget(input: u64, single: u64, total: u64, pages: u64, work: u64) -> ResourceBudget {
    ResourceBudget::new(
        ResourceLimits::new(ReadLimits::new(
            ByteCount::new(input),
            ByteCount::new(single),
            ByteCount::new(total),
        ))
        .with_max_page_visits(pages)
        .with_max_total_work_units(work),
    )
}

#[test]
fn successful_inspection_charges_only_exact_signature_bytes() -> Result<(), CandidateError> {
    let bytes = candidate_bytes(2);
    let mut operation = budget(bytes.len() as u64, PAGE_BYTES as u64, 15, 0, 0);
    let source = SliceSource::new(&bytes, operation.read_budget())
        .unwrap_or_else(|error| unreachable!("bounded fixture must construct: {error}"));

    let candidate = RawJet3Candidate::inspect(source, &mut operation)?;

    assert_eq!(candidate.signature_kind(), JetFileKind::Standard);
    assert_eq!(candidate.geometry().page_count(), 2);
    assert_eq!(candidate.geometry().source_len(), ByteCount::new(4_096));
    assert_eq!(operation.read_budget().total_read(), ByteCount::new(15));
    assert_eq!(operation.page_visits(), 0);
    assert_eq!(operation.total_work_units(), 0);
    Ok(())
}

#[test]
fn inspection_preserves_all_documented_generic_signature_kinds() -> Result<(), CandidateError> {
    for (signature, expected) in [
        (b"Standard Jet DB", JetFileKind::Standard),
        (b"Jet System DB x", JetFileKind::System),
        (b"Temp Jet DB xyz", JetFileKind::Temporary),
    ] {
        let bytes = candidate_bytes_with(1, signature);
        let mut operation = budget(bytes.len() as u64, 15, 15, 0, 0);
        let source = SliceSource::new(&bytes, operation.read_budget())
            .unwrap_or_else(|error| unreachable!("bounded fixture must construct: {error}"));
        let candidate = RawJet3Candidate::inspect(source, &mut operation)?;
        assert_eq!(candidate.signature_kind(), expected);
        assert_eq!(operation.read_budget().total_read(), ByteCount::new(15));
    }
    Ok(())
}

#[test]
fn bytes_outside_signature_window_do_not_change_inspection() -> Result<(), CandidateError> {
    let mut bytes = candidate_bytes(1);
    bytes[..4].copy_from_slice(&[0xff, 0xa5, 0x5a, 0x01]);
    bytes[19] = 0xff;
    bytes[PAGE_BYTES - 1] = 0x7e;
    let mut operation = budget(bytes.len() as u64, 15, 15, 0, 0);
    let source = SliceSource::new(&bytes, operation.read_budget())
        .unwrap_or_else(|error| unreachable!("bounded fixture must construct: {error}"));

    let candidate = RawJet3Candidate::inspect(source, &mut operation)?;

    assert_eq!(candidate.signature_kind(), JetFileKind::Standard);
    assert_eq!(candidate.geometry().page_count(), 1);
    assert_eq!(operation.read_budget().total_read(), ByteCount::new(15));
    Ok(())
}

#[test]
fn every_truncation_before_complete_signature_is_structured() {
    let complete = candidate_bytes(1);
    for length in 0..19 {
        let bytes = &complete[..length];
        let mut operation = budget(length as u64, 15, 15, 0, 0);
        let source = SliceSource::new(bytes, operation.read_budget())
            .unwrap_or_else(|error| unreachable!("bounded fixture must construct: {error}"));
        assert!(matches!(
            RawJet3Candidate::inspect(source, &mut operation),
            Err(CandidateError::Signature(HeaderError::Read(
                Error::OffsetOutOfBounds { .. } | Error::UnexpectedEnd { .. }
            )))
        ));
        assert_eq!(operation.read_budget().total_read(), ByteCount::new(0));
        assert_eq!(operation.page_visits(), 0);
    }
}

#[test]
fn signature_failure_precedes_partial_geometry() {
    let bytes = vec![0_u8; PAGE_BYTES + 1];
    let mut operation = budget(bytes.len() as u64, 15, 15, 0, 0);
    let source = SliceSource::new(&bytes, operation.read_budget())
        .unwrap_or_else(|error| unreachable!("bounded fixture must construct: {error}"));

    assert!(matches!(
        RawJet3Candidate::inspect(source, &mut operation),
        Err(CandidateError::Signature(
            HeaderError::UnknownSignature { .. }
        ))
    ));
    assert_eq!(operation.read_budget().total_read(), ByteCount::new(15));
    assert_eq!(operation.page_visits(), 0);
}

#[test]
fn recognized_signature_reports_geometry_only_after_signature_read() {
    let mut bytes = vec![0_u8; PAGE_BYTES + 1];
    bytes[4..19].copy_from_slice(b"Standard Jet DB");
    let mut operation = budget(bytes.len() as u64, 15, 15, 0, 0);
    let source = SliceSource::new(&bytes, operation.read_budget())
        .unwrap_or_else(|error| unreachable!("bounded fixture must construct: {error}"));

    assert_eq!(
        RawJet3Candidate::inspect(source, &mut operation).map(|_| ()),
        Err(CandidateError::Geometry(Error::PartialPage {
            input_len: ByteCount::new((PAGE_BYTES + 1) as u64),
            page_size: JET3_PAGE_SIZE,
            trailing: ByteCount::new(1),
        }))
    );
    assert_eq!(operation.read_budget().total_read(), ByteCount::new(15));
    assert_eq!(operation.page_visits(), 0);
}

#[test]
fn signature_budget_rejection_is_structured_and_atomic() {
    let bytes = candidate_bytes(1);
    let mut operation = budget(bytes.len() as u64, 14, 15, 0, 0);
    let source = SliceSource::new(&bytes, operation.read_budget())
        .unwrap_or_else(|error| unreachable!("bounded fixture must construct: {error}"));

    assert_eq!(
        RawJet3Candidate::inspect(source, &mut operation).map(|_| ()),
        Err(CandidateError::Signature(HeaderError::Read(
            Error::LimitExceeded {
                kind: LimitKind::SingleReadBytes,
                requested: ByteCount::new(15),
                maximum: ByteCount::new(14),
            }
        )))
    );
    assert_eq!(operation.read_budget().total_read(), ByteCount::new(0));
    assert_eq!(operation.page_visits(), 0);
}

#[test]
fn total_read_budget_one_below_signature_is_atomic() {
    let bytes = candidate_bytes(1);
    let mut operation = budget(bytes.len() as u64, 15, 14, 0, 0);
    let source = SliceSource::new(&bytes, operation.read_budget())
        .unwrap_or_else(|error| unreachable!("bounded fixture must construct: {error}"));

    assert_eq!(
        RawJet3Candidate::inspect(source, &mut operation).map(|_| ()),
        Err(CandidateError::Signature(HeaderError::Read(
            Error::LimitExceeded {
                kind: LimitKind::TotalReadBytes,
                requested: ByteCount::new(15),
                maximum: ByteCount::new(14),
            }
        )))
    );
    assert_eq!(operation.read_budget().total_read(), ByteCount::new(0));
    assert_eq!(operation.page_visits(), 0);
}

#[test]
fn raw_page_read_delegates_exact_bytes_and_accounting() -> Result<(), Box<dyn StdError>> {
    let bytes = candidate_bytes(2);
    let expected = bytes[PAGE_BYTES..].to_vec();
    let mut operation = budget(
        bytes.len() as u64,
        PAGE_BYTES as u64,
        15 + PAGE_BYTES as u64,
        1,
        1,
    );
    let source = SliceSource::new(&bytes, operation.read_budget())
        .unwrap_or_else(|error| unreachable!("bounded fixture must construct: {error}"));
    let mut candidate = RawJet3Candidate::inspect(source, &mut operation)?;
    let mut destination = [0_u8; PAGE_BYTES];

    candidate.read_raw_page(PageNumber::new(1), &mut destination, &mut operation)?;

    assert_eq!(destination.as_slice(), expected);
    assert_eq!(
        operation.read_budget().total_read(),
        ByteCount::new(15 + PAGE_BYTES as u64)
    );
    assert_eq!(operation.page_visits(), 1);
    assert_eq!(operation.total_work_units(), 1);
    Ok(())
}

#[test]
fn invalid_page_preserves_destination_and_counters() -> Result<(), CandidateError> {
    let bytes = candidate_bytes(1);
    let mut operation = budget(
        bytes.len() as u64,
        PAGE_BYTES as u64,
        15 + PAGE_BYTES as u64,
        1,
        1,
    );
    let source = SliceSource::new(&bytes, operation.read_budget())
        .unwrap_or_else(|error| unreachable!("bounded fixture must construct: {error}"));
    let mut candidate = RawJet3Candidate::inspect(source, &mut operation)?;
    let mut destination = [0xa5_u8; PAGE_BYTES];

    let result = candidate.read_raw_page(PageNumber::new(1), &mut destination, &mut operation);

    assert!(matches!(
        result,
        Err(Error::PageOutOfBounds {
            page,
            page_count: 1,
        }) if page == 1
    ));
    assert_eq!(destination, [0xa5; PAGE_BYTES]);
    assert_eq!(operation.read_budget().total_read(), ByteCount::new(15));
    assert_eq!(operation.page_visits(), 0);
    assert_eq!(operation.total_work_units(), 0);
    Ok(())
}

#[derive(Debug)]
struct FailingPageSource {
    length: ByteCount,
    reads: u64,
}

impl ReadAt for FailingPageSource {
    fn len(&self) -> ByteCount {
        self.length
    }

    fn read_exact_at(
        &mut self,
        offset: ByteOffset,
        destination: &mut [u8],
        budget: &mut ReadBudget,
    ) -> Result<(), Error> {
        budget.charge_read_attempt(ByteCount::from_usize(destination.len())?)?;
        self.reads = self.reads.checked_add(1).ok_or(Error::Arithmetic {
            operation: "count candidate test reads",
        })?;
        if offset == ByteOffset::new(4) && destination.len() == 15 {
            destination.copy_from_slice(b"Standard Jet DB");
            Ok(())
        } else {
            Err(Error::Io {
                operation: "read failing candidate test source",
                kind: io::ErrorKind::Other,
            })
        }
    }
}

#[test]
fn source_failure_preserves_destination_after_delegated_attempt() -> Result<(), CandidateError> {
    let source = FailingPageSource {
        length: JET3_PAGE_SIZE,
        reads: 0,
    };
    let mut operation = budget(PAGE_BYTES as u64, PAGE_BYTES as u64, 15 + 2_048, 1, 1);
    let mut candidate = RawJet3Candidate::inspect(source, &mut operation)?;
    let mut destination = [0x5a_u8; PAGE_BYTES];

    let result = candidate.read_raw_page(PageNumber::new(0), &mut destination, &mut operation);

    assert!(matches!(
        result,
        Err(Error::Io {
            operation: "read failing candidate test source",
            kind: io::ErrorKind::Other,
        })
    ));
    assert_eq!(destination, [0x5a; PAGE_BYTES]);
    assert_eq!(operation.read_budget().total_read(), ByteCount::new(2_063));
    assert_eq!(operation.page_visits(), 1);
    assert_eq!(operation.total_work_units(), 1);
    assert_eq!(candidate.source().reads, 2);
    Ok(())
}

#[test]
fn input_policy_is_rechecked_before_signature_or_geometry() {
    let source = FailingPageSource {
        length: JET3_PAGE_SIZE,
        reads: 0,
    };
    let mut operation = budget((PAGE_BYTES - 1) as u64, 15, 15, 0, 0);

    assert_eq!(
        RawJet3Candidate::inspect(source, &mut operation).map(|_| ()),
        Err(CandidateError::Input(Error::LimitExceeded {
            kind: LimitKind::InputBytes,
            requested: JET3_PAGE_SIZE,
            maximum: ByteCount::new((PAGE_BYTES - 1) as u64),
        }))
    );
    assert_eq!(operation.read_budget().total_read(), ByteCount::new(0));
    assert_eq!(operation.page_visits(), 0);
}

#[derive(Debug)]
struct CapturedGrowingSource {
    bytes: Rc<RefCell<Vec<u8>>>,
    captured_len: ByteCount,
}

impl ReadAt for CapturedGrowingSource {
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
        let start = offset.to_usize()?;
        let end = start
            .checked_add(destination.len())
            .ok_or(Error::Arithmetic {
                operation: "compute captured-source read end",
            })?;
        if end > self.captured_len.to_usize()? {
            return Err(Error::UnexpectedEnd {
                offset,
                needed: count,
                available: self
                    .captured_len
                    .checked_sub(ByteCount::new(start as u64))?,
            });
        }
        let bytes = self.bytes.borrow();
        let selected = bytes.get(start..end).ok_or(Error::UnexpectedEnd {
            offset,
            needed: count,
            available: ByteCount::new(bytes.len().saturating_sub(start) as u64),
        })?;
        destination.copy_from_slice(selected);
        Ok(())
    }
}

#[test]
fn inspection_and_page_reads_use_the_sources_captured_length() -> Result<(), Box<dyn StdError>> {
    let backing = Rc::new(RefCell::new(candidate_bytes(1)));
    let source = CapturedGrowingSource {
        bytes: Rc::clone(&backing),
        captured_len: JET3_PAGE_SIZE,
    };
    backing.borrow_mut().extend([0x5a; PAGE_BYTES]);
    let mut operation = budget(
        (PAGE_BYTES * 2) as u64,
        PAGE_BYTES as u64,
        15 + (PAGE_BYTES as u64 * 2),
        1,
        1,
    );

    let mut candidate = RawJet3Candidate::inspect(source, &mut operation)?;

    assert_eq!(candidate.geometry().source_len(), JET3_PAGE_SIZE);
    assert_eq!(candidate.geometry().page_count(), 1);
    assert_eq!(candidate.source().len(), JET3_PAGE_SIZE);
    assert_eq!(candidate.source().bytes.borrow().len(), PAGE_BYTES * 2);
    let mut destination = [0_u8; PAGE_BYTES];
    candidate.read_raw_page(PageNumber::new(0), &mut destination, &mut operation)?;
    assert!(matches!(
        candidate.read_raw_page(PageNumber::new(1), &mut destination, &mut operation),
        Err(Error::PageOutOfBounds {
            page: 1,
            page_count: 1
        })
    ));
    Ok(())
}

#[test]
fn source_accessors_return_the_original_owned_source() -> Result<(), CandidateError> {
    let bytes = candidate_bytes(1);
    let mut operation = budget(bytes.len() as u64, 15, 15, 0, 0);
    let source = SliceSource::new(&bytes, operation.read_budget())
        .unwrap_or_else(|error| unreachable!("bounded fixture must construct: {error}"));
    let candidate = RawJet3Candidate::inspect(source, &mut operation)?;

    assert_eq!(candidate.source().len(), JET3_PAGE_SIZE);
    assert_eq!(candidate.into_inner().len(), JET3_PAGE_SIZE);
    Ok(())
}

#[test]
fn display_and_error_sources_preserve_stage_context() {
    let input = CandidateError::Input(Error::LimitExceeded {
        kind: LimitKind::InputBytes,
        requested: ByteCount::new(2),
        maximum: ByteCount::new(1),
    });
    assert!(input.to_string().contains("candidate input policy failed"));
    assert!(StdError::source(&input).is_some());

    let signature = CandidateError::Signature(HeaderError::UnknownSignature {
        observed: *b"Not a Jet file!",
    });
    assert!(signature.to_string().contains("candidate signature failed"));
    assert!(StdError::source(&signature).is_some());

    let geometry = CandidateError::Geometry(Error::PartialPage {
        input_len: ByteCount::new(1),
        page_size: JET3_PAGE_SIZE,
        trailing: ByteCount::new(1),
    });
    assert!(geometry.to_string().contains("candidate geometry failed"));
    assert!(StdError::source(&geometry).is_some());
}
