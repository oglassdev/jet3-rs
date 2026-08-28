use super::{
    CanonicalSnapshot, FiniteF32, FiniteF64, Guid, HexString, InvariantDateTime, InvariantDecimal,
    Producer, ProducerKind, ScenarioId, Sha256, SnapshotError, TypedValue,
};

fn empty_snapshot() -> Result<CanonicalSnapshot, SnapshotError> {
    Ok(CanonicalSnapshot::new(
        ScenarioId::new("DAO-READ-TYPED-001")?,
        Producer::new(ProducerKind::Rust, "test-revision")?,
        Sha256::new("0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")?,
    ))
}

fn emitted(snapshot: &CanonicalSnapshot) -> Result<String, SnapshotError> {
    let bytes = snapshot.to_canonical_json()?;
    Ok(String::from_utf8_lossy(&bytes).into_owned())
}

#[test]
fn empty_snapshot_matches_protocol_key_order_and_constants() -> Result<(), SnapshotError> {
    let snapshot = empty_snapshot()?;
    let actual = emitted(&snapshot)?;
    let expected = concat!(
        "{\"database_properties\":{},",
        "\"database_sha256\":\"0123456789abcdef0123456789abcdef",
        "0123456789abcdef0123456789abcdef\",",
        "\"document_type\":\"canonical_snapshot\",",
        "\"ordering\":{\"columns\":\"ordinal_ascending\",",
        "\"indexes\":\"name_codepoint_ascending\",",
        "\"object_keys\":\"unicode_codepoint_ascending\",",
        "\"objects\":\"name_codepoint_ascending\",",
        "\"relationships\":\"name_codepoint_ascending\",",
        "\"rows\":\"declared_key_then_canonical_value\"},",
        "\"producer\":{\"kind\":\"rust\",\"source_revision\":\"test-revision\"},",
        "\"protocol_version\":\"1.0.0\",",
        "\"raw_preservation\":[],\"relationships\":[],",
        "\"scenario_id\":\"DAO-READ-TYPED-001\",\"tables\":[]}\n"
    );
    assert_eq!(actual, expected);
    assert!(!actual.contains('\r'));
    assert_eq!(actual.as_bytes().last(), Some(&b'\n'));
    assert_ne!(
        actual.as_bytes().get(actual.len().saturating_sub(2)),
        Some(&b'\n')
    );
    Ok(())
}

#[test]
fn every_typed_value_kind_has_a_distinct_canonical_shape() -> Result<(), SnapshotError> {
    let mut snapshot = empty_snapshot()?;
    let values = &mut snapshot.database_properties;
    values.insert(
        "01_null".to_owned(),
        TypedValue::Null {
            raw_hex: Some(HexString::new("")?),
        },
    );
    values.insert(
        "02_boolean".to_owned(),
        TypedValue::Boolean {
            value: false,
            raw_hex: None,
        },
    );
    values.insert(
        "03_byte".to_owned(),
        TypedValue::Byte {
            value: u8::MAX,
            raw_hex: None,
        },
    );
    values.insert(
        "04_integer".to_owned(),
        TypedValue::Integer {
            value: i16::MIN,
            raw_hex: None,
        },
    );
    values.insert(
        "05_long".to_owned(),
        TypedValue::Long {
            value: i32::MAX,
            raw_hex: None,
        },
    );
    values.insert(
        "06_single".to_owned(),
        TypedValue::Single {
            value: FiniteF32::new(1.25)?,
            raw_hex: None,
        },
    );
    values.insert(
        "07_double".to_owned(),
        TypedValue::Double {
            value: FiniteF64::new(-2.5)?,
            raw_hex: None,
        },
    );
    values.insert(
        "08_decimal".to_owned(),
        TypedValue::Decimal {
            value: InvariantDecimal::new("123.450")?,
            raw_hex: None,
        },
    );
    values.insert(
        "09_currency".to_owned(),
        TypedValue::Currency {
            value: InvariantDecimal::new("-0.0100")?,
            raw_hex: None,
        },
    );
    values.insert(
        "10_datetime".to_owned(),
        TypedValue::DateTime {
            value: InvariantDateTime::new("1997-01-02T03:04:05.600")?,
            raw_hex: None,
        },
    );
    values.insert(
        "11_text".to_owned(),
        TypedValue::Text {
            value: String::new(),
            raw_hex: Some(HexString::new("00ff")?),
            code_page: Some(1252),
        },
    );
    values.insert(
        "12_binary".to_owned(),
        TypedValue::Binary {
            value: HexString::new("")?,
            raw_hex: None,
        },
    );
    values.insert(
        "13_guid".to_owned(),
        TypedValue::Guid {
            value: Guid::new("00112233-4455-6677-8899-aabbccddeeff")?,
            raw_hex: None,
        },
    );
    values.insert(
        "14_memo".to_owned(),
        TypedValue::Memo {
            value: "memo".to_owned(),
            raw_hex: None,
            code_page: None,
        },
    );
    values.insert(
        "15_ole".to_owned(),
        TypedValue::Ole {
            value: HexString::new("deadbeef")?,
            raw_hex: None,
        },
    );

    let actual = emitted(&snapshot)?;
    for expected in [
        "\"01_null\":{\"kind\":\"null\",\"raw_hex\":\"\",\"value\":null}",
        "\"02_boolean\":{\"kind\":\"boolean\",\"value\":false}",
        "\"03_byte\":{\"kind\":\"byte\",\"value\":255}",
        "\"04_integer\":{\"kind\":\"integer\",\"value\":-32768}",
        "\"05_long\":{\"kind\":\"long\",\"value\":2147483647}",
        "\"06_single\":{\"kind\":\"single\",\"value\":1.25}",
        "\"07_double\":{\"kind\":\"double\",\"value\":-2.5}",
        "\"08_decimal\":{\"kind\":\"decimal\",\"value\":\"123.450\"}",
        "\"09_currency\":{\"kind\":\"currency\",\"value\":\"-0.0100\"}",
        concat!(
            "\"10_datetime\":{\"kind\":\"datetime\",",
            "\"value\":\"1997-01-02T03:04:05.600\"}"
        ),
        concat!(
            "\"11_text\":{\"code_page\":1252,\"kind\":\"text\",",
            "\"raw_hex\":\"00ff\",\"value\":\"\"}"
        ),
        "\"12_binary\":{\"kind\":\"binary\",\"value\":\"\"}",
        concat!(
            "\"13_guid\":{\"kind\":\"guid\",",
            "\"value\":\"00112233-4455-6677-8899-aabbccddeeff\"}"
        ),
        "\"14_memo\":{\"kind\":\"memo\",\"value\":\"memo\"}",
        "\"15_ole\":{\"kind\":\"ole\",\"value\":\"deadbeef\"}",
    ] {
        assert!(
            actual.contains(expected),
            "missing canonical value {expected}"
        );
    }
    assert!(actual.find("\"01_null\"") < actual.find("\"02_boolean\""));
    assert!(actual.contains("\"kind\":\"null\",\"raw_hex\""));
    assert!(actual.contains("\"code_page\":1252,\"kind\":\"text\",\"raw_hex\""));
    Ok(())
}

