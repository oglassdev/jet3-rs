//! Uncompressed bulk index trees using EXP-0062 branch separators and links.
//! Children are balanced across each level; this is writer policy, not a DAO
//! split threshold. The existing root stays fixed and other nodes append.

use super::*;
use std::ops::Range;

const CHILD_BYTES: usize = 4;

#[derive(Debug, Clone)]
struct Node {
    entries: Range<usize>,
    children: Range<usize>,
    siblings: Range<usize>,
}

#[derive(Debug, Clone)]
pub(super) struct IndexPages {
    nodes: Vec<Node>,
    entry_bytes: usize,
}

impl IndexPages {
    pub(super) fn new(
        count: usize,
        entry_bytes: usize,
        budget: &mut ResourceBudget,
    ) -> Result<Self, ComposeError> {
        let leaf_capacity = INDEX_ENTRY_AREA_LEN / entry_bytes;
        let fanout = INDEX_ENTRY_AREA_LEN / (entry_bytes + CHILD_BYTES) + 1;
        let leaves = count.div_ceil(leaf_capacity).max(1);
        let mut total = leaves;
        let mut level = leaves;
        while level > 1 {
            level = level.div_ceil(fanout);
            total = total.checked_add(level).ok_or(Error::Arithmetic {
                operation: "count initial index pages",
            })?;
        }
        // All creation maps currently use the established inline representation.
        if total as u64 > MAP_BITMAP_BYTES * 8 {
            return Err(UsageMapWriteError::PageOutOfMap {
                page: PageNumber::new(total as u64 - 1),
                first: PageNumber::new(0),
                page_count: MAP_BITMAP_BYTES * 8,
            }
            .into());
        }
        budget.charge_allocation(ByteCount::new((total * size_of::<Node>()) as u64))?;
        budget.charge_items(total as u64)?;
        let mut nodes = Vec::new();
        nodes.try_reserve_exact(total).map_err(|_| Error::Io {
            operation: "reserve initial index nodes",
            kind: std::io::ErrorKind::OutOfMemory,
        })?;
        for leaf in 0..leaves {
            nodes.push(Node {
                entries: leaf * leaf_capacity..((leaf + 1) * leaf_capacity).min(count),
                children: 0..0,
                siblings: 0..leaves,
            });
        }
        let mut previous = 0..leaves;
        while previous.len() > 1 {
            let parents = previous.len().div_ceil(fanout);
            let start = nodes.len();
            for parent in 0..parents {
                // Balanced groups avoid a non-root branch with only a tail child.
                let first = previous.start + parent * previous.len() / parents;
                let end = previous.start + (parent + 1) * previous.len() / parents;
                nodes.push(Node {
                    entries: nodes[first].entries.start..nodes[end - 1].entries.end,
                    children: first..end,
                    siblings: start..start + parents,
                });
            }
            previous = start..nodes.len();
        }
        Ok(Self { nodes, entry_bytes })
    }

    pub(super) fn extra_count(&self) -> u64 {
        self.nodes.len() as u64 - 1
    }

    fn page(&self, ordinal: usize, root: PageNumber, first_extra: u64) -> u64 {
        if ordinal + 1 == self.nodes.len() {
            root.get()
        } else {
            first_extra + ordinal as u64
        }
    }

    pub(super) fn image(
        &self,
        entries: &[[u8; ENTRY_CAPACITY]],
        owner: PageNumber,
        root: PageNumber,
        first_extra: u64,
        ordinal: Option<usize>,
        budget: &mut ResourceBudget,
    ) -> Result<PageImage, ComposeError> {
        let last = first_extra
            .checked_add(self.extra_count())
            .ok_or(Error::Arithmetic {
                operation: "place initial index pages",
            })?;
        if last > MAP_BITMAP_BYTES * 8 {
            return Err(UsageMapWriteError::PageOutOfMap {
                page: PageNumber::new(last - 1),
                first: PageNumber::new(0),
                page_count: MAP_BITMAP_BYTES * 8,
            }
            .into());
        }
        let entry_bytes = self.entry_bytes;
        let ordinal = ordinal.unwrap_or(self.nodes.len() - 1);
        let node = &self.nodes[ordinal];
        let branch = !node.children.is_empty();
        let mut bytes = [0_u8; PAGE_BYTES];
        bytes[0] = if branch { 3 } else { 4 };
        bytes[1] = 1;
        bytes[21] = u8::from(branch);
        let owner = u32::try_from(owner.get()).map_err(|_| Error::IntegerConversion {
            value: owner.get() as u128,
            target: "u32 index owner",
        })?;
        bytes[4..8].copy_from_slice(&owner.to_le_bytes());
        for (offset, neighbor) in [
            (
                8,
                ordinal.checked_sub(1).filter(|n| *n >= node.siblings.start),
            ),
            (12, (ordinal + 1 < node.siblings.end).then_some(ordinal + 1)),
        ] {
            if let Some(neighbor) = neighbor {
                bytes[offset..offset + 4].copy_from_slice(
                    &(self.page(neighbor, root, first_extra) as u32).to_le_bytes(),
                );
            }
        }
        let count = if branch {
            node.children.len() - 1
        } else {
            node.entries.len()
        };
        let width = entry_bytes + if branch { CHILD_BYTES } else { 0 };
        bytes[2..4].copy_from_slice(&((INDEX_ENTRY_AREA_LEN - count * width) as u16).to_le_bytes());
        if branch {
            bytes[16..20].copy_from_slice(
                &(self.page(node.children.end - 1, root, first_extra) as u32).to_le_bytes(),
            );
        }
        for position in 0..count {
            let source = if branch {
                self.nodes[node.children.start + position].entries.end - 1
            } else {
                node.entries.start + position
            };
            let start = INDEX_ENTRY_AREA_OFFSET + position * width;
            bytes[start..start + entry_bytes].copy_from_slice(&entries[source][..entry_bytes]);
            if branch {
                let child = self.page(node.children.start + position, root, first_extra) as u32;
                bytes[start + entry_bytes..start + width].copy_from_slice(&child.to_be_bytes());
            }
            let end = (position + 1) * width;
            bytes[INDEX_BOUNDARY_BITMAP_OFFSET + end / 8] |= 1 << (end % 8);
        }
        let mut image = PageImage::new(if branch {
            PageKind::IntermediateIndex
        } else {
            PageKind::LeafIndex
        });
        image.write_at(PageOffset::new(0), &bytes, budget)?;
        Ok(image)
    }
}
