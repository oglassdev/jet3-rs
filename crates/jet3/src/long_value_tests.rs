use super::{
    ExternalLongValueStorage, InlineLongValue, LongValue, LongValueChunkValue, LongValueCursor,
    LongValueError, LongValueKind,
};
use crate::{
    ByteCount, DatabaseReader, Error, JET3_PAGE_SIZE, PageNumber, ReadLimits, ResourceBudget,
    ResourceLimitKind, ResourceLimits, RowLocator, SliceSource, TextCodePage,
};
use std::error::Error as _;

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const ROOT: usize = 1;
const MAP_PAGE: usize = 2;
const FIRST_LVAL: usize = 3;
const SECOND_LVAL: usize = 4;

fn table_definition() -> [u8; 45] {
    let mut bytes = [0_u8; 45];
    bytes[..4].copy_from_slice(&[0x02, 0x01, 0x56, 0x43]);
    bytes[8..12].copy_from_slice(&45_u32.to_le_bytes());
    bytes[20] = 0x4e;
    bytes[35..39].copy_from_slice(&[0, MAP_PAGE as u8, 0, 0]);
    bytes[39..43].copy_from_slice(&[1, MAP_PAGE as u8, 0, 0]);
    bytes[43..45].copy_from_slice(&[0xff, 0xff]);
    bytes
}

fn write_rows(page: &mut [u8], owner: [u8; 4], rows: &[&[u8]]) {
    page[0] = 1;
    page[4..8].copy_from_slice(&owner);
    page[8..10].copy_from_slice(&u16::try_from(rows.len()).unwrap_or_default().to_le_bytes());
    let mut start = PAGE_BYTES;
    for (index, row) in rows.iter().enumerate() {
        start -= row.len();
        page[10 + 2 * index..12 + 2 * index]
            .copy_from_slice(&u16::try_from(start).unwrap_or_default().to_le_bytes());
        page[start..start + row.len()].copy_from_slice(row);
    }
}

fn database_bytes(first: &[u8], second: Option<&[u8]>) -> Vec<u8> {
    let page_count = if second.is_some() { 5 } else { 4 };
    let mut bytes = vec![0_u8; page_count * PAGE_BYTES];
    bytes[4..19].copy_from_slice(b"Standard Jet DB");
    bytes[0x41] = 0x4e;
    bytes[0x42..0x50].copy_from_slice(&[
        0x86, 0xfb, 0xec, 0x37, 0x5d, 0x44, 0x9c, 0xfa, 0xc6, 0x5e, 0x28, 0xe6, 0x13, 0xb6,
    ]);
    bytes[ROOT * PAGE_BYTES..ROOT * PAGE_BYTES + 45].copy_from_slice(&table_definition());
    let owned = [0_u8; 6];
    let available = [0_u8; 5];
    write_rows(
        &mut bytes[MAP_PAGE * PAGE_BYTES..(MAP_PAGE + 1) * PAGE_BYTES],
        [0; 4],
        &[&owned, &available],
    );
    write_rows(
        &mut bytes[FIRST_LVAL * PAGE_BYTES..(FIRST_LVAL + 1) * PAGE_BYTES],
        *b"LVAL",
        &[first],
    );
    if let Some(second) = second {
        write_rows(
            &mut bytes[SECOND_LVAL * PAGE_BYTES..(SECOND_LVAL + 1) * PAGE_BYTES],
            *b"LVAL",
            &[second],
        );
    }
    bytes
}

fn limits(bytes: &[u8]) -> ResourceLimits {
    ResourceLimits::new(ReadLimits::new(
        ByteCount::new(bytes.len() as u64),
        JET3_PAGE_SIZE,
        ByteCount::new(u64::MAX),
    ))
}

fn external_header(length: u32, flag: u32, page: usize) -> [u8; 12] {
    let mut raw = [0_u8; 12];
    raw[..4].copy_from_slice(&(length | flag).to_le_bytes());
    raw[4] = 0;
    raw[5..8].copy_from_slice(&(page as u32).to_le_bytes()[..3]);
    raw
}

fn decode_reference(
    raw: &[u8],
    kind: LongValueKind,
    code_page: TextCodePage,
    budget: &mut ResourceBudget,
) -> Result<super::LongValueReference, LongValueError> {
    let decoded = LongValue::decode(
        raw,
        RowLocator::new(PageNumber::new(9), 0),
        kind,
        code_page,
        budget,
    )?;
    match decoded {
        LongValue::External(reference) => Ok(reference),
        LongValue::Inline { .. } => Err(LongValueError::UnsupportedFlags { raw: u32::MAX }),
    }
}

