use super::{IndexKeyEncoding, IndexNodeKind, IndexTree, IndexTreeError, PendingNode};
use crate::index_tree_page::parse_node;
use crate::{
    ByteCount, DatabaseReader, Error, JET3_PAGE_SIZE, PageGeometry, PageKind, PageNumber,
    ReadLimits, ResourceBudget, ResourceLimitKind, ResourceLimits, SliceSource,
};
use std::error::Error as _;

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

struct ColumnSpec {
    physical_type: u8,
    class: u8,
    size: u16,
    ordinal: u16,
    fixed_offset: u16,
}

fn column_record(column: &ColumnSpec) -> [u8; 18] {
    let mut record = [0_u8; 18];
    record[0] = column.physical_type;
    record[1..3].copy_from_slice(&column.ordinal.to_le_bytes());
    record[5..7].copy_from_slice(&column.ordinal.to_le_bytes());
    record[7..9].copy_from_slice(&1_u16.to_le_bytes());
    record[9..13].copy_from_slice(&[0x09, 0x04, 0xe4, 0x04]);
    record[13] = column.class;
    record[14..16].copy_from_slice(&column.fixed_offset.to_le_bytes());
    record[16..18].copy_from_slice(&column.size.to_le_bytes());
    record
}

/// Physical key slots as `(column ordinal, ascending)` pairs.
fn physical_record(keys: &[(u16, bool)]) -> [u8; 39] {
    let mut physical = [0_u8; 39];
    for slot in 0..10 {
        physical[slot * 3..slot * 3 + 2].copy_from_slice(&u16::MAX.to_le_bytes());
    }
    for (slot, (column, ascending)) in keys.iter().enumerate() {
        physical[slot * 3..slot * 3 + 2].copy_from_slice(&column.to_le_bytes());
        physical[slot * 3 + 2] = u8::from(*ascending);
    }
    physical[31..34].copy_from_slice(&[MAP_PAGE as u8, 0, 0]);
    physical[34..38].copy_from_slice(&(INDEX_ROOT as u32).to_le_bytes());
    physical
}

fn definition_with(columns: &[ColumnSpec], keys: &[(u16, bool)]) -> Vec<u8> {
    let column_count = columns.len() as u16;
    let variable_count = columns.iter().filter(|column| column.class == 2).count() as u16;
    let mut bytes = vec![0_u8; 43];
    bytes[..4].copy_from_slice(&[0x02, 0x01, 0x56, 0x43]);
    bytes[20] = 0x4e;
    bytes[21..23].copy_from_slice(&column_count.to_le_bytes());
    bytes[23..25].copy_from_slice(&variable_count.to_le_bytes());
    bytes[25..27].copy_from_slice(&column_count.to_le_bytes());
    bytes[27..29].copy_from_slice(&1_u16.to_le_bytes());
    bytes[31..33].copy_from_slice(&1_u16.to_le_bytes());
    bytes[35..39].copy_from_slice(&[0, MAP_PAGE as u8, 0, 0]);
    bytes[39..43].copy_from_slice(&[1, MAP_PAGE as u8, 0, 0]);
    bytes.extend_from_slice(&[0; 8]);
    for column in columns {
        bytes.extend_from_slice(&column_record(column));
    }
    for ordinal in 0..column_count {
        bytes.extend_from_slice(&[2, b'C', b'0' + ordinal as u8]);
    }
    bytes.extend_from_slice(&physical_record(keys));
    let mut logical = [0_u8; 20];
    logical[9..13].copy_from_slice(&u32::MAX.to_le_bytes());
    logical[17..19].copy_from_slice(&[4, 4]);
    bytes.extend_from_slice(&logical);
    bytes.extend_from_slice(&[3, b'I', b'd', b'x']);
    for column in columns
        .iter()
        .filter(|column| matches!(column.physical_type, 11 | 12))
    {
        let [low, high] = column.ordinal.to_le_bytes();
        bytes.extend_from_slice(&[low, high, 0, MAP_PAGE as u8, 0, 0, 1, MAP_PAGE as u8, 0, 0]);
    }
    bytes.extend_from_slice(&[0xff, 0xff]);
    let length = bytes.len() as u32;
    bytes[8..12].copy_from_slice(&length.to_le_bytes());
    bytes
}

