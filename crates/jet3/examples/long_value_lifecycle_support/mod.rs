//! Public-reader receipts for the long-value lifecycle oracle.
use jet3::{
    DatabaseReader, FileSource, InlineLongValue, LongValue, LongValueChunkValue, ResourceBudget,
    ResourceLimits, TableDefinition, TextCodePage, ValueKind,
};
use std::{collections::BTreeMap, fs, path::Path};
pub type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
pub fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}
pub fn quote(value: &str) -> String {
    let mut result = String::from("\"");
    for character in value.chars() {
        match character {
            '"' => result.push_str("\\\""),
            '\\' => result.push_str("\\\\"),
            c if c.is_control() => result.push_str(&format!("\\u{:04x}", c as u32)),
            c => result.push(c),
        }
    }
    result.push('"');
    result
}
fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
pub fn definition(
    db: &mut DatabaseReader<FileSource>,
    name: &[u8],
    b: &mut ResourceBudget,
) -> Result<TableDefinition> {
    let mut root = None;
    {
        let mut catalog = db.catalog(b)?;
        while let Some(record) = catalog.next_record()? {
            if record.name().raw_bytes() == name {
                root = record.table_definition();
            }
        }
    }
    Ok(db.table_definition(root.ok_or("missing table")?, b)?)
}
fn table_snapshot(
    db: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    b: &mut ResourceBudget,
) -> Result<String> {
    let fields = table
        .columns()
        .iter()
        .map(|column| {
            Ok(format!(
                "[{},{},{},{}]",
                quote(std::str::from_utf8(column.name().raw_bytes())?),
                column.physical_type().raw(),
                column.size(),
                column.auto_increment()
            ))
        })
        .collect::<Result<Vec<_>>>()?
        .join(",");
    let mut rows = BTreeMap::new();
    {
        let mut cursor = db.rows(table, b)?;
        while let Some(mut row) = cursor.next_row()? {
            let locator = row.locator();
            let mut cells = Vec::new();
            let mut external = Vec::new();
            let mut references = Vec::new();
            let mut id = None;
            for (position, column) in table.columns().iter().enumerate() {
                let decoded = row
                    .value(column.ordinal(), TextCodePage::Windows1252)?
                    .ok_or("missing column")?;
                let value = match decoded.kind() {
                    ValueKind::Null => "null".into(),
                    ValueKind::Long(n) => {
                        if position == 0 {
                            id = Some(*n);
                        }
                        n.to_string()
                    }
                    ValueKind::LongValue(LongValue::Inline { value, .. }) => match value {
                        InlineLongValue::Text(text) => {
                            quote(std::str::from_utf8(text.raw_bytes())?)
                        }
                        InlineLongValue::Binary(bytes) => quote(&hex(bytes)),
                        _ => return Err("unknown inline kind".into()),
                    },
                    ValueKind::LongValue(LongValue::External(reference)) => {
                        references.push(format!(
                            "{{\"column\":{position},\"storage\":{},\"length\":{},\"page\":{},\"slot\":{}}}",
                            quote(&format!("{:?}", reference.storage())), reference.length(),
                            reference.target().page().get(), reference.target().slot()
                        ));
                        external.push((position, *reference));
                        "null".into()
                    }
                    _ => return Err("unexpected fixture value".into()),
                };
                cells.push(value);
            }
            for (position, reference) in external {
                let mut stream = cursor.long_value(reference)?;
                let mut bytes = Vec::new();
                while let Some(chunk) = stream.next_chunk()? {
                    bytes.extend_from_slice(match chunk.value() {
                        LongValueChunkValue::Text(text) => text.raw_bytes(),
                        LongValueChunkValue::Binary(bytes) => bytes,
                        _ => return Err("unknown chunk kind".into()),
                    });
                }
                cells[position] = if table.columns()[position].physical_type().raw() == 12 {
                    quote(std::str::from_utf8(&bytes)?)
                } else {
                    quote(&hex(&bytes))
                };
            }
            if rows
                .insert(
                    id.ok_or("missing id")?,
                    format!(
                        "{{\"values\":[{}],\"page\":{},\"slot\":{},\"references\":[{}]}}",
                        cells.join(","),
                        locator.page().get(),
                        locator.slot(),
                        references.join(",")
                    ),
                )
                .is_some()
            {
                return Err("duplicate id".into());
            }
        }
    }
    let mut indexes = Vec::new();
    for logical in table.indexes() {
        let tree = db.index_tree(table, logical.physical_index(), b)?;
        let entries = tree
            .entries()
            .iter()
            .map(|entry| {
                format!(
                    "[{}, {}, {}]",
                    quote(&hex(entry.key().raw_bytes())),
                    entry.row().page().get(),
                    entry.row().slot()
                )
            })
            .collect::<Vec<_>>()
            .join(",");
        let nodes = tree
            .nodes()
            .iter()
            .map(|node| node.page().get().to_string())
            .collect::<Vec<_>>()
            .join(",");
        let depth = tree
            .nodes()
            .iter()
            .map(|node| node.depth())
            .max()
            .ok_or("missing root")?;
        indexes.push(format!(
            "{{\"name\":{},\"entries\":[{entries}],\"nodes\":[{nodes}],\"depth\":{depth}}}",
            quote(std::str::from_utf8(logical.name().raw_bytes())?)
        ));
    }
    Ok(format!(
        "{{\"fields\":[{fields}],\"rows\":[{}],\"indexes\":[{}]}}",
        rows.into_values().collect::<Vec<_>>().join(","),
        indexes.join(",")
    ))
}
pub fn snapshot(path: &Path, output: &Path) -> Result<()> {
    let mut b = budget();
    let mut db = DatabaseReader::open(path, &mut b)?;
    let mut tables = Vec::new();
    for name in [b"Items".as_slice(), b"Notes"] {
        let table = definition(&mut db, name, &mut b)?;
        tables.push(format!(
            "{}:{}",
            quote(std::str::from_utf8(name)?),
            table_snapshot(&mut db, &table, &mut b)?
        ));
    }
    fs::write(output, format!("{{{}}}\n", tables.join(",")))?;
    Ok(())
}