fn first_chunk_error(
    bytes: &[u8],
    header: &[u8],
) -> Result<LongValueError, Box<dyn std::error::Error>> {
    let mut budget = ResourceBudget::new(limits(bytes));
    let reference = decode_reference(
        header,
        LongValueKind::Ole,
        TextCodePage::Windows1252,
        &mut budget,
    )?;
    let source = SliceSource::new(bytes, budget.read_budget())?;
    let mut database = DatabaseReader::from_source(source, &mut budget)?;
    let mut owned = database.owned_pages(PageNumber::new(ROOT as u64), &mut budget)?;
    let mut page = [0_u8; PAGE_BYTES];
    let mut cursor = LongValueCursor::new(&mut owned, &mut page, reference)?;
    match cursor.next_chunk() {
        Err(error) => Ok(error),
        Ok(_) => Err("expected first long-value chunk to fail".into()),
    }
}

#[test]
fn decodes_inline_text_and_binary_losslessly() -> Result<(), Box<dyn std::error::Error>> {
    let mut text_raw = [0_u8; 15];
    text_raw[..4].copy_from_slice(&(0x8000_0003_u32).to_le_bytes());
    text_raw[12..].copy_from_slice(b"A\x80B");
    let mut budget = ResourceBudget::new(ResourceLimits::default());
    let decoded = LongValue::decode(
        &text_raw,
        RowLocator::new(PageNumber::new(3), 0),
        LongValueKind::Memo,
        TextCodePage::Windows1252,
        &mut budget,
    )?;
    let LongValue::Inline { raw_header, value } = decoded else {
        return Err("expected inline value".into());
    };
    assert_eq!(raw_header, text_raw[..12]);
    let InlineLongValue::Text(text) = value else {
        return Err("expected inline text".into());
    };
    assert_eq!(text.raw_bytes(), b"A\x80B");
    assert_eq!(text.as_str(), "A€B");

    let mut binary_raw = [0_u8; 14];
    binary_raw[..4].copy_from_slice(&(0x8000_0002_u32).to_le_bytes());
    binary_raw[12..].copy_from_slice(&[0, 0xff]);
    let decoded = LongValue::decode(
        &binary_raw,
        RowLocator::new(PageNumber::new(3), 0),
        LongValueKind::Ole,
        TextCodePage::Windows1252,
        &mut budget,
    )?;
    assert!(matches!(
        decoded,
        LongValue::Inline {
            value: InlineLongValue::Binary(&[0, 0xff]),
            ..
        }
    ));
    Ok(())
}

#[test]
fn streams_single_and_chained_external_values() -> Result<(), Box<dyn std::error::Error>> {
    let bytes = database_bytes(b"abc", None);
    let mut budget = ResourceBudget::new(limits(&bytes));
    let single_header = external_header(3, 0x4000_0000, FIRST_LVAL);
    let reference = decode_reference(
        &single_header,
        LongValueKind::Ole,
        TextCodePage::Windows1252,
        &mut budget,
    )?;
    assert_eq!(reference.raw_header(), single_header);
    assert_eq!(reference.source(), RowLocator::new(PageNumber::new(9), 0));
    assert_eq!(
        reference.target(),
        RowLocator::new(PageNumber::new(FIRST_LVAL as u64), 0)
    );
    assert_eq!(reference.length(), 3);
    assert_eq!(reference.storage(), ExternalLongValueStorage::SinglePage);
    assert_eq!(reference.kind(), LongValueKind::Ole);
    assert_eq!(reference.code_page(), TextCodePage::Windows1252);
    let source = SliceSource::new(&bytes, budget.read_budget())?;
    let mut database = DatabaseReader::from_source(source, &mut budget)?;
    let mut owned = database.owned_pages(PageNumber::new(ROOT as u64), &mut budget)?;
    let mut page = [0_u8; PAGE_BYTES];
    let mut cursor = LongValueCursor::new(&mut owned, &mut page, reference)?;
    let chunk = cursor.next_chunk()?.ok_or("missing single chunk")?;
    assert_eq!(chunk.raw_row(), b"abc");
    assert_eq!(chunk.value(), &LongValueChunkValue::Binary(b"abc"));
    assert!(cursor.next_chunk()?.is_none());

    let first = [0, SECOND_LVAL as u8, 0, 0, b'a', b'b'];
    let second = [0, 0, 0, 0, b'c', b'd'];
    let bytes = database_bytes(&first, Some(&second));
    let mut budget = ResourceBudget::new(limits(&bytes));
    let chained_header = external_header(4, 0, FIRST_LVAL);
    let reference = decode_reference(
        &chained_header,
        LongValueKind::Ole,
        TextCodePage::Windows1252,
        &mut budget,
    )?;
    assert_eq!(reference.storage(), ExternalLongValueStorage::Chained);
    let source = SliceSource::new(&bytes, budget.read_budget())?;
    let mut database = DatabaseReader::from_source(source, &mut budget)?;
    let mut owned = database.owned_pages(PageNumber::new(ROOT as u64), &mut budget)?;
    let mut page = [0_u8; PAGE_BYTES];
    let mut cursor = LongValueCursor::new(&mut owned, &mut page, reference)?;
    assert_eq!(
        cursor.next_chunk()?.ok_or("missing first chunk")?.value(),
        &LongValueChunkValue::Binary(b"ab")
    );
    assert_eq!(
        cursor.next_chunk()?.ok_or("missing second chunk")?.value(),
        &LongValueChunkValue::Binary(b"cd")
    );
    assert!(cursor.next_chunk()?.is_none());
    Ok(())
}

