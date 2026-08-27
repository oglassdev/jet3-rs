#![no_main]

// Every database byte is project-authored synthetic input. Physical assertions
// are limited to SRC-0020, EXP-0057, and development-only EXP-0059.

use std::hint::black_box;

use jet3::{
    ByteCount, DatabaseReader, IndexDefinitionKind, JET3_PAGE_SIZE, PageNumber, ReadLimits,
    ResourceBudget, ResourceLimits, SliceSource,
};
use libfuzzer_sys::fuzz_target;

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const PAGE_COUNT: usize = 6;
const DATABASE_BYTES: usize = PAGE_COUNT * PAGE_BYTES;
const CONTROL_BYTES: usize = 6;

fuzz_target!(|data: &[u8]| {
    let mut bytes = [0_u8; DATABASE_BYTES];
    supported_header(&mut bytes);
    relationship_definition(&mut bytes);
    let payload = data.get(CONTROL_BYTES..).unwrap_or_default();
    if !payload.starts_with(b"valid-definition") && !payload.starts_with(b"tight-resources") {
        mutate_selected_region(
            &mut bytes,
            selector(data.get(5).copied()) % 5,
            payload,
        );
    }

    let limits = ResourceLimits::new(ReadLimits::default())
        .with_max_allocation_bytes(ByteCount::new(selected_limit(
            data.first().copied(),
            65_536,
        )))
        .with_max_item_work(selected_limit(data.get(1).copied(), 4_096))
        .with_max_total_work_units(selected_limit(data.get(2).copied(), 262_144))
        .with_max_page_visits(selected_limit(data.get(3).copied(), 64))
        .with_max_chain_depth(selected_limit(data.get(4).copied(), 8));
    let mut budget = ResourceBudget::new(limits);
    let Ok(source) = SliceSource::new(&bytes, budget.read_budget()) else {
        return;
    };
    let Ok(mut database) = DatabaseReader::from_source(source, &mut budget) else {
        return;
    };
    let Ok(definition) = database.table_definition(PageNumber::new(1), &mut budget) else {
        return;
    };
    black_box(definition.logical_length());
    black_box(definition.raw_suffix());
    for column in definition.columns() {
        black_box(column.name().raw_bytes());
        black_box(column.physical_type().raw());
        black_box(column.raw_record());
    }
    for physical in definition.physical_indexes() {
        black_box(physical.fields());
        black_box(physical.root());
        black_box(physical.raw_record());
    }
    for index in definition.indexes() {
        black_box(index.name().raw_bytes());
        if let IndexDefinitionKind::Relationship(reference) = index.kind() {
            black_box(reference.related_table());
            black_box(reference.raw_relation_ordinal());
        }
    }
});

fn supported_header(bytes: &mut [u8; DATABASE_BYTES]) {
    bytes[4..19].copy_from_slice(b"Standard Jet DB");
    bytes[0x41] = 0x4e;
    bytes[0x42..0x50].copy_from_slice(&[
        0x86, 0xfb, 0xec, 0x37, 0x5d, 0x44, 0x9c, 0xfa, 0xc6, 0x5e, 0x28, 0xe6, 0x13, 0xb6,
    ]);
}

fn relationship_definition(bytes: &mut [u8; DATABASE_BYTES]) {
    let mut logical = [0x5a_u8; PAGE_BYTES + 34];
    logical[..43].fill(0);
    logical[..4].copy_from_slice(&[0x02, 0x01, 0x56, 0x43]);
    logical[4..8].copy_from_slice(&4_u32.to_le_bytes());
    logical[20] = 0x4e;
    logical[21..23].copy_from_slice(&1_u16.to_le_bytes());
    logical[25..27].copy_from_slice(&1_u16.to_le_bytes());
    logical[27..29].copy_from_slice(&1_u16.to_le_bytes());
    logical[31..33].copy_from_slice(&1_u16.to_le_bytes());
    logical[35..39].copy_from_slice(&[0, 2, 0, 0]);
    logical[39..43].copy_from_slice(&[1, 2, 0, 0]);
    logical[43..51].fill(0);
    let mut column = [0_u8; 18];
    column[0] = 4;
    column[7..9].copy_from_slice(&1_u16.to_le_bytes());
    column[9..13].copy_from_slice(&[0x09, 0x04, 0xe4, 0x04]);
    column[13] = 3;
    column[16..18].copy_from_slice(&4_u16.to_le_bytes());
    logical[51..69].copy_from_slice(&column);
    logical[69..72].copy_from_slice(&[2, b'I', b'd']);
    let mut physical = [0_u8; 39];
    for slot in 0..10 {
        physical[slot * 3..slot * 3 + 2].copy_from_slice(&u16::MAX.to_le_bytes());
    }
    physical[..2].copy_from_slice(&0_u16.to_le_bytes());
    physical[2] = 1;
    physical[31..34].copy_from_slice(&[2, 0, 0]);
    physical[34..38].copy_from_slice(&3_u32.to_le_bytes());
    logical[72..111].copy_from_slice(&physical);
    let mut index = [0_u8; 20];
    index[8] = 2;
    index[9..13].copy_from_slice(&1_u32.to_le_bytes());
    index[13..17].copy_from_slice(&5_u32.to_le_bytes());
    index[17..19].copy_from_slice(&[1, 1]);
    index[19] = 2;
    logical[111..131].copy_from_slice(&index);
    logical[131..135].copy_from_slice(&[3, b'R', b'e', b'l']);
    logical[PAGE_BYTES + 32..].copy_from_slice(&[0xff, 0xff]);
    let logical_length = logical.len() as u32;
    logical[8..12].copy_from_slice(&logical_length.to_le_bytes());

    bytes[PAGE_BYTES..2 * PAGE_BYTES].copy_from_slice(&logical[..PAGE_BYTES]);
    bytes[2 * PAGE_BYTES] = 1;
    bytes[3 * PAGE_BYTES] = 4;
    let continuation = &mut bytes[4 * PAGE_BYTES..5 * PAGE_BYTES];
    continuation[..4].copy_from_slice(&[0x02, 0x01, 0x56, 0x43]);
    continuation[8..8 + logical.len() - PAGE_BYTES].copy_from_slice(&logical[PAGE_BYTES..]);
    bytes[5 * PAGE_BYTES] = 2;
}

fn mutate_selected_region(bytes: &mut [u8; DATABASE_BYTES], mode: u8, payload: &[u8]) {
    if payload.is_empty() {
        return;
    }
    let (start, maximum) = match mode {
        0 => (PAGE_BYTES, PAGE_BYTES),
        1 => (4 * PAGE_BYTES, PAGE_BYTES),
        2 => (2 * PAGE_BYTES, PAGE_BYTES),
        3 => (3 * PAGE_BYTES, PAGE_BYTES),
        _ => (5 * PAGE_BYTES, PAGE_BYTES),
    };
    let length = payload.len().min(maximum);
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
