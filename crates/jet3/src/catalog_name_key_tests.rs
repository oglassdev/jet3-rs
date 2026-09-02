use super::*;

/// Encodes into a fixed buffer and returns the key bytes.
fn key(parent: i32, name: &[u8]) -> Result<Vec<u8>, CatalogNameKeyError> {
    let mut buffer = [0_u8; 64];
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
fn a_name_byte_above_the_established_range_is_refused() {
    // EXP-0087 deliberately derives no weight for these bytes.
    assert_eq!(
        key(TABLES_ID, b"Caf\xe9"),
        Err(CatalogNameKeyError::UnmappedNameByte {
            position: 3,
            byte: 0xe9,
        })
    );
}

#[test]
fn a_name_byte_access_refuses_in_object_names_has_no_weight() {
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
    assert_eq!(catalog_name_key_len(b""), None);
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
fn every_established_weight_has_a_non_zero_high_nibble() {
    // EXP-0087's key framing splits the primary section on the first byte whose
    // high nibble is zero, so no weight may have one.
    for byte in FIRST_MAPPED_BYTE..=LAST_MAPPED_BYTE {
        if let Some(weight) = primary_weight(byte) {
            assert_ne!(weight >> 4, 0, "byte {byte:#04x}");
        }
    }
}
