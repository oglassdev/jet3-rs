use crate::{
    ByteCount, HeaderError, JetFileKind, PAGE_BYTES, RawJet3Candidate, ReadLimits, ResourceBudget,
    ResourceLimits, SliceSource,
};
use std::error::Error as StdError;

use super::database_header::{
    DatabaseFormatError, DatabaseHeaderPage, DatabaseProtection, DatabaseVersion,
};

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
    crate::testkit::write_jet3_header_fields(&mut raw);
    raw
}

fn format_with(offset: usize, change: impl FnOnce(&mut u8)) -> Result<DatabaseFormatError, String> {
    let mut raw = supported_raw_page();
    change(&mut raw[offset]);
    let view = DatabaseHeaderPage::from_raw_bytes(raw).map_err(|error| error.to_string())?;
    view.supported_format()
        .err()
        .ok_or_else(|| format!("offset {offset:#x} was accepted"))
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

    for observed in 1..=u8::MAX {
        assert_eq!(
            format_with(0x14, |byte| *byte = observed)?,
            DatabaseFormatError::UnsupportedVersion { observed }
        );
    }
    for observed in (0..=u8::MAX).filter(|observed| *observed != 0x4e) {
        assert_eq!(
            format_with(0x41, |byte| *byte = observed)?,
            DatabaseFormatError::EncryptedOrUnsupported { observed }
        );
    }
    for offset in 0x42..0x50 {
        assert_eq!(
            format_with(offset, |byte| *byte ^= 0x01)?,
            DatabaseFormatError::PasswordedOrUnsupported
        );
    }
    Ok(())
}

#[test]
fn view_preserves_the_complete_page_and_exposes_only_documented_fields() -> Result<(), HeaderError>
{
    let raw = raw_page(b"Standard Jet DB");
    let view = DatabaseHeaderPage::from_raw_bytes(raw)?;

    assert_eq!(view.raw_bytes(), &raw);
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

    for (signature, expected) in [
        (b"Standard Jet DB", Ok(JetFileKind::Standard)),
        (b"Jet System DB x", Ok(JetFileKind::System)),
        (b"Temp Jet DB xyz", Ok(JetFileKind::Temporary)),
        (
            b"Not a Jet file!",
            Err(HeaderError::UnknownSignature {
                observed: *b"Not a Jet file!",
            }),
        ),
    ] {
        assert_eq!(
            DatabaseHeaderPage::from_raw_bytes(raw_page(signature))
                .map(|view| view.signature_kind()),
            expected
        );
    }
    Ok(())
}

#[test]
fn candidate_reads_one_complete_page_zero_with_shared_accounting() -> Result<(), Box<dyn StdError>>
{
    let raw = raw_page(b"Standard Jet DB");
    let mut budget = ResourceBudget::new(ResourceLimits::new(ReadLimits::new(
        ByteCount::new(PAGE_BYTES as u64),
        ByteCount::new(PAGE_BYTES as u64),
        ByteCount::new((PAGE_BYTES + 15) as u64),
    )));
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
