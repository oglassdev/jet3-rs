//! `jet3-cli inspect`: page-by-page structural dump of one MDB for discovery.
//!
//! Every decode goes through the `jet3` reader; this module adds no format
//! knowledge of its own. Output is a diagnostic aid, not evidence.

use std::ffi::OsString;
use std::fmt::Write as _;
use std::path::PathBuf;

use jet3::{
    ByteCount, DatabaseReader, FileSource, PageKind, PageNumber, ReadLimits, ResourceBudget,
    ResourceLimits, TableDefinition, TableDefinitionKind, TableProperties, TextCodePage, ValueKind,
};
use serde_json::{Value, json};

pub(crate) const HELP: &str = "\
  jet3-cli inspect <file> [--table <name>] [--rows] [--code-page 1252|1251|1253]
  jet3-cli inspect <file> --page <number> [--hex]
  jet3-cli inspect <file> --layout [--table <name>] [--code-page 1252|1251|1253]

inspect classifies every page, lists catalog records, decodes every
catalogued table definition (system tables included), lists the pages each
table owns, and names tag-02 pages no catalog record points at. --rows also
includes every row of every decoded table. --table restricts table definitions
and rows to one exact catalog name; page and catalog diagnostics remain present.
JSON includes ok=false and issues on partial decode failure (exit 1).
--page dumps one page's classification, and --hex adds its bytes.
--layout instead reports user tables (or the --table) as the reader sees them:
column types, each row's locator, stored bytes (hex; Memo/OLE payloads read in
full) and long-value references, and each index's definition and tree entries.
";

#[derive(Debug)]
pub(crate) struct InspectCommand {
    path: PathBuf,
    page: Option<u64>,
    hex: bool,
    rows: bool,
    layout: bool,
    code_page: TextCodePage,
    table: Option<String>,
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
        layout: false,
        code_page: TextCodePage::Windows1252,
        table: None,
    };
    while let Some(option) = arguments.next() {
        if option == "--hex" {
            command.hex = true;
        } else if option == "--rows" {
            command.rows = true;
        } else if option == "--layout" {
            command.layout = true;
        } else if option == "--page" {
            let value = arguments.next().ok_or("missing_option_value")?;
            let text = value.to_str().ok_or("invalid_option_value")?;
            command.page = Some(text.parse().map_err(|_| "invalid_page")?);
        } else if option == "--table" {
            if command.table.is_some() {
                return Err("duplicate_option");
            }
            let value = arguments.next().ok_or("missing_option_value")?;
            let text = value
                .to_str()
                .filter(|text| !text.is_empty())
                .ok_or("invalid_table_name")?;
            command.table = Some(text.to_owned());
        } else if option == "--code-page" {
            let value = arguments.next().ok_or("missing_option_value")?;
            command.code_page = match value.to_str() {
                Some("1252") => TextCodePage::Windows1252,
                Some("1251") => TextCodePage::Windows1251,
                Some("1253") => TextCodePage::Windows1253,
                _ => return Err("invalid_code_page"),
            };
        } else {
            return Err("unknown_option");
        }
    }
    if command.hex && command.page.is_none() {
        return Err("hex_requires_page");
    }
    if command.page.is_some() && (command.rows || command.table.is_some()) {
        return Err("page_conflicts_with_table_or_rows");
    }
    if command.layout && (command.page.is_some() || command.rows) {
        return Err("layout_conflicts_with_page_or_rows");
    }
    Ok(command)
}

fn budget() -> ResourceBudget {
    let read = ReadLimits::new(
        ByteCount::new(crate::DEFAULT_MAX_INPUT_BYTES),
        ByteCount::new(crate::DEFAULT_MAX_INPUT_BYTES),
        ByteCount::new(u64::MAX),
    );
    ResourceBudget::new(ResourceLimits::new(read))
}

pub(crate) struct InspectOutput {
    pub json: String,
    pub complete: bool,
}

/// Returns a diagnostic document, retaining partial results with explicit issues.
pub(crate) fn run(command: &InspectCommand) -> Result<InspectOutput, String> {
    let mut budget = budget();
    let mut database =
        DatabaseReader::open(&command.path, &mut budget).map_err(|e| e.to_string())?;

    let document = match command.page {
        Some(page) => inspect_page(&mut database, &mut budget, page, command.hex)?,
        None if command.layout => crate::layout::document(
            &mut database,
            &mut budget,
            command.table.as_deref(),
            command.code_page,
        )?,
        None => inspect_database(&mut database, &mut budget, command)?,
    };
    let mut text = serde_json::to_string_pretty(&document).map_err(|e| e.to_string())?;
    text.push('\n');
    Ok(InspectOutput {
        complete: document["ok"] == true,
        json: text,
    })
}

