//! Canonical JSON emission for protocol 1.2 documents.

use crate::canonical_json::JsonWriter;
use crate::{
    CoverageReceipt, SemanticColumn, SemanticIndex, SemanticProtocolError, SemanticSnapshot,
    SemanticSnapshotError, SemanticTable, TableKind,
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

pub(super) fn write_snapshot_budgeted(
    snapshot: &SemanticSnapshot,
    budget: &mut ResourceBudget,
) -> Result<Vec<u8>, SemanticSnapshotError> {
    let validation =
        canonicalization_allocation_bound(snapshot).map_err(SemanticSnapshotError::Resource)?;
    budget
        .charge_allocation(validation)
        .map_err(SemanticSnapshotError::Resource)?;
    snapshot.validate()?;
    let bound = snapshot_allocation_bound(snapshot).map_err(SemanticSnapshotError::Resource)?;
    let mut writer = reserved_writer(bound, budget)?;
    write_snapshot_into(&mut writer, snapshot)?;
    writer.into_bytes().ok_or_else(|| {
        SemanticSnapshotError::Protocol(SemanticProtocolError::InvalidModel {
            path: "$".to_owned(),
            reason: "canonical JSON writer did not retain output",
        })
    })
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
    writer.key(&mut first, "ordering");
    ordering(writer);
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
    let mut writer = JsonWriter::new();
    write_receipt_into(&mut writer, receipt);
    writer
        .into_bytes()
        .ok_or_else(|| SemanticProtocolError::InvalidModel {
            path: "$".to_owned(),
            reason: "canonical JSON writer did not retain output",
        })
}

pub(super) fn write_receipt_budgeted(
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

fn write_receipt_into(writer: &mut JsonWriter, receipt: &CoverageReceipt) {
    writer.bytes.push(b'{');
    let mut first = true;
    writer.key(&mut first, "allocated_set_sha256");
    writer.string(receipt.allocated_set_sha256.as_str());
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

fn properties_allocation_bound(values: &crate::PropertyMap) -> Option<usize> {
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
