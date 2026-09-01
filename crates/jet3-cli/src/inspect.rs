//! `jet3-cli inspect`: page-by-page structural dump of one MDB for discovery.
//!
//! Every decode goes through the `jet3` reader; this module adds no format
//! knowledge of its own. Output is a diagnostic aid, not evidence.

use std::ffi::OsString;
use std::fmt::Write as _;
use std::fs;
use std::path::PathBuf;

use jet3::{
    ByteCount, DatabaseReader, PageKind, PageNumber, ReadLimits, ResourceBudget, ResourceLimits,
    SliceSource, TableDefinition, TextCodePage, ValueKind,
};
use serde_json::{Value, json};

pub(crate) const HELP: &str = "\
  jet3-cli inspect <file> [--rows] [--code-page 1252|1251]
  jet3-cli inspect <file> --page <number> [--hex]

inspect classifies every page, lists catalog records, decodes every
catalogued table definition (system tables included), lists the pages each
table owns, and names tag-02 pages no catalog record points at. --rows also
streams every row of every decoded table.
--page dumps one page's classification, and --hex adds its bytes.
";

const MAX_INPUT_BYTES: u64 = 256 * 1024 * 1024;

#[derive(Debug)]
pub(crate) struct InspectCommand {
    path: PathBuf,
    page: Option<u64>,
    hex: bool,
    rows: bool,
    code_page: TextCodePage,
}

pub(crate) fn parse_args(
    mut arguments: impl Iterator<Item = OsString>,
) -> Result<InspectCommand, &'static str> {
    let path = arguments.next().ok_or("missing_file")?;
    if path.to_string_lossy().starts_with('-') {
        return Err("missing_file");
    }
    let mut command = InspectCommand {
        path: PathBuf::from(path),
        page: None,
        hex: false,
        rows: false,
        code_page: TextCodePage::Windows1252,
    };
    while let Some(option) = arguments.next() {
        if option == "--hex" {
            command.hex = true;
        } else if option == "--rows" {
            command.rows = true;
        } else if option == "--page" {
            let value = arguments.next().ok_or("missing_option_value")?;
            let text = value.to_str().ok_or("invalid_option_value")?;
            command.page = Some(text.parse().map_err(|_| "invalid_page")?);
        } else if option == "--code-page" {
            let value = arguments.next().ok_or("missing_option_value")?;
            command.code_page = match value.to_str() {
                Some("1252") => TextCodePage::Windows1252,
                Some("1251") => TextCodePage::Windows1251,
                _ => return Err("invalid_code_page"),
            };
        } else {
            return Err("unknown_option");
        }
    }
    if command.hex && command.page.is_none() {
        return Err("hex_requires_page");
    }
    Ok(command)
}

fn budget() -> ResourceBudget {
    let read = ReadLimits::new(
        ByteCount::new(MAX_INPUT_BYTES),
        ByteCount::new(MAX_INPUT_BYTES),
        ByteCount::new(u64::MAX),
    );
    ResourceBudget::new(ResourceLimits::new(read))
}

/// Returns one pretty-printed JSON document.
pub(crate) fn run(command: &InspectCommand) -> Result<String, String> {
    let bytes = fs::read(&command.path).map_err(|error| format!("read input: {error}"))?;
    let mut budget = budget();
    let source = SliceSource::new(&bytes, budget.read_budget()).map_err(|e| e.to_string())?;
    let mut database =
        DatabaseReader::from_source(source, &mut budget).map_err(|e| e.to_string())?;

    let document = match command.page {
        Some(page) => inspect_page(&mut database, &mut budget, page, command.hex)?,
        None => inspect_database(&mut database, &mut budget, command)?,
    };
    let mut text = serde_json::to_string_pretty(&document).map_err(|e| e.to_string())?;
    text.push('\n');
    Ok(text)
}

fn inspect_page(
    database: &mut DatabaseReader<SliceSource<'_>>,
    budget: &mut ResourceBudget,
    page: u64,
    hex: bool,
) -> Result<Value, String> {
    let mut raw = [0u8; jet3::PAGE_BYTES];
    let classified = database
        .read_classified_page(PageNumber::new(page), &mut raw, budget)
        .map_err(|e| e.to_string())?;
    let mut document = json!({
        "page": page,
        "kind": format!("{:?}", classified.kind()),
        "tag": raw[0],
    });
    if hex {
        let lines: Vec<String> = raw
            .chunks(16)
            .enumerate()
            .map(|(index, chunk)| {
                let mut line = format!("{:04x}:", index * 16);
                for byte in chunk {
                    let _ = write!(line, " {byte:02x}");
                }
                line
            })
            .collect();
        document["hex"] = json!(lines);
    }
    Ok(document)
}

