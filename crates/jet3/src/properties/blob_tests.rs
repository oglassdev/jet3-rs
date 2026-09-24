use super::blob::{Block, FIELD_BLOCK, PropertyBlob, Record, TABLE_BLOCK, TEXT};
use crate::{ColumnPropertyError, ResourceBudget, ResourceLimits};

use crate::testkit::budget;

fn hex(text: &str) -> Vec<u8> {
    (0..text.len())
        .step_by(2)
        .filter_map(|at| u8::from_str_radix(&text[at..at + 2], 16).ok())
        .collect()
}

// EXP-0299 d-table-and-fields table U: a field block then a kind-0 table block with an empty name.
const NATIVE_TABLE_RULE: &str = "4b4b4400200000008000080052657175697265640e0056616c69646174696f6e52756c651600000001000700000001005a0900010100000100001a00000000000600000000000e00010c010006005b5a5d3c3e33";

#[test]
fn native_table_block_round_trips_and_reads_its_rule() -> Result<(), Box<dyn std::error::Error>> {
    let bytes = hex(NATIVE_TABLE_RULE);
    let blob = PropertyBlob::parse(&bytes, &mut budget())?;
    assert_eq!(blob.encode(&mut budget())?, bytes);
    let table = &blob.blocks()[1];
    assert_eq!((table.kind(), table.name()), (TABLE_BLOCK, b"".as_slice()));
    assert_eq!(table.records()[0].value(), Some(b"[Z]<>3".as_slice()));
    let options = crate::properties::reader::options(&blob, &[], &mut budget())?;
    assert!(options.validation_rule);
    Ok(())
}

#[test]
fn unknown_kinds_flags_types_and_trailing_bytes_are_retained()
-> Result<(), Box<dyn std::error::Error>> {
    let mut blob = PropertyBlob::empty();
    let name = blob.intern(b"Odd", &mut budget())?;
    blob.intern(b"Unused", &mut budget())?;
    blob.push(Block::new(7, b"Other", &mut budget())?, &mut budget())?;
    let block = blob.block_at(0)?;
    block.set(
        Record::new(9, 99, name, b"v", &mut budget())?,
        &mut budget(),
    )?;
    let mut bytes = blob.encode(&mut budget())?;
    // A record whose declared value length leaves trailing bytes stays opaque.
    let last = bytes.len();
    bytes.extend_from_slice(&[0xaa, 0xbb]);
    let block_start = 4 + u32::from_le_bytes(bytes[4..8].try_into()?) as usize;
    let block_len = u32::from_le_bytes(bytes[block_start..block_start + 4].try_into()?) + 2;
    bytes[block_start..block_start + 4].copy_from_slice(&block_len.to_le_bytes());
    let record_len = u16::from_le_bytes(bytes[last - 9..last - 7].try_into()?) + 2;
    bytes[last - 9..last - 7].copy_from_slice(&record_len.to_le_bytes());
    let parsed = PropertyBlob::parse(&bytes, &mut budget())?;
    assert_eq!(parsed.encode(&mut budget())?, bytes);
    assert_eq!(parsed.blocks()[0].records()[0].value(), None);
    assert_eq!(parsed.names().len(), 2);
    Ok(())
}

#[test]
fn edits_replace_in_place_append_and_remove() -> Result<(), Box<dyn std::error::Error>> {
    let mut blob = PropertyBlob::empty();
    let first = blob.intern(b"A", &mut budget())?;
    let second = blob.intern(b"B", &mut budget())?;
    assert_eq!(blob.intern(b"A", &mut budget())?, first);
    blob.push(Block::new(FIELD_BLOCK, b"X", &mut budget())?, &mut budget())?;
    let block = blob.block_at(0)?;
    block.set(
        Record::new(1, TEXT, first, b"1", &mut budget())?,
        &mut budget(),
    )?;
    block.set(
        Record::new(1, TEXT, second, b"2", &mut budget())?,
        &mut budget(),
    )?;
    block.set(
        Record::new(1, TEXT, first, b"33", &mut budget())?,
        &mut budget(),
    )?;
    let values: Vec<_> = block.records().iter().map(Record::value).collect();
    assert_eq!(values, [Some(b"33".as_slice()), Some(b"2".as_slice())]);
    block.remove(first);
    block.rename(b"Y", &mut budget())?;
    let bytes = blob.encode(&mut budget())?;
    assert_eq!(bytes.len(), blob.len());
    let parsed = PropertyBlob::parse(&bytes, &mut budget())?;
    assert_eq!(parsed, blob);
    assert_eq!(parsed.blocks()[0].name(), b"Y");
    Ok(())
}

#[test]
fn malformed_framing_is_rejected() -> Result<(), Box<dyn std::error::Error>> {
    let valid = hex(NATIVE_TABLE_RULE);
    let mut cases = Vec::new();
    let mut signature = valid.clone();
    signature[0] = b'X';
    cases.push((signature, "property signature"));
    let mut dictionary = valid.clone();
    dictionary[4] = 0xff;
    cases.push((dictionary, "property block bounds"));
    let mut kind = valid.clone();
    kind[8] = 0x81;
    cases.push((kind, "property dictionary kind"));
    let mut block = valid.clone();
    block[36] = 0xff;
    cases.push((block, "property block bounds"));
    let mut frame = valid.clone();
    frame[42] = 8;
    cases.push((frame, "property field-name framing"));
    let mut reference = valid.clone();
    reference[53] = 9;
    cases.push((reference, "property dictionary reference"));
    let mut record = valid.clone();
    record[49] = 7;
    cases.push((record, "property record bounds"));
    cases.push((valid[..valid.len() - 1].to_vec(), "property block bounds"));
    for (bytes, detail) in cases {
        assert_eq!(
            PropertyBlob::parse(&bytes, &mut budget()),
            Err(ColumnPropertyError::Invalid(detail))
        );
    }
    let limited = ResourceLimits::default().with_max_total_work_units(1);
    assert!(matches!(
        PropertyBlob::parse(&valid, &mut ResourceBudget::new(limited)),
        Err(ColumnPropertyError::Resource(_))
    ));
    Ok(())
}
