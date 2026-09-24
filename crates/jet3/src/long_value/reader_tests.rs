use super::reader::{
    ExternalLongValueStorage, InlineLongValue, LongValue, LongValueChunk, LongValueChunkValue,
    LongValueCursor, LongValueError, LongValueKind, LongValueReference,
};
use crate::testkit::TestResult;
use crate::{
    ByteCount, DatabaseReader, Error, JET3_PAGE_SIZE, PAGE_BYTES, PageNumber, ReadLimits,
    ResourceBudget, ResourceLimitKind, ResourceLimits, RowLocator, SliceSource, TextCodePage,
};

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
    let mut bytes = crate::testkit::database_image(page_count);
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
    budget: &mut ResourceBudget,
) -> TestResult<LongValueReference> {
    match decode(raw, kind, budget)? {
        LongValue::External(reference) => Ok(reference),
        LongValue::Inline { .. } => Err("expected an external reference".into()),
    }
}

fn decode<'a>(
    raw: &'a [u8],
    kind: LongValueKind,
    budget: &mut ResourceBudget,
) -> Result<LongValue<'a>, LongValueError> {
    LongValue::decode(
        raw,
        RowLocator::new(PageNumber::new(9), 0),
        kind,
        TextCodePage::Windows1252,
        budget,
    )
}

