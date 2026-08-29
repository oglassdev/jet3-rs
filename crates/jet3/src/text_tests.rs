// Mapping assertions are bounded to the public SRC-0025 tables.

use super::{TextCodePage, TextError, decode_text};
use crate::{ByteCount, Error, ResourceBudget, ResourceLimits};
use std::error::Error as _;

#[test]
fn decodes_cp1252_discriminator_and_retains_raw_bytes() -> Result<(), Box<dyn std::error::Error>> {
    let raw = b"Caf\xe9 \x80 \x8c \x9f";
    let mut budget = ResourceBudget::new(ResourceLimits::default());
    let decoded = decode_text(raw, TextCodePage::Windows1252, &mut budget)?;
    assert_eq!(decoded.as_str(), "Café € Œ Ÿ");
    assert_eq!(decoded.raw_bytes(), raw);
    assert_eq!(decoded.code_page().number(), 1252);
    assert_eq!(budget.decoded_bytes(), ByteCount::new(15));
    Ok(())
}

#[test]
fn text_errors_expose_display_and_resource_sources() {
    let undefined = TextError::UndefinedByte {
        code_page: TextCodePage::Windows1252,
        index: 0,
        byte: 0x81,
    };
    assert!(undefined.to_string().contains("text decoding failed"));
    assert!(undefined.source().is_none());

    let resource = TextError::Resource(Error::Arithmetic {
        operation: "test text source",
    });
    assert!(resource.source().is_some());
}

#[test]
fn decodes_cp1251_byte_boundaries_and_rejects_undefined_input()
-> Result<(), Box<dyn std::error::Error>> {
    let raw = [0x80, 0x88, 0xc0, 0xff];
    let mut budget = ResourceBudget::new(ResourceLimits::default());
    let decoded = decode_text(&raw, TextCodePage::Windows1251, &mut budget)?;
    assert_eq!(decoded.as_str(), "Ђ€Ая");
    assert_eq!(decoded.raw_bytes(), raw);
    assert_eq!(decoded.code_page().number(), 1251);

    let before = budget.decoded_bytes();
    assert!(matches!(
        decode_text(&[b'a', 0x98], TextCodePage::Windows1251, &mut budget),
        Err(TextError::UndefinedByte {
            index: 1,
            byte: 0x98,
            ..
        })
    ));
    assert_eq!(budget.decoded_bytes(), before);
    Ok(())
}

#[test]
fn decoded_and_allocation_limits_are_charged_before_output() {
    let decoded = ByteCount::new(3);
    for limits in [
        ResourceLimits::default()
            .with_max_decoded_value_bytes(ByteCount::new(2))
            .with_max_total_decoded_bytes(decoded),
        ResourceLimits::default()
            .with_max_decoded_value_bytes(decoded)
            .with_max_total_decoded_bytes(ByteCount::new(2)),
        ResourceLimits::default()
            .with_max_decoded_value_bytes(decoded)
            .with_max_total_decoded_bytes(decoded)
            .with_max_allocation_bytes(ByteCount::new(2)),
        ResourceLimits::default()
            .with_max_decoded_value_bytes(decoded)
            .with_max_total_decoded_bytes(decoded)
            .with_max_allocation_bytes(decoded)
            .with_max_total_work_units(5),
    ] {
        let mut budget = ResourceBudget::new(limits);
        assert!(matches!(
            decode_text(&[0x80], TextCodePage::Windows1252, &mut budget),
            Err(TextError::Resource(_))
        ));
        assert_eq!(budget.decoded_bytes(), ByteCount::new(0));
        assert_eq!(budget.allocation_bytes(), ByteCount::new(0));
        assert_eq!(budget.total_work_units(), 0);
    }

    let mut exact = ResourceBudget::new(
        ResourceLimits::default()
            .with_max_decoded_value_bytes(decoded)
            .with_max_total_decoded_bytes(decoded)
            .with_max_allocation_bytes(decoded)
            .with_max_total_work_units(6),
    );
    assert!(decode_text(&[0x80], TextCodePage::Windows1252, &mut exact).is_ok());
    assert_eq!(exact.decoded_bytes(), decoded);
    assert_eq!(exact.allocation_bytes(), decoded);
    assert_eq!(exact.total_work_units(), 6);
}
