#![no_main]

// Every database byte is project-authored synthetic input. Physical assertions
// are limited to the development-only EXP-0060 observation.

use std::hint::black_box;

use jet3::{
    ByteCount, DatabaseReader, JET3_PAGE_SIZE, PageNumber, ReadLimits, ResourceBudget,
    ResourceLimits, SliceSource,
};
use libfuzzer_sys::fuzz_target;

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const PAGE_COUNT: usize = 5;
const DATABASE_BYTES: usize = PAGE_COUNT * PAGE_BYTES;
const CONTROL_BYTES: usize = 6;

fuzz_target!(|data: &[u8]| {
    let mut bytes = [0_u8; DATABASE_BYTES];
    synthetic_database(&mut bytes);
    let payload = data.get(CONTROL_BYTES..).unwrap_or_default();
    if !payload.starts_with(b"valid-rows") {
        mutate_selected_region(
            &mut bytes,
            selector(data.get(5).copied()) % 5,
            payload,
        );
    }
    let limits = ResourceLimits::new(ReadLimits::default())
        .with_max_allocation_bytes(ByteCount::new(selected_limit(
            data.first().copied(),
            131_072,
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
    let Ok(mut rows) = database.rows(&definition, &mut budget) else {
        return;
    };
    for _ in 0..64 {
        let Ok(next) = rows.next_row() else {
            return;
        };
        let Some(row) = next else { return };
        black_box(row.locator());
        black_box(row.storage_locator());
        black_box(row.raw_bytes());
        for column in definition.columns() {
            if let Some(field) = row.field(column.ordinal()) {
                black_box(field.is_null());
                black_box(field.raw_bytes());
            }
        }
    }
});

fn synthetic_database(bytes: &mut [u8; DATABASE_BYTES]) {
    bytes[4..19].copy_from_slice(b"Standard Jet DB");
    bytes[0x41] = 0x4e;
    bytes[0x42..0x50].copy_from_slice(&[
        0x86, 0xfb, 0xec, 0x37, 0x5d, 0x44, 0x9c, 0xfa, 0xc6, 0x5e, 0x28, 0xe6, 0x13, 0xb6,
    ]);
    table_definition(&mut bytes[PAGE_BYTES..2 * PAGE_BYTES]);
    let owned = [0, 0, 0, 0, 0, (1 << 3) | (1 << 4)];
    let available = [0, 0, 0, 0, 0];
    write_rows(
        &mut bytes[2 * PAGE_BYTES..3 * PAGE_BYTES],
        0,
        &[(&owned, 0), (&available, 0)],
    );
    let direct = [2, 1, 0, 0, 0, b'a', 6, 5, 1, 3];
    let pointer = [0, 4, 0, 0];
    write_rows(
        &mut bytes[3 * PAGE_BYTES..4 * PAGE_BYTES],
        1,
        &[(&direct, 0), (&pointer, 0x4000)],
    );
    let mut target = [b'O'; 265];
    target[0..5].copy_from_slice(&[2, 5, 0, 0, 0]);
    target[260..].copy_from_slice(&[4, 5, 1, 1, 3]);
    write_rows(
        &mut bytes[4 * PAGE_BYTES..5 * PAGE_BYTES],
        1,
        &[(&target, 0x8000)],
    );
}

fn table_definition(page: &mut [u8]) {
    page[..4].copy_from_slice(&[0x02, 0x01, 0x56, 0x43]);
    page[8..12].copy_from_slice(&92_u32.to_le_bytes());
    page[20] = 0x4e;
    page[21..23].copy_from_slice(&2_u16.to_le_bytes());
    page[23..25].copy_from_slice(&1_u16.to_le_bytes());
    page[25..27].copy_from_slice(&2_u16.to_le_bytes());
    page[35..39].copy_from_slice(&[0, 2, 0, 0]);
    page[39..43].copy_from_slice(&[1, 2, 0, 0]);
    let mut fixed = [0_u8; 18];
    fixed[0] = 4;
    fixed[7..9].copy_from_slice(&1_u16.to_le_bytes());
    fixed[9..13].copy_from_slice(&[0x09, 0x04, 0xe4, 0x04]);
    fixed[13] = 3;
    fixed[16..18].copy_from_slice(&4_u16.to_le_bytes());
    page[43..61].copy_from_slice(&fixed);
    let mut variable = [0_u8; 18];
    variable[0] = 10;
    variable[1..3].copy_from_slice(&1_u16.to_le_bytes());
    variable[5..7].copy_from_slice(&1_u16.to_le_bytes());
    variable[7..9].copy_from_slice(&1_u16.to_le_bytes());
    variable[9..13].copy_from_slice(&[0x09, 0x04, 0xe4, 0x04]);
    variable[13] = 2;
    variable[16..18].copy_from_slice(&255_u16.to_le_bytes());
    page[61..79].copy_from_slice(&variable);
    page[79..90].copy_from_slice(&[2, b'I', b'd', 7, b'P', b'a', b'y', b'l', b'o', b'a', b'd']);
    page[90..92].copy_from_slice(&[0xff, 0xff]);
}

fn write_rows(page: &mut [u8], owner: u32, rows: &[(&[u8], u16)]) {
    page[0] = 1;
    page[4..8].copy_from_slice(&owner.to_le_bytes());
    page[8..10].copy_from_slice(&(rows.len() as u16).to_le_bytes());
    let mut start = PAGE_BYTES;
    for (index, (row, flags)) in rows.iter().enumerate() {
        start -= row.len();
        let raw = start as u16 | flags;
        page[10 + 2 * index..12 + 2 * index].copy_from_slice(&raw.to_le_bytes());
        page[start..start + row.len()].copy_from_slice(row);
    }
}

fn mutate_selected_region(bytes: &mut [u8; DATABASE_BYTES], mode: u8, payload: &[u8]) {
    if payload.is_empty() {
        return;
    }
    let page = usize::from(mode);
    let start = page * PAGE_BYTES;
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
