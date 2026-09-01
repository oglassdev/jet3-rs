use super::*;

#[test]
fn key_inventory_is_typed_only_for_observed_encodings_and_other_bytes_are_lossless()
-> Result<(), Box<dyn std::error::Error>> {
    let cases: &[(u8, u8, u16, &[u8], IndexKeyEncoding)] = &[
        (1, 3, 1, &[0x7f, 0xff], IndexKeyEncoding::Boolean),
        (2, 3, 1, &[0x7f, 0x7f], IndexKeyEncoding::Byte),
        (3, 3, 2, &[0x7f, 0x80, 0], IndexKeyEncoding::Integer),
        (4, 3, 4, &[0x7f, 0x80, 0, 0, 0], IndexKeyEncoding::Long),
        (
            5,
            3,
            8,
            &[0x7f, 0x80, 0, 0, 0, 0, 0, 0, 0],
            IndexKeyEncoding::Currency,
        ),
        (6, 3, 4, &[0x7f, 0x80, 0, 0, 0], IndexKeyEncoding::Single),
        (
            7,
            3,
            8,
            &[0x7f, 0x80, 0, 0, 0, 0, 0, 0, 0],
            IndexKeyEncoding::Double,
        ),
        (
            8,
            3,
            8,
            &[0x7f, 0xc0, 0, 0, 0, 0, 0, 0, 0],
            IndexKeyEncoding::DateTime,
        ),
        (9, 2, 3, &[0x7f, 1, 2, 3, 3], IndexKeyEncoding::Binary),
        (10, 2, 20, &[0x7f, 0x60, 0], IndexKeyEncoding::TextCollation),
        (4, 3, 4, &[0], IndexKeyEncoding::Null),
        (11, 2, 0, &[0xde, 0xad], IndexKeyEncoding::Unsupported),
        (12, 2, 0, &[0xbe, 0xef], IndexKeyEncoding::Unsupported),
        (15, 3, 16, &[0xca, 0xfe], IndexKeyEncoding::Unsupported),
    ];
    for (physical_type, class, size, raw_key, expected) in cases {
        let mut bytes = database_bytes(*physical_type, *class, *size);
        let entry = leaf_entry(raw_key, 0);
        write_node(
            &mut bytes,
            NodeSpec {
                page: INDEX_ROOT,
                tag: 4,
                previous: 0,
                next: 0,
                tail_child: 0,
                prefix: &[],
                entries: &[&entry],
            },
        );
        let (tree, _) = traverse_with_limits(&bytes, limits(&bytes))?;
        assert_eq!(tree.entries()[0].key().encoding(), *expected);
        assert_eq!(tree.entries()[0].key().raw_bytes(), *raw_key);
    }
    Ok(())
}
