//! Coverage receipt observations collected through the public reader API.

use super::retained::RetainedLedger;
use super::{CatalogTable, SemanticSnapshotError};
use crate::{
    CoverageBranches, CoverageReceiptOutcome, ProducerKind, ProtocolScenario,
    SemanticProtocolError, SemanticSnapshotArtifacts, SemanticSnapshotOutcome, Sha256Hasher,
    TypedValue,
};
use jet3::{
    AllocationMap, DatabaseReader, IndexDefinitionKind, IndexDirection, IndexKeyEncoding,
    IndexNodeKind, ReadAt, ResourceBudget, TableDefinition, TextCodePage, decode_allocation_map,
    locate_usage_map,
};

pub(super) fn validate_pair_bindings(
    artifacts: &SemanticSnapshotArtifacts,
) -> Result<(), SemanticSnapshotError> {
    let (scenario_id, producer, database_sha256, error_class) = match &artifacts.snapshot {
        SemanticSnapshotOutcome::Success(snapshot) => (
            &snapshot.scenario_id,
            &snapshot.producer,
            &snapshot.database_sha256,
            None,
        ),
        SemanticSnapshotOutcome::OpeningFailure(failure) => (
            &failure.scenario_id,
            &failure.producer,
            &failure.database_sha256,
            Some(failure.error_class),
        ),
    };
    let receipt_error = match &artifacts.coverage_receipt.outcome {
        CoverageReceiptOutcome::Success { .. } => None,
        CoverageReceiptOutcome::OpeningFailure { error_class } => Some(*error_class),
    };
    if producer.kind != ProducerKind::Rust {
        return Err(SemanticProtocolError::InvalidModel {
            path: "$.producer.kind".to_owned(),
            reason: "Rust artifact pairs require a Rust producer",
        }
        .into());
    }
    if scenario_id != &artifacts.coverage_receipt.scenario_id
        || producer.source_revision() != artifacts.coverage_receipt.source_revision
        || database_sha256 != &artifacts.coverage_receipt.database_sha256
        || error_class != receipt_error
    {
        return Err(SemanticProtocolError::InvalidModel {
            path: "$".to_owned(),
            reason: "snapshot and coverage receipt bindings must match exactly",
        }
        .into());
    }
    if artifacts
        .coverage_receipt
        .branches
        .iter()
        .any(|branch| !ProtocolScenario::is_registered_branch(branch))
    {
        return Err(SemanticProtocolError::InvalidModel {
            path: "$.coverage_receipt.branches".to_owned(),
            reason: "coverage receipt branch is not in the closed protocol registry",
        }
        .into());
    }
    ProtocolScenario::validate_artifact(
        scenario_id,
        error_class,
        &artifacts.coverage_receipt.branches,
    )
    .map_err(|_| SemanticProtocolError::InvalidModel {
        path: "$.coverage_receipt".to_owned(),
        reason: "receipt outcome and branches must satisfy its closed scenario",
    })?;
    Ok(())
}

pub(super) fn collect_index_evidence<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    definition: &TableDefinition,
    budget: &mut ResourceBudget,
    ledger: &mut RetainedLedger,
    branches: &mut CoverageBranches,
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
            ledger.branch(budget, branches, "index.branch_leaf_traversal")?;
        }
        if physical.fields().len() == 1
            && !tree.entries().is_empty()
            && tree
                .entries()
                .iter()
                .all(|entry| entry.key().encoding() != IndexKeyEncoding::Unsupported)
        {
            ledger.branch(budget, branches, "index.single_field_key")?;
        }
        let requires_lossless_keys = physical.fields().len() > 1
            || physical
                .fields()
                .iter()
                .any(|field| field.direction() == IndexDirection::Descending);
        let retained_all_keys = !tree.entries().is_empty()
            && tree
                .entries()
                .iter()
                .all(|entry| !entry.key().raw_bytes().is_empty());
        if requires_lossless_keys && retained_all_keys {
            ledger.branch(budget, branches, "index.composite_key_lossless")?;
        }
    }
    Ok(())
}

pub(super) fn collect_allocation_evidence<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    entry: &CatalogTable,
    definition: &TableDefinition,
    budget: &mut ResourceBudget,
    ledger: &mut RetainedLedger,
    branches: &mut CoverageBranches,
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
            ledger.branch(budget, branches, "allocation.inline_map")?;
        }
        AllocationMap::Indirect(map) => {
            ledger.branch(budget, branches, "allocation.indirect_map")?;
            let mut references = map.map_page_references();
            let mut slot = 0_u64;
            while let Some(reference) = references
                .next_reference(budget)
                .map_err(SemanticSnapshotError::AllocationMap)?
            {
                if slot > 0 && reference != 0 {
                    ledger.branch(budget, branches, "allocation.extended_slot")?;
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
        ledger.branch(budget, branches, "tdef.continuation_chain")?;
    } else {
        ledger.branch(budget, branches, "tdef.single_page")?;
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
    budget: &mut ResourceBudget,
    ledger: &mut RetainedLedger,
    branches: &mut CoverageBranches,
) -> Result<(), SemanticSnapshotError> {
    let branch = match value {
        TypedValue::Null { .. } => "values.null_field",
        TypedValue::Text { .. } | TypedValue::Binary { .. } => "values.variable_short",
        TypedValue::Memo { .. } | TypedValue::Ole { .. } => "long_value.inline",
        _ => "values.fixed_scalar",
    };
    ledger.branch(budget, branches, branch)?;
    if matches!(value, TypedValue::Text { .. } | TypedValue::Memo { .. }) {
        let branch = match code_page {
            TextCodePage::Windows1251 => "values.text_cp1251",
            TextCodePage::Windows1252 => "values.text_cp1252",
        };
        ledger.branch(budget, branches, branch)?;
    }
    Ok(())
}
