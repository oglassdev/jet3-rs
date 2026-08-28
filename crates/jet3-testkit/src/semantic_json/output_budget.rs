//! Measurement and budgeted allocation for canonical semantic artifacts.

use super::{
    properties_allocation_bound, write_outcome_into, write_receipt_into, write_snapshot_into,
};
use crate::canonical_json::JsonWriter;
use crate::{
    CoverageReceipt, SemanticProtocolError, SemanticSnapshot, SemanticSnapshotError,
    SemanticSnapshotOutcome,
};
use jet3::{ByteCount, Error, ResourceBudget, ResourceLimitKind};
use std::mem::size_of;

pub(super) fn write_outcome_budgeted_validated(
    outcome: &SemanticSnapshotOutcome,
    budget: &mut ResourceBudget,
) -> Result<Vec<u8>, SemanticSnapshotError> {
    let bound = outcome_allocation_bound(outcome).map_err(SemanticSnapshotError::Resource)?;
    let mut writer = reserved_writer(bound, budget)?;
    write_outcome_into(&mut writer, outcome)?;
    writer.into_bytes().ok_or_else(|| {
        SemanticSnapshotError::Protocol(SemanticProtocolError::InvalidModel {
            path: "$".to_owned(),
            reason: "canonical JSON writer did not retain output",
        })
    })
}

pub(super) fn write_receipt_budgeted_validated(
    receipt: &CoverageReceipt,
    budget: &mut ResourceBudget,
) -> Result<Vec<u8>, SemanticSnapshotError> {
    let bound = receipt_allocation_bound(receipt).map_err(SemanticSnapshotError::Resource)?;
    let mut writer = reserved_writer(bound, budget)?;
    write_receipt_into(&mut writer, receipt);
    writer.into_bytes().ok_or_else(|| {
        SemanticSnapshotError::Protocol(SemanticProtocolError::InvalidModel {
            path: "$".to_owned(),
            reason: "canonical JSON writer did not retain output",
        })
    })
}

pub(super) fn measure_snapshot_allocation(snapshot: &SemanticSnapshot) -> Result<ByteCount, Error> {
    let mut writer = JsonWriter::counting();
    write_snapshot_into(&mut writer, snapshot).map_err(|_| Error::Arithmetic {
        operation: "measure canonical semantic snapshot JSON",
    })?;
    counted_bytes(&writer, "measure canonical semantic snapshot JSON")
}

pub(super) fn outcome_allocation_bound(
    outcome: &SemanticSnapshotOutcome,
) -> Result<ByteCount, Error> {
    let mut writer = JsonWriter::counting();
    write_outcome_into(&mut writer, outcome).map_err(|_| Error::Arithmetic {
        operation: "measure canonical semantic outcome JSON",
    })?;
    counted_bytes(&writer, "measure canonical semantic outcome JSON")
}

pub(super) fn canonicalization_allocation_bound(
    snapshot: &SemanticSnapshot,
) -> Result<ByteCount, Error> {
    const SHA256_TEXT_BYTES: u64 = 64;
    let mut bytes = super::snapshot_allocation_bound(snapshot)?.get();
    for table in &snapshot.tables {
        for row in &table.rows {
            let row_bytes = properties_allocation_bound(&row.values)
                .and_then(|value| value.checked_add(1))
                .ok_or(Error::Arithmetic {
                    operation: "size semantic row canonicalization",
                })?;
            let row_bytes = u64::try_from(row_bytes).map_err(|_| Error::IntegerConversion {
                value: row_bytes as u128,
                target: "u64",
            })?;
            bytes = bytes
                .checked_add(row_bytes.checked_mul(2).ok_or(Error::Arithmetic {
                    operation: "size semantic row canonicalization passes",
                })?)
                .and_then(|value| {
                    value.checked_add(
                        size_of::<(crate::Sha256, Vec<u8>, crate::SemanticRow)>() as u64
                    )
                })
                .and_then(|value| value.checked_add(SHA256_TEXT_BYTES * 3))
                .ok_or(Error::Arithmetic {
                    operation: "size semantic snapshot canonicalization",
                })?;
        }
    }
    Ok(ByteCount::new(bytes))
}

pub(super) fn receipt_allocation_bound(receipt: &CoverageReceipt) -> Result<ByteCount, Error> {
    let mut writer = JsonWriter::counting();
    write_receipt_into(&mut writer, receipt);
    counted_bytes(&writer, "measure canonical coverage receipt JSON")
}

fn counted_bytes(writer: &JsonWriter, operation: &'static str) -> Result<ByteCount, Error> {
    let length = writer
        .counted_len()
        .ok_or(Error::Arithmetic { operation })?;
    let length = u64::try_from(length).map_err(|_| Error::IntegerConversion {
        value: length as u128,
        target: "u64",
    })?;
    Ok(ByteCount::new(length))
}

fn reserved_writer(
    bound: ByteCount,
    budget: &mut ResourceBudget,
) -> Result<JsonWriter, SemanticSnapshotError> {
    preflight_artifact_output(bound, budget)?;
    budget
        .charge_allocation(bound)
        .map_err(SemanticSnapshotError::Resource)?;
    budget
        .charge_encoded_bytes(bound)
        .map_err(SemanticSnapshotError::Resource)?;
    let capacity = usize::try_from(bound.get()).map_err(|_| {
        SemanticSnapshotError::Resource(Error::IntegerConversion {
            value: u128::from(bound.get()),
            target: "usize",
        })
    })?;
    let mut bytes = Vec::new();
    bytes.try_reserve_exact(capacity).map_err(|_| {
        SemanticSnapshotError::Resource(Error::Io {
            operation: "reserve canonical semantic JSON",
            kind: std::io::ErrorKind::OutOfMemory,
        })
    })?;
    Ok(JsonWriter::with_output(bytes))
}

fn preflight_artifact_output(
    bound: ByteCount,
    budget: &ResourceBudget,
) -> Result<(), SemanticSnapshotError> {
    let limits = budget.limits();
    let work = bound.get().checked_mul(2).ok_or({
        SemanticSnapshotError::Resource(Error::Arithmetic {
            operation: "preflight semantic artifact output work",
        })
    })?;
    for (current, amount, maximum, kind) in [
        (
            budget.allocation_bytes().get(),
            bound.get(),
            limits.max_allocation_bytes().get(),
            ResourceLimitKind::AllocationBytes,
        ),
        (
            budget.encoded_bytes().get(),
            bound.get(),
            limits.max_encoded_bytes().get(),
            ResourceLimitKind::EncodedBytes,
        ),
        (
            budget.total_work_units(),
            work,
            limits.max_total_work_units(),
            ResourceLimitKind::TotalWorkUnits,
        ),
    ] {
        let requested = current
            .checked_add(amount)
            .ok_or(SemanticSnapshotError::Resource(Error::Arithmetic {
                operation: "preflight semantic artifact output",
            }))?;
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
