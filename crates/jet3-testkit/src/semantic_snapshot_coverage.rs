//! Coverage receipt observations collected through the public reader API.

use std::collections::BTreeSet;

use super::{CatalogTable, SemanticSnapshotError};
use crate::{SemanticProtocolError, Sha256Hasher, TypedValue};
use jet3::{
    AllocationMap, DatabaseReader, IndexDefinitionKind, IndexDirection, IndexKeyEncoding,
    IndexNodeKind, ReadAt, ResourceBudget, TableDefinition, TextCodePage, decode_allocation_map,
    locate_usage_map,
};

pub(super) fn collect_index_evidence<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    definition: &TableDefinition,
    budget: &mut ResourceBudget,
    branches: &mut BTreeSet<String>,
) -> Result<(), SemanticSnapshotError> {
    for logical in definition.indexes() {
        if matches!(logical.kind(), IndexDefinitionKind::Relationship(_)) {
            continue;
        }
        let physical = definition
            .physical_indexes()
            .get(usize::from(logical.physical_index()))
            .ok_or(SemanticSnapshotError::InvalidIndexReference {
                table: definition.root(),
                physical_index: logical.physical_index(),
            })?;
        let tree = database
            .index_tree(definition, logical.physical_index(), budget)
            .map_err(SemanticSnapshotError::IndexTree)?;
        if tree
            .nodes()
            .iter()
            .any(|node| node.kind() == IndexNodeKind::Intermediate)
            && tree
                .nodes()
                .iter()
                .any(|node| node.kind() == IndexNodeKind::Leaf)
        {
            branches.insert("index.branch_leaf_traversal".to_owned());
        }
        if physical.fields().len() == 1
            && !tree.entries().is_empty()
            && tree
                .entries()
                .iter()
                .all(|entry| entry.key().encoding() != IndexKeyEncoding::Unsupported)
        {
            branches.insert("index.single_field_key".to_owned());
        }
        if physical.fields().len() > 1
            || physical
                .fields()
                .iter()
                .any(|field| field.direction() == IndexDirection::Descending)
        {
            branches.insert("index.composite_key_lossless".to_owned());
        }
    }
    Ok(())
}

pub(super) fn collect_allocation_evidence<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    entry: &CatalogTable,
    definition: &TableDefinition,
    budget: &mut ResourceBudget,
    branches: &mut BTreeSet<String>,
    hasher: &mut Sha256Hasher,
) -> Result<(), SemanticSnapshotError> {
    let name_length = u64::try_from(entry.name.len()).map_err(|_| {
        SemanticSnapshotError::Resource(jet3::Error::IntegerConversion {
            value: entry.name.len() as u128,
            target: "u64",
        })
    })?;
    hash_update(hasher, &name_length.to_le_bytes())?;
    hash_update(hasher, entry.name.as_bytes())?;
    hash_update(hasher, &entry.root.get().to_le_bytes())?;

    let locator = definition.maps().owned();
    let mut page = [0_u8; jet3::JET3_PAGE_SIZE.get() as usize];
    let classified = database
        .read_classified_page(locator.page(), &mut page, budget)
        .map_err(SemanticSnapshotError::DatabasePage)?;
    let record =
        locate_usage_map(classified, locator, budget).map_err(SemanticSnapshotError::UsageMap)?;
    match decode_allocation_map(record.raw(), budget)
        .map_err(SemanticSnapshotError::AllocationMap)?
    {
        AllocationMap::Inline(_) => {
            branches.insert("allocation.inline_map".to_owned());
        }
        AllocationMap::Indirect(map) => {
            branches.insert("allocation.indirect_map".to_owned());
            let mut references = map.map_page_references();
            let mut slot = 0_u64;
            while let Some(reference) = references
                .next_reference(budget)
                .map_err(SemanticSnapshotError::AllocationMap)?
            {
                if slot > 0 && reference != 0 {
                    branches.insert("allocation.extended_slot".to_owned());
                }
                slot = slot.checked_add(1).ok_or(SemanticSnapshotError::Resource(
                    jet3::Error::Arithmetic {
                        operation: "advance allocation-map slot",
                    },
                ))?;
            }
        }
        _ => {
            return Err(SemanticSnapshotError::Protocol(
                SemanticProtocolError::InvalidModel {
                    path: "$.coverage_receipt.branches".to_owned(),
                    reason: "unsupported allocation-map form",
                },
            ));
        }
    }

    let mut owned = database
        .owned_pages(entry.root, budget)
        .map_err(SemanticSnapshotError::Allocation)?;
    while let Some(page) = owned
        .next_page()
        .map_err(SemanticSnapshotError::Allocation)?
    {
        hash_update(hasher, &page.get().to_le_bytes())?;
    }
    hash_update(hasher, &u64::MAX.to_le_bytes())?;
    if u64::from(definition.logical_length()) > jet3::JET3_PAGE_SIZE.get() {
        branches.insert("tdef.continuation_chain".to_owned());
    } else {
        branches.insert("tdef.single_page".to_owned());
    }
    Ok(())
}

fn hash_update(hasher: &mut Sha256Hasher, bytes: &[u8]) -> Result<(), SemanticSnapshotError> {
    hasher.update(bytes).map_err(|_| {
        SemanticSnapshotError::Protocol(SemanticProtocolError::InvalidModel {
            path: "$.allocated_set_sha256".to_owned(),
            reason: "SHA-256 length is not representable",
        })
    })
}

pub(super) fn record_value_branch(
    value: &TypedValue,
    code_page: TextCodePage,
    branches: &mut BTreeSet<String>,
) {
    match value {
        TypedValue::Null { .. } => {
            branches.insert("values.null_field".to_owned());
        }
        TypedValue::Text { .. } | TypedValue::Binary { .. } => {
            branches.insert("values.variable_short".to_owned());
        }
        TypedValue::Memo { .. } | TypedValue::Ole { .. } => {
            branches.insert("long_value.inline".to_owned());
        }
        _ => {
            branches.insert("values.fixed_scalar".to_owned());
        }
    }
    if matches!(value, TypedValue::Text { .. } | TypedValue::Memo { .. }) {
        branches.insert(
            match code_page {
                TextCodePage::Windows1251 => "values.text_cp1251",
                TextCodePage::Windows1252 => "values.text_cp1252",
            }
            .to_owned(),
        );
    }
}
