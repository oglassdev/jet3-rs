//! Canonical JSON emission for protocol 1.2 documents.

use crate::canonical_json::JsonWriter;
use crate::{
    CoverageReceipt, CoverageReceiptOutcome, SemanticColumn, SemanticIndex, SemanticProtocolError,
    SemanticSnapshot, SemanticSnapshotError, SemanticSnapshotOutcome, SemanticTable, TableKind,
    TypedValue,
};
use jet3::{ByteCount, Error, ResourceBudget};
use std::mem::size_of;

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

pub(super) fn validate_outcome_budgeted(
    outcome: &SemanticSnapshotOutcome,
    budget: &mut ResourceBudget,
) -> Result<(), SemanticSnapshotError> {
    precharge_outcome_validation(outcome, budget)?;
    outcome.validate()?;
    Ok(())
}

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

pub(super) fn snapshot_allocation_bound(snapshot: &SemanticSnapshot) -> Result<ByteCount, Error> {
    let mut writer = JsonWriter::counting();
    write_snapshot_into(&mut writer, snapshot).map_err(|_| Error::Arithmetic {
        operation: "measure canonical semantic snapshot JSON",
    })?;
    counted_bytes(&writer, "measure canonical semantic snapshot JSON")
}

fn outcome_allocation_bound(outcome: &SemanticSnapshotOutcome) -> Result<ByteCount, Error> {
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
    let mut bytes = snapshot_allocation_bound(snapshot)?.get();
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

fn receipt_allocation_bound(receipt: &CoverageReceipt) -> Result<ByteCount, Error> {
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
    budget
        .charge_allocation(bound)
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

pub(crate) fn properties_allocation_bound(values: &crate::PropertyMap) -> Option<usize> {
    let mut writer = JsonWriter::counting();
    writer.properties(values).ok()?;
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
