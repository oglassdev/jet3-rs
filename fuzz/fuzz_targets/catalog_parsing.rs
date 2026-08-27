#![no_main]

// Every database byte is project-authored synthetic input. Physical assertions
// are limited to SRC-0020, EXP-0057, and development-only EXP-0058.

use std::hint::black_box;

use jet3::{
    ByteCount, DatabaseReader, JET3_PAGE_SIZE, ReadLimits, ResourceBudget, ResourceLimits,
    SliceSource,
};
use libfuzzer_sys::fuzz_target;

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const PAGE_COUNT: usize = 5;
const DATABASE_BYTES: usize = PAGE_COUNT * PAGE_BYTES;
const CONTROL_BYTES: usize = 5;
const MAX_RECORDS: usize = 32;

fuzz_target!(|data: &[u8]| {
    let mut bytes = [0_u8; DATABASE_BYTES];
    supported_header(&mut bytes);
    table_definition(&mut bytes, 1, 0);
    table_definition(&mut bytes, 4, 2);
    usage_page(&mut bytes);
    catalog_page(&mut bytes);
    let payload = data.get(CONTROL_BYTES..).unwrap_or_default();
    if !payload.starts_with(b"valid-catalog") && !payload.starts_with(b"tight-resources") {
        mutate_selected_region(
            &mut bytes,
            selector(data.get(4).copied()) % 4,
            payload,
        );
    }

    let limits = ResourceLimits::new(ReadLimits::default())
        .with_max_item_work(selected_limit(data.first().copied(), 256))
        .with_max_total_work_units(selected_limit(data.get(1).copied(), 65_536))
        .with_max_page_visits(selected_limit(data.get(2).copied(), 64))
        .with_max_allocation_bytes(ByteCount::new(selected_limit(
            data.get(3).copied(),
            4_096,
        )));
    let mut budget = ResourceBudget::new(limits);
    let Ok(source) = SliceSource::new(&bytes, budget.read_budget()) else {
        return;
    };
    let Ok(mut database) = DatabaseReader::from_source(source, &mut budget) else {
        return;
    };
    let Ok(mut catalog) = database.catalog(&mut budget) else {
        return;
    };
    for _ in 0..MAX_RECORDS {
        match catalog.next_record() {
            Ok(Some(record)) => {
                black_box(record.id());
                black_box(record.kind().raw());
                black_box(record.name().raw_bytes());
            }
            Ok(None) | Err(_) => break,
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

fn table_definition(bytes: &mut [u8; DATABASE_BYTES], page: usize, owned_row: u8) {
    let start = page * PAGE_BYTES;
    bytes[start] = 2;
    bytes[start + 35..start + 39].copy_from_slice(&[owned_row, 2, 0, 0]);
    bytes[start + 39..start + 43].copy_from_slice(&[3, 2, 0, 0]);
}

fn usage_page(bytes: &mut [u8; DATABASE_BYTES]) {
    let rows = [
        [0, 0, 0, 0, 0, 1 << 3],
        [0, 0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0, 0],
    ];
    let start = 2 * PAGE_BYTES;
    bytes[start] = 1;
    bytes[start + 8..start + 10].copy_from_slice(&4_u16.to_le_bytes());
    let mut row_start = PAGE_BYTES;
    for (index, row) in rows.iter().enumerate() {
        row_start -= row.len();
        bytes[start + 10 + 2 * index..start + 12 + 2 * index]
            .copy_from_slice(&(row_start as u16).to_le_bytes());
        bytes[start + row_start..start + row_start + row.len()].copy_from_slice(row);
    }
}

fn catalog_page(bytes: &mut [u8; DATABASE_BYTES]) {
    let rows = [
        catalog_record(1, 1, 0x8000_0000, b"MSysObjects"),
        catalog_record(4, 1, 0, b"Caf\xe9_Euro\x80"),
    ];
    let start = 3 * PAGE_BYTES;
    bytes[start] = 1;
    bytes[start + 8..start + 10].copy_from_slice(&2_u16.to_le_bytes());
    let mut row_start = PAGE_BYTES;
    for (index, row) in rows.iter().enumerate() {
        row_start -= row.len();
        bytes[start + 10 + 2 * index..start + 12 + 2 * index]
            .copy_from_slice(&(row_start as u16).to_le_bytes());
        bytes[start + row_start..start + row_start + row.len()].copy_from_slice(row);
    }
}

fn catalog_record(id: u32, kind: u16, flags: u32, name: &[u8]) -> Vec<u8> {
    let mut row = vec![0_u8; 31 + name.len() + 6];
    row[0] = 17;
    row[1..5].copy_from_slice(&id.to_le_bytes());
    row[9..11].copy_from_slice(&kind.to_le_bytes());
    row[27..31].copy_from_slice(&flags.to_le_bytes());
    row[31..31 + name.len()].copy_from_slice(name);
    let length = row.len();
    row[length - 6] = (31 + name.len()) as u8;
    row[length - 5] = 31;
    row[length - 4] = 11;
    row[length - 3] = 0xff;
    row
}

fn mutate_selected_region(bytes: &mut [u8; DATABASE_BYTES], mode: u8, payload: &[u8]) {
    if payload.is_empty() {
        return;
    }
    let (start, maximum) = match mode {
        0 => (3 * PAGE_BYTES + 8, PAGE_BYTES - 8),
        1 => (PAGE_BYTES + 35, PAGE_BYTES - 35),
        2 => (2 * PAGE_BYTES + 8, PAGE_BYTES - 8),
        _ => (3 * PAGE_BYTES + 27, PAGE_BYTES - 27),
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