fn with_cursor(
    bytes: &[u8],
    header: &[u8],
    kind: LongValueKind,
    policy: ResourceLimits,
    check: impl FnOnce(&mut LongValueCursor<'_, '_, SliceSource<'_>>) -> TestResult,
) -> TestResult {
    let mut budget = ResourceBudget::new(policy);
    let reference = decode_reference(header, kind, &mut budget)?;
    let source = SliceSource::new(bytes, budget.read_budget())?;
    let mut database = DatabaseReader::from_source(source, &mut budget)?;
    let mut owned = database.owned_pages(PageNumber::new(ROOT as u64), &mut budget)?;
    let mut page = [0_u8; PAGE_BYTES];
    let mut cursor = LongValueCursor::new(&mut owned, &mut page, reference)?;
    check(&mut cursor)
}

fn first_chunk_error(bytes: &[u8], header: &[u8]) -> TestResult<LongValueError> {
    let mut error = None;
    with_cursor(bytes, header, LongValueKind::Ole, limits(bytes), |cursor| {
        error = cursor.next_chunk().err();
        Ok(())
    })?;
    error.ok_or_else(|| "expected first long-value chunk to fail".into())
}

type Expected = fn(&LongValueError) -> bool;

fn binary(chunk: Option<LongValueChunk<'_>>) -> TestResult<Vec<u8>> {
    match chunk.ok_or("missing chunk")?.value() {
        LongValueChunkValue::Binary(bytes) => Ok(bytes.to_vec()),
        LongValueChunkValue::Text(_) => Err("expected binary chunk".into()),
    }
}

#[test]
fn decodes_inline_text_and_binary_losslessly() -> TestResult {
    let mut text_raw = [0_u8; 15];
    text_raw[..4].copy_from_slice(&(0x8000_0003_u32).to_le_bytes());
    text_raw[12..].copy_from_slice(b"A\x80B");
    let mut budget = ResourceBudget::new(ResourceLimits::default());
    let LongValue::Inline { raw_header, value } =
        decode(&text_raw, LongValueKind::Memo, &mut budget)?
    else {
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
    assert!(matches!(
        decode(&binary_raw, LongValueKind::Ole, &mut budget)?,
        LongValue::Inline {
            value: InlineLongValue::Binary(&[0, 0xff]),
            ..
        }
    ));
    Ok(())
}

#[test]
fn streams_single_and_chained_external_values() -> TestResult {
    let single_header = external_header(3, 0x4000_0000, FIRST_LVAL);
    let reference = decode_reference(
        &single_header,
        LongValueKind::Ole,
        &mut ResourceBudget::new(ResourceLimits::default()),
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

    // An unlimited chain policy still streams a short payload with bounded state.
    let bytes = database_bytes(b"abc", None);
    let policy = limits(&bytes)
        .with_max_chain_depth(u64::MAX)
        .with_max_allocation_bytes(ByteCount::new(4096));
    with_cursor(
        &bytes,
        &single_header,
        LongValueKind::Ole,
        policy,
        |cursor| {
            let chunk = cursor.next_chunk()?.ok_or("missing single chunk")?;
            assert_eq!(chunk.raw_row(), b"abc");
            assert_eq!(chunk.value(), &LongValueChunkValue::Binary(b"abc"));
            assert!(cursor.next_chunk()?.is_none());
            Ok(())
        },
    )?;

    let first = [0, SECOND_LVAL as u8, 0, 0, b'a', b'b'];
    let second = [0, 0, 0, 0, b'c', b'd'];
    let bytes = database_bytes(&first, Some(&second));
    let chained_header = external_header(4, 0, FIRST_LVAL);
    with_cursor(
        &bytes,
        &chained_header,
        LongValueKind::Ole,
        limits(&bytes),
        |cursor| {
            assert_eq!(binary(cursor.next_chunk()?)?, b"ab");
            assert_eq!(binary(cursor.next_chunk()?)?, b"cd");
            assert!(cursor.next_chunk()?.is_none());
            Ok(())
        },
    )
}

#[test]
fn row_cursor_composes_external_long_value_streams() -> TestResult {
    let bytes = database_bytes(b"abc", None);
    let mut budget = ResourceBudget::new(limits(&bytes));
    let header = external_header(3, 0x4000_0000, FIRST_LVAL);
    let reference = decode_reference(&header, LongValueKind::Ole, &mut budget)?;
    let source = SliceSource::new(&bytes, budget.read_budget())?;
    let mut database = DatabaseReader::from_source(source, &mut budget)?;
    let definition = database.table_definition(PageNumber::new(ROOT as u64), &mut budget)?;
    let mut rows = database.rows(&definition, &mut budget)?;
    let mut cursor = rows.long_value(reference)?;
    assert_eq!(binary(cursor.next_chunk()?)?, b"abc");
    Ok(())
}

#[test]
fn rejects_malformed_headers() {
    let mut external_trailing = external_header(1, 0x4000_0000, FIRST_LVAL).to_vec();
    external_trailing.push(0);
    let mut external_reserved = external_header(1, 0x4000_0000, FIRST_LVAL);
    external_reserved[8] = 1;
    let mut inline_long = [0_u8; 13];
    inline_long[..4].copy_from_slice(&0x8000_0000_u32.to_le_bytes());
    let mut inline_reserved = [0_u8; 12];
    inline_reserved[..4].copy_from_slice(&0x8000_0000_u32.to_le_bytes());
    inline_reserved[8] = 1;
    let cases: [(&str, &[u8], Expected); 7] = [
        ("short", &[0; 11], |error| {
            matches!(error, LongValueError::HeaderTooShort { actual: 11 })
        }),
        ("external trailing", &external_trailing, |error| {
            matches!(error, LongValueError::ExternalHeaderLength { actual: 13 })
        }),
        (
            "unsupported flag",
            &external_header(1, 0x2000_0000, FIRST_LVAL),
            |error| matches!(error, LongValueError::UnsupportedFlags { .. }),
        ),
        (
            "null target",
            &external_header(1, 0x4000_0000, 0),
            |error| matches!(error, LongValueError::MissingExternalTarget),
        ),
        ("external reserved", &external_reserved, |error| {
            matches!(error, LongValueError::NonzeroReservedHeader)
        }),
        ("inline length", &inline_long, |error| {
            matches!(error, LongValueError::LengthMismatch { .. })
        }),
        ("inline reserved", &inline_reserved, |error| {
            matches!(error, LongValueError::NonzeroReservedHeader)
        }),
    ];
    let mut budget = ResourceBudget::new(ResourceLimits::default());
    for (label, raw, expected) in cases {
        let error = decode(raw, LongValueKind::Ole, &mut budget).err();
        assert!(error.as_ref().is_some_and(expected), "{label}: {error:?}");
    }
}

#[test]
fn rejects_external_page_directory_length_cycle_and_owner_corruption() -> TestResult {
    let bytes = database_bytes(b"abc", None);
    let single = |length| external_header(length, 0x4000_0000, FIRST_LVAL);
    let lval = FIRST_LVAL * PAGE_BYTES;

    let mut wrong_kind = bytes.clone();
    wrong_kind[lval] = 2;
    let mut flags = bytes.clone();
    let raw = u16::from_le_bytes([flags[lval + 10], flags[lval + 11]]) | 0x8000;
    flags[lval + 10..lval + 12].copy_from_slice(&raw.to_le_bytes());
    let mut directory = bytes.clone();
    directory[lval + 8..lval + 10].copy_from_slice(&1020_u16.to_le_bytes());
    let mut owner = database_bytes(b"ab", None);
    owner[lval + 4..lval + 8].copy_from_slice(b"NOPE");
    let mut missing = single(3);
    missing[4] = 1;
    let chained = database_bytes(
        &[0, SECOND_LVAL as u8, 0, 0, b'a', b'b'],
        Some(&[0, 0, 0, 0]),
    );

    let cases: [(&str, &[u8], [u8; 12], Expected); 9] = [
        ("length", &bytes, single(2), |error| {
            matches!(error, LongValueError::LengthMismatch { .. })
        }),
        (
            "self link",
            &bytes,
            external_header(1, 0x4000_0000, 9),
            |error| matches!(error, LongValueError::SelfLink { .. }),
        ),
        ("page kind", &wrong_kind, single(3), |error| {
            matches!(error, LongValueError::UnexpectedPageKind { .. })
        }),
        ("row flags", &flags, single(3), |error| {
            matches!(error, LongValueError::InvalidRowFlags { .. })
        }),
        ("directory", &directory, single(3), |error| {
            matches!(error, LongValueError::InvalidDirectory { .. })
        }),
        ("owner", &owner, single(2), |error| {
            matches!(error, LongValueError::InvalidOwner { .. })
        }),
        ("missing row", &bytes, missing, |error| {
            matches!(error, LongValueError::MissingRow { .. })
        }),
        (
            "short chain row",
            &bytes,
            external_header(0, 0, FIRST_LVAL),
            |error| matches!(error, LongValueError::ChainRowTooShort { .. }),
        ),
        (
            "nonterminal",
            &chained,
            external_header(2, 0, FIRST_LVAL),
            |error| matches!(error, LongValueError::NonterminalAtLength { .. }),
        ),
    ];
    for (label, bytes, header, expected) in cases {
        let error = first_chunk_error(bytes, &header)?;
        assert!(expected(&error), "{label}: {error:?}");
    }

    let cycle = database_bytes(&[0, FIRST_LVAL as u8, 0, 0, b'a'], None);
    let header = external_header(2, 0, FIRST_LVAL);
    with_cursor(
        &cycle,
        &header,
        LongValueKind::Ole,
        limits(&cycle),
        |cursor| {
            cursor.next_chunk()?.ok_or("missing cycle prefix")?;
            assert!(matches!(
                cursor.next_chunk(),
                Err(LongValueError::Cycle { .. })
            ));
            assert!(cursor.next_chunk()?.is_none());
            Ok(())
        },
    )
}

#[test]
fn enforces_cumulative_decoded_text_and_chain_depth_limits() -> TestResult {
    let first = [0, SECOND_LVAL as u8, 0, 0, 0x80];
    let second = [0, 0, 0, 0, 0x80];
    let bytes = database_bytes(&first, Some(&second));
    let header = external_header(2, 0, FIRST_LVAL);
    let text_limits = limits(&bytes).with_max_decoded_value_bytes(ByteCount::new(4));
    with_cursor(
        &bytes,
        &header,
        LongValueKind::Memo,
        text_limits,
        |cursor| {
            let chunk = cursor.next_chunk()?.ok_or("missing text chunk")?;
            let LongValueChunkValue::Text(text) = chunk.value() else {
                return Err("expected text chunk".into());
            };
            assert_eq!(text.as_str(), "€");
            assert!(matches!(
                cursor.next_chunk(),
                Err(LongValueError::Resource(Error::ResourceLimitExceeded {
                    kind: ResourceLimitKind::DecodedValueBytes,
                    ..
                }))
            ));
            Ok(())
        },
    )?;

    let depth_limits = limits(&bytes).with_max_chain_depth(1);
    with_cursor(
        &bytes,
        &header,
        LongValueKind::Ole,
        depth_limits,
        |cursor| {
            assert!(cursor.next_chunk()?.is_some());
            assert!(matches!(
                cursor.next_chunk(),
                Err(LongValueError::Resource(Error::ResourceLimitExceeded {
                    kind: ResourceLimitKind::ChainDepth,
                    ..
                }))
            ));
            Ok(())
        },
    )
}

#[test]
fn only_empty_deleted_siblings_are_skipped() -> TestResult {
    let page = PageNumber::new(3);
    for deleted in 0..3 {
        let mut bytes = [0; PAGE_BYTES];
        bytes[4..8].copy_from_slice(b"LVAL");
        bytes[8..10].copy_from_slice(&3_u16.to_le_bytes());
        let mut end = PAGE_BYTES;
        for slot in 0..3 {
            let word = if slot == deleted {
                0xc000 | end as u16
            } else {
                end -= 1;
                bytes[end] = slot as u8;
                end as u16
            };
            bytes[10 + 2 * slot..12 + 2 * slot].copy_from_slice(&word.to_le_bytes());
        }
        for slot in 0..3 {
            let mut budget = ResourceBudget::new(ResourceLimits::default());
            let result =
                super::reader::validate_lval_row(RowLocator::new(page, slot), &bytes, &mut budget);
            if usize::from(slot) == deleted {
                assert!(matches!(
                    result,
                    Err(LongValueError::InvalidRowFlags { .. })
                ));
            } else {
                assert_eq!(&bytes[result?], &[slot]);
            }
        }
        let target = RowLocator::new(page, if deleted == 0 { 1 } else { 0 });
        for flags in [0x2000, 0x4000, 0x8000] {
            let mut malformed = bytes;
            let offset = 10 + 2 * deleted;
            let word = u16::from_le_bytes([bytes[offset], bytes[offset + 1]]) & 0x1fff;
            malformed[offset..offset + 2].copy_from_slice(&(word | flags).to_le_bytes());
            assert!(
                super::reader::validate_lval_row(
                    target,
                    &malformed,
                    &mut ResourceBudget::new(ResourceLimits::default())
                )
                .is_err()
            );
        }
        let mut nonempty = bytes;
        let offset = 10 + 2 * deleted;
        let word = u16::from_le_bytes([bytes[offset], bytes[offset + 1]]);
        nonempty[offset..offset + 2].copy_from_slice(&(word - 1).to_le_bytes());
        assert!(matches!(
            super::reader::validate_lval_row(
                target,
                &nonempty,
                &mut ResourceBudget::new(ResourceLimits::default())
            ),
            Err(LongValueError::InvalidRowFlags { .. })
        ));
    }
    Ok(())
}
