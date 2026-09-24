use super::name_key::*;

/// Encodes into a fixed buffer and returns the key bytes.
fn key(parent: i32, name: &[u8]) -> Result<Vec<u8>, CatalogNameKeyError> {
    let mut buffer = [0_u8; MAX_CREATION_KEY_BYTES];
    let length = encode_catalog_name_key(parent, name, &mut buffer)?;
    Ok(buffer[..length].to_vec())
}

const TABLES_ID: i32 = 0x0f00_0001;
const ROOT_CONTAINER_ID: i32 = 0x0f00_0000;
const DATABASES_ID: i32 = 0x0f00_0002;

#[test]
fn recorded_bootstrap_keys_are_reproduced_exactly() {
    // EXP-0079 recorded these complete keys; EXP-0087 observed them again.
    let recorded: [(i32, &[u8], &[u8]); 4] = [
        (
            ROOT_CONTAINER_ID,
            b"Tables",
            b"\x7f\x8f\x00\x00\x00\x7f\x77\x60\x61\x6d\x66\x76\x00",
        ),
        (
            DATABASES_ID,
            b"MSysDb",
            b"\x7f\x8f\x00\x00\x02\x7f\x6f\x76\x7d\x76\x64\x61\x00",
        ),
        (
            TABLES_ID,
            b"MSysObjects",
            b"\x7f\x8f\x00\x00\x01\x7f\x6f\x76\x7d\x76\x72\x61\x6b\x66\x62\x77\x76\x00",
        ),
        (
            TABLES_ID,
            b"Alpha",
            b"\x7f\x8f\x00\x00\x01\x7f\x60\x6d\x73\x69\x60\x00",
        ),
    ];
    for (parent, name, expected) in recorded {
        assert_eq!(key(parent, name).as_deref(), Ok(expected), "{name:?}");
    }
}

#[test]
fn recorded_probed_keys_are_reproduced_exactly() {
    // EXP-0087 recorded these keys for names built only from probed ASCII
    // bytes, which exercise weights no bootstrap name reaches.
    let recorded: [(&[u8], &[u8]); 2] = [
        (
            b"P01 \"#$%&'()*+,-/01Q",
            b"\x7f\x8f\x00\x00\x01\x7f\x73\x56\x57\x11\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x20\x56\x57\x74\x00",
        ),
        (
            b"P0110/-,+*)('&%$#\" R",
            b"\x7f\x8f\x00\x00\x01\x7f\x73\x56\x57\x57\x56\x20\x1e\x1d\x1c\x1b\x1a\x19\x18\x17\x16\x15\x14\x13\x11\x75\x00",
        ),
    ];
    for (name, expected) in recorded {
        assert_eq!(key(TABLES_ID, name).as_deref(), Ok(expected), "{name:?}");
    }
}

#[test]
fn case_folds_because_letters_share_a_primary_weight() {
    assert_eq!(key(TABLES_ID, b"Alpha"), key(TABLES_ID, b"ALPHA"));
    assert_eq!(key(TABLES_ID, b"Alpha"), key(TABLES_ID, b"alpha"));
}

#[test]
fn keys_order_by_parent_then_name() -> Result<(), CatalogNameKeyError> {
    let ordered = [
        key(ROOT_CONTAINER_ID, b"Tables")?,
        key(TABLES_ID, b"Alpha")?,
        key(TABLES_ID, b"Beta")?,
    ];
    let mut shuffled = [ordered[2].clone(), ordered[0].clone(), ordered[1].clone()];
    shuffled.sort_unstable();
    assert_eq!(shuffled, ordered);
    Ok(())
}

#[test]
fn negative_parents_sort_below_non_negative_ones() -> Result<(), CatalogNameKeyError> {
    assert!(key(-1, b"A")? < key(0, b"A")?);
    Ok(())
}