fn definition(physical_type: u8, class: u8, size: u16) -> Vec<u8> {
    definition_with(
        &[ColumnSpec {
            physical_type,
            class,
            size,
            ordinal: 0,
            fixed_offset: 0,
        }],
        &[(0, true)],
    )
}

fn composite_definition() -> Vec<u8> {
    let long = |ordinal, fixed_offset| ColumnSpec {
        physical_type: 4,
        class: 3,
        size: 4,
        ordinal,
        fixed_offset,
    };
    definition_with(&[long(0, 0), long(1, 4)], &[(0, false), (1, true)])
}

/// Writes a table-owned data page holding `rows` one-byte rows.
fn write_row_page(bytes: &mut [u8], page_number: usize, rows: usize) {
    let page = &mut bytes[page_number * PAGE_BYTES..(page_number + 1) * PAGE_BYTES];
    page[0] = 1;
    page[4..8].copy_from_slice(&(ROOT as u32).to_le_bytes());
    page[8..10].copy_from_slice(&(rows as u16).to_le_bytes());
    for row in 0..rows {
        let start = (PAGE_BYTES - row - 1) as u16;
        page[10 + 2 * row..12 + 2 * row].copy_from_slice(&start.to_le_bytes());
    }
}

fn database_with_definition(definition: &[u8]) -> Vec<u8> {
    let mut bytes = vec![0_u8; PAGE_COUNT * PAGE_BYTES];
    bytes[4..19].copy_from_slice(b"Standard Jet DB");
    bytes[0x41] = 0x4e;
    bytes[0x42..0x50].copy_from_slice(&[
        0x86, 0xfb, 0xec, 0x37, 0x5d, 0x44, 0x9c, 0xfa, 0xc6, 0x5e, 0x28, 0xe6, 0x13, 0xb6,
    ]);
    bytes[ROOT * PAGE_BYTES..ROOT * PAGE_BYTES + definition.len()].copy_from_slice(definition);
    let map = &mut bytes[MAP_PAGE * PAGE_BYTES..(MAP_PAGE + 1) * PAGE_BYTES];
    map[0] = 1;
    map[8..10].copy_from_slice(&2_u16.to_le_bytes());
    map[10..12].copy_from_slice(&((PAGE_BYTES - 1) as u16).to_le_bytes());
    map[12..14].copy_from_slice(&((PAGE_BYTES - 2) as u16).to_le_bytes());
    write_row_page(&mut bytes, ROW_PAGE, 4);
    bytes
}

