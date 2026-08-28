//! Canonical JSON emission for protocol 1.2 documents.

mod output_budget;
mod validation_budget;

use crate::canonical_json::JsonWriter;
use crate::{
    CoverageReceipt, CoverageReceiptOutcome, SemanticColumn, SemanticIndex, SemanticProtocolError,
    SemanticSnapshot, SemanticSnapshotError, SemanticSnapshotOutcome, SemanticTable, TableKind,
};
use jet3::{ByteCount, Error, ResourceBudget};

pub(super) fn canonicalization_allocation_bound(
    snapshot: &SemanticSnapshot,
) -> Result<ByteCount, Error> {
    output_budget::canonicalization_allocation_bound(snapshot)
}

pub(super) fn snapshot_allocation_bound(snapshot: &SemanticSnapshot) -> Result<ByteCount, Error> {
    output_budget::measure_snapshot_allocation(snapshot)
}

pub(super) fn validate_outcome_budgeted(
    outcome: &SemanticSnapshotOutcome,
    budget: &mut ResourceBudget,
) -> Result<(), SemanticSnapshotError> {
    validation_budget::validate_outcome_budgeted(outcome, budget)
}

pub(super) fn validate_receipt_budgeted(
    receipt: &CoverageReceipt,
    budget: &mut ResourceBudget,
) -> Result<(), SemanticSnapshotError> {
    validation_budget::validate_receipt_budgeted(receipt, budget)
}

pub(super) fn write_outcome_budgeted_validated(
    outcome: &SemanticSnapshotOutcome,
    budget: &mut ResourceBudget,
) -> Result<Vec<u8>, SemanticSnapshotError> {
    output_budget::write_outcome_budgeted_validated(outcome, budget)
}

pub(super) fn write_receipt_budgeted_validated(
    receipt: &CoverageReceipt,
    budget: &mut ResourceBudget,
) -> Result<Vec<u8>, SemanticSnapshotError> {
    output_budget::write_receipt_budgeted_validated(receipt, budget)
}

pub(super) fn write_snapshot(
    snapshot: &SemanticSnapshot,
) -> Result<Vec<u8>, SemanticProtocolError> {
    snapshot.validate()?;
    let mut writer = JsonWriter::new();
    write_snapshot_into(&mut writer, snapshot)?;
    writer
        .into_bytes()
        .ok_or_else(|| SemanticProtocolError::InvalidModel {
            path: "$".to_owned(),
            reason: "canonical JSON writer did not retain output",
        })
}

pub(super) fn write_outcome(
    outcome: &SemanticSnapshotOutcome,
) -> Result<Vec<u8>, SemanticProtocolError> {
    outcome.validate()?;
    let mut writer = JsonWriter::new();
    write_outcome_into(&mut writer, outcome)?;
    writer
        .into_bytes()
        .ok_or_else(|| SemanticProtocolError::InvalidModel {
            path: "$".to_owned(),
            reason: "canonical JSON writer did not retain output",
        })
}

