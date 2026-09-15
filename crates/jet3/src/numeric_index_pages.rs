//! Uncompressed bulk trees using EXP-0062 separators, links and bitmaps.
//! Variable-width numeric records reuse EXP-0148/0150. Greedy byte packing and
//! avoiding a final one-child branch are writer policy, not DAO split thresholds.

use crate::numeric_index_entry::NumericIndexEntry;
use crate::{ByteCount, Error, PAGE_BYTES, PageImage, PageNumber, ResourceBudget};
use std::ops::Range;

const AREA: usize = crate::index_tree_page::ENTRY_AREA_OFFSET;
const AREA_BYTES: usize = PAGE_BYTES - AREA;
const BITMAP: usize = 22;
const CHILD_BYTES: usize = 4;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum TreeBuildError {
    NodeLimit { maximum: usize },
    Layout(&'static str),
    Encoding(Error),
}

impl From<Error> for TreeBuildError {
    fn from(error: Error) -> Self {
        Self::Encoding(error)
    }
}

impl std::fmt::Display for TreeBuildError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "numeric index tree: {self:?}")
    }
}

impl std::error::Error for TreeBuildError {}

#[derive(Debug, Clone)]
struct Node {
    entries: Range<usize>,
    children: Range<usize>,
    siblings: Range<usize>,
}

#[derive(Debug, Clone)]
pub(crate) struct NumericIndexPages {
    nodes: Vec<Node>,
}

impl NumericIndexPages {
    /// Plans leaves first and the root last. The caller supplies sorted entries
    /// and an allocation-policy limit on the total number of nodes.
    pub(crate) fn new(
        entries: &[NumericIndexEntry],
        maximum_nodes: usize,
        budget: &mut ResourceBudget,
    ) -> Result<Self, TreeBuildError> {
        let mut result = Self { nodes: Vec::new() };
        let mut first = 0;
        let mut used = 0;
        for (ordinal, entry) in entries.iter().enumerate() {
            budget.charge_work_units(1)?;
            let width = entry.record().len();
            if used + width > AREA_BYTES {
                result.push(first..ordinal, 0..0, maximum_nodes, budget)?;
                first = ordinal;
                used = 0;
            }
            used += width;
        }
        result.push(first..entries.len(), 0..0, maximum_nodes, budget)?;
        let mut previous = 0..result.nodes.len();
        for node in &mut result.nodes {
            node.siblings = previous.clone();
        }
        while previous.len() > 1 {
            let start = result.nodes.len();
            let mut first = previous.start;
            while first < previous.end {
                // A separator is the preceding child's complete maximum record.
                let mut end = first + 1;
                let mut used = 0;
                while end < previous.end {
                    budget.charge_work_units(1)?;
                    let maximum = result.nodes[end - 1].entries.end - 1;
                    let width = entries[maximum].record().len() + CHILD_BYTES;
                    if used + width > AREA_BYTES {
                        break;
                    }
                    used += width;
                    end += 1;
                }
                if previous.end - end == 1 {
                    end -= 1;
                }
                let range = result.nodes[first].entries.start..result.nodes[end - 1].entries.end;
                result.push(range, first..end, maximum_nodes, budget)?;
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
        maximum: usize,
        budget: &mut ResourceBudget,
    ) -> Result<(), TreeBuildError> {
        if self.nodes.len() == maximum {
            return Err(TreeBuildError::NodeLimit { maximum });
        }
        budget.charge_items(1)?;
        if self.nodes.len() == self.nodes.capacity() {
            let additional = self.nodes.capacity().max(1).min(maximum - self.nodes.len());
            let allocation =
                additional
                    .checked_mul(size_of::<Node>())
                    .ok_or(Error::Arithmetic {
                        operation: "size numeric index nodes",
                    })?;
            budget.charge_allocation(ByteCount::new(allocation as u64))?;
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

    pub(crate) fn len(&self) -> usize {
        self.nodes.len()
    }

    /// Uses the same entry inventory as `new`. `page_for` maps node ordinals to
    /// caller-owned page IDs; ownership and distinct allocation are caller checks.
    /// Rebuilds the header and used records while preserving unused payload bytes.
    pub(crate) fn image(
        &self,
        ordinal: usize,
        entries: &[NumericIndexEntry],
        page_for: impl Fn(usize) -> Option<PageNumber>,
        owner: PageNumber,
        original: &[u8; PAGE_BYTES],
        budget: &mut ResourceBudget,
    ) -> Result<PageImage, TreeBuildError> {
        let node = self
            .nodes
            .get(ordinal)
            .ok_or(TreeBuildError::Layout("node ordinal"))?;
        if self.nodes.last().map(|root| root.entries.end) != Some(entries.len()) {
            return Err(TreeBuildError::Layout("entry inventory"));
        }
        let owner = u32::try_from(owner.get()).map_err(|_| Error::IntegerConversion {
            value: owner.get() as u128,
            target: "u32 index owner",
        })?;
        let page = |ordinal| -> Result<u32, TreeBuildError> {
            let value = page_for(ordinal).ok_or(TreeBuildError::Layout("node page assignment"))?;
            u32::try_from(value.get()).map_err(|_| {
                Error::IntegerConversion {
                    value: value.get() as u128,
                    target: "u32 index node page",
                }
                .into()
            })
        };
        page(ordinal)?;
        budget.charge_encoded_bytes(ByteCount::new(PAGE_BYTES as u64))?;
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
                bytes[offset..offset + 4].copy_from_slice(&page(neighbor)?.to_le_bytes());
            }
        }
        let count = if branch {
            node.children.len() - 1
        } else {
            node.entries.len()
        };
        if branch {
            bytes[16..20].copy_from_slice(&page(node.children.end - 1)?.to_le_bytes());
        }
        let mut used = 0;
        for position in 0..count {
            let source = if branch {
                self.nodes[node.children.start + position].entries.end - 1
            } else {
                node.entries.start + position
            };
            let entry = entries[source].record();
            let width = entry.len() + if branch { CHILD_BYTES } else { 0 };
            if used + width > AREA_BYTES {
                return Err(TreeBuildError::Layout(
                    "changed entry widths exceed page capacity",
                ));
            }
            let start = AREA + used;
            bytes[start..start + entry.len()].copy_from_slice(entry);
            if branch {
                let child = page(node.children.start + position)?;
                bytes[start + entry.len()..start + width].copy_from_slice(&child.to_be_bytes());
            }
            used += width;
            bytes[BITMAP + used / 8] |= 1 << (used % 8);
        }
        bytes[2..4].copy_from_slice(&((AREA_BYTES - used) as u16).to_le_bytes());
        Ok(PageImage::from_bytes(bytes))
    }
}

#[cfg(test)]
#[path = "numeric_index_pages_tests.rs"]
mod tests;
