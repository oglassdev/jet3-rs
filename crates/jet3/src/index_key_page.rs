//! Preservation-aware fixed Long leaf records from EXP-0062/0126.
use crate::index_tree::{IndexNodeKind, PendingNode};
use crate::{
    PAGE_BYTES, PageGeometry, PageImage, PageKind, PageNumber, PageOffset, ResourceBudget,
    UpdateError,
};

pub(crate) const RECORD_BYTES: usize = 9;
pub(crate) const MAX_ENTRIES: usize =
    (PAGE_BYTES - crate::index_tree_page::ENTRY_AREA_OFFSET) / RECORD_BYTES;

pub(crate) fn validate(
    page: PageNumber,
    owner: PageNumber,
    geometry: PageGeometry,
    bytes: &[u8; PAGE_BYTES],
    budget: &mut ResourceBudget,
) -> Result<usize, UpdateError> {
    if bytes[0] != 4 || bytes[20] != 0 {
        return Err(UpdateError::Unsupported(
            "key update requires uncompressed leaf",
        ));
    }
    let parsed = crate::index_tree_page::parse_node(
        PageKind::LeafIndex,
        PendingNode { page, depth: 1 },
        owner,
        geometry,
        bytes,
        budget,
    )?;
    if parsed.node.kind() != IndexNodeKind::Leaf
        || parsed.node.previous().is_some()
        || parsed.node.next().is_some()
    {
        return Err(UpdateError::Unsupported(
            "key update requires isolated root leaf",
        ));
    }
    let mut count = 0;
    for boundary in crate::index_tree_page::boundaries(bytes) {
        count += 1;
        if boundary != count * RECORD_BYTES {
            return Err(UpdateError::Unsupported(
                "key update requires fixed Long leaf records",
            ));
        }
    }
    Ok(count)
}

pub(crate) fn record(bytes: &[u8; PAGE_BYTES], ordinal: usize) -> Option<&[u8]> {
    let start = crate::index_tree_page::ENTRY_AREA_OFFSET
        .checked_add(ordinal.checked_mul(RECORD_BYTES)?)?;
    bytes.get(start..start.checked_add(RECORD_BYTES)?)
}

pub(crate) fn replace(
    original: &[u8; PAGE_BYTES],
    records: &[[u8; RECORD_BYTES]],
    budget: &mut ResourceBudget,
) -> Result<PageImage, UpdateError> {
    if records.len() > MAX_ENTRIES {
        return Err(UpdateError::Mismatch("leaf capacity"));
    }
    let mut image = PageImage::from_bytes(*original);
    for (ordinal, record) in records.iter().enumerate() {
        image.write_at(
            PageOffset::new(
                (crate::index_tree_page::ENTRY_AREA_OFFSET + ordinal * RECORD_BYTES) as u64,
            ),
            record,
            budget,
        )?;
    }
    Ok(image)
}

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

// EXP-0062 cumulative boundaries and free bytes; retain all vacated entry slack.
pub(crate) fn resize(
    original: &[u8; PAGE_BYTES],
    records: &[[u8; RECORD_BYTES]],
    budget: &mut ResourceBudget,
) -> Result<PageImage, UpdateError> {
    let mut image = replace(original, records, budget)?;
    let mut bitmap = [0_u8; crate::index_tree_page::ENTRY_AREA_OFFSET - 22];
    for ordinal in 1..=records.len() {
        let end = ordinal * RECORD_BYTES;
        bitmap[end / 8] |= 1 << (end % 8);
    }
    image.write_at(PageOffset::new(22), &bitmap, budget)?;
    let free = (PAGE_BYTES
        - crate::index_tree_page::ENTRY_AREA_OFFSET
        - records.len() * RECORD_BYTES) as u16;
    image.write_at(PageOffset::new(2), &free.to_le_bytes(), budget)?;
    Ok(image)
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
