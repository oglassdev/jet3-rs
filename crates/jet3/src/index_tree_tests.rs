use super::{IndexKeyEncoding, IndexNodeKind, IndexTree, IndexTreeError};
use crate::{
    ByteCount, DatabaseReader, Error, JET3_PAGE_SIZE, PageNumber, ReadLimits, ResourceBudget,
    ResourceLimitKind, ResourceLimits, SliceSource,
};

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const PAGE_COUNT: usize = 8;
const ROOT: usize = 1;
const MAP_PAGE: usize = 2;
const INDEX_ROOT: usize = 3;
const FIRST_LEAF: usize = 4;
const SECOND_LEAF: usize = 5;
const ROW_PAGE: usize = 6;
const ENTRY_AREA_OFFSET: usize = 248;
const ENTRY_AREA_LEN: usize = PAGE_BYTES - ENTRY_AREA_OFFSET;

fn column_record(physical_type: u8, class: u8, size: u16) -> [u8; 18] {
    let mut record = [0_u8; 18];
    record[0] = physical_type;
    record[7..9].copy_from_slice(&1_u16.to_le_bytes());
    record[9..13].copy_from_slice(&[0x09, 0x04, 0xe4, 0x04]);
    record[13] = class;
    record[16..18].copy_from_slice(&size.to_le_bytes());
    record
}

fn definition(physical_type: u8, class: u8, size: u16) -> Vec<u8> {
    let mut bytes = vec![0_u8; 43];
    bytes[..4].copy_from_slice(&[0x02, 0x01, 0x56, 0x43]);
    bytes[20] = 0x4e;
    bytes[21..23].copy_from_slice(&1_u16.to_le_bytes());
    if class == 2 {
        bytes[23..25].copy_from_slice(&1_u16.to_le_bytes());
    }
    bytes[25..27].copy_from_slice(&1_u16.to_le_bytes());
    bytes[27..29].copy_from_slice(&1_u16.to_le_bytes());
    bytes[31..33].copy_from_slice(&1_u16.to_le_bytes());
    bytes[35..39].copy_from_slice(&[0, MAP_PAGE as u8, 0, 0]);
    bytes[39..43].copy_from_slice(&[1, MAP_PAGE as u8, 0, 0]);
    bytes.extend_from_slice(&[0; 8]);
    bytes.extend_from_slice(&column_record(physical_type, class, size));
    bytes.extend_from_slice(&[3, b'K', b'e', b'y']);
    let mut physical = [0_u8; 39];
    for slot in 0..10 {
        physical[slot * 3..slot * 3 + 2].copy_from_slice(&u16::MAX.to_le_bytes());
    }
    physical[..2].copy_from_slice(&0_u16.to_le_bytes());
    physical[2] = 1;
    physical[31..34].copy_from_slice(&[MAP_PAGE as u8, 0, 0]);
    physical[34..38].copy_from_slice(&(INDEX_ROOT as u32).to_le_bytes());
    bytes.extend_from_slice(&physical);
    let mut logical = [0_u8; 20];
    logical[9..13].copy_from_slice(&u32::MAX.to_le_bytes());
    logical[17..19].copy_from_slice(&[4, 4]);
    bytes.extend_from_slice(&logical);
    bytes.extend_from_slice(&[3, b'I', b'd', b'x', 0xff, 0xff]);
    let length = bytes.len() as u32;
    bytes[8..12].copy_from_slice(&length.to_le_bytes());
    bytes
}

fn database_bytes(physical_type: u8, class: u8, size: u16) -> Vec<u8> {
    let mut bytes = vec![0_u8; PAGE_COUNT * PAGE_BYTES];
    bytes[4..19].copy_from_slice(b"Standard Jet DB");
    bytes[0x41] = 0x4e;
    bytes[0x42..0x50].copy_from_slice(&[
        0x86, 0xfb, 0xec, 0x37, 0x5d, 0x44, 0x9c, 0xfa, 0xc6, 0x5e, 0x28, 0xe6, 0x13, 0xb6,
    ]);
    let definition = definition(physical_type, class, size);
    bytes[ROOT * PAGE_BYTES..ROOT * PAGE_BYTES + definition.len()].copy_from_slice(&definition);
    bytes[MAP_PAGE * PAGE_BYTES] = 1;
    bytes[ROW_PAGE * PAGE_BYTES] = 1;
    bytes
}

struct NodeSpec<'a> {
    page: usize,
    tag: u8,
    previous: usize,
    next: usize,
    tail_child: usize,
    prefix: &'a [u8],
    entries: &'a [&'a [u8]],
}

