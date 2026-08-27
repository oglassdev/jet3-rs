use super::{
    CATALOG_COLUMN_COUNT, CatalogObjectClass, CatalogObjectKind, CatalogPageDirectory,
    CatalogRecordError, decode_catalog_record,
};
use crate::{
    ByteCount, Error, JET3_PAGE_SIZE, ReadLimits, ResourceBudget, ResourceLimitKind, ResourceLimits,
};

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;

fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::new(ReadLimits::default()))
}

fn record(id: u32, kind: u16, flags: u32, name: &[u8]) -> Vec<u8> {
    let mut row = vec![0_u8; 31 + name.len() + 6];
    row[0] = CATALOG_COLUMN_COUNT;
    row[1..5].copy_from_slice(&id.to_le_bytes());
    row[9..11].copy_from_slice(&kind.to_le_bytes());
    row[27..31].copy_from_slice(&flags.to_le_bytes());
    row[31..31 + name.len()].copy_from_slice(name);
    let length = row.len();
    row[length - 6] = u8::try_from(31 + name.len()).unwrap_or_default();
    row[length - 5] = 31;
    row[length - 4] = 11;
    row[length - 3] = 0xff;
    row
}

fn page_with_rows(rows: &[(u16, Vec<u8>)]) -> [u8; PAGE_BYTES] {
    let mut page = [0_u8; PAGE_BYTES];
    page[0] = 1;
    page[8..10].copy_from_slice(&u16::try_from(rows.len()).unwrap_or_default().to_le_bytes());
    let mut start = PAGE_BYTES;
    for (index, (flags, row)) in rows.iter().enumerate() {
        start -= row.len();
        page[10 + index * 2..12 + index * 2]
            .copy_from_slice(&(u16::try_from(start).unwrap_or_default() | flags).to_le_bytes());
        page[start..start + row.len()].copy_from_slice(row);
    }
    page
}

#[test]
fn decodes_minimum_fields_and_preserves_cp1252_bytes() -> Result<(), Box<dyn std::error::Error>> {
    let bytes = record(23, 1, 0, b"Caf\xe9_Euro\x80");
    let mut resources = budget();
    let view = decode_catalog_record(&bytes, &mut resources)?;
    assert_eq!(view.id().get(), 23);
    assert_eq!(view.kind(), CatalogObjectKind::Table);
    assert_eq!(view.class(), CatalogObjectClass::User);
    assert_eq!(view.name_bytes(), b"Caf\xe9_Euro\x80");
    let owned = view.into_owned(Some(crate::PageNumber::new(23)), &mut resources)?;
    assert_eq!(owned.name().raw_bytes(), b"Caf\xe9_Euro\x80");
    assert_eq!(owned.name().decoded_ascii(), None);
    assert_eq!(resources.allocation_bytes(), ByteCount::new(10));
    Ok(())
}

#[test]
fn unknown_kinds_and_ascii_views_remain_lossless() -> Result<(), Box<dyn std::error::Error>> {
    let bytes = record(0x0f00_0001, 3, 0x8000_0000, b"Tables");
    let mut resources = budget();
    let view = decode_catalog_record(&bytes, &mut resources)?;
    assert_eq!(view.kind(), CatalogObjectKind::Unknown(3));
    assert_eq!(view.class(), CatalogObjectClass::System);
    let owned = view.into_owned(None, &mut resources)?;
    assert_eq!(owned.kind().raw(), 3);
    assert_eq!(owned.raw_flags(), 0x8000_0000);
    assert_eq!(owned.name().decoded_ascii(), Some("Tables"));
    assert_eq!(owned.table_definition(), None);
    Ok(())
}

#[test]
fn bad_lengths_trailers_columns_and_flags_are_structured() {
    let mut resources = budget();
    assert_eq!(
        decode_catalog_record(&[0; 36], &mut resources),
        Err(CatalogRecordError::RecordTooShort {
            length: 36,
            minimum: 37,
        })
    );

    let mut wrong_columns = record(1, 1, 0, b"A");
    wrong_columns[0] = 16;
    assert_eq!(
        decode_catalog_record(&wrong_columns, &mut resources),
        Err(CatalogRecordError::UnexpectedColumnCount { observed: 16 })
    );

    let mut bad_trailer = record(1, 1, 0, b"A");
    let length = bad_trailer.len();
    bad_trailer[length - 5] = 30;
    assert!(matches!(
        decode_catalog_record(&bad_trailer, &mut resources),
        Err(CatalogRecordError::InvalidNameTrailer { .. })
    ));

    let flags = record(1, 1, 1, b"A");
    assert_eq!(
        decode_catalog_record(&flags, &mut resources),
        Err(CatalogRecordError::UnsupportedObjectFlags { raw: 1 })
    );
}

