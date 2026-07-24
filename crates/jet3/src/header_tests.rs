use std::error::Error as StdError;

use super::{HeaderError, JET3_PAGE_SIZE, JetFileKind, jet3_page_geometry, read_jet_signature};
use crate::{ByteCount, ByteOffset, Error, LimitKind, ReadBudget, ReadLimits, SliceSource};

const SIGNATURE_OFFSET: usize = 4;
const SIGNATURE_LENGTH: usize = 15;
const COMPLETE_HEADER_LENGTH: usize = SIGNATURE_OFFSET + SIGNATURE_LENGTH;

fn budget(single_read: u64, total_read: u64) -> ReadBudget {
    ReadBudget::new(ReadLimits::new(
        ByteCount::new(COMPLETE_HEADER_LENGTH as u64),
        ByteCount::new(single_read),
        ByteCount::new(total_read),
    ))
}

fn input_with(signature: [u8; SIGNATURE_LENGTH]) -> [u8; COMPLETE_HEADER_LENGTH] {
    let mut input = [0xA5; COMPLETE_HEADER_LENGTH];
    input[SIGNATURE_OFFSET..].copy_from_slice(&signature);
    input
}

fn classify(signature: [u8; SIGNATURE_LENGTH]) -> Result<JetFileKind, HeaderError> {
    let input = input_with(signature);
    let mut read_budget = budget(SIGNATURE_LENGTH as u64, SIGNATURE_LENGTH as u64);
    let mut source = SliceSource::new(&input, &read_budget)?;
    read_jet_signature(&mut source, &mut read_budget)
}

#[test]
fn recognizes_all_documented_file_kinds() {
    assert_eq!(classify(*b"Standard Jet DB"), Ok(JetFileKind::Standard));
    assert_eq!(classify(*b"Jet System DB x"), Ok(JetFileKind::System));
    assert_eq!(classify(*b"Temp Jet DB xyz"), Ok(JetFileKind::Temporary));
}

#[test]
fn reads_at_offset_four_despite_unrelated_leading_bytes() -> Result<(), HeaderError> {
    for leading in [
        [0_u8; SIGNATURE_OFFSET],
        [u8::MAX; SIGNATURE_OFFSET],
        [0x00, 0xFF, 0x55, 0xAA],
    ] {
        let mut input = input_with(*b"Standard Jet DB");
        input[..SIGNATURE_OFFSET].copy_from_slice(&leading);
        let mut read_budget = budget(SIGNATURE_LENGTH as u64, SIGNATURE_LENGTH as u64);
        let mut source = SliceSource::new(&input, &read_budget)?;
        assert_eq!(
            read_jet_signature(&mut source, &mut read_budget),
            Ok(JetFileKind::Standard)
        );
    }
    Ok(())
}

#[test]
fn shorter_documented_literals_ignore_the_unspecified_suffix() {
    for suffix in [[0_u8; 3], [u8::MAX; 3], [b' ', b'X', b'\n']] {
        let mut system = *b"Jet System DB  ";
        system[14] = suffix[0];
        assert_eq!(classify(system), Ok(JetFileKind::System));

        let mut temporary = *b"Temp Jet DB    ";
        temporary[12..].copy_from_slice(&suffix);
        assert_eq!(classify(temporary), Ok(JetFileKind::Temporary));
    }
}

#[test]
fn every_documented_signature_byte_is_significant() {
    let cases: &[(&[u8], [u8; SIGNATURE_LENGTH])] = &[
        (b"Standard Jet DB", *b"Standard Jet DB"),
        (b"Jet System DB ", *b"Jet System DB x"),
        (b"Temp Jet DB ", *b"Temp Jet DB xyz"),
    ];

    for (documented, signature) in cases {
        for index in 0..documented.len() {
            let mut mutated = *signature;
            mutated[index] ^= u8::MAX;
            assert!(
                matches!(
                    classify(mutated),
                    Err(HeaderError::UnknownSignature { observed }) if observed == mutated
                ),
                "mutation at documented byte {index} unexpectedly matched"
            );
        }
    }
}

#[test]
fn unknown_signature_preserves_the_complete_observation() {
    let observed = *b"Not a Jet file!";
    assert_eq!(
        classify(observed),
        Err(HeaderError::UnknownSignature { observed })
    );
}

#[test]
fn every_truncation_before_the_complete_window_is_structured() {
    let complete = input_with(*b"Standard Jet DB");

    for length in 0..COMPLETE_HEADER_LENGTH {
        let input = &complete[..length];
        let mut read_budget = budget(SIGNATURE_LENGTH as u64, SIGNATURE_LENGTH as u64);
        let mut source = SliceSource::new(input, &read_budget)
            .unwrap_or_else(|error| unreachable!("test input is within its limit: {error}"));
        let result = read_jet_signature(&mut source, &mut read_budget);

        if length < SIGNATURE_OFFSET {
            assert_eq!(
                result,
                Err(HeaderError::Read(Error::OffsetOutOfBounds {
                    offset: ByteOffset::new(SIGNATURE_OFFSET as u64),
                    input_len: ByteCount::new(length as u64),
                }))
            );
        } else {
            assert_eq!(
                result,
                Err(HeaderError::Read(Error::UnexpectedEnd {
                    offset: ByteOffset::new(SIGNATURE_OFFSET as u64),
                    needed: ByteCount::new(SIGNATURE_LENGTH as u64),
                    available: ByteCount::new((length - SIGNATURE_OFFSET) as u64),
                }))
            );
        }
    }
}

