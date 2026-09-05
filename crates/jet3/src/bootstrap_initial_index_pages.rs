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
}

impl IndexPages {
    pub(super) fn new(
        entries: &[Entry],
        budget: &mut ResourceBudget,
    ) -> Result<Self, ComposeError> {
        let mut result = Self { nodes: Vec::new() };
        let mut first = 0;
        let mut used = 0;
        for (ordinal, entry) in entries.iter().enumerate() {
            budget.charge_work_units(1)?;
            let width = entry.record().len();
            if used + width > INDEX_ENTRY_AREA_LEN {
                result.push(first..ordinal, 0..0, budget)?;
                first = ordinal;
                used = 0;
            }
            used += width;
        }
        result.push(first..entries.len(), 0..0, budget)?;
        let mut previous = 0..result.nodes.len();
        for node in &mut result.nodes {
            node.siblings = previous.clone();
        }
        while previous.len() > 1 {
            let start = result.nodes.len();
            let mut first = previous.start;
            while first < previous.end {
                // Each separator stores the preceding child's complete maximum.
                let mut end = first + 1;
                let mut used = 0;
                while end < previous.end {
                    budget.charge_work_units(1)?;
                    let maximum = result.nodes[end - 1].entries.end - 1;
                    let width = entries[maximum].record().len() + CHILD_BYTES;
                    if used + width > INDEX_ENTRY_AREA_LEN {
                        break;
                    }
                    used += width;
                    end += 1;
                }
                // Avoid a final branch with only its tail child.
                if previous.end - end == 1 {
                    end -= 1;
                }
                let range = result.nodes[first].entries.start..result.nodes[end - 1].entries.end;
                result.push(range, first..end, budget)?;
                first = end;
            }
            let parents = start..result.nodes.len();
            for node in &mut result.nodes[parents.clone()] {
                node.siblings = parents.clone();
            }
            previous = parents;
        }
        Ok(result)
    }

    fn push(
        &mut self,
        entries: Range<usize>,
        children: Range<usize>,
        budget: &mut ResourceBudget,
    ) -> Result<(), ComposeError> {
        let maximum = (MAP_BITMAP_BYTES * 8) as usize;
        if self.nodes.len() == maximum {
            return Err(UsageMapWriteError::PageOutOfMap {
                page: PageNumber::new(maximum as u64),
                first: PageNumber::new(0),
                page_count: maximum as u64,
            }
            .into());
        }
        budget.charge_items(1)?;
        if self.nodes.len() == self.nodes.capacity() {
            let additional = self.nodes.capacity().max(1).min(maximum - self.nodes.len());
            budget.charge_allocation(ByteCount::new((additional * size_of::<Node>()) as u64))?;
            self.nodes
                .try_reserve_exact(additional)
                .map_err(|_| Error::Io {
                    operation: "reserve initial index nodes",
                    kind: std::io::ErrorKind::OutOfMemory,
                })?;
        }
        self.nodes.push(Node {
            entries,
            children,
            siblings: 0..0,
        });
        Ok(())
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
        entries: &[Entry],
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
        if branch {
            bytes[16..20].copy_from_slice(
                &(self.page(node.children.end - 1, root, first_extra) as u32).to_le_bytes(),
            );
        }
        let mut used = 0;
        for position in 0..count {
            let source = if branch {
                self.nodes[node.children.start + position].entries.end - 1
            } else {
                node.entries.start + position
            };
            let entry = entries[source].record();
            let entry_bytes = entry.len();
            let width = entry_bytes + if branch { CHILD_BYTES } else { 0 };
            let start = INDEX_ENTRY_AREA_OFFSET + used;
            bytes[start..start + entry_bytes].copy_from_slice(entry);
            if branch {
                let child = self.page(node.children.start + position, root, first_extra) as u32;
                bytes[start + entry_bytes..start + width].copy_from_slice(&child.to_be_bytes());
            }
            used += width;
            let end = used;
            bytes[INDEX_BOUNDARY_BITMAP_OFFSET + end / 8] |= 1 << (end % 8);
        }
        bytes[2..4].copy_from_slice(&((INDEX_ENTRY_AREA_LEN - used) as u16).to_le_bytes());
        let mut image = PageImage::new(if branch {
            PageKind::IntermediateIndex
        } else {
            PageKind::LeafIndex
        });
        image.write_at(PageOffset::new(0), &bytes, budget)?;
        Ok(image)
    }
}
