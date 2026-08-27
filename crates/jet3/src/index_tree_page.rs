//! Checked decoding of one physical index page from `EXP-0062`.

use crate::index_tree::{
    IndexNode, IndexNodeKind, IndexTreeError, PendingNode, push_charged, resource_arithmetic,
};
use crate::{JET3_PAGE_SIZE, PageGeometry, PageKind, PageNumber, ResourceBudget};

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
pub(crate) const ENTRY_AREA_OFFSET: usize = 248;
const ENTRY_AREA_LEN: usize = PAGE_BYTES - ENTRY_AREA_OFFSET;
const BOUNDARY_BITMAP_OFFSET: usize = 22;
const HEADER_MARKER: u8 = 1;
const LEAF_NODE_MARKER: u8 = 0;
const INTERMEDIATE_NODE_MARKER: u8 = 1;

#[derive(Debug)]
pub(crate) struct ParsedNode {
    pub(crate) node: IndexNode,
    pub(crate) boundaries: Vec<usize>,
    pub(crate) prefix_len: usize,
    pub(crate) tail_child: PageNumber,
}

pub(crate) fn parse_node(
    kind: PageKind,
    pending: PendingNode,
    expected_owner: PageNumber,
    geometry: PageGeometry,
    page: &[u8; PAGE_BYTES],
    budget: &mut ResourceBudget,
) -> Result<ParsedNode, IndexTreeError> {
    let node_kind = match kind {
        PageKind::IntermediateIndex => IndexNodeKind::Intermediate,
        PageKind::LeafIndex => IndexNodeKind::Leaf,
        actual => {
            return Err(IndexTreeError::UnexpectedPageKind {
                page: pending.page,
                actual,
            });
        }
    };
    if page[1] != HEADER_MARKER {
        return Err(IndexTreeError::InvalidHeaderMarker {
            page: pending.page,
            offset: 1,
            raw: page[1],
        });
    }
    let owner = PageNumber::new(u64::from(u32_at_le(page, 4)));
    if owner != expected_owner {
        return Err(IndexTreeError::UnexpectedOwner {
            page: pending.page,
            expected: expected_owner,
            actual: owner,
        });
    }
    let marker = page[21];
    let expected_marker = match node_kind {
        IndexNodeKind::Intermediate => INTERMEDIATE_NODE_MARKER,
        IndexNodeKind::Leaf => LEAF_NODE_MARKER,
    };
    if marker != expected_marker {
        return Err(IndexTreeError::InvalidHeaderMarker {
            page: pending.page,
            offset: 21,
            raw: marker,
        });
    }
    let previous = optional_reference(page, 8, pending.page, "previous sibling", geometry)?;
    let next = optional_reference(page, 12, pending.page, "next sibling", geometry)?;
    let tail_child = PageNumber::new(u64::from(u32_at_le(page, 16)));
    match node_kind {
        IndexNodeKind::Leaf if tail_child.get() != 0 => {
            return Err(IndexTreeError::InvalidTailChild {
                page: pending.page,
                child: tail_child,
            });
        }
        IndexNodeKind::Intermediate if tail_child.get() == 0 => {
            return Err(IndexTreeError::InvalidTailChild {
                page: pending.page,
                child: tail_child,
            });
        }
        _ => {}
    }
    let prefix_len = usize::from(page[20]);
    let mut boundaries = Vec::new();
    for (byte_index, value) in page[BOUNDARY_BITMAP_OFFSET..ENTRY_AREA_OFFSET]
        .iter()
        .copied()
        .enumerate()
    {
        for bit in 0..8_u32 {
            if value & (1_u8 << bit) == 0 {
                continue;
            }
            let boundary = byte_index
                .checked_mul(8)
                .and_then(|offset| offset.checked_add(bit as usize))
                .ok_or_else(|| resource_arithmetic("decode index entry boundary"))?;
            if boundary > ENTRY_AREA_LEN {
                return Err(IndexTreeError::BoundaryOutsideEntryArea {
                    page: pending.page,
                    boundary,
                });
            }
            push_charged(
                &mut boundaries,
                boundary,
                budget,
                "reserve index entry boundaries",
            )?;
        }
    }
    let mut previous_boundary = prefix_len;
    for boundary in &boundaries {
        if *boundary <= previous_boundary || *boundary > ENTRY_AREA_LEN {
            return Err(IndexTreeError::InvalidEntryBoundary {
                page: pending.page,
                boundary: *boundary,
                previous: previous_boundary,
            });
        }
        previous_boundary = *boundary;
    }
    if boundaries.is_empty() && prefix_len != 0 {
        return Err(IndexTreeError::InvalidEntryBoundary {
            page: pending.page,
            boundary: 0,
            previous: prefix_len,
        });
    }
    let expected_free = ENTRY_AREA_LEN.saturating_sub(previous_boundary);
    let raw_free = u16_at_le(page, 2);
    if usize::from(raw_free) != expected_free {
        return Err(IndexTreeError::InvalidFreeSpace {
            page: pending.page,
            raw: raw_free,
            expected: expected_free,
        });
    }
    if node_kind == IndexNodeKind::Intermediate && boundaries.is_empty() {
        return Err(IndexTreeError::EmptyIntermediate { page: pending.page });
    }
    Ok(ParsedNode {
        node: IndexNode {
            page: pending.page,
            kind: node_kind,
            depth: pending.depth,
            previous,
            next,
        },
        boundaries,
        prefix_len,
        tail_child,
    })
}

fn optional_reference(
    page: &[u8],
    offset: usize,
    containing: PageNumber,
    role: &'static str,
    geometry: PageGeometry,
) -> Result<Option<PageNumber>, IndexTreeError> {
    let reference = PageNumber::new(u64::from(u32_at_le(page, offset)));
    if reference.get() == 0 {
        return Ok(None);
    }
    if reference == containing {
        return Err(IndexTreeError::SelfReference {
            page: containing,
            role,
        });
    }
    geometry
        .validate_reference(reference)
        .map_err(|source| IndexTreeError::InvalidReference {
            page: containing,
            role,
            reference,
            source,
        })?;
    Ok(Some(reference))
}

fn u16_at_le(bytes: &[u8], offset: usize) -> u16 {
    u16::from_le_bytes([bytes[offset], bytes[offset + 1]])
}

fn u32_at_le(bytes: &[u8], offset: usize) -> u32 {
    u32::from_le_bytes([
        bytes[offset],
        bytes[offset + 1],
        bytes[offset + 2],
        bytes[offset + 3],
    ])
}

pub(crate) fn u24_at_be(bytes: &[u8], offset: usize) -> u32 {
    (u32::from(bytes[offset]) << 16)
        | (u32::from(bytes[offset + 1]) << 8)
        | u32::from(bytes[offset + 2])
}

pub(crate) fn u32_at_be(bytes: &[u8], offset: usize) -> u32 {
    u32::from_be_bytes([
        bytes[offset],
        bytes[offset + 1],
        bytes[offset + 2],
        bytes[offset + 3],
    ])
}
