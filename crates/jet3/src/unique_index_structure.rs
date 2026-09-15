//! EXP-0062/0126: branch separators duplicate each non-tail child's maximum record.
use crate::index_key_page::RECORD_BYTES;
use crate::index_tree_page::{ENTRY_AREA_OFFSET, boundaries, parse_node, u32_at_be};
use crate::{
    DatabaseReader, FileSource, IndexNodeKind, IndexTree, PAGE_BYTES, PageNumber, ResourceBudget,
    TableDefinition, UpdateError,
};

pub(crate) fn validate(
    database: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    tree: &IndexTree,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let mut maxima: Vec<(PageNumber, Option<[u8; RECORD_BYTES]>)> = Vec::new();
    crate::page_edits::reserve(&mut maxima, tree.nodes().len(), budget)?;
    maxima.extend(tree.nodes().iter().map(|n| (n.page(), None)));
    budget.charge_work_units(
        (maxima.len() as u64).saturating_mul((maxima.len().max(1).ilog2() + 1) as u64),
    )?;
    maxima.sort_unstable_by_key(|n| n.0);
    // The reader returns nodes by depth, so every child is available in reverse order.
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
        let mut maximum = None;
        for end in boundaries(&bytes) {
            let suffix = &data[start..end];
            let size = RECORD_BYTES + if branch { 4 } else { 0 };
            if prefix.len() + suffix.len() != size {
                return Err(UpdateError::Mismatch("Long index record width"));
            }
            let mut record = [0; RECORD_BYTES + 4];
            record[..prefix.len()].copy_from_slice(prefix);
            record[prefix.len()..size].copy_from_slice(suffix);
            let mut key = [0; RECORD_BYTES];
            key.copy_from_slice(&record[..RECORD_BYTES]);
            if branch {
                let child = PageNumber::new(u64::from(u32_at_be(&record, RECORD_BYTES)));
                if child_maximum(&maxima, child, budget)? != key {
                    return Err(UpdateError::Mismatch(
                        "branch separator differs from child maximum",
                    ));
                }
            }
            maximum = Some(key);
            start = end;
        }
        if branch {
            maximum = Some(child_maximum(&maxima, parsed.tail_child, budget)?);
        }
        let position = maxima
            .binary_search_by_key(&node.page(), |n| n.0)
            .map_err(|_| UpdateError::Mismatch("index node inventory"))?;
        maxima[position].1 = maximum;
    }
    Ok(())
}

fn child_maximum(
    maxima: &[(PageNumber, Option<[u8; RECORD_BYTES]>)],
    page: PageNumber,
    budget: &mut ResourceBudget,
) -> Result<[u8; RECORD_BYTES], UpdateError> {
    budget.charge_work_units((maxima.len().max(1).ilog2() + 1) as u64)?;
    let position = maxima
        .binary_search_by_key(&page, |n| n.0)
        .map_err(|_| UpdateError::Mismatch("missing index child"))?;
    maxima[position]
        .1
        .ok_or(UpdateError::Mismatch("empty index child"))
}
