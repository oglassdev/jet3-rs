//! Allocation precharges for semantic artifact model validation.

use super::output_budget::{
    canonicalization_allocation_bound, outcome_allocation_bound, preflight_allocation_and_work,
    preflight_canonicalization_measurement, preflight_outcome_measurement,
    preflight_receipt_measurement, receipt_allocation_bound,
};
use crate::{
    CoverageReceipt, SemanticSnapshot, SemanticSnapshotError, SemanticSnapshotOutcome, TypedValue,
};
use jet3::{ByteCount, Error, ResourceBudget, ResourceLimitKind};

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
    match outcome {
        SemanticSnapshotOutcome::Success(snapshot) => {
            preflight_snapshot_validation_shape(snapshot, budget)?;
            preflight_canonicalization_measurement(snapshot, budget, 0)?;
        }
        SemanticSnapshotOutcome::OpeningFailure(_) => {
            preflight_outcome_measurement(outcome, budget)?;
        }
    }
    precharge_outcome_validation(outcome, budget)?;
    outcome.validate()?;
    Ok(())
}

pub(super) fn validate_receipt_budgeted(
    receipt: &CoverageReceipt,
    budget: &mut ResourceBudget,
) -> Result<(), SemanticSnapshotError> {
    preflight_validation_nodes(budget, receipt_validation_nodes(receipt)?)?;
    preflight_receipt_measurement(receipt, budget)?;
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
    let (logical_bytes, raw_hex, decoded) = match value {
        TypedValue::Null { raw_hex } | TypedValue::Boolean { raw_hex, .. } => {
            (0, raw_hex, ByteCount::new(0))
        }
        TypedValue::Byte { raw_hex, .. }
        | TypedValue::Integer { raw_hex, .. }
        | TypedValue::Long { raw_hex, .. }
        | TypedValue::Single { raw_hex, .. }
        | TypedValue::Double { raw_hex, .. } => (0, raw_hex, ByteCount::new(0)),
        TypedValue::Decimal { value, raw_hex } | TypedValue::Currency { value, raw_hex } => {
            (value.as_str().len(), raw_hex, ByteCount::new(0))
        }
        TypedValue::DateTime { value, raw_hex } => {
            (value.as_str().len(), raw_hex, ByteCount::new(0))
        }
        TypedValue::Text {
            value,
            raw_hex,
            code_page,
        }
        | TypedValue::Memo {
            value,
            raw_hex,
            code_page,
        } => {
            let decoded = match (raw_hex, code_page) {
                (Some(raw), Some(code_page)) => crate::semantic_protocol::measure_text_payload(
                    value,
                    raw,
                    *code_page,
                    "$.property",
                )
                .map_err(|error| match error {
                    crate::semantic_protocol::TextPayloadValidationError::Protocol(error) => {
                        SemanticSnapshotError::Protocol(error)
                    }
                    crate::semantic_protocol::TextPayloadValidationError::Resource(error) => {
                        SemanticSnapshotError::Resource(error)
                    }
                })?,
                _ => ByteCount::new(0),
            };
            (value.len(), raw_hex, decoded)
        }
        TypedValue::Binary { value, raw_hex } | TypedValue::Ole { value, raw_hex } => {
            (value.as_str().len(), raw_hex, ByteCount::new(0))
        }
        TypedValue::Guid { value, raw_hex } => (value.as_str().len(), raw_hex, ByteCount::new(0)),
    };
    let raw_bytes = raw_hex.as_ref().map_or(0, |raw| raw.as_str().len());
    let dynamic_bytes = logical_bytes.checked_add(raw_bytes).ok_or({
        SemanticSnapshotError::Resource(Error::Arithmetic {
            operation: "size semantic validation property value",
        })
    })?;
    let allocation =
        ByteCount::from_usize(dynamic_bytes).map_err(SemanticSnapshotError::Resource)?;
    let text_prevalidated = matches!(
        value,
        TypedValue::Text {
            raw_hex: Some(_),
            code_page: Some(_),
            ..
        } | TypedValue::Memo {
            raw_hex: Some(_),
            code_page: Some(_),
            ..
        }
    );
    let validation_work = if text_prevalidated {
        decoded.get().checked_add(allocation.get()).ok_or({
            SemanticSnapshotError::Resource(Error::Arithmetic {
                operation: "size semantic text property validation work",
            })
        })?
    } else {
        0
    };
    preflight_property_value_charge(decoded, allocation, validation_work, budget)?;
    budget
        .charge_decoded_value(decoded)
        .map_err(SemanticSnapshotError::Resource)?;
    budget
        .charge_allocation(allocation)
        .map_err(SemanticSnapshotError::Resource)?;
    budget
        .charge_work_units(validation_work)
        .map_err(SemanticSnapshotError::Resource)
}

