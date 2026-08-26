use std::error::Error as StdError;
use std::io;

use super::{
    DatabaseFormatError, DatabaseHeaderPage, DatabaseHeaderPageError, DatabaseProtection,
    DatabaseVersion,
};
use crate::{
    ByteCount, ByteOffset, Error, HeaderError, JetFileKind, LimitKind, RawJet3Candidate, ReadAt,
    ReadBudget, ReadLimits, ResourceBudget, ResourceLimits, SliceSource,
};

const PAGE_BYTES: usize = 2_048;
const SIGNATURE_START: usize = 4;
const SIGNATURE_END: usize = 19;
const COMMIT_START: usize = 0x600;
const COMMIT_END: usize = 0x800;

fn raw_page(signature: &[u8; 15]) -> [u8; PAGE_BYTES] {
    let mut raw = [0xA5; PAGE_BYTES];
    raw[SIGNATURE_START..SIGNATURE_END].copy_from_slice(signature);
    for (index, byte) in raw[COMMIT_START..COMMIT_END].iter_mut().enumerate() {
        *byte = index as u8;
    }
    raw
}

fn supported_raw_page() -> [u8; PAGE_BYTES] {
    let mut raw = raw_page(b"Standard Jet DB");
    raw[0x14] = 0x00;
    raw[0x41] = 0x4e;
    raw[0x42..0x50].copy_from_slice(&[
        0x86, 0xfb, 0xec, 0x37, 0x5d, 0x44, 0x9c, 0xfa, 0xc6, 0x5e, 0x28, 0xe6, 0x13, 0xb6,
    ]);
    raw
}

#[test]
fn supported_format_accepts_only_the_observed_v1_opening_state() -> Result<(), Box<dyn StdError>> {
    let view = DatabaseHeaderPage::from_raw_bytes(supported_raw_page())?;
    let format = view.supported_format()?;
    assert_eq!(format.version(), DatabaseVersion::Jet3);
    assert_eq!(
        format.protection(),
        DatabaseProtection::UnencryptedWithoutPassword
    );
    Ok(())
}

#[test]
fn supported_format_rejects_the_observed_jet4_marker() -> Result<(), HeaderError> {
    for observed in 1..=u8::MAX {
        let mut raw = supported_raw_page();
        raw[0x14] = observed;
        let view = DatabaseHeaderPage::from_raw_bytes(raw)?;
        assert_eq!(
            view.supported_format(),
            Err(DatabaseFormatError::UnsupportedVersion { observed })
        );
    }
    Ok(())
}

#[test]
fn supported_format_rejects_the_observed_encrypted_marker() -> Result<(), HeaderError> {
    for observed in 0..=u8::MAX {
        if observed == 0x4e {
            continue;
        }
        let mut raw = supported_raw_page();
        raw[0x41] = observed;
        let view = DatabaseHeaderPage::from_raw_bytes(raw)?;
        assert_eq!(
            view.supported_format(),
            Err(DatabaseFormatError::EncryptedOrUnsupported { observed })
        );
    }
    Ok(())
}

#[test]
fn supported_format_rejects_passworded_or_unknown_header_state() -> Result<(), HeaderError> {
    for offset in 0x42..0x50 {
        let mut raw = supported_raw_page();
        raw[offset] ^= 0x01;
        let view = DatabaseHeaderPage::from_raw_bytes(raw)?;
        assert_eq!(
            view.supported_format(),
            Err(DatabaseFormatError::PasswordedOrUnsupported)
        );
    }
    Ok(())
}

fn operation_budget(single_read: u64, total_read: u64) -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::new(ReadLimits::new(
        ByteCount::new(PAGE_BYTES as u64),
        ByteCount::new(single_read),
        ByteCount::new(total_read),
    )))
}

#[test]
fn view_preserves_the_complete_page_and_exposes_only_documented_fields() -> Result<(), HeaderError>
{
    let raw = raw_page(b"Standard Jet DB");
    let view = DatabaseHeaderPage::from_raw_bytes(raw)?;

    assert_eq!(view.raw_bytes(), &raw);
    assert_eq!(view.signature_kind(), JetFileKind::Standard);
    assert_eq!(
        view.commit_region().raw_bytes().as_slice(),
        &raw[COMMIT_START..COMMIT_END]
    );
    assert_eq!(
        view.commit_region().slot(0).map(|slot| slot.raw()),
        Some([0, 1])
    );
    assert_eq!(
        view.commit_region().slot(255).map(|slot| slot.raw()),
        Some([254, 255])
    );
    Ok(())
}

