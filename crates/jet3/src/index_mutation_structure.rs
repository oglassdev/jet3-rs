//! EXP-0062/0126/0148/0225: bounded numeric records and subtree separator fences.
use crate::index_tree_page::{ENTRY_AREA_OFFSET, boundaries, parse_node, u32_at_be};
use crate::numeric_index_entry::ENTRY_CAPACITY;
use crate::{
    DatabaseReader, FileSource, IndexNodeKind, IndexTree, PAGE_BYTES, PageNumber, ResourceBudget,
    TableDefinition, UpdateError,
};

#[derive(Clone, Copy, PartialEq, Eq)]
struct Record {
    bytes: [u8; ENTRY_CAPACITY],
    length: usize,
}
impl Record {
    fn bytes(&self) -> &[u8] {
        &self.bytes[..self.length]
    }
}
type Bounds = (Record, Record);

pub(crate) fn validate(
    database: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    tree: &IndexTree,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let mut ranges: Vec<(PageNumber, Option<Bounds>)> = Vec::new();
    crate::page_edits::reserve(&mut ranges, tree.nodes().len(), budget)?;
    ranges.extend(tree.nodes().iter().map(|n| (n.page(), None)));
    budget.charge_work_units(
        (ranges.len() as u64).saturating_mul((ranges.len().max(1).ilog2() + 1) as u64),
    )?;
    ranges.sort_unstable_by_key(|n| n.0);
    for node in tree.nodes().iter().rev() {
        let mut bytes = [0; PAGE_BYTES];
        let classified = database
            .read_classified_page(node.page(), &mut bytes, budget)
            .map_err(crate::TableDefinitionError::Page)?;
        let parsed = parse_node(
            classified.kind(),
            crate::index_tree::PendingNode {
                page: node.page(),
                depth: node.depth(),
            },
            table.root(),
            database.geometry(),
            &bytes,
            budget,
        )?;
        let data = &bytes[ENTRY_AREA_OFFSET..];
        let prefix = &data[..parsed.prefix_len];
        let mut start = prefix.len();
        let branch = node.kind() == IndexNodeKind::Intermediate;
        let mut bounds: Option<Bounds> = None;
        let mut previous: Option<Record> = None;
        for end in boundaries(&bytes) {
            let suffix = &data[start..end];
            let size = prefix.len() + suffix.len();
            let key_size = size
                .checked_sub(if branch { 4 } else { 0 })
                .filter(|n| (5..=ENTRY_CAPACITY).contains(n))
                .ok_or(UpdateError::Mismatch("numeric index record width"))?;
            let mut record = [0; ENTRY_CAPACITY + 4];
            record[..prefix.len()].copy_from_slice(prefix);
            record[prefix.len()..size].copy_from_slice(suffix);
            let mut key = Record {
                bytes: [0; ENTRY_CAPACITY],
                length: key_size,
            };
            key.bytes[..key_size].copy_from_slice(&record[..key_size]);
            let (minimum, maximum) = if branch {
                let child = PageNumber::new(u64::from(u32_at_be(&record, key_size)));
                let range = child_bounds(&ranges, child, budget)?;
                if range.1.bytes() > key.bytes()
                    || previous.is_some_and(|p| p.bytes() >= range.0.bytes())
                {
                    return Err(UpdateError::Mismatch("invalid branch separator bounds"));
                }
                range
            } else {
                if previous.is_some_and(|p| p.bytes() >= key.bytes()) {
                    return Err(UpdateError::Mismatch("unordered numeric index records"));
                }
                (key, key)
            };
            bounds = Some((bounds.map_or(minimum, |(first, _)| first), maximum));
            previous = Some(key);
            start = end;
        }
        if branch {
            let (minimum, maximum) = child_bounds(&ranges, parsed.tail_child, budget)?;
            if previous.is_some_and(|p| p.bytes() >= minimum.bytes()) {
                return Err(UpdateError::Mismatch("invalid branch separator bounds"));
            }
            bounds = Some((bounds.map_or(minimum, |(first, _)| first), maximum));
        }
        let position = ranges
            .binary_search_by_key(&node.page(), |n| n.0)
            .map_err(|_| UpdateError::Mismatch("index node inventory"))?;
        ranges[position].1 = bounds;
    }
    Ok(())
}

fn child_bounds(
    ranges: &[(PageNumber, Option<Bounds>)],
    page: PageNumber,
    budget: &mut ResourceBudget,
) -> Result<Bounds, UpdateError> {
    budget.charge_work_units((ranges.len().max(1).ilog2() + 1) as u64)?;
    let position = ranges
        .binary_search_by_key(&page, |n| n.0)
        .map_err(|_| UpdateError::Mismatch("missing index child"))?;
    ranges[position]
        .1
        .ok_or(UpdateError::Mismatch("empty index child"))
}