#[test]
fn row_cursor_composes_external_long_value_streams() -> Result<(), Box<dyn std::error::Error>> {
    let bytes = database_bytes(b"abc", None);
    let mut budget = ResourceBudget::new(limits(&bytes));
    let header = external_header(3, 0x4000_0000, FIRST_LVAL);
    let reference = decode_reference(
        &header,
        LongValueKind::Ole,
        TextCodePage::Windows1252,
        &mut budget,
    )?;
    let source = SliceSource::new(&bytes, budget.read_budget())?;
    let mut database = DatabaseReader::from_source(source, &mut budget)?;
    let definition = database.table_definition(PageNumber::new(ROOT as u64), &mut budget)?;
    let mut rows = database.rows(&definition, &mut budget)?;
    let mut cursor = rows.long_value(reference)?;
    assert_eq!(
        cursor.next_chunk()?.ok_or("missing chunk")?.value(),
        &LongValueChunkValue::Binary(b"abc")
    );
    Ok(())
}

#[test]
fn rejects_malformed_headers_lengths_cycles_and_owner() -> Result<(), Box<dyn std::error::Error>> {
    let source = RowLocator::new(PageNumber::new(9), 0);
    let mut budget = ResourceBudget::new(ResourceLimits::default());
    assert!(matches!(
        LongValue::decode(
            &[0; 11],
            source,
            LongValueKind::Ole,
            TextCodePage::Windows1252,
            &mut budget,
        ),
        Err(LongValueError::HeaderTooShort { actual: 11 })
    ));
    let mut wrong_external_length = external_header(1, 0x4000_0000, FIRST_LVAL).to_vec();
    wrong_external_length.push(0);
    assert!(matches!(
        LongValue::decode(
            &wrong_external_length,
            source,
            LongValueKind::Ole,
            TextCodePage::Windows1252,
            &mut budget,
        ),
        Err(LongValueError::ExternalHeaderLength { actual: 13 })
    ));
    let unsupported = external_header(1, 0x2000_0000, FIRST_LVAL);
    assert!(matches!(
        LongValue::decode(
            &unsupported,
            source,
            LongValueKind::Ole,
            TextCodePage::Windows1252,
            &mut budget,
        ),
        Err(LongValueError::UnsupportedFlags { .. })
    ));
    let missing = external_header(1, 0x4000_0000, 0);
    assert!(matches!(
        LongValue::decode(
            &missing,
            source,
            LongValueKind::Ole,
            TextCodePage::Windows1252,
            &mut budget,
        ),
        Err(LongValueError::MissingExternalTarget)
    ));
    let mut external_reserved = external_header(1, 0x4000_0000, FIRST_LVAL);
    external_reserved[8] = 1;
    assert!(matches!(
        LongValue::decode(
            &external_reserved,
            source,
            LongValueKind::Ole,
            TextCodePage::Windows1252,
            &mut budget,
        ),
        Err(LongValueError::NonzeroReservedHeader)
    ));
    let mut malformed = [0_u8; 13];
    malformed[..4].copy_from_slice(&0x8000_0000_u32.to_le_bytes());
    assert!(matches!(
        LongValue::decode(
            &malformed,
            source,
            LongValueKind::Ole,
            TextCodePage::Windows1252,
            &mut budget,
        ),
        Err(LongValueError::LengthMismatch { .. })
    ));
    let mut reserved = [0_u8; 12];
    reserved[..4].copy_from_slice(&0x8000_0000_u32.to_le_bytes());
    reserved[8] = 1;
    assert!(matches!(
        LongValue::decode(
            &reserved,
            source,
            LongValueKind::Ole,
            TextCodePage::Windows1252,
            &mut budget,
        ),
        Err(LongValueError::NonzeroReservedHeader)
    ));

    let first = [0, FIRST_LVAL as u8, 0, 0, b'a'];
    let bytes = database_bytes(&first, None);
    let mut budget = ResourceBudget::new(limits(&bytes));
    let header = external_header(2, 0, FIRST_LVAL);
    let reference = decode_reference(
        &header,
        LongValueKind::Ole,
        TextCodePage::Windows1252,
        &mut budget,
    )?;
    let source = SliceSource::new(&bytes, budget.read_budget())?;
    let mut database = DatabaseReader::from_source(source, &mut budget)?;
    let mut owned = database.owned_pages(PageNumber::new(ROOT as u64), &mut budget)?;
    let mut page = [0_u8; PAGE_BYTES];
    let mut cursor = LongValueCursor::new(&mut owned, &mut page, reference)?;
    let _ = cursor.next_chunk()?.ok_or("missing cycle prefix")?;
    assert!(matches!(
        cursor.next_chunk(),
        Err(LongValueError::Cycle { .. })
    ));
    assert!(cursor.next_chunk()?.is_none());

    let mut bytes = database_bytes(b"ab", None);
    bytes[FIRST_LVAL * PAGE_BYTES + 4..FIRST_LVAL * PAGE_BYTES + 8].copy_from_slice(b"NOPE");
    let mut budget = ResourceBudget::new(limits(&bytes));
    let header = external_header(2, 0x4000_0000, FIRST_LVAL);
    let reference = decode_reference(
        &header,
        LongValueKind::Ole,
        TextCodePage::Windows1252,
        &mut budget,
    )?;
    let source = SliceSource::new(&bytes, budget.read_budget())?;
    let mut database = DatabaseReader::from_source(source, &mut budget)?;
    let mut owned = database.owned_pages(PageNumber::new(ROOT as u64), &mut budget)?;
    let mut page = [0_u8; PAGE_BYTES];
    let mut cursor = LongValueCursor::new(&mut owned, &mut page, reference)?;
    assert!(matches!(
        cursor.next_chunk(),
        Err(LongValueError::InvalidOwner { .. })
    ));
    Ok(())
}