fn inspect_page(
    database: &mut DatabaseReader<FileSource>,
    budget: &mut ResourceBudget,
    page: u64,
    hex: bool,
) -> Result<Value, String> {
    let mut raw = [0u8; jet3::PAGE_BYTES];
    let classified = database
        .read_classified_page(PageNumber::new(page), &mut raw, budget)
        .map_err(|e| e.to_string())?;
    let mut document = json!({
        "ok": true,
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
    database: &mut DatabaseReader<FileSource>,
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
    let mut table_names = std::collections::BTreeMap::new();
    let mut cursor = database.catalog(budget).map_err(|e| e.to_string())?;
    while let Some(record) = cursor.next_record().map_err(|e| e.to_string())? {
        if let Some(root) = record.table_definition() {
            roots.push(root.get());
            table_names.insert(root.get(), record.name().raw_bytes().to_vec());
        }
        catalog.push(json!({
            "id": format!("{:?}", record.id()),
            "kind": format!("{:?}", record.kind()),
            "class": format!("{:?}", record.class()),
            "raw_flags": record.raw_flags(),
            "name": name_json(record.name().raw_bytes(), command.code_page),
            "table_definition": record.table_definition().map(PageNumber::get),
        }));
    }
    drop(cursor);

    let mut tables = Vec::new();
    let mut issues = Vec::new();
    let mut selected = false;
    for &root in &roots {
        let raw_name = &table_names[&root];
        if command
            .table
            .as_ref()
            .is_some_and(|name| decoded_name(raw_name, command.code_page).as_ref() != Some(name))
        {
            continue;
        }
        selected = true;
        let name = name_json(raw_name, command.code_page);

        let definition = match database.table_definition(PageNumber::new(root), budget) {
            Ok(definition) => definition,
            Err(error) => {
                issues.push(
                    json!({"root": root, "operation": "definition", "error": error.to_string()}),
                );
                tables.push(json!({"root": root, "name": name, "error": error.to_string()}));
                continue;
            }
        };
        let mut entry = definition_json(&definition, command.code_page);
        entry["name"] = name;
        entry["owned_pages"] = owned_pages_json(database, budget, root, &mut issues);
        if definition.kind() == TableDefinitionKind::User {
            match database.table_properties(&definition, budget) {
                Ok(properties) => {
                    entry["properties"] = properties_json(&properties, command.code_page);
                }
                Err(error) => issues.push(
                    json!({"root": root, "operation": "properties", "error": error.to_string()}),
                ),
            }
        }
        if command.rows {
            entry["rows"] = rows_json(
                database,
                budget,
                &definition,
                command.code_page,
                &mut issues,
            );
        }
        tables.push(entry);
    }
    if command.table.is_some() && !selected {
        return Err("table not found".to_owned());
    }
    let relationships = match database.relationship_catalog(budget) {
        Ok(relationships) => relationships
            .iter()
            .map(|relation| {
                let name = |raw: &[u8]| name_json(raw, command.code_page);
                json!({
                    "name": name(relation.name()),
                    "parent": name(relation.parent_table()),
                    "child": name(relation.child_table()),
                    "fields": relation.fields().iter().map(|field| json!({
                        "parent": name(field.parent()),
                        "child": name(field.child()),
                    })).collect::<Vec<_>>(),
                    "raw_attributes": relation.raw_attributes(),
                    "enforced": relation.enforced(),
                    "cascade_updates": relation.cascade_updates(),
                    "cascade_deletes": relation.cascade_deletes(),
                    "join": match relation.join() {
                        jet3::RelationshipJoin::Inner => "inner",
                        jet3::RelationshipJoin::Left => "left",
                        jet3::RelationshipJoin::Right => "right",
                        jet3::RelationshipJoin::LeftAndRight => "left_and_right",
                    },
                    "interpreted": relation.interpreted(),
                })
            })
            .collect(),
        Err(error) => {
            issues.push(json!({"operation": "relationships", "error": error.to_string()}));
            Vec::new()
        }
    };
    // Continuation pages share the definition tag, so these are listed, not decoded.
    let uncatalogued: Vec<u64> = definition_pages
        .into_iter()
        .filter(|page| !roots.contains(page))
        .collect();

    Ok(json!({
        "ok": issues.is_empty(),
        "issues": issues,
        "file": command.path.display().to_string(),
        "page_count": page_count,
        "pages": pages,
        "catalog": catalog,
        "tables": tables,
        "relationships": relationships,
        "uncatalogued_definition_pages": uncatalogued,
    }))
}

pub(crate) fn decoded_name(raw: &[u8], code_page: TextCodePage) -> Option<String> {
    code_page
        .decode(raw, &mut crate::values::budget())
        .ok()
        .map(|decoded| decoded.as_str().to_owned())
}

pub(crate) fn name_json(raw: &[u8], code_page: TextCodePage) -> Value {
    match decoded_name(raw, code_page) {
        Some(text) => json!(text),
        None => json!({"raw_hex": hex_string(raw)}),
    }
}

pub(crate) fn hex_string(bytes: &[u8]) -> String {
    bytes.iter().fold(String::new(), |mut out, byte| {
        let _ = write!(out, "{byte:02x}");
        out
    })
}

fn text_json(value: Option<&[u8]>, code_page: TextCodePage) -> Value {
    value.map_or(Value::Null, |bytes| name_json(bytes, code_page))
}

fn properties_json(properties: &TableProperties, code_page: TextCodePage) -> Value {
    let columns: Vec<Value> = properties
        .columns()
        .iter()
        .map(|column| {
            json!({
                "ordinal": column.ordinal().get(),
                "required": column.required(),
                "allow_zero_length": column.allow_zero_length(),
                "default_value": text_json(column.default_value(), code_page),
                "validation_rule": text_json(column.validation_rule(), code_page),
                "validation_text": text_json(column.validation_text(), code_page),
                "description": text_json(column.description(), code_page),
            })
        })
        .collect();
    json!({
        "validation_rule": text_json(properties.validation_rule(), code_page),
        "validation_text": text_json(properties.validation_text(), code_page),
        "columns": columns,
    })
}

fn definition_json(definition: &TableDefinition, code_page: TextCodePage) -> Value {
    let maps = definition.maps();
    let columns: Vec<Value> = definition
        .columns()
        .iter()
        .map(|column| {
            json!({
                "ordinal": column.ordinal().get(),
                "name": name_json(column.name().raw_bytes(), code_page),
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
                "name": name_json(index.name().raw_bytes(), code_page),
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
    database: &mut DatabaseReader<FileSource>,
    budget: &mut ResourceBudget,
    root: u64,
    issues: &mut Vec<Value>,
) -> Value {
    let mut owned = match database.owned_pages(PageNumber::new(root), budget) {
        Ok(owned) => owned,
        Err(error) => {
            issues.push(
                json!({"root": root, "operation": "owned_pages", "error": error.to_string()}),
            );
            return json!({"error": error.to_string()});
        }
    };
    let mut pages = Vec::new();
    loop {
        match owned.next_page() {
            Ok(Some(page)) => pages.push(json!(page.get())),
            Ok(None) => break,
            Err(error) => {
                issues.push(
                    json!({"root": root, "operation": "owned_pages", "error": error.to_string()}),
                );
                pages.push(json!({"error": error.to_string()}));
                break;
            }
        }
    }
    json!(pages)
}

fn rows_json(
    database: &mut DatabaseReader<FileSource>,
    budget: &mut ResourceBudget,
    definition: &TableDefinition,
    code_page: TextCodePage,
    issues: &mut Vec<Value>,
) -> Value {
    let mut cursor = match database.rows(definition, budget) {
        Ok(cursor) => cursor,
        Err(error) => {
            issues.push(json!({"root": definition.root().get(), "operation": "rows", "error": error.to_string()}));
            return json!({"error": error.to_string()});
        }
    };
    let mut rows = Vec::new();
    loop {
        let mut row = match cursor.next_row() {
            Ok(Some(row)) => row,
            Ok(None) => break,
            Err(error) => {
                issues.push(json!({"root": definition.root().get(), "operation": "rows", "error": error.to_string()}));
                rows.push(json!({"error": error.to_string()}));
                break;
            }
        };
        let mut fields = serde_json::Map::new();
        for column in definition.columns() {
            let key = decoded_name(column.name().raw_bytes(), code_page)
                .unwrap_or_else(|| hex_string(column.name().raw_bytes()));
            let value = match row.value(column.ordinal(), code_page) {
                Ok(Some(decoded)) => value_json(decoded.kind(), decoded.raw_bytes()),
                Ok(None) => Value::Null,
                Err(error) => {
                    issues.push(json!({"root": definition.root().get(), "operation": "value", "column": column.ordinal().get(), "error": error.to_string()}));
                    json!({"error": error.to_string()})
                }
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

#[cfg(test)]
mod name_tests {
    use super::{TextCodePage, decoded_name, json, name_json};

    #[test]
    fn metadata_names_use_the_selected_code_page_and_retain_undefined_bytes() {
        assert_eq!(
            name_json(b"Caf\xe9", TextCodePage::Windows1252),
            json!("Café")
        );
        assert_eq!(
            name_json(b"\xc0\xff", TextCodePage::Windows1251),
            json!("Ая")
        );
        assert_eq!(
            decoded_name(b"\xc0\xff", TextCodePage::Windows1251).as_deref(),
            Some("Ая")
        );
        assert_ne!(
            decoded_name(b"\xc0\xff", TextCodePage::Windows1252).as_deref(),
            Some("Ая")
        );
        assert_eq!(
            name_json(b"A\x81", TextCodePage::Windows1252),
            json!({"raw_hex": "4181"})
        );
    }
}