fn preflight_property_value_charge(
    decoded: ByteCount,
    allocation: ByteCount,
    validation_work: u64,
    budget: &ResourceBudget,
) -> Result<(), SemanticSnapshotError> {
    budget
        .check_decoded_value(decoded)
        .map_err(SemanticSnapshotError::Resource)?;
    let charged_work = decoded.get().checked_add(allocation.get()).ok_or({
        SemanticSnapshotError::Resource(Error::Arithmetic {
            operation: "preflight semantic property validation work",
        })
    })?;
    let work = charged_work.checked_add(validation_work).ok_or({
        SemanticSnapshotError::Resource(Error::Arithmetic {
            operation: "preflight complete semantic property validation work",
        })
    })?;
    let limits = budget.limits();
    for (current, amount, maximum, kind) in [
        (
            budget.decoded_bytes().get(),
            decoded.get(),
            limits.max_total_decoded_bytes().get(),
            ResourceLimitKind::TotalDecodedBytes,
        ),
        (
            budget.allocation_bytes().get(),
            allocation.get(),
            limits.max_allocation_bytes().get(),
            ResourceLimitKind::AllocationBytes,
        ),
        (
            budget.total_work_units(),
            work,
            limits.max_total_work_units(),
            ResourceLimitKind::TotalWorkUnits,
        ),
    ] {
        let requested = current.checked_add(amount).ok_or({
            SemanticSnapshotError::Resource(Error::Arithmetic {
                operation: "preflight semantic property validation",
            })
        })?;
        if requested > maximum {
            return Err(SemanticSnapshotError::Resource(
                Error::ResourceLimitExceeded {
                    kind,
                    requested,
                    maximum,
                },
            ));
        }
    }
    Ok(())
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

fn preflight_snapshot_validation_shape(
    snapshot: &SemanticSnapshot,
    budget: &ResourceBudget,
) -> Result<(), SemanticSnapshotError> {
    let mut nodes = 1_usize
        .checked_add(snapshot.tables.len())
        .and_then(|value| value.checked_add(snapshot.relationships.len()))
        .and_then(|value| value.checked_add(snapshot.raw_preservation.len()))
        .ok_or(SemanticSnapshotError::Resource(Error::Arithmetic {
            operation: "count semantic validation shape nodes",
        }))?;
    preflight_validation_nodes(budget, nodes)?;
    for table in &snapshot.tables {
        nodes = nodes
            .checked_add(table.columns.len())
            .and_then(|value| value.checked_add(table.indexes.len()))
            .and_then(|value| value.checked_add(table.rows.len()))
            .ok_or(SemanticSnapshotError::Resource(Error::Arithmetic {
                operation: "count semantic validation shape nodes",
            }))?;
        preflight_validation_nodes(budget, nodes)?;
        for index in &table.indexes {
            nodes =
                nodes
                    .checked_add(index.fields.len())
                    .ok_or(SemanticSnapshotError::Resource(Error::Arithmetic {
                        operation: "count semantic validation shape nodes",
                    }))?;
            preflight_validation_nodes(budget, nodes)?;
        }
    }
    for relationship in &snapshot.relationships {
        nodes =
            nodes
                .checked_add(relationship.fields.len())
                .ok_or(SemanticSnapshotError::Resource(Error::Arithmetic {
                    operation: "count semantic validation shape nodes",
                }))?;
        preflight_validation_nodes(budget, nodes)?;
    }
    Ok(())
}

fn preflight_validation_nodes(
    budget: &ResourceBudget,
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
            operation: "size semantic artifact validation shape",
        })
    })?;
    preflight_allocation_and_work(ByteCount::new(bytes), 0, budget)
}

#[cfg(test)]
mod tests {
    use super::{precharge_property_value, validate_outcome_budgeted};
    use crate::{
        HexString, Producer, ProducerKind, ScenarioId, SemanticSnapshot, SemanticSnapshotError,
        SemanticSnapshotOutcome, Sha256, TypedValue,
    };
    use jet3::{ByteCount, Error, ResourceBudget, ResourceLimitKind, ResourceLimits};