#[test]
fn rejects_external_page_directory_and_length_corruption() -> Result<(), Box<dyn std::error::Error>>
{
    let bytes = database_bytes(b"abc", None);
    assert!(matches!(
        first_chunk_error(&bytes, &external_header(2, 0x4000_0000, FIRST_LVAL))?,
        LongValueError::LengthMismatch { .. }
    ));
    assert!(matches!(
        first_chunk_error(&bytes, &external_header(1, 0x4000_0000, 9))?,
        LongValueError::SelfLink { .. }
    ));

    let mut wrong_kind = bytes.clone();
    wrong_kind[FIRST_LVAL * PAGE_BYTES] = 2;
    assert!(matches!(
        first_chunk_error(&wrong_kind, &external_header(3, 0x4000_0000, FIRST_LVAL),)?,
        LongValueError::UnexpectedPageKind { .. }
    ));

    let mut flags = bytes.clone();
    let raw = u16::from_le_bytes([
        flags[FIRST_LVAL * PAGE_BYTES + 10],
        flags[FIRST_LVAL * PAGE_BYTES + 11],
    ]) | 0x8000;
    flags[FIRST_LVAL * PAGE_BYTES + 10..FIRST_LVAL * PAGE_BYTES + 12]
        .copy_from_slice(&raw.to_le_bytes());
    assert!(matches!(
        first_chunk_error(&flags, &external_header(3, 0x4000_0000, FIRST_LVAL))?,
        LongValueError::InvalidRowFlags { .. }
    ));

    let mut directory = bytes.clone();
    directory[FIRST_LVAL * PAGE_BYTES + 8..FIRST_LVAL * PAGE_BYTES + 10]
        .copy_from_slice(&1020_u16.to_le_bytes());
    assert!(matches!(
        first_chunk_error(&directory, &external_header(3, 0x4000_0000, FIRST_LVAL),)?,
        LongValueError::InvalidDirectory { .. }
    ));

    let mut missing = external_header(3, 0x4000_0000, FIRST_LVAL);
    missing[4] = 1;
    assert!(matches!(
        first_chunk_error(&bytes, &missing)?,
        LongValueError::MissingRow { .. }
    ));

    assert!(matches!(
        first_chunk_error(&bytes, &external_header(0, 0, FIRST_LVAL))?,
        LongValueError::ChainRowTooShort { .. }
    ));

    let first = [0, SECOND_LVAL as u8, 0, 0, b'a', b'b'];
    let second = [0, 0, 0, 0];
    let chained = database_bytes(&first, Some(&second));
    assert!(matches!(
        first_chunk_error(&chained, &external_header(2, 0, FIRST_LVAL))?,
        LongValueError::NonterminalAtLength { .. }
    ));
    Ok(())
}

