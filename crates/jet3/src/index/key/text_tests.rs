use super::text::*;
use crate::{
    IndexDirection, IndexNullPolicy, PageNumber, ResourceBudget, ResourceLimits, RowLocator,
    RowValue,
    index::{
        entry::{ScalarIndexEntry, ScalarIndexField, valid_key_shape},
        key::scalar::{KeyPrefix, MAX_COMPONENT_BYTES, ScalarKeyType},
    },
};

use crate::testkit::TestResult;

#[test]
fn native_text_vectors_preserve_accent_positions_expansions_and_spaces() -> TestResult {
    for (value, expected) in [
        (b"".as_slice(), vec![0x7f, 0]),
        (b"   ", vec![0x7f, 0]),
        (b"a  ", vec![0x7f, 0x60, 0]),
        (b" a", vec![0x7f, 0x11, 0x60, 0]),
        (b"a\xa0", vec![0x7f, 0x60, 0x11, 0]),
        (b"a\t\r\n", vec![0x7f, 0x60, 0x11, 0x10, 0x10, 0]),
        (b"\xe9", vec![0x7f, 0x66, 0x04, 0]),
        (b"A\xe9Z", vec![0x7f, 0x60, 0x66, 0x7e, 0x02, 0x40]),
        (
            b"A\xe9B\xe9Z",
            vec![0x7f, 0x60, 0x66, 0x61, 0x66, 0x7e, 0x02, 0x44, 0],
        ),
        (b"\xc6", vec![0x7f, 0x60, 0x66, 0]),
        (b"\xdf", vec![0x7f, 0x76, 0x76, 0]),
    ] {
        for direction in [IndexDirection::Ascending, IndexDirection::Descending] {
            let mut output = [0; MAX_COMPONENT_BYTES];
            let length = encode(value, 255, direction, &mut output).ok_or("encoding")?;
            let mask = u8::from(direction == IndexDirection::Descending).wrapping_neg();
            assert_eq!(
                &output[..length],
                expected.iter().map(|b| b ^ mask).collect::<Vec<_>>()
            );
            assert!(
                matches!(prefix(&output[..length], 255, direction), Some(KeyPrefix::Complete(n)) if n == length)
            );
        }
    }
    Ok(())
}

#[test]
fn text_capacity_undefined_bytes_and_secondary_corruption_are_checked() -> TestResult {
    let mut output = [0; MAX_COMPONENT_BYTES];
    for value in [
        &[0x81][..],
        &[0x8d],
        &[0x8f],
        &[0x90],
        &[0x9d],
        &[b'a'; 256],
    ] {
        assert_eq!(
            encode(value, 255, IndexDirection::Ascending, &mut output),
            None
        );
    }
    assert_eq!(
        encode(b"ab ", 2, IndexDirection::Ascending, &mut output),
        None
    );
    for key in [
        &[0x7f, 0x63, 0][..],   // No primary with this weight.
        &[0x7f, 0x61, 0x04, 0], // B has no secondary position.
        &[0x7f, 0x60, 0x02, 0], // Trailing neutral secondary is omitted.
        &[0x7f, 0x60, 0x01, 0], // Undefined secondary.
        &[0x7f, 0x60, 0x0b, 0],
        &[0x7f, 0x60, 0x0a, 0],    // S accent on A.
        &[0x7f, 0x76, 0x04, 0],    // A accent on S.
        &[0x7f, 0x60, 0x04, 1],    // Nonzero padding after terminal nibble.
        &[0x7f, 0x60, 0x04, 0x40], // More secondaries than eligible primaries.
    ] {
        for direction in [IndexDirection::Ascending, IndexDirection::Descending] {
            let mask = u8::from(direction == IndexDirection::Descending).wrapping_neg();
            let key: Vec<_> = key.iter().map(|b| b ^ mask).collect();
            assert!(prefix(&key, 255, direction).is_none(), "{key:x?}");
        }
    }
    assert!(prefix(&[0x7f, 0x60, 0x60, 0x60, 0], 1, IndexDirection::Ascending).is_none());
    for direction in [IndexDirection::Ascending, IndexDirection::Descending] {
        let mask = u8::from(direction == IndexDirection::Descending).wrapping_neg();
        for key in [&[0x7f, 0x60, 0x60, 0][..], &[0x7f, 0x60, 0x66, 0x03, 0]] {
            let key: Vec<_> = key.iter().map(|b| b ^ mask).collect();
            assert!(prefix(&key, 1, direction).is_none());
            assert!(matches!(
                prefix(&key, 2, direction),
                Some(KeyPrefix::Complete(_))
            ));
        }
        let expansion: Vec<_> = [0x7f, 0x60, 0x66, 0]
            .into_iter()
            .map(|b| b ^ mask)
            .collect();
        assert!(matches!(
            prefix(&expansion, 1, direction),
            Some(KeyPrefix::Complete(4))
        ));
    }
    for byte in 0..=255 {
        if matches!(byte, 0x81 | 0x8d | 0x8f | 0x90 | 0x9d) {
            continue;
        }
        let length = encode(&[byte; 255], 255, IndexDirection::Ascending, &mut output)
            .ok_or("maximum encoding")?;
        assert!(length <= MAX_TEXT_COMPONENT);
        assert!(
            matches!(prefix(&output[..length], 255, IndexDirection::Ascending), Some(KeyPrefix::Complete(n)) if n == length)
        );
    }
    Ok(())
}

