#![no_main]

// Every database byte is project-authored synthetic input. Physical assertions
// are limited to EXP-0057, EXP-0059, and development-only EXP-0062.

use std::hint::black_box;

use jet3::{
    ByteCount, DatabaseReader, JET3_PAGE_SIZE, PageNumber, ReadLimits, ResourceBudget,
    ResourceLimits, SliceSource,
};
use libfuzzer_sys::fuzz_target;

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const PAGE_COUNT: usize = 8;
const DATABASE_BYTES: usize = PAGE_COUNT * PAGE_BYTES;
const CONTROL_BYTES: usize = 6;
const TABLE_ROOT: usize = 1;
const MAP_PAGE: usize = 2;
const INDEX_ROOT: usize = 3;
const FIRST_LEAF: usize = 4;
const SECOND_LEAF: usize = 5;
const ROW_PAGE: usize = 6;
const ENTRY_OFFSET: usize = 248;

fuzz_target!(|data: &[u8]| {
    let mut bytes = [0_u8; DATABASE_BYTES];
    synthetic_database(&mut bytes);
    let payload = data.get(CONTROL_BYTES..).unwrap_or_default();
    if !payload.starts_with(b"valid-index") && !payload.starts_with(b"tight-resources") {
        mutate_selected_page(&mut bytes, selector(data.get(5).copied()) % 8, payload);
    }

    let limits = ResourceLimits::new(ReadLimits::default())
        .with_max_allocation_bytes(ByteCount::new(selected_limit(
            data.first().copied(),
            131_072,
        )))
        .with_max_item_work(selected_limit(data.get(1).copied(), 8_192))
        .with_max_total_work_units(selected_limit(data.get(2).copied(), 524_288))
        .with_max_page_visits(selected_limit(data.get(3).copied(), 64))
        .with_max_chain_depth(selected_limit(data.get(4).copied(), 8));
    let mut budget = ResourceBudget::new(limits);
    let Ok(source) = SliceSource::new(&bytes, budget.read_budget()) else {
        return;
    };
    let Ok(mut database) = DatabaseReader::from_source(source, &mut budget) else {
        return;
    };
    let Ok(definition) = database.table_definition(PageNumber::new(TABLE_ROOT as u64), &mut budget)
    else {
        return;
    };
    let Ok(tree) = database.index_tree(&definition, 0, &mut budget) else {
        return;
    };
    black_box(tree.root());
    for node in tree.nodes() {
        black_box(node.page());
        black_box(node.kind());
        black_box(node.depth());
        black_box(node.previous());
        black_box(node.next());
    }
    for entry in tree.entries() {
        black_box(entry.key().raw_bytes());
        black_box(entry.key().encoding());
        black_box(entry.row());
    }
});

fn synthetic_database(bytes: &mut [u8; DATABASE_BYTES]) {
    bytes[4..19].copy_from_slice(b"Standard Jet DB");
    bytes[0x41] = 0x4e;
    bytes[0x42..0x50].copy_from_slice(&[
        0x86, 0xfb, 0xec, 0x37, 0x5d, 0x44, 0x9c, 0xfa, 0xc6, 0x5e, 0x28, 0xe6, 0x13, 0xb6,
    ]);
    write_definition(&mut bytes[TABLE_ROOT * PAGE_BYTES..(TABLE_ROOT + 1) * PAGE_BYTES]);
    bytes[MAP_PAGE * PAGE_BYTES] = 1;
    bytes[ROW_PAGE * PAGE_BYTES] = 1;

    let first = leaf_entry([0x7f, 0x80, 0, 0, 0], 0);
    let second = leaf_entry([0x7f, 0x80, 0, 0, 1], 1);
    let third = leaf_entry([0x7f, 0x80, 0, 0, 2], 2);
    let branch = branch_entry([0x7f, 0x80, 0, 0, 1], 1, FIRST_LEAF);
    write_node(bytes, INDEX_ROOT, 3, 0, 0, SECOND_LEAF, &[], &[&branch]);
    write_node(
        bytes,
        FIRST_LEAF,
        4,
        0,
        SECOND_LEAF,
        0,
        &[0x7f, 0x80, 0, 0],
        &[&first, &second],
    );
    write_node(
        bytes,
        SECOND_LEAF,
        4,
        FIRST_LEAF,
        0,
        0,
        &[0x7f, 0x80, 0, 0],
        &[&third],
    );
}

