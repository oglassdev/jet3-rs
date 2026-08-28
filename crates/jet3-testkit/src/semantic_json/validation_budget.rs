//! Allocation precharges for semantic artifact model validation.

use super::output_budget::{
    canonicalization_allocation_bound, outcome_allocation_bound, receipt_allocation_bound,
};
use crate::{
    CoverageReceipt, SemanticSnapshot, SemanticSnapshotError, SemanticSnapshotOutcome, TypedValue,
};
use jet3::{ByteCount, Error, ResourceBudget};

// A successful validation node constructs at most four owned diagnostic paths.
// The longest static path fragment is shorter than 64 bytes and the deepest
// path has at most three decimal `usize` indexes. Dynamic property-name bytes
// are charged separately below. Keeping the derivation explicit prevents this
// early traversal reservation from becoming an unexplained policy constant.
const MAX_VALIDATION_PATHS_PER_NODE: u64 = 4;
const MAX_VALIDATION_STATIC_PATH_BYTES: u64 = 64;
const MAX_VALIDATION_PATH_INDEXES: u64 = 3;
const MAX_USIZE_DECIMAL_BYTES: u64 = if usize::BITS <= 32 { 10 } else { 20 };
const VALIDATION_NODE_BYTES: u64 = MAX_VALIDATION_PATHS_PER_NODE
    * (MAX_VALIDATION_STATIC_PATH_BYTES + MAX_VALIDATION_PATH_INDEXES * MAX_USIZE_DECIMAL_BYTES);

pub(super) fn validate_outcome_budgeted(
    outcome: &SemanticSnapshotOutcome,
    budget: &mut ResourceBudget,
) -> Result<(), SemanticSnapshotError> {
    precharge_outcome_validation(outcome, budget)?;
    outcome.validate()?;
    Ok(())
}

pub(super) fn validate_receipt_budgeted(
    receipt: &CoverageReceipt,
    budget: &mut ResourceBudget,
) -> Result<(), SemanticSnapshotError> {
    precharge_validation_nodes(budget, receipt_validation_nodes(receipt)?)?;
    precharge_validation_text(budget, receipt.scenario_id.as_str(), 1)?;
    precharge_validation_text(budget, &receipt.source_revision, 1)?;
    for branch in &receipt.branches {
        precharge_validation_text(budget, branch, 1)?;
    }
    let validation = receipt_allocation_bound(receipt).map_err(SemanticSnapshotError::Resource)?;
    budget
        .charge_allocation(validation)
        .map_err(SemanticSnapshotError::Resource)?;
    receipt.validate()?;
    Ok(())
}

fn precharge_outcome_validation(
    outcome: &SemanticSnapshotOutcome,
    budget: &mut ResourceBudget,
) -> Result<(), SemanticSnapshotError> {
    match outcome {
        SemanticSnapshotOutcome::Success(snapshot) => {
            precharge_snapshot_validation_shape(snapshot, budget)?;
            let validation = canonicalization_allocation_bound(snapshot)
                .map_err(SemanticSnapshotError::Resource)?;
            budget
                .charge_allocation(validation)
                .map_err(SemanticSnapshotError::Resource)
        }
        SemanticSnapshotOutcome::OpeningFailure(failure) => {
            precharge_validation_nodes(budget, 1)?;
            precharge_validation_text(budget, failure.scenario_id.as_str(), 1)?;
            precharge_validation_text(budget, failure.producer.source_revision(), 1)?;
            let validation =
                outcome_allocation_bound(outcome).map_err(SemanticSnapshotError::Resource)?;
            budget
                .charge_allocation(validation)
                .map_err(SemanticSnapshotError::Resource)
        }
    }
}