    fn text_value(
        value: &str,
        raw_hex: &str,
        code_page: u32,
    ) -> Result<TypedValue, Box<dyn std::error::Error>> {
        Ok(TypedValue::Text {
            value: value.to_owned(),
            raw_hex: Some(HexString::new(raw_hex)?),
            code_page: Some(code_page),
        })
    }

    #[test]
    fn text_validation_has_exact_atomic_caller_boundaries() -> Result<(), Box<dyn std::error::Error>>
    {
        for (property, decoded, allocation) in [
            (text_value("A", "41", 1252)?, 1_u64, 3_u64),
            (text_value("А", "c0", 1251)?, 2_u64, 4_u64),
            (
                TypedValue::Memo {
                    value: "€".to_owned(),
                    raw_hex: Some(HexString::new("80")?),
                    code_page: Some(1252),
                },
                3_u64,
                5_u64,
            ),
        ] {
            let work = decoded
                .checked_add(allocation)
                .and_then(|one_validation| one_validation.checked_mul(2))
                .ok_or("bounded work")?;
            let exact_limits = ResourceLimits::default()
                .with_max_decoded_value_bytes(ByteCount::new(decoded))
                .with_max_total_decoded_bytes(ByteCount::new(decoded))
                .with_max_allocation_bytes(ByteCount::new(allocation))
                .with_max_total_work_units(work);
            let mut exact = ResourceBudget::new(exact_limits);
            precharge_property_value(&property, &mut exact)?;
            assert_eq!(exact.decoded_bytes(), ByteCount::new(decoded));
            assert_eq!(exact.allocation_bytes(), ByteCount::new(allocation));
            assert_eq!(exact.total_work_units(), work);

            for (limits, expected_kind) in [
                (
                    exact_limits.with_max_decoded_value_bytes(ByteCount::new(decoded - 1)),
                    ResourceLimitKind::DecodedValueBytes,
                ),
                (
                    exact_limits.with_max_total_decoded_bytes(ByteCount::new(decoded - 1)),
                    ResourceLimitKind::TotalDecodedBytes,
                ),
                (
                    exact_limits.with_max_allocation_bytes(ByteCount::new(allocation - 1)),
                    ResourceLimitKind::AllocationBytes,
                ),
                (
                    exact_limits.with_max_total_work_units(work - 1),
                    ResourceLimitKind::TotalWorkUnits,
                ),
            ] {
                let original = property.clone();
                let mut rejected = ResourceBudget::new(limits);
                assert!(matches!(
                    precharge_property_value(&property, &mut rejected),
                    Err(SemanticSnapshotError::Resource(
                        Error::ResourceLimitExceeded { kind, .. }
                    )) if kind == expected_kind
                ));
                assert_eq!(property, original);
                assert_eq!(rejected.decoded_bytes(), ByteCount::new(0));
                assert_eq!(rejected.allocation_bytes(), ByteCount::new(0));
                assert_eq!(rejected.total_work_units(), 0);
            }
        }
        Ok(())
    }

    #[test]
    fn text_validation_honors_raised_limits_and_preserves_error_classes()
    -> Result<(), Box<dyn std::error::Error>> {
        const PREVIOUS_DEFAULT: usize = 16 * 1024 * 1024;
        let length = PREVIOUS_DEFAULT + 1;
        let property = text_value(&"a".repeat(length), &"61".repeat(length), 1252)?;
        let decoded = ByteCount::from_usize(length)?;
        let allocation = ByteCount::from_usize(length.checked_mul(3).ok_or("bounded allocation")?)?;
        let work = decoded
            .get()
            .checked_add(allocation.get())
            .and_then(|one_validation| one_validation.checked_mul(2))
            .ok_or("bounded work")?;
        let mut raised = ResourceBudget::new(
            ResourceLimits::default()
                .with_max_decoded_value_bytes(decoded)
                .with_max_total_decoded_bytes(decoded)
                .with_max_allocation_bytes(allocation)
                .with_max_total_work_units(work),
        );
        precharge_property_value(&property, &mut raised)?;
        assert_eq!(raised.decoded_bytes(), decoded);
        assert_eq!(raised.allocation_bytes(), allocation);
        assert_eq!(raised.total_work_units(), work);

        for (property, reason) in [
            (
                text_value("x", "81", 1252)?,
                "text raw_hex contains an undefined code-page byte",
            ),
            (
                text_value("x", "61", 1252)?,
                "text raw_hex must decode exactly to value",
            ),
        ] {
            let original = property.clone();
            let mut budget = ResourceBudget::new(ResourceLimits::default());
            assert!(matches!(
                precharge_property_value(&property, &mut budget),
                Err(SemanticSnapshotError::Protocol(
                    crate::SemanticProtocolError::InvalidModel {
                        reason: actual,
                        ..
                    }
                )) if actual == reason
            ));
            assert_eq!(property, original);
            assert_eq!(budget.decoded_bytes(), ByteCount::new(0));
            assert_eq!(budget.allocation_bytes(), ByteCount::new(0));
            assert_eq!(budget.total_work_units(), 0);
        }
        Ok(())
    }