fn write_definition(page: &mut [u8]) {
    let mut definition = vec![0_u8; 43];
    definition[..4].copy_from_slice(&[0x02, 0x01, 0x56, 0x43]);
    definition[20] = 0x4e;
    definition[21..23].copy_from_slice(&1_u16.to_le_bytes());
    definition[25..27].copy_from_slice(&1_u16.to_le_bytes());
    definition[27..29].copy_from_slice(&1_u16.to_le_bytes());
    definition[31..33].copy_from_slice(&1_u16.to_le_bytes());
    definition[35..39].copy_from_slice(&[0, MAP_PAGE as u8, 0, 0]);
    definition[39..43].copy_from_slice(&[1, MAP_PAGE as u8, 0, 0]);
    definition.extend_from_slice(&[0; 8]);
    let mut column = [0_u8; 18];
    column[0] = 4;
    column[7..9].copy_from_slice(&1_u16.to_le_bytes());
    column[9..13].copy_from_slice(&[0x09, 0x04, 0xe4, 0x04]);
    column[13] = 3;
    column[16..18].copy_from_slice(&4_u16.to_le_bytes());
    definition.extend_from_slice(&column);
    definition.extend_from_slice(&[3, b'K', b'e', b'y']);
    let mut physical = [0_u8; 39];
    for slot in 0..10 {
        physical[slot * 3..slot * 3 + 2].copy_from_slice(&u16::MAX.to_le_bytes());
    }
    physical[..2].copy_from_slice(&0_u16.to_le_bytes());
    physical[2] = 1;
    physical[31..34].copy_from_slice(&[MAP_PAGE as u8, 0, 0]);
    physical[34..38].copy_from_slice(&(INDEX_ROOT as u32).to_le_bytes());
    definition.extend_from_slice(&physical);
    let mut logical = [0_u8; 20];
    logical[9..13].copy_from_slice(&u32::MAX.to_le_bytes());
    logical[17..19].copy_from_slice(&[4, 4]);
    definition.extend_from_slice(&logical);
    definition.extend_from_slice(&[3, b'I', b'd', b'x', 0xff, 0xff]);
    let length = definition.len() as u32;
    definition[8..12].copy_from_slice(&length.to_le_bytes());
    page[..definition.len()].copy_from_slice(&definition);
}

#[allow(clippy::too_many_arguments)]
fn write_node(
    bytes: &mut [u8; DATABASE_BYTES],
    page_number: usize,
    tag: u8,
    previous: usize,
    next: usize,
    tail_child: usize,
    prefix: &[u8],
    entries: &[&[u8]],
) {
    let page = &mut bytes[page_number * PAGE_BYTES..(page_number + 1) * PAGE_BYTES];
    page[0] = tag;
    page[1] = 1;
    page[4..8].copy_from_slice(&(TABLE_ROOT as u32).to_le_bytes());
    page[8..12].copy_from_slice(&(previous as u32).to_le_bytes());
    page[12..16].copy_from_slice(&(next as u32).to_le_bytes());
    page[16..20].copy_from_slice(&(tail_child as u32).to_le_bytes());
    page[20] = prefix.len() as u8;
    page[21] = u8::from(tag == 3);
    page[ENTRY_OFFSET..ENTRY_OFFSET + prefix.len()].copy_from_slice(prefix);
    let mut boundary = prefix.len();
    for entry in entries {
        let suffix = entry.strip_prefix(prefix).unwrap_or(entry);
        let start = ENTRY_OFFSET + boundary;
        page[start..start + suffix.len()].copy_from_slice(suffix);
        boundary += suffix.len();
        page[22 + boundary / 8] |= 1 << (boundary % 8);
    }
    page[2..4].copy_from_slice(&((PAGE_BYTES - ENTRY_OFFSET - boundary) as u16).to_le_bytes());
}

fn leaf_entry(key: [u8; 5], slot: u8) -> Vec<u8> {
    let mut entry = key.to_vec();
    entry.extend_from_slice(&[0, 0, ROW_PAGE as u8, slot]);
    entry
}

fn branch_entry(key: [u8; 5], slot: u8, child: usize) -> Vec<u8> {
    let mut entry = leaf_entry(key, slot);
    entry.extend_from_slice(&(child as u32).to_be_bytes());
    entry
}

fn mutate_selected_page(bytes: &mut [u8; DATABASE_BYTES], page: u8, payload: &[u8]) {
    if payload.is_empty() {
        return;
    }
    let start = usize::from(page) * PAGE_BYTES;
    let length = payload.len().min(PAGE_BYTES);
    bytes[start..start + length].copy_from_slice(&payload[..length]);
}

fn selector(value: Option<u8>) -> u8 {
    let value = value.unwrap_or_default();
    if value.is_ascii_digit() {
        value - b'0'
    } else {
        value
    }
}

fn selected_limit(value: Option<u8>, generous: u64) -> u64 {
    match selector(value) % 4 {
        0 => 0,
        1 => 1,
        2 => generous.saturating_sub(1),
        _ => generous,
    }
}