fn precharge_snapshot_validation_shape(
    snapshot: &SemanticSnapshot,
    budget: &mut ResourceBudget,
) -> Result<(), SemanticSnapshotError> {
    precharge_validation_nodes(budget, 1)?;
    precharge_validation_text(budget, snapshot.scenario_id.as_str(), 1)?;
    precharge_validation_text(budget, snapshot.producer.source_revision(), 1)?;
    precharge_validation_nodes(budget, snapshot.tables.len())?;
    for table in &snapshot.tables {
        precharge_validation_text(budget, &table.name, 1)?;
        precharge_validation_nodes(budget, table.columns.len())?;
        precharge_validation_nodes(budget, table.indexes.len())?;
        precharge_validation_nodes(budget, table.rows.len())?;
        precharge_property_validation(&table.properties, budget, 2)?;
        for column in &table.columns {
            precharge_validation_text(budget, &column.name, 1)?;
            precharge_validation_text(budget, &column.dao_type, 1)?;
            precharge_property_validation(&column.properties, budget, 2)?;
        }
        for index in &table.indexes {
            precharge_validation_text(budget, &index.name, 1)?;
            precharge_validation_nodes(budget, index.fields.len())?;
            for field in &index.fields {
                precharge_validation_text(budget, &field.name, 1)?;
            }
            precharge_property_validation(&index.properties, budget, 2)?;
        }
        for row in &table.rows {
            precharge_property_validation(&row.values, budget, 2)?;
        }
    }
    precharge_property_validation(&snapshot.database_properties, budget, 2)?;
    precharge_validation_nodes(budget, snapshot.relationships.len())?;
    for relationship in &snapshot.relationships {
        precharge_validation_text(budget, &relationship.name, 1)?;
        precharge_validation_text(budget, &relationship.table, 1)?;
        precharge_validation_text(budget, &relationship.foreign_table, 1)?;
        precharge_validation_nodes(budget, relationship.fields.len())?;
        for field in &relationship.fields {
            precharge_validation_text(budget, &field.field, 1)?;
            precharge_validation_text(budget, &field.foreign_field, 1)?;
        }
        precharge_property_validation(&relationship.properties, budget, 2)?;
    }
    precharge_validation_nodes(budget, snapshot.raw_preservation.len())?;
    for raw in &snapshot.raw_preservation {
        precharge_validation_text(budget, &raw.semantic_path, 1)?;
        precharge_validation_text(budget, raw.raw_hex.as_str(), 1)?;
        precharge_validation_text(budget, &raw.purpose, 1)?;
    }
    precharge_property_validation(&snapshot.producer_extensions, budget, 8)
}

fn precharge_property_validation(
    properties: &crate::PropertyMap,
    budget: &mut ResourceBudget,
    dynamic_path_copies: u64,
) -> Result<(), SemanticSnapshotError> {
    precharge_validation_nodes(budget, properties.len())?;
    for (name, value) in properties {
        let length = u64::try_from(name.len()).map_err(|_| {
            SemanticSnapshotError::Resource(Error::IntegerConversion {
                value: name.len() as u128,
                target: "u64",
            })
        })?;
        let bytes = length.checked_mul(dynamic_path_copies).ok_or({
            SemanticSnapshotError::Resource(Error::Arithmetic {
                operation: "size semantic validation property paths",
            })
        })?;
        budget
            .charge_allocation(ByteCount::new(bytes))
            .map_err(SemanticSnapshotError::Resource)?;
        precharge_property_value(value, budget)?;
    }
    Ok(())
}

fn precharge_validation_text(
    budget: &mut ResourceBudget,
    value: &str,
    copies: u64,
) -> Result<(), SemanticSnapshotError> {
    let length = u64::try_from(value.len()).map_err(|_| {
        SemanticSnapshotError::Resource(Error::IntegerConversion {
            value: value.len() as u128,
            target: "u64",
        })
    })?;
    let bytes = length.checked_mul(copies).ok_or({
        SemanticSnapshotError::Resource(Error::Arithmetic {
            operation: "size semantic validation text work",
        })
    })?;
    budget
        .charge_allocation(ByteCount::new(bytes))
        .map_err(SemanticSnapshotError::Resource)
}

