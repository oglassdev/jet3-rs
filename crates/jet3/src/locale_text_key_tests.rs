use super::*;

fn bytes(hex: &str) -> Vec<u8> {
    hex.as_bytes()
        .chunks_exact(2)
        .map(|pair| {
            let digit = |b| if b <= b'9' { b - b'0' } else { b - b'a' + 10 };
            digit(pair[0]) * 16 + digit(pair[1])
        })
        .collect()
}

#[test]
fn native_locale_keys_cover_expansions_accents_contractions_and_spaces()
-> Result<(), Box<dyn std::error::Error>> {
    for (order, raw, expected) in [
        (SortOrder::General, "20", "7f00"),
        (SortOrder::General, "00", "7f1000"),
        (SortOrder::General, "df", "7f767600"),
        (SortOrder::General, "de", "7f7f00"),
        (SortOrder::General, "ff", "7f7d0600"),
        (SortOrder::General, "e9", "7f660400"),
        (SortOrder::General, "6368", "7f626900"),
        (SortOrder::General, "4368", "7f626900"),
        (SortOrder::General, "6c6c", "7f6d6d00"),
        (SortOrder::General, "4c6c", "7f6d6d00"),
        (SortOrder::General, "6368e9", "7f6269660240"),
        (SortOrder::General, "c0", "7f600300"),
        (SortOrder::General, "c1", "7f600400"),
        (SortOrder::General, "d3", "7f720400"),
        (SortOrder::General, "f3", "7f720400"),
        (SortOrder::Nordic, "20", "7f00"),
        (SortOrder::Nordic, "00", "7f1000"),
        (SortOrder::Nordic, "df", "7f737300"),
        (SortOrder::Nordic, "de", "7f746800"),
        (SortOrder::Nordic, "ff", "7f790600"),
        (SortOrder::Nordic, "e9", "7f650300"),
        (SortOrder::Nordic, "6368", "7f626800"),
        (SortOrder::Nordic, "4368", "7f626800"),
        (SortOrder::Nordic, "6c6c", "7f6c6c00"),
        (SortOrder::Nordic, "4c6c", "7f6c6c00"),
        (SortOrder::Nordic, "6368e9", "7f6268650230"),
        (SortOrder::Nordic, "c0", "7f600400"),
        (SortOrder::Nordic, "c1", "7f600300"),
        (SortOrder::Nordic, "d3", "7f6f0300"),
        (SortOrder::Nordic, "f3", "7f6f0300"),
        (SortOrder::Spanish, "20", "7f00"),
        (SortOrder::Spanish, "00", "7f1000"),
        (SortOrder::Spanish, "df", "7f767600"),
        (SortOrder::Spanish, "de", "7f7f00"),
        (SortOrder::Spanish, "ff", "7f7d0600"),
        (SortOrder::Spanish, "e9", "7f660400"),
        (SortOrder::Spanish, "6368", "7f6300"),
        (SortOrder::Spanish, "4368", "7f6300"),
        (SortOrder::Spanish, "6c6c", "7f6e00"),
        (SortOrder::Spanish, "4c6c", "7f6e00"),
        (SortOrder::Spanish, "6368e9", "7f63660240"),
        (SortOrder::Spanish, "c0", "7f600300"),
        (SortOrder::Spanish, "c1", "7f600400"),
        (SortOrder::Spanish, "d3", "7f720400"),
        (SortOrder::Spanish, "f3", "7f720400"),
        (SortOrder::Dutch, "20", "7f00"),
        (SortOrder::Dutch, "00", "7f1000"),
        (SortOrder::Dutch, "df", "7f767600"),
        (SortOrder::Dutch, "de", "7f7f00"),
        (SortOrder::Dutch, "ff", "7f6a6b00"),
        (SortOrder::Dutch, "e9", "7f660400"),
        (SortOrder::Dutch, "6368", "7f626900"),
        (SortOrder::Dutch, "4368", "7f626900"),
        (SortOrder::Dutch, "6c6c", "7f6d6d00"),
        (SortOrder::Dutch, "4c6c", "7f6d6d00"),
        (SortOrder::Dutch, "6368e9", "7f6269660240"),
        (SortOrder::Dutch, "c0", "7f600300"),
        (SortOrder::Dutch, "c1", "7f600400"),
        (SortOrder::Dutch, "d3", "7f720400"),
        (SortOrder::Dutch, "f3", "7f720400"),
        (SortOrder::Cyrillic, "20", "7f00"),
        (SortOrder::Cyrillic, "00", "7f1000"),
        (SortOrder::Cyrillic, "df", "7fb900"),
        (SortOrder::Cyrillic, "de", "7fb800"),
        (SortOrder::Cyrillic, "ff", "7fb900"),
        (SortOrder::Cyrillic, "e9", "7f9c00"),
        (SortOrder::Cyrillic, "6368", "7f646f00"),
        (SortOrder::Cyrillic, "4368", "7f646f00"),
        (SortOrder::Cyrillic, "6c6c", "7f747400"),
        (SortOrder::Cyrillic, "4c6c", "7f747400"),
        (SortOrder::Cyrillic, "6368e9", "7f646f9c00"),
        (SortOrder::Cyrillic, "c0", "7f8b00"),
        (SortOrder::Cyrillic, "c1", "7f8c00"),
        (SortOrder::Cyrillic, "d3", "7fab00"),
        (SortOrder::Cyrillic, "f3", "7fab00"),
        (SortOrder::Greek, "20", "7f00"),
        (SortOrder::Greek, "00", "7f1000"),
        (SortOrder::Greek, "df", "7f870300"),
        (SortOrder::Greek, "de", "7f850300"),
        (SortOrder::Greek, "e9", "7f8700"),
        (SortOrder::Greek, "6368", "7f666b00"),
        (SortOrder::Greek, "4368", "7f666b00"),
        (SortOrder::Greek, "6c6c", "7f6f6f00"),
        (SortOrder::Greek, "4c6c", "7f6f6f00"),
        (SortOrder::Greek, "6368e9", "7f666b8700"),
        (SortOrder::Greek, "c0", "7f870500"),
        (SortOrder::Greek, "c1", "7f7f00"),
        (SortOrder::Greek, "d3", "7f9000"),
        (SortOrder::Greek, "f3", "7f9000"),
    ] {
        let raw = bytes(raw);
        let expected = bytes(expected);
        for direction in [IndexDirection::Ascending, IndexDirection::Descending] {
            let mut output = [0; MAX_COMPONENT_BYTES];
            let length =
                encode(&raw, raw.len() as u8, direction, order, &mut output).ok_or("native key")?;
            let mask = if direction == IndexDirection::Ascending {
                0
            } else {
                255
            };
            assert_eq!(
                output[..length],
                expected.iter().map(|b| b ^ mask).collect::<Vec<_>>(),
                "{order:?}/{raw:x?}"
            );
            assert!(
                matches!(prefix(&output[..length], raw.len() as u8, direction, order), Some(KeyPrefix::Complete(n)) if n == length),
                "{order:?}/{raw:x?}"
            );
        }
    }
    Ok(())
}

