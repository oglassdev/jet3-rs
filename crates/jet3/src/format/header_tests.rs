use super::header::{
    HeaderError, JET3_PAGE_SIZE, JetFileKind, jet3_page_geometry, read_jet_signature,
};
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

fn read(input: &[u8], read_budget: &mut ReadBudget) -> Result<JetFileKind, HeaderError> {
    let mut source = SliceSource::new(input, read_budget)?;
    read_jet_signature(&mut source, read_budget)
}

fn classify(signature: [u8; SIGNATURE_LENGTH]) -> Result<JetFileKind, HeaderError> {
    let mut read_budget = budget(SIGNATURE_LENGTH as u64, SIGNATURE_LENGTH as u64);
    read(&input_with(signature), &mut read_budget)
}

#[test]
fn recognizes_documented_kinds_at_offset_four_ignoring_unspecified_bytes() {
    for leading in [[0_u8; 4], [u8::MAX; 4], [0x00, 0xFF, 0x55, 0xAA]] {
        let mut input = input_with(*b"Standard Jet DB");
        input[..SIGNATURE_OFFSET].copy_from_slice(&leading);
        let mut read_budget = budget(SIGNATURE_LENGTH as u64, SIGNATURE_LENGTH as u64);
        assert_eq!(read(&input, &mut read_budget), Ok(JetFileKind::Standard));
    }
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
            assert_eq!(
                classify(mutated),
                Err(HeaderError::UnknownSignature { observed: mutated }),
                "mutation at documented byte {index} unexpectedly matched"
            );
        }
    }
}

#[test]
fn every_truncation_before_the_complete_window_is_structured() {
    let complete = input_with(*b"Standard Jet DB");

    for length in 0..COMPLETE_HEADER_LENGTH {
        let mut read_budget = budget(SIGNATURE_LENGTH as u64, SIGNATURE_LENGTH as u64);
        let expected = if length < SIGNATURE_OFFSET {
            Error::OffsetOutOfBounds {
                offset: ByteOffset::new(SIGNATURE_OFFSET as u64),
                input_len: ByteCount::new(length as u64),
            }
        } else {
            Error::UnexpectedEnd {
                offset: ByteOffset::new(SIGNATURE_OFFSET as u64),
                needed: ByteCount::new(SIGNATURE_LENGTH as u64),
                available: ByteCount::new((length - SIGNATURE_OFFSET) as u64),
            }
        };
        assert_eq!(
            read(&complete[..length], &mut read_budget),
            Err(HeaderError::Read(expected)),
            "length {length}"
        );
    }
}

#[test]
fn read_budget_accepts_exact_and_rejects_one_below_without_charging() {
    let input = input_with(*b"Standard Jet DB");
    let exact = SIGNATURE_LENGTH as u64;
    let below = exact - 1;

    let mut read_budget = budget(exact, exact);
    assert_eq!(read(&input, &mut read_budget), Ok(JetFileKind::Standard));
    assert_eq!(read_budget.total_read(), ByteCount::new(exact));

    for (single, total, kind) in [
        (below, exact, LimitKind::SingleReadBytes),
        (exact, below, LimitKind::TotalReadBytes),
    ] {
        let mut read_budget = budget(single, total);
        assert_eq!(
            read(&input, &mut read_budget),
            Err(HeaderError::Read(Error::LimitExceeded {
                kind,
                requested: ByteCount::new(exact),
                maximum: ByteCount::new(below),
            }))
        );
        assert_eq!(read_budget.total_read(), ByteCount::new(0));
    }
}

#[test]
fn jet3_geometry_accepts_whole_pages_and_rejects_partial_without_reading() -> Result<(), Error> {
    for (length, pages) in [(2_048, Some(1)), (4_096, Some(2)), (2_049, None)] {
        let input = vec![0_u8; length];
        let read_budget = ReadBudget::new(ReadLimits::new(
            ByteCount::new(length as u64),
            ByteCount::new(0),
            ByteCount::new(0),
        ));
        let source = SliceSource::new(&input, &read_budget)?;
        match pages {
            Some(pages) => {
                let geometry = jet3_page_geometry(&source)?;
                assert_eq!(geometry.source_len(), ByteCount::new(length as u64));
                assert_eq!(geometry.page_size(), JET3_PAGE_SIZE);
                assert_eq!(geometry.page_count(), pages);
            }
            None => assert_eq!(
                jet3_page_geometry(&source),
                Err(Error::PartialPage {
                    input_len: ByteCount::new(length as u64),
                    page_size: JET3_PAGE_SIZE,
                    trailing: ByteCount::new(1),
                })
            ),
        }
        assert_eq!(read_budget.total_read(), ByteCount::new(0));
    }
    Ok(())
}