fn precharge_property_value(
    value: &TypedValue,
    budget: &mut ResourceBudget,
) -> Result<(), SemanticSnapshotError> {
    let (logical_bytes, raw_hex) = match value {
        TypedValue::Null { raw_hex } | TypedValue::Boolean { raw_hex, .. } => (0, raw_hex),
        TypedValue::Byte { raw_hex, .. }
        | TypedValue::Integer { raw_hex, .. }
        | TypedValue::Long { raw_hex, .. }
        | TypedValue::Single { raw_hex, .. }
        | TypedValue::Double { raw_hex, .. } => (0, raw_hex),
        TypedValue::Decimal { value, raw_hex } | TypedValue::Currency { value, raw_hex } => {
            (value.as_str().len(), raw_hex)
        }
        TypedValue::DateTime { value, raw_hex } => (value.as_str().len(), raw_hex),
        TypedValue::Text { value, raw_hex, .. } | TypedValue::Memo { value, raw_hex, .. } => {
            if let Some(raw) = raw_hex {
                precharge_text_decode(raw.as_str().len(), budget)?;
            }
            (value.len(), raw_hex)
        }
        TypedValue::Binary { value, raw_hex } | TypedValue::Ole { value, raw_hex } => {
            (value.as_str().len(), raw_hex)
        }
        TypedValue::Guid { value, raw_hex } => (value.as_str().len(), raw_hex),
    };
    let raw_bytes = raw_hex.as_ref().map_or(0, |raw| raw.as_str().len());
    let dynamic_bytes = logical_bytes.checked_add(raw_bytes).ok_or({
        SemanticSnapshotError::Resource(Error::Arithmetic {
            operation: "size semantic validation property value",
        })
    })?;
    budget
        .charge_allocation(
            ByteCount::from_usize(dynamic_bytes).map_err(SemanticSnapshotError::Resource)?,
        )
        .map_err(SemanticSnapshotError::Resource)
}

fn precharge_text_decode(
    raw_hex_bytes: usize,
    budget: &mut ResourceBudget,
) -> Result<(), SemanticSnapshotError> {
    let raw_bytes = raw_hex_bytes / 2;
    let maximum_decoded = raw_bytes.checked_mul(3).ok_or({
        SemanticSnapshotError::Resource(Error::Arithmetic {
            operation: "size semantic text validation decode",
        })
    })?;
    let temporary_bytes = raw_bytes.checked_add(maximum_decoded).ok_or({
        SemanticSnapshotError::Resource(Error::Arithmetic {
            operation: "size semantic text validation buffers",
        })
    })?;
    let decoded =
        ByteCount::from_usize(maximum_decoded).map_err(SemanticSnapshotError::Resource)?;
    budget
        .charge_decoded_value(decoded)
        .map_err(SemanticSnapshotError::Resource)?;
    budget
        .charge_allocation(
            ByteCount::from_usize(temporary_bytes).map_err(SemanticSnapshotError::Resource)?,
        )
        .map_err(SemanticSnapshotError::Resource)
}

fn receipt_validation_nodes(receipt: &CoverageReceipt) -> Result<usize, SemanticSnapshotError> {
    receipt.branches.len().checked_add(1).ok_or({
        SemanticSnapshotError::Resource(Error::Arithmetic {
            operation: "count coverage receipt validation nodes",
        })
    })
}

fn precharge_validation_nodes(
    budget: &mut ResourceBudget,
    count: usize,
) -> Result<(), SemanticSnapshotError> {
    let count = u64::try_from(count).map_err(|_| {
        SemanticSnapshotError::Resource(Error::IntegerConversion {
            value: count as u128,
            target: "u64",
        })
    })?;
    let bytes = count.checked_mul(VALIDATION_NODE_BYTES).ok_or({
        SemanticSnapshotError::Resource(Error::Arithmetic {
            operation: "size semantic artifact validation nodes",
        })
    })?;
    budget
        .charge_allocation(ByteCount::new(bytes))
        .map_err(SemanticSnapshotError::Resource)
}
