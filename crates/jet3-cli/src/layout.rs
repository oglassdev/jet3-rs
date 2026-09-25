//! `jet3-cli inspect --layout`: the public reader's physical view of tables.

use jet3::{
    DatabaseReader, FileSource, InlineLongValue, LongValue, LongValueChunkValue, ResourceBudget,
    TableDefinition, TableDefinitionKind, TextCodePage, ValueKind,
};
use serde_json::{Value, json};

use crate::inspect::hex_string;

/// User tables (or the one named table) with every row locator, stored value
/// and long-value reference, and every index definition and tree.
pub(crate) fn document(
    database: &mut DatabaseReader<FileSource>,
    budget: &mut ResourceBudget,
    table: Option<&str>,
    code_page: TextCodePage,
) -> Result<Value, String> {
    let mut roots = Vec::new();
    let mut cursor = database.catalog(budget).map_err(|e| e.to_string())?;
    while let Some(record) = cursor.next_record().map_err(|e| e.to_string())? {
        if let Some(root) = record.table_definition() {
            roots.push((record.name().raw_bytes().to_vec(), root));
        }
    }
    drop(cursor);
    let mut tables = Vec::new();
    for (name, root) in roots {
        let decoded = crate::inspect::decoded_name(&name, code_page);
        let definition = database
            .table_definition(root, budget)
            .map_err(|e| format!("table {}: {e}", root.get()))?;
        let selected = match table {
            Some(wanted) => decoded.as_deref() == Some(wanted),
            None => definition.kind() == TableDefinitionKind::User,
        };
        if selected {
            let mut entry = table_layout(database, budget, &definition, code_page)
                .map_err(|e| format!("table {}: {e}", root.get()))?;
            entry["name"] = crate::inspect::name_json(&name, code_page);
            tables.push(entry);
        }
    }
    if table.is_some() && tables.is_empty() {
        return Err("table not found".to_owned());
    }
    Ok(json!({
        "ok": true,
        "page_count": database.geometry().page_count(),
        "tables": tables,
    }))
}

fn table_layout(
    database: &mut DatabaseReader<FileSource>,
    budget: &mut ResourceBudget,
    table: &TableDefinition,
    code_page: TextCodePage,
) -> Result<Value, Box<dyn std::error::Error>> {
    let columns: Vec<Value> = table
        .columns()
        .iter()
        .map(|column| {
            json!({
                "name": crate::inspect::name_json(column.name().raw_bytes(), code_page),
                "type": column.physical_type().raw(),
                "size": column.size(),
                "auto_increment": column.auto_increment(),
                "class_flags": column.raw_class_flags(),
            })
        })
        .collect();
    let mut rows = Vec::new();
    let mut cursor = database.rows(table, budget)?;
    while let Some(mut row) = cursor.next_row()? {
        let locator = row.locator();
        let mut values = Vec::new();
        let mut references = Vec::new();
        for (position, column) in table.columns().iter().enumerate() {
            let value = row.value(column.ordinal(), code_page)?;
            values.push(match value.as_ref().map(|value| value.kind()) {
                None | Some(ValueKind::Null) => Value::Null,
                Some(ValueKind::Boolean(value)) => json!(value),
                Some(ValueKind::LongValue(LongValue::Inline { value, .. })) => {
                    json!(hex_string(match value {
                        InlineLongValue::Text(text) => text.raw_bytes(),
                        InlineLongValue::Binary(bytes) => bytes,
                        _ => return Err("unknown inline long value".into()),
                    }))
                }
                Some(ValueKind::LongValue(LongValue::External(reference))) => {
                    references.push((position, *reference));
                    Value::Null
                }
                Some(kind) => match value.as_ref().and_then(|value| value.raw_bytes()) {
                    Some(bytes) => json!(hex_string(bytes)),
                    None => return Err(format!("{kind:?} has no stored bytes").into()),
                },
            });
        }
        let mut long_values = Vec::new();
        for (position, reference) in references {
            let mut stream = cursor.long_value(reference)?;
            let mut payload = Vec::new();
            while let Some(chunk) = stream.next_chunk()? {
                payload.extend_from_slice(match chunk.value() {
                    LongValueChunkValue::Text(text) => text.raw_bytes(),
                    LongValueChunkValue::Binary(bytes) => bytes,
                    _ => return Err("unknown long value chunk".into()),
                });
            }
            values[position] = json!(hex_string(&payload));
            long_values.push(json!({
                "column": position,
                "storage": format!("{:?}", reference.storage()),
                "length": reference.length(),
                "page": reference.target().page().get(),
                "slot": reference.target().slot(),
            }));
        }
        rows.push(json!({
            "page": locator.page().get(),
            "slot": locator.slot(),
            "values": values,
            "long_values": long_values,
        }));
    }
    drop(cursor);
    let mut indexes = Vec::new();
    for logical in table.indexes() {
        let physical = &table.physical_indexes()[usize::from(logical.physical_index())];
        let tree = database.index_tree(table, logical.physical_index(), budget)?;
        indexes.push(json!({
            "name": crate::inspect::name_json(logical.name().raw_bytes(), code_page),
            "raw_record": hex_string(logical.raw_record()),
            "root": physical.root().get(),
            "raw_flags": physical.raw_flags(),
            "usage_map": [physical.usage_map().page().get(), physical.usage_map().row()],
            "fields": physical.fields().iter().map(|field| json!({
                "column": field.column().get(),
                "descending": field.direction() == jet3::IndexDirection::Descending,
            })).collect::<Vec<_>>(),
            "sourced_prefix": hex_string(physical.sourced_prefix()),
            "depth": tree.nodes().iter().map(|node| node.depth()).max(),
            "nodes": tree.nodes().iter().map(|node| node.page().get()).collect::<Vec<_>>(),
            "entries": tree.entries().iter().map(|entry| json!([
                hex_string(entry.key().raw_bytes()),
                entry.row().page().get(),
                entry.row().slot(),
            ])).collect::<Vec<_>>(),
        }));
    }
    Ok(json!({
        "root": table.root().get(),
        "row_count": table.row_count(),
        "columns": columns,
        "rows": rows,
        "indexes": indexes,
    }))
}