fn inspect_database(
    database: &mut DatabaseReader<SliceSource<'_>>,
    budget: &mut ResourceBudget,
    command: &InspectCommand,
) -> Result<Value, String> {
    let page_count = database.geometry().page_count();
    let mut pages = Vec::new();
    let mut definition_pages = Vec::new();
    let mut raw = [0u8; jet3::PAGE_BYTES];
    for number in 0..page_count {
        let classified = database
            .read_classified_page(PageNumber::new(number), &mut raw, budget)
            .map_err(|e| format!("page {number}: {e}"))?;
        if classified.kind() == PageKind::TableDefinition {
            definition_pages.push(number);
        }
        pages.push(json!({"page": number, "kind": format!("{:?}", classified.kind())}));
    }

    let mut catalog = Vec::new();
    let mut roots = Vec::new();
    let mut cursor = database.catalog(budget).map_err(|e| e.to_string())?;
    while let Some(record) = cursor.next_record().map_err(|e| e.to_string())? {
        if let Some(root) = record.table_definition() {
            roots.push(root.get());
        }
        catalog.push(json!({
            "id": format!("{:?}", record.id()),
            "kind": format!("{:?}", record.kind()),
            "class": format!("{:?}", record.class()),
            "raw_flags": record.raw_flags(),
            "name": name_json(record.name().decoded_ascii(), record.name().raw_bytes()),
            "table_definition": record.table_definition().map(PageNumber::get),
        }));
    }
    drop(cursor);

    let mut tables = Vec::new();
    for &root in &roots {
        let definition = match database.table_definition(PageNumber::new(root), budget) {
            Ok(definition) => definition,
            Err(error) => {
                tables.push(json!({"root": root, "error": error.to_string()}));
                continue;
            }
        };
        let mut entry = definition_json(&definition);
        entry["owned_pages"] = owned_pages_json(database, budget, root);
        if command.rows {
            entry["rows"] = rows_json(database, budget, &definition, command.code_page);
        }
        tables.push(entry);
    }
    // Continuation pages share the definition tag, so these are listed, not decoded.
    let uncatalogued: Vec<u64> = definition_pages
        .into_iter()
        .filter(|page| !roots.contains(page))
        .collect();

    Ok(json!({
        "file": command.path.display().to_string(),
        "page_count": page_count,
        "pages": pages,
        "catalog": catalog,
        "tables": tables,
        "uncatalogued_definition_pages": uncatalogued,
    }))
}

fn name_json(decoded: Option<&str>, raw: &[u8]) -> Value {
    match decoded {
        Some(text) => json!(text),
        None => json!({"raw_hex": hex_string(raw)}),
    }
}

fn hex_string(bytes: &[u8]) -> String {
    bytes.iter().fold(String::new(), |mut out, byte| {
        let _ = write!(out, "{byte:02x}");
        out
    })
}

