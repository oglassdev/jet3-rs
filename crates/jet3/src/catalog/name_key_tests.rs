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
fn recorded_keys_are_reproduced_exactly() {
    // EXP-0079 recorded the bootstrap keys; EXP-0087 observed them again and
    // recorded the probed-ASCII names, which reach weights no bootstrap name does.
    let recorded: [(i32, &[u8], &[u8]); 6] = [
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
        (
            TABLES_ID,
            b"P01 \"#$%&'()*+,-/01Q",
            b"\x7f\x8f\x00\x00\x01\x7f\x73\x56\x57\x11\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x20\x56\x57\x74\x00",
        ),
        (
            TABLES_ID,
            b"P0110/-,+*)('&%$#\" R",
            b"\x7f\x8f\x00\x00\x01\x7f\x73\x56\x57\x57\x56\x20\x1e\x1d\x1c\x1b\x1a\x19\x18\x17\x16\x15\x14\x13\x11\x75\x00",
        ),
    ];
    for (parent, name, expected) in recorded {
        assert_eq!(key(parent, name).as_deref(), Ok(expected), "{name:?}");
    }
}

#[test]
fn keys_case_fold_and_order_by_signed_parent_then_name() -> Result<(), CatalogNameKeyError> {
    // Letters share a primary weight across case.
    assert_eq!(key(TABLES_ID, b"Alpha"), key(TABLES_ID, b"ALPHA"));
    assert_eq!(key(TABLES_ID, b"Alpha"), key(TABLES_ID, b"alpha"));
    let ordered = [
        key(-1, b"A")?,
        key(0, b"A")?,
        key(ROOT_CONTAINER_ID, b"Tables")?,
        key(TABLES_ID, b"Alpha")?,
        key(TABLES_ID, b"Beta")?,
    ];
    let mut shuffled = ordered.clone();
    shuffled.reverse();
    shuffled.sort_unstable();
    assert_eq!(shuffled, ordered);
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
fn invalid_names_and_short_buffers_are_refused_without_writing() {
    let mut output = [0xa5; MAX_CREATION_KEY_BYTES];
    let mut cases = vec![
        (vec![], CatalogNameKeyError::EmptyName),
        (
            vec![0],
            CatalogNameKeyError::UnmappedNameByte {
                position: 0,
                byte: 0,
            },
        ),
        (
            vec![b'A'; 65],
            CatalogNameKeyError::NameTooLong {
                length: 65,
                maximum: 64,
            },
        ),
    ];
    // Forbidden punctuation, then bytes with no code-page mapping.
    for byte in [
        b'!', b'.', b'[', b']', b'`', 0x7f, 0x81, 0x8d, 0x8f, 0x90, 0x9d,
    ] {
        cases.push((
            vec![b'A', byte],
            CatalogNameKeyError::UnmappedNameByte { position: 1, byte },
        ));
    }
    for (name, expected) in cases {
        assert_eq!(
            encode_catalog_name_key(TABLES_ID, &name, &mut output),
            Err(expected),
            "{name:x?}"
        );
    }
    assert_eq!(output, [0xa5; MAX_CREATION_KEY_BYTES]);

    let mut short = [0_u8; 8];
    assert_eq!(
        encode_catalog_name_key(TABLES_ID, b"Alpha", &mut short),
        Err(CatalogNameKeyError::KeyTooLong {
            needed: 12,
            available: 8,
        })
    );
    assert_eq!(short, [0; 8]);
}

#[test]
fn native_locale_names_use_their_own_weights_and_code_page()
-> Result<(), Box<dyn std::error::Error>> {
    use super::name_key::{NameKey, catalog_names_equal, validate_catalog_name};
    use crate::SortOrder;

    assert!(!catalog_names_equal(b"V", b"W", SortOrder::Nordic));
    assert!(catalog_names_equal(b"V", b"v", SortOrder::Nordic));
    assert!(!catalog_names_equal(b"V", b"W", SortOrder::General));
    assert!(catalog_names_equal(b"IJ", b"\xff", SortOrder::Dutch));
    for order in SortOrder::known() {
        assert!(catalog_names_equal(b"Name", b"NAME ", order));
        let ch = NameKey::new(b"ch", order)?;
        let cz = NameKey::new(b"cz", order)?;
        assert_eq!(ch.bytes() > cz.bytes(), order == SortOrder::Spanish);
    }
    assert!(validate_catalog_name(b"N\x81", SortOrder::Cyrillic).is_ok());
    assert!(validate_catalog_name(b"N\x81", SortOrder::General).is_err());
    assert!(validate_catalog_name(b"N\xd2", SortOrder::Greek).is_err());
    assert!(validate_catalog_name(b"N\xd2", SortOrder::Cyrillic).is_ok());
    Ok(())
}
