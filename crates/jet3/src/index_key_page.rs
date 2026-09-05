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

// EXP-0073: table row count at [12,16), physical prefix distinct count at [4,8).
// A unique present-key replacement preserves both counts.
pub(crate) fn check_counts(
    table: &[u8; PAGE_BYTES],
    prefix: &[u8; 8],
    rows: usize,
) -> Result<(), UpdateError> {
    let expected = u32::try_from(rows)
        .map_err(|_| UpdateError::Mismatch("key count range"))?
        .to_le_bytes();
    if table[12..16] != expected || prefix[4..8] != expected {
        return Err(UpdateError::Mismatch("table or distinct key count"));
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
