//! Apply a retained relationship lifecycle recipe through the public mutation APIs.
use jet3::{
    ColumnPhysicalType, DatabaseReader, FieldUpdate, FileSource, InlineLongValue, LongValue,
    LongValueChunkValue, ResourceBudget, ResourceLimits, RowDelete, RowLocator, RowUpdate,
    RowValue, TableDefinition, TextCodePage, ValueKind,
};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}
fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}
fn bytes(value: &Value) -> Result<Vec<u8>> {
    let text = value.as_str().ok_or("hex string required")?;
    if text.len() % 2 != 0 {
        return Err("odd hex length".into());
    }
    text.as_bytes()
        .chunks_exact(2)
        .map(|pair| Ok(u8::from_str_radix(std::str::from_utf8(pair)?, 16)?))
        .collect()
}
fn number(value: &Value) -> Result<i32> {
    Ok(i32::try_from(value.as_i64().ok_or("Long required")?)?)
}
fn definition(
    db: &mut DatabaseReader<FileSource>,
    name: &[u8],
    work: &mut ResourceBudget,
) -> Result<TableDefinition> {
    let mut root = None;
    {
        let mut catalog = db.catalog(work)?;
        while let Some(row) = catalog.next_record()? {
            if row.name().raw_bytes() == name {
                root = row.table_definition();
            }
        }
    }
    Ok(db.table_definition(root.ok_or("table absent")?, work)?)
}

fn read_rows(
    db: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    work: &mut ResourceBudget,
) -> Result<BTreeMap<i32, (Vec<Value>, RowLocator)>> {
    let mut result = BTreeMap::new();
    let mut rows = db.rows(table, work)?;
    while let Some(mut row) = rows.next_row()? {
        let mut values = Vec::new();
        let mut external = Vec::new();
        for column in table.columns() {
            let value = row
                .value(column.ordinal(), TextCodePage::Windows1252)?
                .ok_or("field absent")?;
            values.push(match value.kind() {
                ValueKind::Null => Value::Null,
                ValueKind::Long(value) => json!(value),
                ValueKind::Text(value) => json!(hex(value.raw_bytes())),
                ValueKind::LongValue(LongValue::Inline { value, .. }) => json!(hex(match value {
                    InlineLongValue::Text(value) => value.raw_bytes(),
                    InlineLongValue::Binary(value) => value,
                    _ => return Err("unsupported inline payload".into()),
                })),
                ValueKind::LongValue(LongValue::External(reference)) => {
                    external.push((values.len(), *reference));
                    Value::Null
                }
                _ => return Err("unsupported fixture value".into()),
            });
        }
        let locator = row.locator();
        for (ordinal, reference) in external {
            let mut stream = rows.long_value(reference)?;
            let mut value = Vec::new();
            while let Some(chunk) = stream.next_chunk()? {
                value.extend_from_slice(match chunk.value() {
                    LongValueChunkValue::Text(value) => value.raw_bytes(),
                    LongValueChunkValue::Binary(value) => value,
                    _ => return Err("unsupported payload chunk".into()),
                });
            }
            values[ordinal] = json!(hex(&value));
        }
        if result
            .insert(number(&values[0])?, (values, locator))
            .is_some()
        {
            return Err("duplicate Id".into());
        }
    }
    Ok(result)
}