fn write_node(bytes: &mut [u8], spec: NodeSpec<'_>) {
    let page = &mut bytes[spec.page * PAGE_BYTES..(spec.page + 1) * PAGE_BYTES];
    page[0] = spec.tag;
    page[1] = 1;
    page[4..8].copy_from_slice(&(ROOT as u32).to_le_bytes());
    page[8..12].copy_from_slice(&(spec.previous as u32).to_le_bytes());
    page[12..16].copy_from_slice(&(spec.next as u32).to_le_bytes());
    page[16..20].copy_from_slice(&(spec.tail_child as u32).to_le_bytes());
    page[20] = spec.prefix.len() as u8;
    page[21] = u8::from(spec.tag == 3);
    page[ENTRY_AREA_OFFSET..ENTRY_AREA_OFFSET + spec.prefix.len()].copy_from_slice(spec.prefix);
    let mut boundary = spec.prefix.len();
    for entry in spec.entries {
        let suffix = entry.strip_prefix(spec.prefix).unwrap_or(entry);
        let start = ENTRY_AREA_OFFSET + boundary;
        page[start..start + suffix.len()].copy_from_slice(suffix);
        boundary += suffix.len();
        page[22 + boundary / 8] |= 1 << (boundary % 8);
    }
    page[2..4].copy_from_slice(&((ENTRY_AREA_LEN - boundary) as u16).to_le_bytes());
}

fn leaf_entry(key: &[u8], slot: u8) -> Vec<u8> {
    let mut entry = key.to_vec();
    entry.extend_from_slice(&[0, 0, ROW_PAGE as u8, slot]);
    entry
}

fn branch_entry(key: &[u8], slot: u8, child: usize) -> Vec<u8> {
    let mut entry = leaf_entry(key, slot);
    entry.extend_from_slice(&(child as u32).to_be_bytes());
    entry
}

fn limits(bytes: &[u8]) -> ResourceLimits {
    ResourceLimits::new(ReadLimits::new(
        ByteCount::new(bytes.len() as u64),
        JET3_PAGE_SIZE,
        ByteCount::new(u64::MAX),
    ))
}

fn traverse_with_limits(
    bytes: &[u8],
    limits: ResourceLimits,
) -> Result<(IndexTree, ResourceBudget), Box<dyn std::error::Error>> {
    let mut budget = ResourceBudget::new(limits);
    let source = SliceSource::new(bytes, budget.read_budget())?;
    let mut database = DatabaseReader::from_source(source, &mut budget)?;
    let definition = database.table_definition(PageNumber::new(ROOT as u64), &mut budget)?;
    let tree = database.index_tree(&definition, 0, &mut budget)?;
    Ok((tree, budget))
}

fn branch_tree() -> Vec<u8> {
    let mut bytes = database_bytes(4, 3, 4);
    let first = leaf_entry(&[0x7f, 0x80, 0, 0, 0], 0);
    let separator = leaf_entry(&[0x7f, 0x80, 0, 0, 1], 1);
    let third = leaf_entry(&[0x7f, 0x80, 0, 0, 2], 2);
    let fourth = leaf_entry(&[0x7f, 0x80, 0, 0, 3], 3);
    let branch = branch_entry(&[0x7f, 0x80, 0, 0, 1], 1, FIRST_LEAF);
    write_node(
        &mut bytes,
        NodeSpec {
            page: INDEX_ROOT,
            tag: 3,
            previous: 0,
            next: 0,
            tail_child: SECOND_LEAF,
            prefix: &[],
            entries: &[&branch],
        },
    );
    write_node(
        &mut bytes,
        NodeSpec {
            page: FIRST_LEAF,
            tag: 4,
            previous: 0,
            next: SECOND_LEAF,
            tail_child: 0,
            prefix: &[0x7f, 0x80, 0, 0],
            entries: &[&first, &separator],
        },
    );
    write_node(
        &mut bytes,
        NodeSpec {
            page: SECOND_LEAF,
            tag: 4,
            previous: FIRST_LEAF,
            next: 0,
            tail_child: 0,
            prefix: &[0x7f, 0x80, 0, 0],
            entries: &[&third, &fourth],
        },
    );
    bytes
}

#[test]
fn traverses_branch_and_prefixed_leaves_in_physical_order() -> Result<(), Box<dyn std::error::Error>>
{
    let bytes = branch_tree();
    let (tree, _) = traverse_with_limits(&bytes, limits(&bytes))?;
    assert_eq!(tree.root(), PageNumber::new(INDEX_ROOT as u64));
    assert_eq!(tree.nodes().len(), 3);
    assert_eq!(tree.nodes()[0].kind(), IndexNodeKind::Intermediate);
    assert_eq!(
        tree.nodes()[1].next(),
        Some(PageNumber::new(SECOND_LEAF as u64))
    );
    assert_eq!(
        tree.nodes()[2].previous(),
        Some(PageNumber::new(FIRST_LEAF as u64))
    );
    assert_eq!(tree.entries().len(), 4);
    for (ordinal, entry) in tree.entries().iter().enumerate() {
        assert_eq!(entry.key().encoding(), IndexKeyEncoding::Long);
        assert_eq!(entry.key().raw_bytes()[4], ordinal as u8);
        assert_eq!(entry.row().slot(), ordinal as u8);
    }
    Ok(())
}

