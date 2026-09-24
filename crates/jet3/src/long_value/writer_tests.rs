use super::writer::*;
use crate::{
    ByteCount, ExternalLongValueStorage, InlineLongValue, JET3_PAGE_SIZE, LongValue,
    LongValueError, LongValueKind, PageNumber, ReadLimits, ResourceBudget, ResourceLimits,
    RowLocator, TextCodePage, format::data_page_directory::MAX_STORED_ROW_LEN,
};

use crate::testkit::TestResult;

fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::new(ReadLimits::new(
        ByteCount::new(1 << 20),
        JET3_PAGE_SIZE,
        ByteCount::new(1 << 20),
    )))
}

fn locator(page: u64, slot: u8) -> RowLocator {
    RowLocator::new(PageNumber::new(page), slot)
}

#[test]
fn an_inline_value_round_trips_through_the_reader() -> TestResult {
    let mut output = [0_u8; 64];
    let written = encode_inline_long_value(b"hello", &mut output)?;
    assert_eq!(written, HEADER_LEN + 5);
    let decoded = LongValue::decode(
        &output[..written],
        locator(30, 0),
        LongValueKind::Ole,
        TextCodePage::Windows1252,
        &mut budget(),
    )?;
    assert!(matches!(
        decoded,
        LongValue::Inline {
            value: InlineLongValue::Binary(b"hello"),
            ..
        }
    ));

    let mut output = [0xff_u8; HEADER_LEN];
    assert_eq!(encode_inline_long_value(b"", &mut output)?, HEADER_LEN);
    assert_eq!(output, [0, 0, 0, 0x80, 0, 0, 0, 0, 0, 0, 0, 0]);
    Ok(())
}

#[test]
fn external_headers_round_trip_through_the_reader() -> TestResult {
    for (storage, length) in [
        (ExternalLongValueStorage::SinglePage, 512),
        (ExternalLongValueStorage::Chained, 4096),
    ] {
        let header = external_long_value_header(length, storage, locator(0x01_0203, 7))?;
        assert_eq!(&header[4..8], &[7, 0x03, 0x02, 0x01]);
        let decoded = LongValue::decode(
            &header,
            locator(30, 0),
            LongValueKind::Memo,
            TextCodePage::Windows1252,
            &mut budget(),
        )?;
        let LongValue::External(reference) = decoded else {
            return Err("expected an external reference".into());
        };
        assert_eq!(reference.storage(), storage);
        assert_eq!(reference.length(), length as u32);
        assert_eq!(reference.target(), locator(0x01_0203, 7));
    }
    Ok(())
}

#[test]
fn external_headers_refuse_null_empty_and_unrepresentable_targets() {
    assert_eq!(
        external_long_value_header(8, ExternalLongValueStorage::SinglePage, null_locator()),
        Err(LongValueWriteError::NullTarget)
    );
    // The reader rejects the null target too.
    let mut header = [0_u8; HEADER_LEN];
    header[..4].copy_from_slice(&(SINGLE_PAGE_FLAG | 8).to_le_bytes());
    assert_eq!(
        LongValue::decode(
            &header,
            locator(30, 0),
            LongValueKind::Ole,
            TextCodePage::Windows1252,
            &mut budget()
        ),
        Err(LongValueError::MissingExternalTarget)
    );

    // No external row can be empty, so nothing could back such a header.
    for storage in [
        ExternalLongValueStorage::SinglePage,
        ExternalLongValueStorage::Chained,
    ] {
        assert_eq!(
            external_long_value_header(0, storage, locator(30, 0)),
            Err(LongValueWriteError::EmptyRow)
        );
    }

    let too_high = locator(MAX_LOCATOR_PAGE + 1, 0);
    assert_eq!(
        external_long_value_header(8, ExternalLongValueStorage::Chained, too_high),
        Err(LongValueWriteError::LocatorNotRepresentable { locator: too_high })
    );
    assert!(
        external_long_value_header(
            8,
            ExternalLongValueStorage::Chained,
            locator(MAX_LOCATOR_PAGE, 0)
        )
        .is_ok()
    );
}