fn definition_json(definition: &TableDefinition) -> Value {
    let maps = definition.maps();
    let columns: Vec<Value> = definition
        .columns()
        .iter()
        .map(|column| {
            json!({
                "ordinal": column.ordinal().get(),
                "name": name_json(column.name().decoded_ascii(), column.name().raw_bytes()),
                "physical_type": format!("{:?}", column.physical_type()),
                "storage": format!("{:?}", column.storage()),
                "size": column.size(),
                "auto_increment": column.auto_increment(),
                "raw_record": hex_string(column.raw_record()),
            })
        })
        .collect();
    let physical_indexes: Vec<Value> = definition
        .physical_indexes()
        .iter()
        .map(|index| {
            let fields: Vec<Value> = index
                .fields()
                .iter()
                .map(|field| {
                    json!({
                        "column": field.column().get(),
                        "direction": format!("{:?}", field.direction()),
                    })
                })
                .collect();
            json!({
                "root": index.root().get(),
                "unique": index.unique(),
                "required": index.required(),
                "usage_map": {
                    "page": index.usage_map().page().get(),
                    "row": index.usage_map().row(),
                },
                "fields": fields,
                "sourced_prefix": hex_string(index.sourced_prefix()),
            })
        })
        .collect();
    let indexes: Vec<Value> = definition
        .indexes()
        .iter()
        .map(|index| {
            json!({
                "name": name_json(index.name().decoded_ascii(), index.name().raw_bytes()),
                "physical_index": index.physical_index(),
                "kind": format!("{:?}", index.kind()),
                "raw_record": hex_string(index.raw_record()),
            })
        })
        .collect();
    let long_value_maps: Vec<Value> = definition
        .long_value_maps()
        .iter()
        .map(|map| {
            json!({
                "column": map.column().get(),
                "owned": {"page": map.owned().page().get(), "row": map.owned().row()},
                "available": {"page": map.available().page().get(), "row": map.available().row()},
            })
        })
        .collect();
    json!({
        "root": definition.root().get(),
        "kind": format!("{:?}", definition.kind()),
        "logical_length": definition.logical_length(),
        "raw_header": hex_string(definition.raw_header()),
        "maps": {
            "owned": {"page": maps.owned().page().get(), "row": maps.owned().row()},
            "available": {"page": maps.available().page().get(), "row": maps.available().row()},
        },
        "columns": columns,
        "physical_indexes": physical_indexes,
        "indexes": indexes,
        "long_value_maps": long_value_maps,
        "raw_suffix": hex_string(definition.raw_suffix()),
    })
}

fn owned_pages_json(
    database: &mut DatabaseReader<SliceSource<'_>>,
    budget: &mut ResourceBudget,
    root: u64,
) -> Value {
    let mut owned = match database.owned_pages(PageNumber::new(root), budget) {
        Ok(owned) => owned,
        Err(error) => return json!({"error": error.to_string()}),
    };
    let mut pages = Vec::new();
    loop {
        match owned.next_page() {
            Ok(Some(page)) => pages.push(json!(page.get())),
            Ok(None) => break,
            Err(error) => {
                pages.push(json!({"error": error.to_string()}));
                break;
            }
        }
    }
    json!(pages)
}

fn rows_json(
    database: &mut DatabaseReader<SliceSource<'_>>,
    budget: &mut ResourceBudget,
    definition: &TableDefinition,
    code_page: TextCodePage,
) -> Value {
    let mut cursor = match database.rows(definition, budget) {
        Ok(cursor) => cursor,
        Err(error) => return json!({"error": error.to_string()}),
    };
    let mut rows = Vec::new();
    loop {
        let mut row = match cursor.next_row() {
            Ok(Some(row)) => row,
            Ok(None) => break,
            Err(error) => {
                rows.push(json!({"error": error.to_string()}));
                break;
            }
        };
        let mut fields = serde_json::Map::new();
        for column in definition.columns() {
            let key = column
                .name()
                .decoded_ascii()
                .map_or_else(|| hex_string(column.name().raw_bytes()), str::to_owned);
            let value = match row.value(column.ordinal(), code_page) {
                Ok(Some(decoded)) => value_json(decoded.kind(), decoded.raw_bytes()),
                Ok(None) => Value::Null,
                Err(error) => json!({"error": error.to_string()}),
            };
            fields.insert(key, value);
        }
        rows.push(Value::Object(fields));
    }
    json!(rows)
}

fn value_json(kind: &ValueKind<'_>, raw: Option<&[u8]>) -> Value {
    match kind {
        ValueKind::Null => Value::Null,
        ValueKind::Boolean(value) => json!(value),
        ValueKind::Byte(value) => json!(value),
        ValueKind::Integer(value) => json!(value),
        ValueKind::Long(value) => json!(value),
        ValueKind::Currency(value) => json!({"scaled": value.scaled()}),
        ValueKind::Single(value) => json!(value),
        ValueKind::Double(value) => json!(value),
        ValueKind::DateTime(value) => json!({"days": value.days()}),
        ValueKind::Binary(bytes) => json!({"hex": hex_string(bytes)}),
        ValueKind::Text(text) => json!(text.as_str()),
        ValueKind::Guid(value) => json!({"hex": hex_string(&value.display_bytes())}),
        _ => json!({
            "kind": format!("{kind:?}"),
            "raw_hex": raw.map(hex_string),
        }),
    }
}