#[test]
fn sql_null_empty_text_and_empty_binary_remain_distinct() -> Result<(), SnapshotError> {
    let mut snapshot = empty_snapshot()?;
    snapshot.database_properties.insert(
        "binary".to_owned(),
        TypedValue::Binary {
            value: HexString::new("")?,
            raw_hex: None,
        },
    );
    snapshot
        .database_properties
        .insert("null".to_owned(), TypedValue::Null { raw_hex: None });
    snapshot.database_properties.insert(
        "text".to_owned(),
        TypedValue::Text {
            value: String::new(),
            raw_hex: None,
            code_page: None,
        },
    );
    let actual = emitted(&snapshot)?;
    assert!(actual.contains("\"binary\":{\"kind\":\"binary\",\"value\":\"\"}"));
    assert!(actual.contains("\"null\":{\"kind\":\"null\",\"value\":null}"));
    assert!(actual.contains("\"text\":{\"kind\":\"text\",\"value\":\"\"}"));
    Ok(())
}

#[test]
fn strings_use_compact_utf8_json_escaping() -> Result<(), SnapshotError> {
    let mut snapshot = empty_snapshot()?;
    snapshot.database_properties.insert(
        "a\"\\\n\u{0001}é😀".to_owned(),
        TypedValue::Text {
            value: "\u{0008}\u{000c}\n\r\t\"\\/\u{001f}é😀".to_owned(),
            raw_hex: None,
            code_page: None,
        },
    );
    let actual = emitted(&snapshot)?;
    assert!(actual.contains(concat!(
        "\"a\\\"\\\\\\n\\u0001é😀\":{\"kind\":\"text\",",
        "\"value\":\"\\b\\f\\n\\r\\t\\\"\\\\/\\u001fé😀\"}"
    )));
    assert!(!actual.contains("\\u00e9"));
    assert!(!actual.contains("\\ud83d"));
    Ok(())
}

#[test]
fn finite_numbers_are_normalized_and_nonfinite_values_are_rejected() -> Result<(), SnapshotError> {
    assert_eq!(
        FiniteF32::new(f32::NAN),
        Err(SnapshotError::NonFiniteNumber)
    );
    assert_eq!(
        FiniteF32::new(f32::INFINITY),
        Err(SnapshotError::NonFiniteNumber)
    );
    assert_eq!(
        FiniteF64::new(f64::NEG_INFINITY),
        Err(SnapshotError::NonFiniteNumber)
    );

    let mut snapshot = empty_snapshot()?;
    for (key, value) in [
        ("negative_zero", -0.0),
        ("positive_zero", 0.0),
        ("integral_fixed", 1.0),
        ("small_fixed", 0.0001),
        ("small_scientific", 0.00001),
        ("large_fixed", 1e15),
        ("large_scientific", 1e16),
    ] {
        snapshot.database_properties.insert(
            key.to_owned(),
            TypedValue::Double {
                value: FiniteF64::new(value)?,
                raw_hex: None,
            },
        );
    }
    let actual = emitted(&snapshot)?;
    assert!(actual.contains("\"negative_zero\":{\"kind\":\"double\",\"value\":-0.0}"));
    assert!(actual.contains("\"positive_zero\":{\"kind\":\"double\",\"value\":0.0}"));
    assert!(actual.contains("\"integral_fixed\":{\"kind\":\"double\",\"value\":1.0}"));
    assert!(actual.contains("\"small_fixed\":{\"kind\":\"double\",\"value\":0.0001}"));
    assert!(actual.contains("\"small_scientific\":{\"kind\":\"double\",\"value\":1e-05}"));
    assert!(actual.contains("\"large_fixed\":{\"kind\":\"double\",\"value\":1000000000000000.0}"));
    assert!(actual.contains("\"large_scientific\":{\"kind\":\"double\",\"value\":1e+16}"));
    assert!(!actual.contains("NaN"));
    assert!(!actual.contains("Infinity"));
    Ok(())
}