fn write_outcome_into(
    writer: &mut JsonWriter,
    outcome: &SemanticSnapshotOutcome,
) -> Result<(), SemanticProtocolError> {
    match outcome {
        SemanticSnapshotOutcome::Success(snapshot) => write_snapshot_into(writer, snapshot),
        SemanticSnapshotOutcome::OpeningFailure(failure) => {
            writer.bytes.push(b'{');
            let mut first = true;
            writer.key(&mut first, "comparison_projection");
            writer.bytes.extend_from_slice(br#"["/producer"]"#);
            writer.key(&mut first, "database_sha256");
            writer.string(failure.database_sha256.as_str());
            writer.key(&mut first, "document_type");
            writer.string("canonical_semantic_snapshot");
            writer.key(&mut first, "error_class");
            writer.string(failure.error_class.as_str());
            writer.key(&mut first, "outcome");
            writer.string("opening_failure");
            writer.key(&mut first, "producer");
            writer.producer(&failure.producer);
            writer.key(&mut first, "protocol_version");
            writer.string("1.2.0");
            writer.key(&mut first, "scenario_id");
            writer.string(failure.scenario_id.as_str());
            writer.bytes.push(b'}');
            writer.bytes.push(b'\n');
            Ok(())
        }
    }
}

fn write_snapshot_into(
    writer: &mut JsonWriter,
    snapshot: &SemanticSnapshot,
) -> Result<(), SemanticProtocolError> {
    writer.bytes.push(b'{');
    let mut first = true;
    writer.key(&mut first, "comparison_projection");
    writer
        .bytes
        .extend_from_slice(br#"["/producer","/producer_extensions"]"#);
    writer.key(&mut first, "database_properties");
    properties(writer, &snapshot.database_properties)?;
    writer.key(&mut first, "database_sha256");
    writer.string(snapshot.database_sha256.as_str());
    writer.key(&mut first, "document_type");
    writer.string("canonical_semantic_snapshot");
    writer.key(&mut first, "error_class");
    writer.bytes.extend_from_slice(b"null");
    writer.key(&mut first, "ordering");
    ordering(writer);
    writer.key(&mut first, "outcome");
    writer.string("success");
    writer.key(&mut first, "producer");
    writer.producer(&snapshot.producer);
    writer.key(&mut first, "producer_extensions");
    properties(writer, &snapshot.producer_extensions)?;
    writer.key(&mut first, "protocol_version");
    writer.string("1.2.0");
    writer.key(&mut first, "raw_preservation");
    writer.raw_preservation(&snapshot.raw_preservation);
    writer.key(&mut first, "relationships");
    writer.relationships(&snapshot.relationships)?;
    writer.key(&mut first, "scenario_id");
    writer.string(snapshot.scenario_id.as_str());
    writer.key(&mut first, "tables");
    tables(writer, &snapshot.tables)?;
    writer.bytes.push(b'}');
    writer.bytes.push(b'\n');
    Ok(())
}

pub(super) fn write_receipt(receipt: &CoverageReceipt) -> Result<Vec<u8>, SemanticProtocolError> {
    receipt.validate()?;
    let mut writer = JsonWriter::new();
    write_receipt_into(&mut writer, receipt);
    writer
        .into_bytes()
        .ok_or_else(|| SemanticProtocolError::InvalidModel {
            path: "$".to_owned(),
            reason: "canonical JSON writer did not retain output",
        })
}

fn write_receipt_into(writer: &mut JsonWriter, receipt: &CoverageReceipt) {
    writer.bytes.push(b'{');
    let mut first = true;
    writer.key(&mut first, "allocated_set_sha256");
    match &receipt.outcome {
        CoverageReceiptOutcome::Success {
            allocated_set_sha256,
        } => writer.string(allocated_set_sha256.as_str()),
        CoverageReceiptOutcome::OpeningFailure { .. } => {
            writer.bytes.extend_from_slice(b"null");
        }
    }
    writer.key(&mut first, "branches");
    writer.bytes.push(b'[');
    for (index, branch) in receipt.branches.iter().enumerate() {
        writer.comma(index);
        writer.string(branch);
    }
    writer.bytes.push(b']');
    writer.key(&mut first, "database_sha256");
    writer.string(receipt.database_sha256.as_str());
    writer.key(&mut first, "document_type");
    writer.string("rust_coverage_receipt");
    writer.key(&mut first, "error_class");
    match &receipt.outcome {
        CoverageReceiptOutcome::Success { .. } => writer.bytes.extend_from_slice(b"null"),
        CoverageReceiptOutcome::OpeningFailure { error_class } => {
            writer.string(error_class.as_str());
        }
    }
    writer.key(&mut first, "outcome");
    match receipt.outcome {
        CoverageReceiptOutcome::Success { .. } => writer.string("success"),
        CoverageReceiptOutcome::OpeningFailure { .. } => writer.string("opening_failure"),
    }
    writer.key(&mut first, "protocol_version");
    writer.string("1.2.0");
    writer.key(&mut first, "scenario_id");
    writer.string(receipt.scenario_id.as_str());
    writer.key(&mut first, "source_revision");
    writer.string(&receipt.source_revision);
    writer.bytes.push(b'}');
    writer.bytes.push(b'\n');
}

#[cfg(test)]
pub(super) fn write_properties(
    values: &crate::PropertyMap,
    path: &str,
) -> Result<Vec<u8>, SemanticProtocolError> {
    let capacity =
        properties_allocation_bound(values).ok_or_else(|| SemanticProtocolError::InvalidModel {
            path: path.to_owned(),
            reason: "canonical property JSON length is not representable",
        })?;
    let mut bytes = Vec::new();
    bytes
        .try_reserve_exact(capacity)
        .map_err(|_| SemanticProtocolError::InvalidModel {
            path: path.to_owned(),
            reason: "canonical property JSON allocation failed",
        })?;
    let mut writer = JsonWriter::with_output(bytes);
    super::semantic_protocol::validate_property_map(values, path)?;
    properties(&mut writer, values)?;
    writer
        .into_bytes()
        .ok_or_else(|| SemanticProtocolError::InvalidModel {
            path: path.to_owned(),
            reason: "canonical JSON writer did not retain output",
        })
}

pub(super) fn write_row_properties(
    values: &crate::PropertyMap,
    path: &str,
) -> Result<Vec<u8>, SemanticProtocolError> {
    let capacity = row_properties_allocation_bound(values).ok_or_else(|| {
        SemanticProtocolError::InvalidModel {
            path: path.to_owned(),
            reason: "canonical row JSON length is not representable",
        }
    })?;
    let mut bytes = Vec::new();
    bytes
        .try_reserve_exact(capacity)
        .map_err(|_| SemanticProtocolError::InvalidModel {
            path: path.to_owned(),
            reason: "canonical row JSON allocation failed",
        })?;
    let mut writer = JsonWriter::with_output(bytes);
    super::semantic_protocol::validate_property_map(values, path)?;
    properties(&mut writer, values)?;
    writer.bytes.push(b'\n');
    writer
        .into_bytes()
        .ok_or_else(|| SemanticProtocolError::InvalidModel {
            path: path.to_owned(),
            reason: "canonical JSON writer did not retain output",
        })
}

pub(super) fn write_row_properties_budgeted(
    values: &crate::PropertyMap,
    path: &str,
    budget: &mut ResourceBudget,
) -> Result<Vec<u8>, SemanticSnapshotError> {
    super::semantic_protocol::validate_property_map(values, path)?;
    output_budget::write_row_properties_budgeted_validated(values, path, budget)
}

#[cfg(test)]
pub(crate) fn properties_allocation_bound(values: &crate::PropertyMap) -> Option<usize> {
    let mut writer = JsonWriter::counting();
    writer.properties(values).ok()?;
    writer.counted_len()
}

pub(crate) fn row_properties_allocation_bound(values: &crate::PropertyMap) -> Option<usize> {
    let mut writer = JsonWriter::counting();
    writer.properties(values).ok()?;
    writer.bytes.push(b'\n');
    writer.counted_len()
}

fn properties(
    writer: &mut JsonWriter,
    values: &crate::PropertyMap,
) -> Result<(), SemanticProtocolError> {
    writer.properties(values).map_err(Into::into)
}

fn ordering(writer: &mut JsonWriter) {
    writer.bytes.push(b'{');
    let mut first = true;
    for (key, value) in [
        ("columns", "ordinal_ascending"),
        ("indexes", "name_codepoint_ascending"),
        ("object_keys", "unicode_codepoint_ascending"),
        ("objects", "name_codepoint_ascending"),
        ("relationships", "name_codepoint_ascending"),
        ("rows", "values_sha256_then_duplicate_ordinal"),
    ] {
        writer.key(&mut first, key);
        writer.string(value);
    }
    writer.bytes.push(b'}');
}

fn tables(writer: &mut JsonWriter, tables: &[SemanticTable]) -> Result<(), SemanticProtocolError> {
    writer.bytes.push(b'[');
    for (index, table) in tables.iter().enumerate() {
        writer.comma(index);
        table_json(writer, table)?;
    }
    writer.bytes.push(b']');
    Ok(())
}

fn table_json(writer: &mut JsonWriter, table: &SemanticTable) -> Result<(), SemanticProtocolError> {
    writer.bytes.push(b'{');
    let mut first = true;
    writer.key(&mut first, "attributes");
    writer.integer(table.attributes);
    writer.key(&mut first, "columns");
    columns(writer, &table.columns)?;
    writer.key(&mut first, "indexes");
    indexes(writer, &table.indexes)?;
    writer.key(&mut first, "kind");
    writer.string(match table.kind {
        TableKind::User => "user",
        TableKind::System => "system",
        TableKind::Linked => "linked",
    });
    writer.key(&mut first, "name");
    writer.string(&table.name);
    writer.key(&mut first, "properties");
    properties(writer, &table.properties)?;
    writer.key(&mut first, "rows");
    writer.bytes.push(b'[');
    for (index, row) in table.rows.iter().enumerate() {
        writer.comma(index);
        writer.bytes.push(b'{');
        let mut first = true;
        writer.key(&mut first, "canonical_key");
        writer.string(row.canonical_key.as_str());
        writer.key(&mut first, "duplicate_ordinal");
        writer.unsigned(row.duplicate_ordinal);
        writer.key(&mut first, "values");
        properties(writer, &row.values)?;
        writer.bytes.push(b'}');
    }
    writer.bytes.push(b']');
    writer.bytes.push(b'}');
    Ok(())
}

fn columns(
    writer: &mut JsonWriter,
    columns: &[SemanticColumn],
) -> Result<(), SemanticProtocolError> {
    writer.bytes.push(b'[');
    for (index, column) in columns.iter().enumerate() {
        writer.comma(index);
        writer.bytes.push(b'{');
        let mut first = true;
        writer.key(&mut first, "attributes");
        writer.integer(column.attributes);
        writer.key(&mut first, "auto_increment");
        writer.boolean(column.auto_increment);
        writer.key(&mut first, "dao_type");
        writer.string(&column.dao_type);
        writer.key(&mut first, "name");
        writer.string(&column.name);
        writer.key(&mut first, "ordinal");
        writer.unsigned(column.ordinal);
        writer.key(&mut first, "properties");
        properties(writer, &column.properties)?;
        writer.key(&mut first, "size");
        if let Some(size) = column.size {
            writer.unsigned(size);
        } else {
            writer.bytes.extend_from_slice(b"null");
        }
        writer.bytes.push(b'}');
    }
    writer.bytes.push(b']');
    Ok(())
}

fn indexes(
    writer: &mut JsonWriter,
    indexes: &[SemanticIndex],
) -> Result<(), SemanticProtocolError> {
    writer.bytes.push(b'[');
    for (index, value) in indexes.iter().enumerate() {
        writer.comma(index);
        writer.bytes.push(b'{');
        let mut first = true;
        writer.key(&mut first, "fields");
        writer.bytes.push(b'[');
        for (field_index, field) in value.fields.iter().enumerate() {
            writer.comma(field_index);
            writer.bytes.push(b'{');
            let mut first = true;
            writer.key(&mut first, "descending");
            writer.boolean(field.descending);
            writer.key(&mut first, "name");
            writer.string(&field.name);
            writer.bytes.push(b'}');
        }
        writer.bytes.push(b']');
        writer.key(&mut first, "name");
        writer.string(&value.name);
        writer.key(&mut first, "primary");
        writer.boolean(value.primary);
        writer.key(&mut first, "properties");
        properties(writer, &value.properties)?;
        writer.key(&mut first, "required");
        writer.boolean(value.required);
        writer.key(&mut first, "unique");
        writer.boolean(value.unique);
        writer.bytes.push(b'}');
    }
    writer.bytes.push(b']');
    Ok(())
}