#[test]
fn rejects_child_cycles_repeats_and_leaf_link_self_references() {
    let mut child_cycle = branch_tree();
    let branch_end = ENTRY_AREA_OFFSET + 13;
    child_cycle[INDEX_ROOT * PAGE_BYTES + branch_end - 4..INDEX_ROOT * PAGE_BYTES + branch_end]
        .copy_from_slice(&(INDEX_ROOT as u32).to_be_bytes());
    assert!(matches!(
        traverse_with_limits(&child_cycle, limits(&child_cycle)),
        Err(error) if error.downcast_ref::<IndexTreeError>().is_some_and(|source| matches!(source, IndexTreeError::SelfReference { role: "branch child", .. }))
    ));

    let mut repeated = branch_tree();
    repeated[INDEX_ROOT * PAGE_BYTES + 16..INDEX_ROOT * PAGE_BYTES + 20]
        .copy_from_slice(&(FIRST_LEAF as u32).to_le_bytes());
    assert!(matches!(
        traverse_with_limits(&repeated, limits(&repeated)),
        Err(error) if error.downcast_ref::<IndexTreeError>().is_some_and(|source| matches!(source, IndexTreeError::RepeatedPage { .. }))
    ));

    let mut link_cycle = branch_tree();
    link_cycle[FIRST_LEAF * PAGE_BYTES + 12..FIRST_LEAF * PAGE_BYTES + 16]
        .copy_from_slice(&(FIRST_LEAF as u32).to_le_bytes());
    assert!(matches!(
        traverse_with_limits(&link_cycle, limits(&link_cycle)),
        Err(error) if error.downcast_ref::<IndexTreeError>().is_some_and(|source| matches!(source, IndexTreeError::SelfReference { role: "next sibling", .. }))
    ));
}

#[test]
fn exact_depth_succeeds_and_one_over_is_resource_rejected() -> Result<(), Box<dyn std::error::Error>>
{
    let bytes = branch_tree();
    let exact = limits(&bytes).with_max_chain_depth(2);
    assert_eq!(traverse_with_limits(&bytes, exact)?.0.nodes().len(), 3);
    let one_over = limits(&bytes).with_max_chain_depth(1);
    let result = traverse_with_limits(&bytes, one_over);
    let error = result.err().ok_or("depth two unexpectedly succeeded")?;
    let source = error
        .downcast_ref::<IndexTreeError>()
        .ok_or("missing index error")?;
    assert!(matches!(
        source,
        IndexTreeError::Resource(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::ChainDepth,
            requested: 2,
            maximum: 1,
        })
    ));
    Ok(())
}

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

#[test]
fn rejects_bitmap_free_space_and_sibling_corruption() {
    let mut outside = branch_tree();
    outside[INDEX_ROOT * PAGE_BYTES + 247] |= 1 << 1;
    assert!(traverse_with_limits(&outside, limits(&outside)).is_err());

    let mut free = branch_tree();
    free[INDEX_ROOT * PAGE_BYTES + 2] ^= 1;
    assert!(matches!(
        traverse_with_limits(&free, limits(&free)),
        Err(error) if error.downcast_ref::<IndexTreeError>().is_some_and(|source| matches!(source, IndexTreeError::InvalidFreeSpace { .. }))
    ));

    let mut sibling = branch_tree();
    sibling[FIRST_LEAF * PAGE_BYTES + 12..FIRST_LEAF * PAGE_BYTES + 16]
        .copy_from_slice(&0_u32.to_le_bytes());
    assert!(matches!(
        traverse_with_limits(&sibling, limits(&sibling)),
        Err(error) if error.downcast_ref::<IndexTreeError>().is_some_and(|source| matches!(source, IndexTreeError::InvalidSiblingLink { .. }))
    ));

    let prefix = [0x7f, 0x80, 0, 0, 0];
    let mut short_leaf = database_bytes(4, 3, 4);
    let mut short_leaf_entry = prefix.to_vec();
    short_leaf_entry.extend_from_slice(&[0, 0, ROW_PAGE as u8]);
    write_node(
        &mut short_leaf,
        NodeSpec {
            page: INDEX_ROOT,
            tag: 4,
            previous: 0,
            next: 0,
            tail_child: 0,
            prefix: &prefix,
            entries: &[&short_leaf_entry],
        },
    );
    assert!(matches!(
        traverse_with_limits(&short_leaf, limits(&short_leaf)),
        Err(error) if error.downcast_ref::<IndexTreeError>().is_some_and(|source| matches!(source, IndexTreeError::TruncatedEntry { .. }))
    ));

    let mut short_branch = database_bytes(4, 3, 4);
    let mut short_branch_entry = prefix.to_vec();
    short_branch_entry.extend_from_slice(&[0, 0, ROW_PAGE as u8, 0, 0, 4, 0]);
    write_node(
        &mut short_branch,
        NodeSpec {
            page: INDEX_ROOT,
            tag: 3,
            previous: 0,
            next: 0,
            tail_child: SECOND_LEAF,
            prefix: &prefix,
            entries: &[&short_branch_entry],
        },
    );
    assert!(matches!(
        traverse_with_limits(&short_branch, limits(&short_branch)),
        Err(error) if error.downcast_ref::<IndexTreeError>().is_some_and(|source| matches!(source, IndexTreeError::TruncatedEntry { .. }))
    ));
}