fn apply(path: &Path, operation: &Value) -> Result<()> {
    let name = operation["table"].as_str().ok_or("table name required")?;
    let kind = operation["kind"]
        .as_str()
        .ok_or("operation kind required")?;
    let mut work = budget();
    let mut db = DatabaseReader::open(path, &mut work)?;
    let table = definition(&mut db, name.as_bytes(), &mut work)?;
    let rows = read_rows(&mut db, &table, &mut work)?;
    drop(db);
    let existing = if kind == "insert" {
        None
    } else {
        Some(
            rows.get(&number(&operation["id"])?)
                .ok_or("target absent")?,
        )
    };
    let mut values = if kind == "insert" || kind == "replace" {
        operation["row"]
            .as_array()
            .ok_or("replacement row required")?
            .clone()
    } else {
        existing.ok_or("existing row required")?.0.clone()
    };
    if kind == "field" {
        let ordinal = usize::try_from(operation["column"].as_u64().ok_or("column required")?)?;
        *values.get_mut(ordinal).ok_or("column absent")? = operation["value"].clone();
    }
    let owned = values
        .iter()
        .map(|value| {
            if value.is_string() {
                bytes(value)
            } else {
                Ok(Vec::new())
            }
        })
        .collect::<Result<Vec<_>>>()?;
    let encoded = values
        .iter()
        .zip(&owned)
        .zip(table.columns())
        .map(|((value, bytes), column)| {
            Ok(if value.is_null() {
                RowValue::Null
            } else {
                match column.physical_type() {
                    ColumnPhysicalType::Long => RowValue::Long(number(value)?),
                    ColumnPhysicalType::Text => RowValue::Text(bytes),
                    ColumnPhysicalType::Memo => RowValue::Memo(bytes),
                    ColumnPhysicalType::LongBinary => RowValue::LongBinary(bytes),
                    _ => return Err("unsupported fixture column".into()),
                }
            })
        })
        .collect::<Result<Vec<_>>>()?;
    if encoded.len() != table.columns().len() || values.len() != encoded.len() {
        return Err("row arity".into());
    }
    let mut work = if operation["limited"] == true {
        ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(0))
    } else {
        budget()
    };
    match kind {
        "insert" => {
            jet3::insert_row(path, name.as_bytes(), &encoded, &mut work)?;
        }
        "replace" => jet3::update_row(
            path,
            RowUpdate {
                table: name.as_bytes(),
                row: existing.ok_or("row absent")?.1,
                values: &encoded,
            },
            &mut work,
        )?,
        "field" => {
            let ordinal = usize::try_from(operation["column"].as_u64().ok_or("column required")?)?;
            jet3::update_field(
                path,
                FieldUpdate {
                    table: name.as_bytes(),
                    row: existing.ok_or("row absent")?.1,
                    column: table.columns()[ordinal].ordinal(),
                    value: encoded[ordinal],
                },
                &mut work,
            )?;
        }
        "delete" => jet3::delete_row(
            path,
            RowDelete {
                table: name.as_bytes(),
                row: existing.ok_or("row absent")?.1,
            },
            &mut work,
        )?,
        _ => return Err("unknown operation".into()),
    }
    Ok(())
}

fn snapshot(path: &Path) -> Result<Value> {
    let mut work = budget();
    let mut db = DatabaseReader::open(path, &mut work)?;
    let mut result = serde_json::Map::new();
    for name in ["Parent", "Child", "Notes Preserve"] {
        let table = definition(&mut db, name.as_bytes(), &mut work)?;
        let rows = read_rows(&mut db, &table, &mut work)?;
        let mut indexes = Vec::new();
        for (ordinal, physical) in table.physical_indexes().iter().enumerate() {
            let tree = db.index_tree(&table, u16::try_from(ordinal)?, &mut work)?;
            indexes.push(json!({"ordinal":ordinal,"counter":u32::from_le_bytes(physical.sourced_prefix()[4..8].try_into()?),"first_word":u32::from_le_bytes(physical.sourced_prefix()[..4].try_into()?),
                "nodes":tree.nodes().iter().map(|node| json!([node.page().get(),node.depth()])).collect::<Vec<_>>(),
                "entries":tree.entries().iter().map(|entry| json!([hex(entry.key().raw_bytes()),entry.row().page().get(),entry.row().slot()])).collect::<Vec<_>>() }));
        }
        result.insert(name.into(), json!({"root":table.root().get(),"rows":rows.values().map(|(row,locator)|json!({"values":row,"locator":[locator.page().get(),locator.slot()]})).collect::<Vec<_>>(),"indexes":indexes}));
    }
    Ok(Value::Object(result))
}

fn main() -> Result<()> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    let [source, recipe, output] = args.as_slice() else {
        return Err("usage: relationship_mutation_candidate SOURCE RECIPE NEW_DIRECTORY".into());
    };
    let recipe: Value = serde_json::from_slice(&fs::read(recipe)?)?;
    let directory = Path::new(output);
    fs::create_dir(directory)?;
    let working = directory.join("working.mdb");
    fs::copy(source, &working)?;
    for stage in recipe["stages"].as_array().ok_or("stages required")? {
        for operation in stage["operations"]
            .as_array()
            .ok_or("operations required")?
        {
            apply(&working, operation)?;
        }
        let name = stage["name"].as_str().ok_or("stage name required")?;
        fs::copy(&working, directory.join(format!("{name}.mdb")))?;
        fs::write(
            directory.join(format!("{name}.snapshot.json")),
            serde_json::to_vec(&snapshot(&working)?)?,
        )?;
    }
    let mut refusals = Vec::new();
    for request in recipe["refusals"].as_array().ok_or("refusals required")? {
        let name = request["name"].as_str().ok_or("refusal name required")?;
        let before = fs::read(source)?;
        let path = directory.join(format!("refusal-{name}.mdb"));
        fs::write(&path, &before)?;
        let error = apply(&path, &request["operation"])
            .err()
            .ok_or("refusal unexpectedly succeeded")?;
        if fs::read(&path)? != before {
            return Err("refusal changed image".into());
        }
        refusals.push(json!({"name":name,"error":format!("{error:?}"),"preserved":true}));
    }
    fs::write(
        directory.join("refusals.json"),
        serde_json::to_vec(&refusals)?,
    )?;
    fs::remove_file(working)?;
    Ok(())
}