#[test]
fn extended_names_use_expansions_and_accent_weights() {
    // EXP-0101/0248: neutral C/A nibbles precede the acute E nibble.
    assert_eq!(
        key(TABLES_ID, b"Caf\xe9"),
        Ok(b"\x7f\x8f\x00\x00\x01\x7f\x62\x60\x67\x66\x02\x24\x00".to_vec())
    );
    assert_eq!(key(TABLES_ID, b"Caf\xe9"), key(TABLES_ID, b"CAF\xc9"));
    assert_eq!(key(TABLES_ID, b"AE"), key(TABLES_ID, b"\xc6"));
    assert_eq!(key(TABLES_ID, b"ss"), key(TABLES_ID, b"\xdf"));
    assert_ne!(key(TABLES_ID, b"e"), key(TABLES_ID, b"\xe9"));
}

#[test]
fn forbidden_punctuation_is_refused_before_encoding() {
    for (position, byte) in [b'!', b'.', b'[', b']', b'`'].into_iter().enumerate() {
        assert_eq!(
            key(TABLES_ID, &[b'A', byte]),
            Err(CatalogNameKeyError::UnmappedNameByte { position: 1, byte }),
            "excluded byte {position}"
        );
    }
}

#[test]
fn a_control_byte_is_refused_rather_than_indexed() {
    assert_eq!(
        key(TABLES_ID, b"\x00"),
        Err(CatalogNameKeyError::UnmappedNameByte {
            position: 0,
            byte: 0,
        })
    );
}

#[test]
fn an_empty_name_is_refused() {
    assert_eq!(key(TABLES_ID, b""), Err(CatalogNameKeyError::EmptyName));
}

#[test]
fn a_short_buffer_is_refused_without_writing_a_partial_key() {
    let mut buffer = [0_u8; 8];
    assert_eq!(
        encode_catalog_name_key(TABLES_ID, b"Alpha", &mut buffer),
        Err(CatalogNameKeyError::KeyTooLong {
            needed: 12,
            available: 8,
        })
    );
    assert_eq!(buffer, [0; 8]);
}

#[test]
fn undefined_and_overlong_names_preserve_the_output_buffer() {
    let mut output = [0xa5; MAX_CREATION_KEY_BYTES];
    for byte in [0x7f, 0x81, 0x8d, 0x8f, 0x90, 0x9d] {
        assert_eq!(
            encode_catalog_name_key(TABLES_ID, &[b'A', byte], &mut output),
            Err(CatalogNameKeyError::UnmappedNameByte { position: 1, byte })
        );
    }
    assert_eq!(
        encode_catalog_name_key(TABLES_ID, &[b'A'; 65], &mut output),
        Err(CatalogNameKeyError::NameTooLong {
            length: 65,
            maximum: 64
        })
    );
    assert_eq!(output, [0xa5; MAX_CREATION_KEY_BYTES]);
}

#[test]
fn native_locale_names_use_their_own_weights_and_code_page()
-> Result<(), Box<dyn std::error::Error>> {
    use super::name_key::{NameKey, catalog_names_equal_for, validate_catalog_name_for};
    use crate::SortOrder;

    assert!(!catalog_names_equal_for(SortOrder::Nordic, b"V", b"W"));
    assert!(catalog_names_equal_for(SortOrder::Nordic, b"V", b"v"));
    assert!(!catalog_names_equal_for(SortOrder::General, b"V", b"W"));
    assert!(catalog_names_equal_for(SortOrder::Dutch, b"IJ", b"\xff"));
    for order in SortOrder::known() {
        assert!(catalog_names_equal_for(order, b"Name", b"NAME "));
        let ch = NameKey::for_order(b"ch", order)?;
        let cz = NameKey::for_order(b"cz", order)?;
        assert_eq!(ch.bytes() > cz.bytes(), order == SortOrder::Spanish);
    }
    assert!(validate_catalog_name_for(b"N\x81", SortOrder::Cyrillic).is_ok());
    assert!(validate_catalog_name_for(b"N\x81", SortOrder::General).is_err());
    assert!(validate_catalog_name_for(b"N\xd2", SortOrder::Greek).is_err());
    assert!(validate_catalog_name_for(b"N\xd2", SortOrder::Cyrillic).is_ok());
    Ok(())
}
