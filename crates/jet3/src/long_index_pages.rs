//! EXP-0062/0126: Long leaf records, branch maxima, sibling links and bitmaps.
//! Bulk rebuilding is writer policy; node capacity is a physical byte limit.
use crate::index_key_page::{MAX_ENTRIES, RECORD_BYTES};
use crate::page_edits::reserve;
use crate::{PageImage, PageNumber, ResourceBudget, UpdateError};
use std::ops::Range;

const AREA: usize = crate::index_tree_page::ENTRY_AREA_OFFSET;
const AREA_BYTES: usize = crate::PAGE_BYTES - AREA;
const BRANCH_CHILDREN: usize = AREA_BYTES / (RECORD_BYTES + 4) + 1;

struct Node {
    entries: Range<usize>,
    children: Range<usize>,
    siblings: Range<usize>,
}

pub(crate) struct LongIndexPages {
    nodes: Vec<Node>,
}

impl LongIndexPages {
    pub fn new(entries: usize, budget: &mut ResourceBudget) -> Result<Self, UpdateError> {
        let leaves = entries.div_ceil(MAX_ENTRIES).max(1);
        let mut nodes = Vec::new();
        reserve(&mut nodes, leaves, budget)?;
        budget.charge_items(leaves as u64)?;
        for ordinal in 0..leaves {
            nodes.push(Node {
                entries: ordinal * MAX_ENTRIES..((ordinal + 1) * MAX_ENTRIES).min(entries),
                children: 0..0,
                siblings: 0..leaves,
            });
        }
        let mut level = 0..leaves;
        while level.len() > 1 {
            let groups = level.len().div_ceil(BRANCH_CHILDREN);
            let first_parent = nodes.len();
            reserve(&mut nodes, groups, budget)?;
            budget.charge_items(level.len() as u64)?;
            let mut child = level.start;
            for group in 0..groups {
                let count = level.len() / groups + usize::from(group < level.len() % groups);
                let end = child + count;
                nodes.push(Node {
                    entries: nodes[child].entries.start..nodes[end - 1].entries.end,
                    children: child..end,
                    siblings: first_parent..first_parent + groups,
                });
                child = end;
            }
            level = first_parent..nodes.len();
        }
        Ok(Self { nodes })
    }

    pub fn len(&self) -> usize {
        self.nodes.len()
    }

    pub fn image(
        &self,
        ordinal: usize,
        entries: &[[u8; RECORD_BYTES]],
        pages: &[PageNumber],
        owner: PageNumber,
        original: &[u8; crate::PAGE_BYTES],
        budget: &mut ResourceBudget,
    ) -> Result<PageImage, UpdateError> {
        let node = self
            .nodes
            .get(ordinal)
            .ok_or(UpdateError::Mismatch("index node ordinal"))?;
        if pages.len() != self.nodes.len() || node.entries.end > entries.len() {
            return Err(UpdateError::Mismatch("index node inventory"));
        }
        let owner =
            u32::try_from(owner.get()).map_err(|_| UpdateError::Mismatch("index owner width"))?;
        let mut bytes = *original;
        let branch = !node.children.is_empty();
        bytes[0] = if branch { 3 } else { 4 };
        bytes[1] = 1;
        bytes[4..8].copy_from_slice(&owner.to_le_bytes());
        bytes[8..AREA].fill(0);
        bytes[21] = u8::from(branch);
        for (offset, neighbor) in [
            (
                8,
                ordinal.checked_sub(1).filter(|n| *n >= node.siblings.start),
            ),
            (12, (ordinal + 1 < node.siblings.end).then_some(ordinal + 1)),
        ] {
            if let Some(neighbor) = neighbor {
                let number = u32::try_from(pages[neighbor].get())
                    .map_err(|_| UpdateError::Mismatch("index sibling width"))?;
                bytes[offset..offset + 4].copy_from_slice(&number.to_le_bytes());
            }
        }
        if branch {
            let tail = u32::try_from(pages[node.children.end - 1].get())
                .map_err(|_| UpdateError::Mismatch("index child width"))?;
            bytes[16..20].copy_from_slice(&tail.to_le_bytes());
        }
        let count = if branch {
            node.children.len() - 1
        } else {
            node.entries.len()
        };
        let mut used = 0;
        budget.charge_work_units((count * (RECORD_BYTES + 4) + AREA) as u64)?;
        for position in 0..count {
            let entry = if branch {
                self.nodes[node.children.start + position].entries.end - 1
            } else {
                node.entries.start + position
            };
            bytes[AREA + used..AREA + used + RECORD_BYTES].copy_from_slice(&entries[entry]);
            used += RECORD_BYTES;
            if branch {
                let child = u32::try_from(pages[node.children.start + position].get())
                    .map_err(|_| UpdateError::Mismatch("index child width"))?;
                bytes[AREA + used..AREA + used + 4].copy_from_slice(&child.to_be_bytes());
                used += 4;
            }
            bytes[22 + used / 8] |= 1 << (used % 8);
        }
        bytes[2..4].copy_from_slice(&((AREA_BYTES - used) as u16).to_le_bytes());
        Ok(PageImage::from_bytes(bytes))
    }
}