#[test]
fn text_shortening_composite_boundaries_and_empty_presence_are_checked() -> TestResult {
    for direction in [IndexDirection::Ascending, IndexDirection::Descending] {
        for length in [0, 1, 126, 127, 168, 169, 223, 224, 253, 254, 255] {
            for byte in [b' ', b'a', 0xe9, 0xc6, 0xdf] {
                let payload = vec![byte; length];
                let fields = [
                    ScalarIndexField {
                        column: 0,
                        direction,
                        kind: ScalarKeyType::Text {
                            max_len: 255,
                            sort_order: crate::SortOrder::General,
                        },
                    },
                    ScalarIndexField {
                        column: 1,
                        direction: IndexDirection::Descending,
                        kind: ScalarKeyType::Long,
                    },
                ];
                for fields in [&fields[..1], &fields[..]] {
                    let entry = ScalarIndexEntry::encode(
                        fields,
                        &[RowValue::Text(&payload), RowValue::Long(13)],
                        IndexNullPolicy::Required,
                        RowLocator::new(PageNumber::new(20), 0),
                        &mut ResourceBudget::new(ResourceLimits::default()),
                    )?
                    .ok_or("present Text omitted")?;
                    assert!(!entry.has_null());
                    assert!(
                        valid_key_shape(fields, IndexNullPolicy::Required, entry.key()),
                        "{byte:x} {length}"
                    );
                    let truncated = &entry.key()[..entry.key().len() - 1];
                    assert!(!valid_key_shape(
                        fields,
                        IndexNullPolicy::Required,
                        truncated
                    ));
                }
            }
        }
    }
    Ok(())
}

#[test]
fn guid_display_order_uses_two_full_binary_chunks() -> TestResult {
    let value = [3, 2, 1, 0, 5, 4, 7, 6, 8, 9, 10, 11, 12, 13, 14, 15];
    let expected = [
        0x7f, 3, 2, 1, 0, 5, 4, 7, 6, 9, 8, 9, 10, 11, 12, 13, 14, 15, 8,
    ];
    for direction in [IndexDirection::Ascending, IndexDirection::Descending] {
        let mut output = [0; MAX_COMPONENT_BYTES];
        assert_eq!(
            ScalarKeyType::Guid.encode(RowValue::Guid(value), direction, &mut output),
            Some(19)
        );
        let mask = u8::from(direction == IndexDirection::Descending).wrapping_neg();
        for (i, byte) in expected.iter().enumerate() {
            assert_eq!(output[i], if i == 9 { 9 } else { byte ^ mask });
        }
        assert!(matches!(
            ScalarKeyType::Guid.prefix(&output[..19], direction),
            Some(KeyPrefix::Complete(19))
        ));
        for length in 0..19 {
            assert!(matches!(
                ScalarKeyType::Guid.prefix(&output[..length], direction),
                Some(KeyPrefix::Partial { maximum: 19 })
            ));
        }
        for position in [9, 18] {
            output[position] ^= 1;
            assert!(
                ScalarKeyType::Guid
                    .prefix(&output[..19], direction)
                    .is_none()
            );
            output[position] ^= 1;
        }
    }
    Ok(())
}
