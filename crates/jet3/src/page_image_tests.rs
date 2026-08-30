use super::{DataPageBuilder, PAGE_BYTES, PageImage, PageImageError};
use crate::data_page_directory::DataPageDirectory;
use crate::limits::ReadLimits;
use crate::row_directory::RowDirectory;
use crate::{
    ByteCount, ByteOffset, Error, MapRowLocator, PageKind, PageNumber, PageOffset, ResourceBudget,
    ResourceLimits, classify_page, locate_usage_map,
};

type TestResult = Result<(), Box<dyn std::error::Error>>;

fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}

fn budget_with_encoded_limit(bytes: u64) -> ResourceBudget {
    ResourceBudget::new(
        ResourceLimits::new(ReadLimits::default()).with_max_encoded_bytes(ByteCount::new(bytes)),
    )
}

#[test]
fn new_image_carries_only_its_tag() {
    let image = PageImage::new(PageKind::TableDefinition);
    assert_eq!(image.tag(), 0x02);
    assert!(image.as_bytes()[1..].iter().all(|byte| *byte == 0));
    assert_eq!(PageImage::new(PageKind::Unknown(0x08)).tag(), 0x08);
}

#[test]
fn write_at_accepts_last_byte_and_rejects_past_end() -> TestResult {
    let mut image = PageImage::new(PageKind::Data);
    let mut budget = budget();
    image.write_at(PageOffset::new(2047), &[0xaa], &mut budget)?;
    assert_eq!(image.as_bytes()[2047], 0xaa);
    assert_eq!(
        image.write_at(PageOffset::new(2047), &[1, 2], &mut budget),
        Err(Error::OutputCapacityExceeded {
            offset: ByteOffset::new(2047),
            needed: ByteCount::new(2),
            available: ByteCount::new(1),
        })
    );
    assert_eq!(image.as_bytes()[2047], 0xaa);
    Ok(())
}

#[test]
fn appended_rows_round_trip_through_directory_decoders() -> TestResult {
    let owner = PageNumber::new(20);
    let mut budget = budget();
    let mut builder = DataPageBuilder::new(owner, &mut budget)?;
    let rows: [&[u8]; 3] = [&[0x00, 1, 2, 3], &[0x01, 0, 0, 0, 0, 9], &[0xff]];
    for (index, row) in rows.iter().enumerate() {
        assert_eq!(builder.append_row(row, &mut budget)?, index as u8);
    }
    assert_eq!(builder.row_count(), 3);
    let image = builder.finish();
    let raw = image.as_bytes();

    let classified = classify_page(PageNumber::new(21), raw, &mut budget)?;
    assert_eq!(classified.kind(), PageKind::Data);
    RowDirectory::validate_owner(owner, raw)?;
    let mut directory =
        DataPageDirectory::validate(raw, &mut budget).map_err(|error| format!("{error:?}"))?;
    let mut end = PAGE_BYTES;
    for row in rows {
        let entry = directory.next_entry(raw).ok_or("missing entry")?;
        assert_eq!(entry.range(), end - row.len()..end);
        assert!(!entry.overflow() && !entry.hidden());
        assert_eq!(&raw[entry.range()], row);
        end -= row.len();
    }
    assert!(directory.next_entry(raw).is_none());

    let record = locate_usage_map(
        classified,
        MapRowLocator::new(PageNumber::new(21), 1),
        &mut budget,
    )?;
    assert_eq!(record.raw(), rows[1]);
    Ok(())
}

#[test]
fn page_full_boundary_is_exact() -> TestResult {
    let mut budget = budget();
    let mut builder = DataPageBuilder::new(PageNumber::new(20), &mut budget)?;
    let free = builder.free_bytes().get() as usize;
    let too_big = vec![7; free - 1];
    assert_eq!(
        builder.append_row(&too_big, &mut budget),
        Err(PageImageError::PageFull {
            needed: ByteCount::new(free as u64 + 1),
            available: ByteCount::new(free as u64),
        })
    );
    builder.append_row(&too_big[..free - 2], &mut budget)?;
    assert_eq!(builder.free_bytes(), ByteCount::new(0));
    assert_eq!(
        builder.append_row(&[1], &mut budget),
        Err(PageImageError::PageFull {
            needed: ByteCount::new(3),
            available: ByteCount::new(0),
        })
    );
    assert_eq!(
        builder.append_row(&[], &mut budget),
        Err(PageImageError::EmptyRow)
    );
    Ok(())
}

#[test]
fn slot_space_ends_at_256_rows() -> TestResult {
    let mut budget = budget();
    let mut builder = DataPageBuilder::new(PageNumber::new(20), &mut budget)?;
    for _ in 0..256 {
        builder.append_row(&[1], &mut budget)?;
    }
    assert_eq!(
        builder.append_row(&[1], &mut budget),
        Err(PageImageError::RowSlotsExhausted { maximum: 256 })
    );
    Ok(())
}

#[test]
fn owner_beyond_u32_is_rejected() {
    assert_eq!(
        DataPageBuilder::new(PageNumber::new(u64::from(u32::MAX) + 1), &mut budget()).err(),
        Some(PageImageError::OwnerNotRepresentable {
            owner: PageNumber::new(u64::from(u32::MAX) + 1),
        })
    );
}

#[test]
fn budget_exhaustion_leaves_row_count_unchanged() -> TestResult {
    let mut budget = budget_with_encoded_limit(4 + 3);
    let mut builder = DataPageBuilder::new(PageNumber::new(20), &mut budget)?;
    assert!(matches!(
        builder.append_row(&[1, 2, 3], &mut budget),
        Err(PageImageError::Encoding(
            Error::ResourceLimitExceeded { .. }
        ))
    ));
    assert_eq!(builder.row_count(), 0);
    assert_eq!(&builder.image().as_bytes()[8..10], &[0, 0]);
    Ok(())
}