#[test]
fn exact_read_budget_is_accepted() -> Result<(), HeaderError> {
    let input = input_with(*b"Standard Jet DB");
    let mut read_budget = budget(SIGNATURE_LENGTH as u64, SIGNATURE_LENGTH as u64);
    let mut source = SliceSource::new(&input, &read_budget)?;

    assert_eq!(
        read_jet_signature(&mut source, &mut read_budget),
        Ok(JetFileKind::Standard)
    );
    assert_eq!(
        read_budget.total_read(),
        ByteCount::new(SIGNATURE_LENGTH as u64)
    );
    Ok(())
}

#[test]
fn one_below_single_read_budget_is_rejected_without_charging() -> Result<(), HeaderError> {
    let input = input_with(*b"Standard Jet DB");
    let maximum = (SIGNATURE_LENGTH - 1) as u64;
    let mut read_budget = budget(maximum, SIGNATURE_LENGTH as u64);
    let mut source = SliceSource::new(&input, &read_budget)?;

    assert_eq!(
        read_jet_signature(&mut source, &mut read_budget),
        Err(HeaderError::Read(Error::LimitExceeded {
            kind: LimitKind::SingleReadBytes,
            requested: ByteCount::new(SIGNATURE_LENGTH as u64),
            maximum: ByteCount::new(maximum),
        }))
    );
    assert_eq!(read_budget.total_read(), ByteCount::new(0));
    Ok(())
}

#[test]
fn one_below_total_read_budget_is_rejected_without_charging() -> Result<(), HeaderError> {
    let input = input_with(*b"Standard Jet DB");
    let maximum = (SIGNATURE_LENGTH - 1) as u64;
    let mut read_budget = budget(SIGNATURE_LENGTH as u64, maximum);
    let mut source = SliceSource::new(&input, &read_budget)?;

    assert_eq!(
        read_jet_signature(&mut source, &mut read_budget),
        Err(HeaderError::Read(Error::LimitExceeded {
            kind: LimitKind::TotalReadBytes,
            requested: ByteCount::new(SIGNATURE_LENGTH as u64),
            maximum: ByteCount::new(maximum),
        }))
    );
    assert_eq!(read_budget.total_read(), ByteCount::new(0));
    Ok(())
}

#[test]
fn display_and_error_source_preserve_failure_context() {
    let source = Error::UnexpectedEnd {
        offset: ByteOffset::new(4),
        needed: ByteCount::new(15),
        available: ByteCount::new(2),
    };
    let read_error = HeaderError::from(source.clone());
    assert!(read_error.to_string().contains("needed 15"));
    assert_eq!(
        StdError::source(&read_error).and_then(|error| error.downcast_ref::<Error>()),
        Some(&source)
    );

    let unknown = HeaderError::UnknownSignature {
        observed: *b"Not a Jet file!",
    };
    assert!(unknown.to_string().contains("unknown Jet signature"));
    assert!(StdError::source(&unknown).is_none());
}

#[test]
fn jet3_geometry_accepts_exact_single_page_length() -> Result<(), Error> {
    let input = [0_u8; 2_048];
    let read_budget = ReadBudget::new(ReadLimits::new(
        JET3_PAGE_SIZE,
        ByteCount::new(0),
        ByteCount::new(0),
    ));
    let source = SliceSource::new(&input, &read_budget)?;

    let geometry = jet3_page_geometry(&source)?;
    assert_eq!(geometry.source_len(), JET3_PAGE_SIZE);
    assert_eq!(geometry.page_size(), JET3_PAGE_SIZE);
    assert_eq!(geometry.page_count(), 1);
    Ok(())
}

#[test]
fn jet3_geometry_accepts_aligned_multiple_page_length() -> Result<(), Error> {
    let input = [0_u8; 4_096];
    let read_budget = ReadBudget::new(ReadLimits::new(
        ByteCount::new(input.len() as u64),
        ByteCount::new(0),
        ByteCount::new(0),
    ));
    let source = SliceSource::new(&input, &read_budget)?;

    let geometry = jet3_page_geometry(&source)?;
    assert_eq!(geometry.source_len(), ByteCount::new(4_096));
    assert_eq!(geometry.page_size(), JET3_PAGE_SIZE);
    assert_eq!(geometry.page_count(), 2);
    Ok(())
}

#[test]
fn jet3_geometry_rejects_partial_page_length_without_reading() -> Result<(), Error> {
    let input = [0_u8; 2_049];
    let read_budget = ReadBudget::new(ReadLimits::new(
        ByteCount::new(input.len() as u64),
        ByteCount::new(0),
        ByteCount::new(0),
    ));
    let source = SliceSource::new(&input, &read_budget)?;

    assert_eq!(
        jet3_page_geometry(&source),
        Err(Error::PartialPage {
            input_len: ByteCount::new(2_049),
            page_size: JET3_PAGE_SIZE,
            trailing: ByteCount::new(1),
        })
    );
    assert_eq!(read_budget.total_read(), ByteCount::new(0));
    Ok(())
}