    fn large_string_outcome() -> Result<SemanticSnapshotOutcome, Box<dyn std::error::Error>> {
        let repetitions = 4_096;
        let mut snapshot = SemanticSnapshot::new(
            ScenarioId::new("DAO-READ-ROWS-SINGLE")?,
            Producer::new(ProducerKind::Rust, "test")?,
            Sha256::new("ab".repeat(32))?,
        );
        snapshot.database_properties.insert(
            "LargeMemo".to_owned(),
            TypedValue::Memo {
                value: "\\\n".repeat(repetitions),
                raw_hex: Some(HexString::new("5c0a".repeat(repetitions))?),
                code_page: Some(1252),
            },
        );
        Ok(SemanticSnapshotOutcome::Success(snapshot))
    }

    #[test]
    fn validation_preflights_large_strings_before_exact_measurement()
    -> Result<(), Box<dyn std::error::Error>> {
        let outcome = large_string_outcome()?;
        let SemanticSnapshotOutcome::Success(snapshot) = &outcome else {
            return Err(std::io::Error::other("expected success outcome").into());
        };
        let exact = super::canonicalization_allocation_bound(snapshot)?;
        let minimum =
            super::super::output_budget::canonicalization_unescaped_bound(snapshot)?.get();
        assert!(
            exact.get() > minimum,
            "escape expansion must remain exact later"
        );

        let mut exhausted = ResourceBudget::new(
            ResourceLimits::default().with_max_allocation_bytes(ByteCount::new(0)),
        );
        let Err(error) = validate_outcome_budgeted(&outcome, &mut exhausted) else {
            return Err(std::io::Error::other(
                "zero allocation ceiling accepted the retained model",
            )
            .into());
        };
        let SemanticSnapshotError::Resource(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::AllocationBytes,
            requested: _,
            maximum: 0,
        }) = error
        else {
            return Err(std::io::Error::other("unexpected exhausted-budget error").into());
        };
        assert_eq!(exhausted.allocation_bytes(), ByteCount::new(0));
        assert_eq!(exhausted.total_work_units(), 0);

        for limits in [
            ResourceLimits::default().with_max_allocation_bytes(ByteCount::new(minimum - 1)),
            ResourceLimits::default().with_max_total_work_units(minimum - 1),
        ] {
            let original = outcome.clone();
            let mut budget = ResourceBudget::new(limits);
            assert!(matches!(
                validate_outcome_budgeted(&outcome, &mut budget),
                Err(SemanticSnapshotError::Resource(
                    Error::ResourceLimitExceeded {
                        requested,
                        maximum,
                        ..
                    }
                )) if requested == minimum && maximum == minimum - 1
            ));
            assert_eq!(outcome, original);
            assert_eq!(budget.allocation_bytes(), ByteCount::new(0));
            assert_eq!(budget.decoded_bytes(), ByteCount::new(0));
            assert_eq!(budget.total_work_units(), 0);
        }

        let mut measured = ResourceBudget::new(ResourceLimits::default());
        validate_outcome_budgeted(&outcome, &mut measured)?;
        let required_allocation = measured.allocation_bytes();
        let required_work = measured.total_work_units();
        let mut exact_budget = ResourceBudget::new(
            ResourceLimits::default()
                .with_max_allocation_bytes(required_allocation)
                .with_max_total_work_units(required_work),
        );
        validate_outcome_budgeted(&outcome, &mut exact_budget)?;
        assert_eq!(exact_budget.allocation_bytes(), required_allocation);
        assert_eq!(exact_budget.total_work_units(), required_work);
        Ok(())
    }
}
