//! Preservation-aware fixed Long leaf records from EXP-0062/0126.
use crate::{PAGE_BYTES, PageImage, PageNumber, PageOffset, ResourceBudget, UpdateError};

pub(crate) const RECORD_BYTES: usize = 9;
pub(crate) const MAX_ENTRIES: usize =
    (PAGE_BYTES - crate::index_tree_page::ENTRY_AREA_OFFSET) / RECORD_BYTES;

// EXP-0073/0219: the prefix counter retains deleted keys; live rows do not.
pub(crate) fn check_counts(
    table: &[u8; PAGE_BYTES],
    prefix: &[u8; 8],
    rows: usize,
) -> Result<(), UpdateError> {
    let expected = u32::try_from(rows).map_err(|_| UpdateError::Mismatch("key count range"))?;
    let stored = u32::from_le_bytes([prefix[4], prefix[5], prefix[6], prefix[7]]);
    if table[12..16] != expected.to_le_bytes() || stored < expected {
        return Err(UpdateError::Mismatch("table or index counter"));
    }
    Ok(())
}

// EXP-0062: three big-endian page bytes and one slot follow the Long component.
pub(crate) fn locator(record: &[u8]) -> Option<crate::RowLocator> {
    if record.len() != RECORD_BYTES {
        return None;
    }
    Some(crate::RowLocator::new(
        PageNumber::new(u64::from(crate::index_tree_page::u24_at_be(record, 5))),
        record[8],
    ))
}

pub(crate) fn encode_record(
    key: [u8; 5],
    row: crate::RowLocator,
) -> Result<[u8; RECORD_BYTES], UpdateError> {
    let page = u32::try_from(row.page().get())
        .ok()
        .filter(|p| *p <= 0xff_ffff)
        .ok_or(UpdateError::Unsupported("index row page reference"))?;
    let mut record = [0; RECORD_BYTES];
    record[..5].copy_from_slice(&key);
    record[5..8].copy_from_slice(&page.to_be_bytes()[1..]);
    record[8] = row.slot();
    Ok(record)
}

// EXP-0059/0219: increment the retained counter on unique present-key insertion.
// Callers require exactly one physical index and have validated its old counter.
pub(crate) fn increment_counter(
    image: &mut PageImage,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let bytes = image.as_bytes();
    let count = u32::from_le_bytes([bytes[47], bytes[48], bytes[49], bytes[50]])
        .checked_add(1)
        .ok_or(UpdateError::Unsupported("index counter overflow"))?;
    image.write_at(PageOffset::new(47), &count.to_le_bytes(), budget)?;
    Ok(())
}
