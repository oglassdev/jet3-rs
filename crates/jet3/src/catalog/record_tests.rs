use super::cursor_tests::{record, write_rows};
use super::record::{
    CatalogObjectClass, CatalogObjectKind, CatalogPageDirectory, CatalogRecordError,
    decode_catalog_record,
};
use super::record_writer::{CatalogRecordWriteError, catalog_record_len};
use crate::{
    ByteCount, CatalogNameEncoding, Error, PAGE_BYTES, ReadLimits, ResourceBudget,
    ResourceLimitKind, ResourceLimits,
};

use crate::testkit::budget;

fn page_with_rows(rows: &[(u16, Vec<u8>)]) -> [u8; PAGE_BYTES] {
    let mut page = [0_u8; PAGE_BYTES];
    let bytes: Vec<_> = rows.iter().map(|(_, row)| row.clone()).collect();
    write_rows(&mut page, &bytes);
    for (index, (flags, _)) in rows.iter().enumerate() {
        page[11 + 2 * index] |= (flags >> 8) as u8;
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
    assert_eq!(view.kind().raw(), 1);
    assert_eq!(view.class(), CatalogObjectClass::User);
    assert_eq!(view.name_bytes(), b"Caf\xe9_Euro\x80");
    let owned = view.into_owned(Some(crate::PageNumber::new(23)), &mut resources)?;
    assert_eq!(owned.id().get(), 23);
    assert_eq!(owned.kind(), CatalogObjectKind::Table);
    assert_eq!(owned.class(), CatalogObjectClass::User);
    assert_eq!(owned.raw_flags(), 0);
    assert_eq!(owned.name().raw_bytes(), b"Caf\xe9_Euro\x80");
    assert_eq!(
        owned.name().encoding(),
        CatalogNameEncoding::DatabaseCodePage
    );
    assert_eq!(owned.name().decoded_ascii(), None);
    assert_eq!(owned.table_definition(), Some(crate::PageNumber::new(23)));
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
fn saved_query_flags_remain_lossless_and_require_the_query_kind()
-> Result<(), Box<dyn std::error::Error>> {
    // EXP-0303: each native QueryDef form has an exact catalog kind/flag pair.
    for flags in [0, 16, 32, 48, 64, 80, 96, 128] {
        let bytes = record(0x8000_0001, 5, flags, b"SavedQuery");
        let mut resources = budget();
        let owned =
            decode_catalog_record(&bytes, &mut resources)?.into_owned(None, &mut resources)?;
        assert_eq!(owned.kind(), CatalogObjectKind::Unknown(5));
        assert_eq!(owned.class(), CatalogObjectClass::User);
        assert_eq!(owned.raw_flags(), flags);
        assert_eq!(owned.table_definition(), None);
        if flags != 0 {
            for kind in [1, 2, 3] {
                assert_eq!(
                    decode_catalog_record(&record(23, kind, flags, b"Other"), &mut budget()),
                    Err(CatalogRecordError::UnsupportedObjectFlags { raw: flags })
                );
            }
        }
    }
    for flags in [1, 17, 112, 144, 0x8000_0010] {
        assert_eq!(
            decode_catalog_record(&record(23, 5, flags, b"UnknownQuery"), &mut budget()),
            Err(CatalogRecordError::UnsupportedObjectFlags { raw: flags })
        );
    }
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
    assert_eq!(
        directory
            .next_active(&page)?
            .map(|entry| &page[entry.range()]),
        Some(active.as_slice())
    );
    assert_eq!(
        directory
            .next_active(&page)?
            .map(|entry| &page[entry.range()]),
        Some(later.as_slice())
    );
    assert_eq!(directory.next_active(&page)?, None);
    assert_eq!(resources.item_work(), 3);
    Ok(())
}

#[test]
fn directory_rejects_count_flags_offsets_and_overlap() {
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
    ] as [(u16, CatalogRecordError); 3]
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

#[test]
fn record_length_accepts_one_to_224_name_bytes() {
    assert_eq!(catalog_record_len(1), Ok(38));
    assert_eq!(catalog_record_len(224), Ok(261));
    assert_eq!(
        catalog_record_len(0),
        Err(CatalogRecordWriteError::EmptyName)
    );
    assert_eq!(
        catalog_record_len(225),
        Err(CatalogRecordWriteError::NameTooLong {
            length: 225,
            maximum: 224,
        })
    );
}
