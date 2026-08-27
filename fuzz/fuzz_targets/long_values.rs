#![no_main]

// Every database byte is project-authored synthetic input. Physical assertions
// are limited to the development-only EXP-0061 observation.

use std::hint::black_box;

use jet3::{
    ByteCount, DatabaseReader, JET3_PAGE_SIZE, LongValue, PageNumber, ReadLimits,
    ResourceBudget, ResourceLimits, SliceSource, TextCodePage, ValueKind,
};
use libfuzzer_sys::fuzz_target;

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const PAGE_COUNT: usize = 6;
const DATABASE_BYTES: usize = PAGE_COUNT * PAGE_BYTES;
const CONTROL_BYTES: usize = 8;

fuzz_target!(|data: &[u8]| {
    let mut bytes = [0_u8; DATABASE_BYTES];
    synthetic_database(&mut bytes);
    let payload = data.get(CONTROL_BYTES..).unwrap_or_default();
    if !payload.starts_with(b"valid-values") {
        mutate_selected_page(
            &mut bytes,
            selector(data.get(7).copied()) % PAGE_COUNT as u8,
            payload,
        );
    }
    let limits = ResourceLimits::new(ReadLimits::default())
        .with_max_allocation_bytes(ByteCount::new(selected_limit(
            data.first().copied(),
            131_072,
        )))
        .with_max_decoded_value_bytes(ByteCount::new(selected_limit(
            data.get(1).copied(),
            4_096,
        )))
        .with_max_total_decoded_bytes(ByteCount::new(selected_limit(
            data.get(2).copied(),
            8_192,
        )))
        .with_max_item_work(selected_limit(data.get(3).copied(), 4_096))
        .with_max_total_work_units(selected_limit(data.get(4).copied(), 262_144))
        .with_max_page_visits(selected_limit(data.get(5).copied(), 64))
        .with_max_chain_depth(selected_limit(data.get(6).copied(), 8));
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
    let Some(mut row) = rows.next_row().ok().flatten() else {
        return;
    };
    let mut external = None;
    for column in definition.columns() {
        let Ok(Some(value)) = row.value(column.ordinal(), TextCodePage::Windows1252) else {
            return;
        };
        black_box(value.raw_bytes());
        match value.kind() {
            ValueKind::LongValue(LongValue::External(reference)) => external = Some(*reference),
            ValueKind::LongValue(LongValue::Inline { raw_header, value }) => {
                black_box(raw_header);
                black_box(value);
            }
            other => {
                black_box(other);
            }
        }
    }
    drop(row);
    let Some(reference) = external else { return };
    let Ok(mut long_value) = rows.long_value(reference) else {
        return;
    };
    for _ in 0..16 {
        let Ok(next) = long_value.next_chunk() else {
            return;
        };
        let Some(chunk) = next else { return };
        black_box(chunk.raw_row());
        black_box(chunk.value());
    }
});

fn synthetic_database(bytes: &mut [u8; DATABASE_BYTES]) {
    bytes[4..19].copy_from_slice(b"Standard Jet DB");
    bytes[0x41] = 0x4e;
    bytes[0x42..0x50].copy_from_slice(&[
        0x86, 0xfb, 0xec, 0x37, 0x5d, 0x44, 0x9c, 0xfa, 0xc6, 0x5e, 0x28, 0xe6, 0x13, 0xb6,
    ]);
    table_definition(&mut bytes[PAGE_BYTES..2 * PAGE_BYTES]);
    let owned = [0, 0, 0, 0, 0, (1 << 3) | (1 << 4) | (1 << 5)];
    let available = [0, 0, 0, 0, 0];
    write_rows(
        &mut bytes[2 * PAGE_BYTES..3 * PAGE_BYTES],
        [0; 4],
        &[&owned, &available],
    );

    let mut row = [0_u8; 33];
    row[0] = 2;
    row[1..5].copy_from_slice(&0x8000_0003_u32.to_le_bytes());
    row[13..16].copy_from_slice(b"A\x80B");
    row[16..20].copy_from_slice(&4_u32.to_le_bytes());
    row[20..24].copy_from_slice(&[0, 4, 0, 0]);
    row[28..].copy_from_slice(&[28, 16, 1, 2, 3]);
    write_rows(
        &mut bytes[3 * PAGE_BYTES..4 * PAGE_BYTES],
        1_u32.to_le_bytes(),
        &[&row],
    );

    let first = [0, 5, 0, 0, b'x', b'y'];
    write_rows(
        &mut bytes[4 * PAGE_BYTES..5 * PAGE_BYTES],
        *b"LVAL",
        &[&first],
    );
    let second = [0, 0, 0, 0, b'z', b'z'];
    write_rows(
        &mut bytes[5 * PAGE_BYTES..6 * PAGE_BYTES],
        *b"LVAL",
        &[&second],
    );
}

fn table_definition(page: &mut [u8]) {
    page[..4].copy_from_slice(&[0x02, 0x01, 0x56, 0x43]);
    page[8..12].copy_from_slice(&90_u32.to_le_bytes());
    page[20] = 0x4e;
    page[21..23].copy_from_slice(&2_u16.to_le_bytes());
    page[23..25].copy_from_slice(&2_u16.to_le_bytes());
    page[25..27].copy_from_slice(&2_u16.to_le_bytes());
    page[35..39].copy_from_slice(&[0, 2, 0, 0]);
    page[39..43].copy_from_slice(&[1, 2, 0, 0]);
    variable_column(&mut page[43..61], 12, 0);
    variable_column(&mut page[61..79], 11, 1);
    page[79..88].copy_from_slice(&[4, b'M', b'e', b'm', b'o', 3, b'O', b'l', b'e']);
    page[88..90].copy_from_slice(&[0xff, 0xff]);
}

fn variable_column(record: &mut [u8], physical_type: u8, ordinal: u16) {
    record[0] = physical_type;
    record[1..3].copy_from_slice(&ordinal.to_le_bytes());
    record[3..5].copy_from_slice(&ordinal.to_le_bytes());
    record[5..7].copy_from_slice(&ordinal.to_le_bytes());
    record[7..9].copy_from_slice(&1_u16.to_le_bytes());
    record[9..13].copy_from_slice(&[0x09, 0x04, 0xe4, 0x04]);
    record[13] = 2;
}

fn write_rows(page: &mut [u8], owner: [u8; 4], rows: &[&[u8]]) {
    page[0] = 1;
    page[4..8].copy_from_slice(&owner);
    page[8..10].copy_from_slice(&(rows.len() as u16).to_le_bytes());
    let mut start = PAGE_BYTES;
    for (index, row) in rows.iter().enumerate() {
        start -= row.len();
        page[10 + 2 * index..12 + 2 * index].copy_from_slice(&(start as u16).to_le_bytes());
        page[start..start + row.len()].copy_from_slice(row);
    }
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
