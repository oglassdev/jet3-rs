//! Canonical JSON emission for protocol 1.2 documents.

use crate::canonical_json::JsonWriter;
use crate::{
    CoverageReceipt, SemanticColumn, SemanticIndex, SemanticProtocolError, SemanticSnapshot,
    SemanticSnapshotError, SemanticTable, TableKind, TypedValue,
};
use jet3::{ByteCount, Error, ResourceBudget};

pub(super) fn write_snapshot(
    snapshot: &SemanticSnapshot,
) -> Result<Vec<u8>, SemanticProtocolError> {
    let mut writer = JsonWriter::new();
    write_snapshot_into(&mut writer, snapshot)?;
    Ok(writer.bytes)
}

pub(super) fn write_snapshot_budgeted(
    snapshot: &SemanticSnapshot,
    budget: &mut ResourceBudget,
) -> Result<Vec<u8>, SemanticSnapshotError> {
    snapshot.validate()?;
    let bound = snapshot_allocation_bound(snapshot).map_err(SemanticSnapshotError::Resource)?;
    let mut writer = reserved_writer(bound, budget)?;
    write_snapshot_into(&mut writer, snapshot)?;
    Ok(writer.bytes)
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
    properties(
        writer,
        &snapshot.database_properties,
        "$.database_properties",
    )?;
    writer.key(&mut first, "database_sha256");
    writer.string(snapshot.database_sha256.as_str());
    writer.key(&mut first, "document_type");
    writer.string("canonical_semantic_snapshot");
    writer.key(&mut first, "ordering");
    ordering(writer);
    writer.key(&mut first, "producer");
    writer.producer(&snapshot.producer);
    writer.key(&mut first, "producer_extensions");
    properties(
        writer,
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
    tables(writer, &snapshot.tables)?;
    writer.bytes.push(b'}');
    writer.bytes.push(b'\n');
    Ok(())
}

pub(super) fn write_receipt(receipt: &CoverageReceipt) -> Result<Vec<u8>, SemanticProtocolError> {
    let mut writer = JsonWriter::new();
    write_receipt_into(&mut writer, receipt);
    Ok(writer.bytes)
}

pub(super) fn write_receipt_budgeted(
    receipt: &CoverageReceipt,
    budget: &mut ResourceBudget,
) -> Result<Vec<u8>, SemanticSnapshotError> {
    let bound = receipt_allocation_bound(receipt).map_err(SemanticSnapshotError::Resource)?;
    let mut writer = reserved_writer(bound, budget)?;
    write_receipt_into(&mut writer, receipt);
    Ok(writer.bytes)
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
    let mut bound = AllocationBound::new(4_096);
    bound.string(snapshot.scenario_id.as_str())?;
    bound.string(snapshot.producer.source_revision())?;
    bound.properties(&snapshot.database_properties)?;
    bound.properties(&snapshot.producer_extensions)?;
    for table in &snapshot.tables {
        bound.object(1_024)?;
        bound.string(&table.name)?;
        bound.properties(&table.properties)?;
        for column in &table.columns {
            bound.object(512)?;
            bound.string(&column.name)?;
            bound.string(&column.dao_type)?;
            bound.properties(&column.properties)?;
        }
        for index in &table.indexes {
            bound.object(512)?;
            bound.string(&index.name)?;
            bound.properties(&index.properties)?;
            for field in &index.fields {
                bound.object(128)?;
                bound.string(&field.name)?;
            }
        }
        for row in &table.rows {
            bound.object(512)?;
            bound.properties(&row.values)?;
        }
    }
    for relationship in &snapshot.relationships {
        bound.object(768)?;
        bound.string(&relationship.name)?;
        bound.string(&relationship.table)?;
        bound.string(&relationship.foreign_table)?;
        bound.properties(&relationship.properties)?;
        for field in &relationship.fields {
            bound.object(256)?;
            bound.string(&field.field)?;
            bound.string(&field.foreign_field)?;
        }
    }
    for raw in &snapshot.raw_preservation {
        bound.object(512)?;
        bound.string(&raw.semantic_path)?;
        bound.string(raw.raw_hex.as_str())?;
        bound.string(&raw.purpose)?;
    }
    Ok(ByteCount::new(bound.bytes))
}

fn receipt_allocation_bound(receipt: &CoverageReceipt) -> Result<ByteCount, Error> {
    let mut bound = AllocationBound::new(4_096);
    bound.string(receipt.scenario_id.as_str())?;
    bound.string(&receipt.source_revision)?;
    for branch in &receipt.branches {
        bound.string(branch)?;
        bound.object(64)?;
    }
    Ok(ByteCount::new(bound.bytes))
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
    Ok(JsonWriter { bytes })
}

struct AllocationBound {
    bytes: u64,
}

impl AllocationBound {
    const fn new(bytes: u64) -> Self {
        Self { bytes }
    }

    fn object(&mut self, bytes: u64) -> Result<(), Error> {
        self.bytes = self.bytes.checked_add(bytes).ok_or(Error::Arithmetic {
            operation: "size canonical semantic JSON allocation",
        })?;
        Ok(())
    }

    fn string(&mut self, value: &str) -> Result<(), Error> {
        let bytes = u64::try_from(value.len()).map_err(|_| Error::IntegerConversion {
            value: value.len() as u128,
            target: "u64",
        })?;
        self.object(bytes.checked_mul(6).ok_or(Error::Arithmetic {
            operation: "size escaped canonical JSON string",
        })?)?;
        self.object(32)
    }

    fn properties(&mut self, properties: &crate::PropertyMap) -> Result<(), Error> {
        self.object(256)?;
        for (key, value) in properties {
            self.string(key)?;
            self.typed_value(value)?;
        }
        Ok(())
    }

    fn typed_value(&mut self, value: &TypedValue) -> Result<(), Error> {
        self.object(512)?;
        match value {
            TypedValue::Text { value, .. } | TypedValue::Memo { value, .. } => {
                self.string(value)?;
            }
            TypedValue::Binary { value, .. } | TypedValue::Ole { value, .. } => {
                self.string(value.as_str())?;
            }
            TypedValue::Guid { value, .. } => self.string(value.as_str())?,
            TypedValue::Decimal { value, .. } | TypedValue::Currency { value, .. } => {
                self.string(value.as_str())?;
            }
            TypedValue::DateTime { value, .. } => self.string(value.as_str())?,
            _ => {}
        }
        let raw = match value {
            TypedValue::Null { raw_hex }
            | TypedValue::Boolean { raw_hex, .. }
            | TypedValue::Byte { raw_hex, .. }
            | TypedValue::Integer { raw_hex, .. }
            | TypedValue::Long { raw_hex, .. }
            | TypedValue::Single { raw_hex, .. }
            | TypedValue::Double { raw_hex, .. }
            | TypedValue::Decimal { raw_hex, .. }
            | TypedValue::Currency { raw_hex, .. }
            | TypedValue::DateTime { raw_hex, .. }
            | TypedValue::Text { raw_hex, .. }
            | TypedValue::Binary { raw_hex, .. }
            | TypedValue::Guid { raw_hex, .. }
            | TypedValue::Memo { raw_hex, .. }
            | TypedValue::Ole { raw_hex, .. } => raw_hex.as_ref(),
        };
        if let Some(raw) = raw {
            self.string(raw.as_str())?;
        }
        Ok(())
    }
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