#[test]
fn enforces_cumulative_decoded_text_and_chain_depth_limits()
-> Result<(), Box<dyn std::error::Error>> {
    let first = [0, SECOND_LVAL as u8, 0, 0, 0x80];
    let second = [0, 0, 0, 0, 0x80];
    let bytes = database_bytes(&first, Some(&second));
    let text_limits = limits(&bytes).with_max_decoded_value_bytes(ByteCount::new(4));
    let mut budget = ResourceBudget::new(text_limits);
    let header = external_header(2, 0, FIRST_LVAL);
    let reference = decode_reference(
        &header,
        LongValueKind::Memo,
        TextCodePage::Windows1252,
        &mut budget,
    )?;
    let source = SliceSource::new(&bytes, budget.read_budget())?;
    let mut database = DatabaseReader::from_source(source, &mut budget)?;
    let mut owned = database.owned_pages(PageNumber::new(ROOT as u64), &mut budget)?;
    let mut page = [0_u8; PAGE_BYTES];
    let mut cursor = LongValueCursor::new(&mut owned, &mut page, reference)?;
    let first_chunk = cursor.next_chunk()?.ok_or("missing text chunk")?;
    let LongValueChunkValue::Text(text) = first_chunk.value() else {
        return Err("expected text chunk".into());
    };
    assert_eq!(text.as_str(), "€");
    assert!(matches!(
        cursor.next_chunk(),
        Err(LongValueError::Resource(
            crate::Error::ResourceLimitExceeded {
                kind: ResourceLimitKind::DecodedValueBytes,
                ..
            }
        ))
    ));

    let bytes = database_bytes(&first, Some(&second));
    let mut budget = ResourceBudget::new(limits(&bytes).with_max_chain_depth(1));
    let reference = decode_reference(
        &header,
        LongValueKind::Ole,
        TextCodePage::Windows1252,
        &mut budget,
    )?;
    let source = SliceSource::new(&bytes, budget.read_budget())?;
    let mut database = DatabaseReader::from_source(source, &mut budget)?;
    let mut owned = database.owned_pages(PageNumber::new(ROOT as u64), &mut budget)?;
    let mut page = [0_u8; PAGE_BYTES];
    let mut cursor = LongValueCursor::new(&mut owned, &mut page, reference)?;
    assert!(cursor.next_chunk()?.is_some());
    assert!(matches!(
        cursor.next_chunk(),
        Err(LongValueError::Resource(
            crate::Error::ResourceLimitExceeded {
                kind: ResourceLimitKind::ChainDepth,
                ..
            }
        ))
    ));
    Ok(())
}

#[test]
fn long_value_errors_expose_display_and_nested_sources() {
    let plain = LongValueError::HeaderTooShort { actual: 1 };
    assert!(plain.to_string().contains("long value failed"));
    assert!(plain.source().is_none());

    let resource = LongValueError::Resource(Error::Arithmetic {
        operation: "test long value source",
    });
    assert!(resource.source().is_some());
    let text = LongValueError::Text(crate::TextError::UndefinedByte {
        code_page: TextCodePage::Windows1251,
        index: 0,
        byte: 0x98,
    });
    assert!(text.source().is_some());
}