#[test]
fn directory_accepts_deleted_zero_length_tombstones_and_skips_them()
-> Result<(), Box<dyn std::error::Error>> {
    let active = record(1, 1, 0, b"A");
    let later = record(2, 1, 0, b"B");
    let page = page_with_rows(&[
        (0, active.clone()),
        (0xc000, Vec::new()),
        (0, later.clone()),
    ]);
    let mut resources = budget();
    let mut directory = CatalogPageDirectory::validate(&page, &mut resources)?;
    assert_eq!(directory.next_active(&page)?, Some(active.as_slice()));
    assert_eq!(directory.next_active(&page)?, Some(later.as_slice()));
    assert_eq!(directory.next_active(&page)?, None);
    assert_eq!(resources.item_work(), 3);
    Ok(())
}

#[test]
fn directory_rejects_count_flags_offsets_overlap_and_active_overflow() {
    let mut resources = budget();
    let mut page = [0_u8; PAGE_BYTES];
    page[8..10].copy_from_slice(&1020_u16.to_le_bytes());
    assert_eq!(
        CatalogPageDirectory::validate(&page, &mut resources),
        Err(CatalogRecordError::RowCountTooLarge {
            row_count: 1020,
            maximum: 1019,
        })
    );

    for (raw, expected) in [
        (
            0x2000 | 2040,
            CatalogRecordError::UnknownDirectoryFlag {
                row: 0,
                raw_offset: 0x2000 | 2040,
            },
        ),
        (
            2048,
            CatalogRecordError::RowOffsetOutOfPage {
                row: 0,
                raw_offset: 2048,
            },
        ),
        (
            11,
            CatalogRecordError::InvalidRowBounds {
                row: 0,
                start: 11,
                end: 2048,
                directory_end: 12,
            },
        ),
        (
            0x4000 | 2040,
            CatalogRecordError::ActiveOverflowRow { row: 0 },
        ),
    ] as [(u16, CatalogRecordError); 4]
    {
        let mut candidate = [0_u8; PAGE_BYTES];
        candidate[8..10].copy_from_slice(&1_u16.to_le_bytes());
        candidate[10..12].copy_from_slice(&raw.to_le_bytes());
        assert_eq!(
            CatalogPageDirectory::validate(&candidate, &mut budget()),
            Err(expected)
        );
    }
}

#[test]
fn item_and_name_allocation_limits_accept_exact_and_reject_one_over()
-> Result<(), Box<dyn std::error::Error>> {
    let page = page_with_rows(&[(0, record(1, 1, 0, b"A")), (0, record(2, 1, 0, b"B"))]);
    let mut exact =
        ResourceBudget::new(ResourceLimits::new(ReadLimits::default()).with_max_item_work(2));
    CatalogPageDirectory::validate(&page, &mut exact)?;
    assert_eq!(exact.item_work(), 2);

    let mut one_below =
        ResourceBudget::new(ResourceLimits::new(ReadLimits::default()).with_max_item_work(1));
    assert!(matches!(
        CatalogPageDirectory::validate(&page, &mut one_below),
        Err(CatalogRecordError::Resource(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::ItemWork,
            requested: 2,
            maximum: 1,
        }))
    ));
    assert_eq!(one_below.item_work(), 0);

    let row = record(1, 1, 0, b"AB");
    let mut exact = ResourceBudget::new(
        ResourceLimits::new(ReadLimits::default()).with_max_allocation_bytes(ByteCount::new(2)),
    );
    decode_catalog_record(&row, &mut exact)?.into_owned(None, &mut exact)?;
    assert_eq!(exact.allocation_bytes(), ByteCount::new(2));

    let mut one_below = ResourceBudget::new(
        ResourceLimits::new(ReadLimits::default()).with_max_allocation_bytes(ByteCount::new(1)),
    );
    let view = decode_catalog_record(&row, &mut one_below)?;
    assert!(matches!(
        view.into_owned(None, &mut one_below),
        Err(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::AllocationBytes,
            requested: 2,
            maximum: 1,
        })
    ));
    assert_eq!(one_below.allocation_bytes(), ByteCount::new(0));
    Ok(())
}