#[test]
fn view_accepts_every_documented_generic_signature_kind() -> Result<(), HeaderError> {
    for (signature, expected) in [
        (b"Standard Jet DB", JetFileKind::Standard),
        (b"Jet System DB x", JetFileKind::System),
        (b"Temp Jet DB xyz", JetFileKind::Temporary),
    ] {
        let view = DatabaseHeaderPage::from_raw_bytes(raw_page(signature))?;
        assert_eq!(view.signature_kind(), expected);
    }
    Ok(())
}

#[test]
fn unknown_signature_preserves_the_exact_observation() {
    let observed = *b"Not a Jet file!";
    assert_eq!(
        DatabaseHeaderPage::from_raw_bytes(raw_page(&observed)),
        Err(HeaderError::UnknownSignature { observed })
    );
}

#[test]
fn candidate_reads_one_complete_page_zero_with_shared_accounting() -> Result<(), Box<dyn StdError>>
{
    let raw = raw_page(b"Standard Jet DB");
    let mut budget = operation_budget(PAGE_BYTES as u64, (PAGE_BYTES + 15) as u64);
    let source = SliceSource::new(&raw, budget.read_budget())?;
    let mut candidate = RawJet3Candidate::inspect(source, &mut budget)?;

    let view = candidate.read_database_header_page(&mut budget)?;

    assert_eq!(view.raw_bytes(), &raw);
    assert_eq!(view.signature_kind(), JetFileKind::Standard);
    assert_eq!(
        budget.read_budget().total_read(),
        ByteCount::new((PAGE_BYTES + 15) as u64)
    );
    assert_eq!(budget.page_visits(), 1);
    Ok(())
}

#[derive(Debug)]
struct FailingPageSource {
    raw: [u8; PAGE_BYTES],
    reads: usize,
}

impl ReadAt for FailingPageSource {
    fn len(&self) -> ByteCount {
        ByteCount::new(PAGE_BYTES as u64)
    }

    fn read_exact_at(
        &mut self,
        _offset: ByteOffset,
        destination: &mut [u8],
        budget: &mut ReadBudget,
    ) -> Result<(), Error> {
        let count = ByteCount::from_usize(destination.len())?;
        budget.charge_read_attempt(count)?;
        self.reads += 1;
        if destination.len() == 15 {
            destination.copy_from_slice(&self.raw[SIGNATURE_START..SIGNATURE_END]);
            return Ok(());
        }
        destination[..31].fill(0xD3);
        Err(Error::Io {
            operation: "read failing database-header test source",
            kind: io::ErrorKind::Other,
        })
    }
}

#[test]
fn failed_complete_page_read_returns_only_a_structured_error() -> Result<(), Box<dyn StdError>> {
    let source = FailingPageSource {
        raw: raw_page(b"Standard Jet DB"),
        reads: 0,
    };
    let mut budget = operation_budget(PAGE_BYTES as u64, (PAGE_BYTES + 15) as u64);
    let mut candidate = RawJet3Candidate::inspect(source, &mut budget)?;

    assert_eq!(
        candidate.read_database_header_page(&mut budget),
        Err(DatabaseHeaderPageError::Read(Error::Io {
            operation: "read failing database-header test source",
            kind: io::ErrorKind::Other,
        }))
    );
    assert_eq!(candidate.source().reads, 2);
    assert_eq!(budget.page_visits(), 1);
    assert_eq!(
        budget.read_budget().total_read(),
        ByteCount::new((PAGE_BYTES + 15) as u64)
    );
    Ok(())
}

#[test]
fn page_read_limit_rejection_precedes_source_access_and_page_charging()
-> Result<(), Box<dyn StdError>> {
    let source = FailingPageSource {
        raw: raw_page(b"Standard Jet DB"),
        reads: 0,
    };
    let maximum = (PAGE_BYTES - 1) as u64;
    let mut budget = operation_budget(maximum, u64::MAX);
    let mut candidate = RawJet3Candidate::inspect(source, &mut budget)?;

    assert_eq!(
        candidate.read_database_header_page(&mut budget),
        Err(DatabaseHeaderPageError::Read(Error::LimitExceeded {
            kind: LimitKind::SingleReadBytes,
            requested: ByteCount::new(PAGE_BYTES as u64),
            maximum: ByteCount::new(maximum),
        }))
    );
    assert_eq!(candidate.source().reads, 1);
    assert_eq!(budget.page_visits(), 0);
    assert_eq!(budget.read_budget().total_read(), ByteCount::new(15));
    Ok(())
}