#[test]
fn locale_key_bounds_and_undefined_bytes_are_checked() -> Result<(), Box<dyn std::error::Error>> {
    let mut output = [0; MAX_COMPONENT_BYTES];
    for order in SortOrder::known() {
        let page = order.code_page().ok_or("known code page")?;
        for byte in 0..=255 {
            let length = encode(&[byte], 1, IndexDirection::Ascending, order, &mut output);
            assert_eq!(
                length.is_some(),
                crate::text::mapped_character(page, byte).is_some()
            );
            if let Some(length) = length {
                assert!(
                    matches!(prefix(&output[..length], 1, IndexDirection::Ascending, order), Some(KeyPrefix::Complete(n)) if n == length),
                    "{order:?}/{byte:02x}"
                );
            }
        }
        assert!(encode(b"aa", 1, IndexDirection::Ascending, order, &mut output).is_none());
    }
    for key in [b"\x7f\x63\0".as_slice(), b"\x7f\x6e\0"] {
        assert!(prefix(key, 1, IndexDirection::Ascending, SortOrder::Spanish).is_none());
        assert!(matches!(
            prefix(key, 2, IndexDirection::Ascending, SortOrder::Spanish),
            Some(KeyPrefix::Complete(3))
        ));
    }
    for key in [
        b"\x7f\x60\x01\0".as_slice(),
        b"\x7f\x60\x02\0",
        b"\x7f\x60\x04\x01",
        b"\x7f\x61\x04\0",
    ] {
        assert!(prefix(key, 255, IndexDirection::Ascending, SortOrder::Nordic).is_none());
    }
    Ok(())
}

#[test]
#[ignore = "requires the private EXP-0309 KEYS.json acquisition"]
fn complete_native_locale_key_inventory() -> Result<(), Box<dyn std::error::Error>> {
    let path = std::env::var("JET3_COLLATION_VECTORS")?;
    let corpus: serde_json::Value = serde_json::from_slice(&std::fs::read(path)?)?;
    let mut compared = 0;
    for (name, order) in [
        ("general", SortOrder::General),
        ("nordic", SortOrder::Nordic),
        ("spanish", SortOrder::Spanish),
        ("dutch", SortOrder::Dutch),
        ("cyrillic", SortOrder::Cyrillic),
        ("greek", SortOrder::Greek),
    ] {
        let page = order.code_page().ok_or("code page")?;
        for (raw, expected) in corpus[name]["keys"].as_object().ok_or("native keys")? {
            let raw = bytes(raw);
            let mut output = [0; MAX_COMPONENT_BYTES];
            let length = encode(&raw, 255, IndexDirection::Ascending, order, &mut output);
            if raw
                .iter()
                .any(|&b| crate::text::mapped_character(page, b).is_none())
            {
                assert!(length.is_none());
                continue;
            }
            let length = length.ok_or("native encoding")?;
            assert!(
                matches!(prefix(&output[..length], 255, IndexDirection::Ascending, order), Some(KeyPrefix::Complete(n)) if n == length),
                "{name}/{raw:x?}"
            );
            let length = crate::binary_index_key::shorten(&mut output[..length]);
            assert_eq!(
                output[..length],
                bytes(expected.as_str().ok_or("key bytes")?),
                "{name}/{raw:x?}"
            );
            compared += 1;
        }
    }
    println!("{compared} complete native keys matched; undefined code-page inputs refused");
    Ok(())
}