#[test]
fn each_storage_class_refuses_one_byte_over_its_limit() {
    let mut output = vec![0_u8; HEADER_LEN + MAX_DECLARED_LENGTH + 1];
    let inline = vec![0_u8; MAX_DECLARED_LENGTH + 1];
    assert_eq!(
        encode_inline_long_value(&inline, &mut output),
        Err(LongValueWriteError::PayloadTooLong {
            length: MAX_DECLARED_LENGTH + 1,
            maximum: MAX_DECLARED_LENGTH,
        })
    );
    assert_eq!(
        external_long_value_header(
            MAX_SINGLE_PAGE_PAYLOAD + 1,
            ExternalLongValueStorage::SinglePage,
            locator(30, 0)
        ),
        Err(LongValueWriteError::PayloadTooLong {
            length: MAX_SINGLE_PAGE_PAYLOAD + 1,
            maximum: MAX_SINGLE_PAGE_PAYLOAD,
        })
    );
    assert_eq!(
        external_long_value_header(
            MAX_DECLARED_LENGTH + 1,
            ExternalLongValueStorage::Chained,
            locator(30, 0)
        ),
        Err(LongValueWriteError::PayloadTooLong {
            length: MAX_DECLARED_LENGTH + 1,
            maximum: MAX_DECLARED_LENGTH,
        })
    );
    let single = vec![0_u8; MAX_SINGLE_PAGE_PAYLOAD + 1];
    assert_eq!(
        validate_single_page_row(&single),
        Err(LongValueWriteError::PayloadTooLong {
            length: MAX_SINGLE_PAGE_PAYLOAD + 1,
            maximum: MAX_SINGLE_PAGE_PAYLOAD,
        })
    );
    assert_eq!(
        validate_single_page_row(b""),
        Err(LongValueWriteError::EmptyRow)
    );
}

#[test]
fn chained_fragments_match_the_observed_controls() {
    // EXP-0061: 2,048 bytes chained as 2,032 then 16; 4,096 as 2,032, 2,032, 32.
    let sizes = |length: usize| {
        chained_fragments(&vec![0_u8; length])
            .map(<[u8]>::len)
            .collect::<Vec<_>>()
    };
    assert_eq!(sizes(2048), [2032, 16]);
    assert_eq!(sizes(4096), [2032, 2032, 32]);
    assert_eq!(sizes(2032), [2032]);
}

#[test]
fn a_chained_row_carries_its_pointer_then_a_bounded_fragment() -> TestResult {
    let mut output = vec![0xaa_u8; MAX_STORED_ROW_LEN + 8];
    let written = encode_chained_row(b"abc", Some(locator(0x2a, 3)), &mut output)?;
    assert_eq!(&output[..written], &[3, 0x2a, 0, 0, b'a', b'b', b'c']);
    let written = encode_chained_row(b"abc", None, &mut output)?;
    assert_eq!(&output[..written], &[0, 0, 0, 0, b'a', b'b', b'c']);

    // The null locator marks the chain end, so it cannot name a following row.
    let mut untouched = [0xaa_u8; 8];
    assert_eq!(
        encode_chained_row(b"abc", Some(null_locator()), &mut untouched),
        Err(LongValueWriteError::NullTarget)
    );
    assert!(untouched.iter().all(|byte| *byte == 0xaa));

    assert_eq!(
        encode_chained_row(b"", None, &mut output),
        Err(LongValueWriteError::EmptyRow)
    );
    let oversized = vec![0_u8; MAX_CHAINED_FRAGMENT + 1];
    assert_eq!(
        encode_chained_row(&oversized, None, &mut output),
        Err(LongValueWriteError::FragmentTooLong {
            length: MAX_CHAINED_FRAGMENT + 1,
            maximum: MAX_CHAINED_FRAGMENT,
        })
    );
    // The largest fragment fills exactly one stored row.
    let largest = vec![0_u8; MAX_CHAINED_FRAGMENT];
    assert_eq!(
        encode_chained_row(&largest, None, &mut output),
        Ok(MAX_STORED_ROW_LEN)
    );
    Ok(())
}

#[test]
fn a_short_output_is_refused_without_a_partial_write() {
    let mut output = [0xaa_u8; HEADER_LEN + 2];
    assert_eq!(
        encode_inline_long_value(b"abc", &mut output),
        Err(LongValueWriteError::OutputTooSmall {
            needed: HEADER_LEN + 3,
            available: HEADER_LEN + 2,
        })
    );
    assert!(output.iter().all(|byte| *byte == 0xaa));
    let mut output = [0xaa_u8; 5];
    assert_eq!(
        encode_chained_row(b"ab", None, &mut output),
        Err(LongValueWriteError::OutputTooSmall {
            needed: 6,
            available: 5,
        })
    );
    assert!(output.iter().all(|byte| *byte == 0xaa));
}
