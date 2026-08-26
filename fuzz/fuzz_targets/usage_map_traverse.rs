#![no_main]

// Every database byte is project-authored synthetic input. Layout assertions
// are limited to SRC-0020 and the development-only EXP-0057 observations.

use jet3::{
    ByteCount, DatabaseReader, JET3_PAGE_SIZE, PageNumber, ReadLimits, ResourceBudget,
    ResourceLimits, SliceSource,
};
use libfuzzer_sys::fuzz_target;

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const PAGE_COUNT: usize = 9;
const DATABASE_BYTES: usize = PAGE_COUNT * PAGE_BYTES;
const RECORD_BYTES: usize = 133;
const MAX_RESULTS: usize = 65;

fuzz_target!(|data: &[u8]| {
    let mode = selector(data.first().copied()) % 2;
    let selected_work = u64::from(selector(data.get(1).copied())) * 128;
    let selected_visits = u64::from(selector(data.get(2).copied()) % 12);
    let selected_depth = u64::from(selector(data.get(3).copied()) % 8);
    let payload = data.get(4..).unwrap_or_default();

    let mut bytes = [0_u8; DATABASE_BYTES];
    supported_header(&mut bytes);
    table_definition(&mut bytes);
    usage_page(&mut bytes, mode, payload);
    extended_pages(&mut bytes, payload);

    let limits = ResourceLimits::new(ReadLimits::default())
        .with_max_item_work(selected_work)
        .with_max_total_work_units(selected_work + 64)
        .with_max_page_visits(selected_visits)
        .with_max_chain_depth(selected_depth)
        .with_max_allocation_bytes(ByteCount::new(2));
    let mut resources = ResourceBudget::new(limits);
    let Ok(source) = SliceSource::new(&bytes, resources.read_budget()) else {
        return;
    };
    let Ok(mut database) = DatabaseReader::from_source(source, &mut resources) else {
        return;
    };
    let Ok(mut pages) = database.owned_pages(PageNumber::new(1), &mut resources) else {
        return;
    };
    for _ in 0..MAX_RESULTS {
        match pages.next_page() {
            Ok(Some(page)) => assert!(page.get() < PAGE_COUNT as u64),
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

fn table_definition(bytes: &mut [u8; DATABASE_BYTES]) {
    let start = PAGE_BYTES;
    bytes[start] = 2;
    bytes[start + 35..start + 39].copy_from_slice(&[0, 2, 0, 0]);
    bytes[start + 39..start + 43].copy_from_slice(&[1, 2, 0, 0]);
}

fn usage_page(bytes: &mut [u8; DATABASE_BYTES], mode: u8, payload: &[u8]) {
    let start = 2 * PAGE_BYTES;
    bytes[start] = 1;
    bytes[start + 8..start + 10].copy_from_slice(&2_u16.to_le_bytes());
    let owned_start = PAGE_BYTES - RECORD_BYTES;
    let available_start = owned_start - 5;
    bytes[start + 10..start + 12].copy_from_slice(&(owned_start as u16).to_le_bytes());
    bytes[start + 12..start + 14].copy_from_slice(&(available_start as u16).to_le_bytes());
    bytes[start + available_start..start + owned_start].copy_from_slice(&[0, 0, 0, 0, 0]);
    let record = &mut bytes[start + owned_start..start + PAGE_BYTES];
    if mode == 0 {
        record[0] = 0;
        record[1..5].copy_from_slice(&u32::from(selector(payload.first().copied()) % 9).to_le_bytes());
        fill_repeating(&mut record[5..], payload.get(1..).unwrap_or_default());
    } else {
        record[0] = 1;
        for (slot, entry) in record[1..].chunks_exact_mut(4).enumerate() {
            let raw = selector(payload.get(slot).copied()) % 10;
            entry.copy_from_slice(&u32::from(raw).to_le_bytes());
        }
    }
}

fn extended_pages(bytes: &mut [u8; DATABASE_BYTES], payload: &[u8]) {
    for page in 3..PAGE_COUNT {
        let start = page * PAGE_BYTES;
        let selector = selector(payload.get(page).copied());
        bytes[start] = if selector % 3 == 0 { 1 } else { 5 };
        fill_repeating(
            &mut bytes[start + 4..start + PAGE_BYTES],
            payload.get(page + 1..).unwrap_or_default(),
        );
    }
}

fn selector(value: Option<u8>) -> u8 {
    let value = value.unwrap_or_default();
    if value.is_ascii_digit() {
        value - b'0'
    } else if value.is_ascii_whitespace() {
        0
    } else {
        value
    }
}

fn fill_repeating(destination: &mut [u8], source: &[u8]) {
    if source.is_empty() {
        return;
    }
    for (index, byte) in destination.iter_mut().enumerate() {
        *byte = source[index % source.len()];
    }
}