fn database_bytes(physical_type: u8, class: u8, size: u16) -> Vec<u8> {
    database_with_definition(&definition(physical_type, class, size))
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
    assert_eq!(tree.nodes()[0].page(), PageNumber::new(INDEX_ROOT as u64));
    assert_eq!(tree.nodes()[0].kind(), IndexNodeKind::Intermediate);
    assert_eq!(tree.nodes()[0].depth(), 1);
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
fn composite_descending_keys_stay_lossless_in_physical_order()
-> Result<(), Box<dyn std::error::Error>> {
    let mut bytes = database_with_definition(&composite_definition());
    let keys: [&[u8]; 3] = [
        &[0x7f, 0x7f, 0xff, 0xff, 0xfd, 0x7f, 0x80, 0, 0, 0],
        &[0x7f, 0x7f, 0xff, 0xff, 0xfd, 0x7f, 0x80, 0, 0, 1],
        &[0],
    ];
    let entries: Vec<Vec<u8>> = keys
        .iter()
        .enumerate()
        .map(|(slot, key)| leaf_entry(key, slot as u8))
        .collect();
    let entry_refs: Vec<&[u8]> = entries.iter().map(Vec::as_slice).collect();
    write_node(
        &mut bytes,
        NodeSpec {
            page: INDEX_ROOT,
            tag: 4,
            previous: 0,
            next: 0,
            tail_child: 0,
            prefix: &[],
            entries: &entry_refs,
        },
    );
    let (tree, _) = traverse_with_limits(&bytes, limits(&bytes))?;
    assert_eq!(tree.entries().len(), 3);
    for (slot, (entry, key)) in tree.entries().iter().zip(keys).enumerate() {
        assert_eq!(entry.key().raw_bytes(), key);
        assert_eq!(entry.key().encoding(), IndexKeyEncoding::Unsupported);
        assert_eq!(entry.row().slot(), slot as u8);
    }
    Ok(())
}

#[test]
fn item_work_is_charged_online_for_nodes_entries_and_rows() -> Result<(), Box<dyn std::error::Error>>
{
    let bytes = branch_tree();
    let (_, budget) = traverse_with_limits(&bytes, limits(&bytes))?;
    let work = budget.item_work();
    assert!(
        work >= 3 + 5 + 4,
        "nodes, entries, and row slots must be charged"
    );
    let exact = limits(&bytes).with_max_item_work(work);
    assert_eq!(traverse_with_limits(&bytes, exact)?.0.entries().len(), 4);
    let one_over = limits(&bytes).with_max_item_work(work - 1);
    let error = traverse_with_limits(&bytes, one_over)
        .err()
        .ok_or("one-over item work unexpectedly succeeded")?;
    assert!(matches!(
        error.downcast_ref::<IndexTreeError>(),
        Some(IndexTreeError::Resource(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::ItemWork,
            ..
        }))
    ));
    Ok(())
}

#[test]
fn interleaved_row_pages_are_validated_once_each() -> Result<(), Box<dyn std::error::Error>> {
    const SECOND_ROW_PAGE: usize = 7;
    let mut bytes = database_bytes(4, 3, 4);
    write_row_page(&mut bytes, SECOND_ROW_PAGE, 1);
    let entries: Vec<Vec<u8>> = (0..4_u8)
        .map(|ordinal| {
            let mut entry = leaf_entry(&[0x7f, 0x80, 0, 0, ordinal], 0);
            entry[7] = if ordinal % 2 == 0 {
                ROW_PAGE
            } else {
                SECOND_ROW_PAGE
            } as u8;
            entry
        })
        .collect();
    let entry_refs: Vec<&[u8]> = entries.iter().map(Vec::as_slice).collect();
    write_node(
        &mut bytes,
        NodeSpec {
            page: INDEX_ROOT,
            tag: 4,
            previous: 0,
            next: 0,
            tail_child: 0,
            prefix: &[],
            entries: &entry_refs,
        },
    );
    let mut opened = ResourceBudget::new(limits(&bytes));
    let source = SliceSource::new(&bytes, opened.read_budget())?;
    let mut database = DatabaseReader::from_source(source, &mut opened)?;
    let definition = database.table_definition(PageNumber::new(ROOT as u64), &mut opened)?;
    let before = opened.page_visits();
    database.index_tree(&definition, 0, &mut opened)?;
    // One leaf visit plus one visit for each of the two distinct row pages.
    assert_eq!(opened.page_visits() - before, 3);
    Ok(())
}