#[test]
fn lowercase_hex_and_guid_rules_fail_closed() -> Result<(), SnapshotError> {
    assert_eq!(HexString::new(""), Ok(HexString::from_bytes(&[])));
    assert_eq!(
        HexString::new("00abcdef"),
        Ok(HexString::from_bytes(&[0, 0xab, 0xcd, 0xef]))
    );
    for invalid in ["0", "ABCDEF", "0g", "ab cd", "é0"] {
        assert_eq!(HexString::new(invalid), Err(SnapshotError::InvalidHex));
    }

    let accepted = "00112233-4455-6677-8899-aabbccddeeff";
    assert_eq!(Guid::new(accepted)?.as_str(), accepted);
    for invalid in [
        "00112233-4455-6677-8899-AABBCCDDEEFF",
        "{00112233-4455-6677-8899-aabbccddeeff}",
        "001122334455-6677-8899-aabbccddeeff",
        "00112233-4455-6677-8899-aabbccddeefg",
    ] {
        assert_eq!(Guid::new(invalid), Err(SnapshotError::InvalidGuid));
    }
    Ok(())
}

#[test]
fn invariant_string_grammars_match_the_protocol_validator() -> Result<(), SnapshotError> {
    for accepted in ["0", "-0", "1", "-12", "12.0", "-12.3400"] {
        assert_eq!(InvariantDecimal::new(accepted)?.as_str(), accepted);
    }
    for invalid in ["", "-", "+1", "01", "1.", ".1", "1e2", "1,2"] {
        assert_eq!(
            InvariantDecimal::new(invalid),
            Err(SnapshotError::InvalidDecimal)
        );
    }

    for accepted in ["1997-01-02T03:04:05", "1997-01-02T03:04:05.000001"] {
        assert_eq!(InvariantDateTime::new(accepted)?.as_str(), accepted);
    }
    for invalid in [
        "",
        "1997-01-02",
        "1997-01-02 03:04:05",
        "1997-01-02T03:04:05.",
        "1997-01-02T03:04:05Z",
        "1997-01-02T03:04:05+00:00",
    ] {
        assert_eq!(
            InvariantDateTime::new(invalid),
            Err(SnapshotError::InvalidDateTime)
        );
    }
    Ok(())
}

#[test]
fn identifiers_hashes_and_producer_revisions_are_validated() -> Result<(), SnapshotError> {
    for accepted in [
        "DAO-GEN-ABC",
        "DAO-READ-A_1",
        "DAO-WRITE-A-B",
        "DAO-UPDATE-ABC_123",
    ] {
        assert_eq!(ScenarioId::new(accepted)?.as_str(), accepted);
    }
    for invalid in [
        "DAO-GEN-AB",
        "DAO-GEN-_AB",
        "DAO-OTHER-ABC",
        "DAO-READ-Abc",
        "DAO-READ-ABC!",
    ] {
        assert_eq!(
            ScenarioId::new(invalid),
            Err(SnapshotError::InvalidScenarioId)
        );
    }
    assert_eq!(Sha256::new("0".repeat(64))?.as_str(), "0".repeat(64));
    assert_eq!(
        Sha256::new("0".repeat(63)),
        Err(SnapshotError::InvalidSha256)
    );
    assert_eq!(
        Sha256::new("A".repeat(64)),
        Err(SnapshotError::InvalidSha256)
    );
    assert_eq!(
        Producer::new(ProducerKind::Dao, ""),
        Err(SnapshotError::InvalidSourceRevision)
    );
    Ok(())
}

#[test]
fn repeated_serialization_is_byte_identical() -> Result<(), SnapshotError> {
    let mut snapshot = empty_snapshot()?;
    snapshot.database_properties.insert(
        "é".to_owned(),
        TypedValue::Text {
            value: "same".to_owned(),
            raw_hex: None,
            code_page: Some(1252),
        },
    );
    let first = snapshot.to_canonical_json()?;
    let second = snapshot.clone().to_canonical_json()?;
    let sorted_clone = snapshot.to_canonicalized_json()?;
    assert_eq!(first, second);
    assert_eq!(first, sorted_clone);
    Ok(())
}
