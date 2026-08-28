//! Canonical JSON emission for protocol 1.2 documents.

use crate::canonical_json::JsonWriter;
use crate::{
    CoverageReceipt, SemanticColumn, SemanticIndex, SemanticProtocolError, SemanticSnapshot,
    SemanticTable, TableKind,
};

pub(super) fn write_snapshot(
    snapshot: &SemanticSnapshot,
) -> Result<Vec<u8>, SemanticProtocolError> {
    let mut writer = JsonWriter::new();
    writer.bytes.push(b'{');
    let mut first = true;
    writer.key(&mut first, "comparison_projection");
    writer
        .bytes
        .extend_from_slice(br#"["/producer","/producer_extensions"]"#);
    writer.key(&mut first, "database_properties");
    properties(
        &mut writer,
        &snapshot.database_properties,
        "$.database_properties",
    )?;
    writer.key(&mut first, "database_sha256");
    writer.string(snapshot.database_sha256.as_str());
    writer.key(&mut first, "document_type");
    writer.string("canonical_semantic_snapshot");
    writer.key(&mut first, "ordering");
    ordering(&mut writer);
    writer.key(&mut first, "producer");
    writer.producer(&snapshot.producer);
    writer.key(&mut first, "producer_extensions");
    properties(
        &mut writer,
        &snapshot.producer_extensions,
        "$.producer_extensions",
    )?;
    writer.key(&mut first, "protocol_version");
    writer.string("1.2.0");
    writer.key(&mut first, "raw_preservation");
    writer.raw_preservation(&snapshot.raw_preservation);
    writer.key(&mut first, "relationships");
    for (index, relationship) in snapshot.relationships.iter().enumerate() {
        super::semantic_protocol::validate_property_map(
            &relationship.properties,
            &format!("$.relationships[{index}].properties"),
        )?;
    }
    writer.relationships(&snapshot.relationships)?;
    writer.key(&mut first, "scenario_id");
    writer.string(snapshot.scenario_id.as_str());
    writer.key(&mut first, "tables");
    tables(&mut writer, &snapshot.tables)?;
    writer.bytes.push(b'}');
    writer.bytes.push(b'\n');
    Ok(writer.bytes)
}

pub(super) fn write_receipt(receipt: &CoverageReceipt) -> Result<Vec<u8>, SemanticProtocolError> {
    let mut writer = JsonWriter::new();
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
    Ok(writer.bytes)
}

pub(super) fn write_properties(
    values: &crate::PropertyMap,
    path: &str,
) -> Result<Vec<u8>, SemanticProtocolError> {
    let mut writer = JsonWriter::new();
    properties(&mut writer, values, path)?;
    Ok(writer.bytes)
}

fn properties(
    writer: &mut JsonWriter,
    values: &crate::PropertyMap,
    path: &str,
) -> Result<(), SemanticProtocolError> {
    super::semantic_protocol::validate_property_map(values, path)?;
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
        table_json(writer, table, index)?;
    }
    writer.bytes.push(b']');
    Ok(())
}

fn table_json(
    writer: &mut JsonWriter,
    table: &SemanticTable,
    table_index: usize,
) -> Result<(), SemanticProtocolError> {
    let path = format!("$.tables[{table_index}]");
    writer.bytes.push(b'{');
    let mut first = true;
    writer.key(&mut first, "attributes");
    writer.integer(table.attributes);
    writer.key(&mut first, "columns");
    columns(writer, &table.columns, &path)?;
    writer.key(&mut first, "indexes");
    indexes(writer, &table.indexes, &path)?;
    writer.key(&mut first, "kind");
    writer.string(match table.kind {
        TableKind::User => "user",
        TableKind::System => "system",
        TableKind::Linked => "linked",
    });
    writer.key(&mut first, "name");
    writer.string(&table.name);
    writer.key(&mut first, "properties");
    properties(writer, &table.properties, &format!("{path}.properties"))?;
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
        properties(writer, &row.values, &format!("{path}.rows[{index}].values"))?;
        writer.bytes.push(b'}');
    }
    writer.bytes.push(b']');
    writer.bytes.push(b'}');
    Ok(())
}

fn columns(
    writer: &mut JsonWriter,
    columns: &[SemanticColumn],
    path: &str,
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
        properties(
            writer,
            &column.properties,
            &format!("{path}.columns[{index}].properties"),
        )?;
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
    path: &str,
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
        properties(
            writer,
            &value.properties,
            &format!("{path}.indexes[{index}].properties"),
        )?;
        writer.key(&mut first, "required");
        writer.boolean(value.required);
        writer.key(&mut first, "unique");
        writer.boolean(value.unique);
        writer.bytes.push(b'}');
    }
    writer.bytes.push(b']');
    Ok(())
}
