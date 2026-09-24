// Mapping assertions are bounded to the public SRC-0025 tables.

use super::text::{TextCodePage, TextError, decode_text};
use crate::{ByteCount, ResourceBudget, ResourceLimits};

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
    let limits = ResourceLimits::default()
        .with_max_decoded_value_bytes(ByteCount::new(2))
        .with_max_total_decoded_bytes(ByteCount::new(2));
    let mut budget = ResourceBudget::new(limits);
    assert!(matches!(
        decode_text(&[0x80], TextCodePage::Windows1252, &mut budget),
        Err(TextError::Resource(_))
    ));
    assert_eq!(budget.decoded_bytes(), ByteCount::new(0));
}

#[test]
fn explicit_encoding_preserves_accents_expansions_and_controls()
-> Result<(), Box<dyn std::error::Error>> {
    let mut budget = ResourceBudget::new(ResourceLimits::default());
    assert_eq!(
        TextCodePage::Windows1252.encode("Café € Œ Ÿ\0\t", &mut budget)?,
        b"Caf\xe9 \x80 \x8c \x9f\0\t"
    );
    assert_eq!(
        TextCodePage::Windows1251.encode("Ђ€Ая", &mut budget)?,
        [0x80, 0x88, 0xc0, 0xff]
    );
    for code_page in [
        TextCodePage::Windows1251,
        TextCodePage::Windows1252,
        TextCodePage::Windows1253,
    ] {
        for byte in 0..=u8::MAX {
            let raw = [byte];
            if let Ok(decoded) = code_page.decode(&raw, &mut budget) {
                assert_eq!(code_page.encode(decoded.as_str(), &mut budget)?, raw);
            }
        }
    }
    Ok(())
}

#[test]
fn encoding_refuses_replacement_and_reports_utf8_offset() {
    for character in ['\u{81}', '漢', '😀'] {
        let mut budget = ResourceBudget::new(ResourceLimits::default());
        assert_eq!(
            TextCodePage::Windows1252.encode(&format!("é{character}"), &mut budget),
            Err(TextError::UnrepresentableCharacter {
                code_page: TextCodePage::Windows1252,
                index: 2,
                character,
            })
        );
    }
}

#[test]
fn encoding_work_is_bounded_before_scanning_the_mapping() {
    let mut budget = ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(128));
    assert!(matches!(
        TextCodePage::Windows1252.encode("a", &mut budget),
        Err(TextError::Resource(_))
    ));
    let mut budget =
        ResourceBudget::new(ResourceLimits::default().with_max_allocation_bytes(ByteCount::new(1)));
    assert!(matches!(
        TextCodePage::Windows1252.encode("éé", &mut budget),
        Err(TextError::Resource(_))
    ));
    let mut budget =
        ResourceBudget::new(ResourceLimits::default().with_max_encoded_bytes(ByteCount::new(1)));
    assert!(matches!(
        TextCodePage::Windows1252.encode("éé", &mut budget),
        Err(TextError::Resource(_))
    ));
    let mut budget = ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(0));
    assert!(matches!(
        TextCodePage::Windows1252.decode(&[0x81], &mut budget),
        Err(TextError::Resource(_))
    ));
}

#[test]
fn greek_text_is_lossless_and_rejects_undefined_bytes() -> Result<(), Box<dyn std::error::Error>> {
    let mut budget = ResourceBudget::new(ResourceLimits::default());
    let page = TextCodePage::Windows1253;
    let raw = [0xc1, 0xe1, 0xb8, 0x80];
    assert_eq!(decode_text(&raw, page, &mut budget)?.as_str(), "ΑαΈ€");
    assert_eq!(page.encode("ΑαΈ€", &mut budget)?, raw);
    assert!(matches!(
        decode_text(&[0xd2], page, &mut budget),
        Err(TextError::UndefinedByte { byte: 0xd2, .. })
    ));
    assert!(page.encode("Я", &mut budget).is_err());
    Ok(())
}