#[test]
fn rejects_row_locators_outside_validated_data_pages() {
    let entry = leaf_entry(&[0x7f, 0x80, 0, 0, 0], 0);
    let trailer = INDEX_ROOT * PAGE_BYTES + ENTRY_AREA_OFFSET + entry.len() - 4;
    let single_leaf = |mutate: &dyn Fn(&mut [u8])| {
        let mut bytes = database_bytes(4, 3, 4);
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
        mutate(&mut bytes);
        traverse_with_limits(&bytes, limits(&bytes))
            .err()
            .and_then(|error| error.downcast::<IndexTreeError>().ok())
            .map(|error| *error)
    };

    let table_page = single_leaf(&|bytes| bytes[trailer + 2] = ROOT as u8);
    assert!(matches!(
        table_page,
        Some(IndexTreeError::UnexpectedRowPageKind {
            actual: PageKind::TableDefinition,
            ..
        })
    ));

    let foreign_owner = single_leaf(&|bytes| bytes[trailer + 2] = MAP_PAGE as u8);
    assert!(matches!(
        foreign_owner,
        Some(IndexTreeError::RowDirectory {
            source: crate::RowDirectoryError::UnexpectedOwner { .. },
            ..
        })
    ));

    let missing_slot = single_leaf(&|bytes| bytes[trailer + 3] = 4);
    assert!(matches!(
        missing_slot,
        Some(IndexTreeError::RowDirectory {
            source: crate::RowDirectoryError::MissingRow {
                row: 4,
                row_count: 4
            },
            ..
        })
    ));
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

    let mut previous_sibling = branch_tree();
    previous_sibling[SECOND_LEAF * PAGE_BYTES + 8..SECOND_LEAF * PAGE_BYTES + 12]
        .copy_from_slice(&0_u32.to_le_bytes());
    assert!(matches!(
        traverse_with_limits(&previous_sibling, limits(&previous_sibling)),
        Err(error) if error.downcast_ref::<IndexTreeError>().is_some_and(|source| matches!(source, IndexTreeError::InvalidSiblingLink { role: "previous sibling", .. }))
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

fn parse_page(
    page: &[u8; PAGE_BYTES],
    kind: PageKind,
    page_number: usize,
) -> Result<crate::index_tree_page::ParsedNode, IndexTreeError> {
    let geometry = PageGeometry::new(
        ByteCount::new((PAGE_COUNT * PAGE_BYTES) as u64),
        JET3_PAGE_SIZE,
    )
    .map_err(IndexTreeError::Resource)?;
    let mut budget = ResourceBudget::new(ResourceLimits::default());
    parse_node(
        kind,
        PendingNode {
            page: PageNumber::new(page_number as u64),
            depth: 1,
        },
        PageNumber::new(ROOT as u64),
        geometry,
        page,
        &mut budget,
    )
}

fn page_copy(bytes: &[u8], page: usize) -> [u8; PAGE_BYTES] {
    let mut copy = [0_u8; PAGE_BYTES];
    let start = page * PAGE_BYTES;
    copy.copy_from_slice(&bytes[start..start + PAGE_BYTES]);
    copy
}

#[test]
fn rejects_each_node_header_and_reference_corruption() {
    let bytes = branch_tree();
    let root = page_copy(&bytes, INDEX_ROOT);
    assert!(matches!(
        parse_page(&root, PageKind::Data, INDEX_ROOT),
        Err(IndexTreeError::UnexpectedPageKind { .. })
    ));

    let mut header = root;
    header[1] = 0;
    assert!(matches!(
        parse_page(&header, PageKind::IntermediateIndex, INDEX_ROOT),
        Err(IndexTreeError::InvalidHeaderMarker { offset: 1, .. })
    ));

    let mut owner = root;
    owner[4..8].copy_from_slice(&(SECOND_LEAF as u32).to_le_bytes());
    assert!(matches!(
        parse_page(&owner, PageKind::IntermediateIndex, INDEX_ROOT),
        Err(IndexTreeError::UnexpectedOwner { .. })
    ));

    let mut marker = root;
    marker[21] = 0;
    assert!(matches!(
        parse_page(&marker, PageKind::IntermediateIndex, INDEX_ROOT),
        Err(IndexTreeError::InvalidHeaderMarker { offset: 21, .. })
    ));

    let mut branch_without_tail = root;
    branch_without_tail[16..20].copy_from_slice(&0_u32.to_le_bytes());
    assert!(matches!(
        parse_page(
            &branch_without_tail,
            PageKind::IntermediateIndex,
            INDEX_ROOT,
        ),
        Err(IndexTreeError::InvalidTailChild { child, .. }) if child == PageNumber::new(0)
    ));

    let mut leaf_with_tail = page_copy(&bytes, FIRST_LEAF);
    leaf_with_tail[16..20].copy_from_slice(&(SECOND_LEAF as u32).to_le_bytes());
    assert!(matches!(
        parse_page(&leaf_with_tail, PageKind::LeafIndex, FIRST_LEAF),
        Err(IndexTreeError::InvalidTailChild { .. })
    ));

    let mut reversed_boundary = root;
    reversed_boundary[20] = 13;
    assert!(matches!(
        parse_page(&reversed_boundary, PageKind::IntermediateIndex, INDEX_ROOT,),
        Err(IndexTreeError::InvalidEntryBoundary {
            boundary: 13,
            previous: 13,
            ..
        })
    ));

    let mut empty_prefixed_leaf = [0_u8; PAGE_BYTES];
    write_node(
        &mut empty_prefixed_leaf,
        NodeSpec {
            page: 0,
            tag: 4,
            previous: 0,
            next: 0,
            tail_child: 0,
            prefix: &[0x7f],
            entries: &[],
        },
    );
    assert!(matches!(
        parse_page(&empty_prefixed_leaf, PageKind::LeafIndex, 0),
        Err(IndexTreeError::InvalidEntryBoundary {
            boundary: 0,
            previous: 1,
            ..
        })
    ));

    let mut empty_branch = [0_u8; PAGE_BYTES];
    write_node(
        &mut empty_branch,
        NodeSpec {
            page: 0,
            tag: 3,
            previous: 0,
            next: 0,
            tail_child: FIRST_LEAF,
            prefix: &[],
            entries: &[],
        },
    );
    assert!(matches!(
        parse_page(&empty_branch, PageKind::IntermediateIndex, 0),
        Err(IndexTreeError::EmptyIntermediate { .. })
    ));

    let mut outside_sibling = root;
    outside_sibling[8..12].copy_from_slice(&(PAGE_COUNT as u32).to_le_bytes());
    assert!(matches!(
        parse_page(&outside_sibling, PageKind::IntermediateIndex, INDEX_ROOT,),
        Err(IndexTreeError::InvalidReference {
            role: "previous sibling",
            ..
        })
    ));
}

#[test]
fn invalid_row_reference_and_error_sources_remain_structured()
-> Result<(), Box<dyn std::error::Error>> {
    let mut bytes = database_bytes(4, 3, 4);
    let entry = leaf_entry(&[0x7f, 0x80, 0, 0, 0], 0);
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
    let trailer_page = INDEX_ROOT * PAGE_BYTES + ENTRY_AREA_OFFSET + entry.len() - 4;
    bytes[trailer_page..trailer_page + 3].copy_from_slice(&[0, 0, PAGE_COUNT as u8]);
    let error = traverse_with_limits(&bytes, limits(&bytes))
        .err()
        .ok_or("out-of-range row page unexpectedly succeeded")?;
    let source = error
        .downcast_ref::<IndexTreeError>()
        .ok_or("missing index traversal error")?;
    assert!(matches!(
        source,
        IndexTreeError::InvalidReference {
            role: "leaf row page",
            ..
        }
    ));
    assert!(source.to_string().contains("index traversal failed"));
    assert!(source.source().is_some());

    let plain = IndexTreeError::RepeatedPage {
        page: PageNumber::new(INDEX_ROOT as u64),
    };
    assert!(plain.source().is_none());
    Ok(())
}

#[path = "index_tree_key_inventory_tests.rs"]
mod key_inventory;
