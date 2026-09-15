//! Complete public reader receipts and canonical length-prefixed row streams.
use super::*;

fn table_snapshot(
    db: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    output: &Path,
    b: &mut ResourceBudget,
) -> Result<String> {
    let fields = table
        .columns()
        .iter()
        .map(|c| {
            Ok(format!(
                "[{},{},{},{}]",
                quote(std::str::from_utf8(c.name().raw_bytes())?),
                c.physical_type().raw(),
                c.size(),
                c.raw_class_flags()
            ))
        })
        .collect::<Result<Vec<_>>>()?
        .join(",");
    let mut rows = BTreeMap::new();
    let mut locators = BTreeMap::new();
    {
        let mut cursor = db.rows(table, b)?;
        while let Some(mut row) = cursor.next_row()? {
            let locator = row.locator();
            let mut cells = Vec::new();
            let mut external = Vec::new();
            let mut id = None;
            for (position, column) in table.columns().iter().enumerate() {
                let value = row
                    .value(column.ordinal(), TextCodePage::Windows1252)?
                    .ok_or("missing column")?;
                cells.push(match value.kind() {
                    ValueKind::Null => None,
                    ValueKind::Long(n) => {
                        if position == 0 {
                            id = Some(*n);
                        }
                        Some(n.to_le_bytes().to_vec())
                    }
                    ValueKind::Text(text) => Some(text.raw_bytes().to_vec()),
                    ValueKind::LongValue(LongValue::Inline { value, .. }) => Some(match value {
                        InlineLongValue::Text(text) => text.raw_bytes().to_vec(),
                        InlineLongValue::Binary(bytes) => bytes.to_vec(),
                        _ => return Err("unknown inline payload kind".into()),
                    }),
                    ValueKind::LongValue(LongValue::External(reference)) => {
                        external.push((position, *reference));
                        None
                    }
                    _ => return Err("unknown fixture scalar".into()),
                });
            }
            for (position, reference) in external {
                let mut stream = cursor.long_value(reference)?;
                let mut bytes = Vec::new();
                while let Some(chunk) = stream.next_chunk()? {
                    bytes.extend_from_slice(match chunk.value() {
                        LongValueChunkValue::Text(text) => text.raw_bytes(),
                        LongValueChunkValue::Binary(bytes) => bytes,
                        _ => return Err("unknown payload chunk".into()),
                    });
                }
                cells[position] = Some(bytes);
            }
            let id = id.ok_or("missing primary Id")?;
            let mut record = Vec::new();
            for cell in cells {
                feed(&mut record, cell.as_deref());
            }
            if rows.insert(id, record).is_some() {
                return Err("duplicate reader Id".into());
            }
            locators.insert(id, (locator.page().get(), locator.slot()));
        }
    }
    let mut stream = BufWriter::new(fs::File::create(output)?);
    for bytes in rows.values() {
        stream.write_all(bytes)?;
    }
    stream.flush()?;
    let locators = locators
        .into_iter()
        .map(|(id, (page, slot))| format!("[{id},{page},{slot}]"))
        .collect::<Vec<_>>()
        .join(",");
    let mut indexes = Vec::new();
    for logical in table.indexes() {
        let physical = &table.physical_indexes()[usize::from(logical.physical_index())];
        let keys = physical
            .fields()
            .iter()
            .map(|f| {
                format!(
                    "[{},{}]",
                    f.column().get(),
                    u8::from(f.direction() == jet3::IndexDirection::Ascending)
                )
            })
            .collect::<Vec<_>>()
            .join(",");
        let metadata = format!(
            "{{\"logical\":{},\"prefix\":{},\"root\":{},\"flags\":{},\"map\":[{},{}],\"keys\":[{}]}}",
            quote(&hex(logical.raw_record())),
            quote(&hex(physical.sourced_prefix())),
            physical.root().get(),
            physical.raw_flags(),
            physical.usage_map().page().get(),
            physical.usage_map().row(),
            keys
        );
        let tree = db.index_tree(table, logical.physical_index(), b)?;
        let entries = tree
            .entries()
            .iter()
            .map(|e| {
                format!(
                    "[{},{},{}]",
                    quote(&hex(e.key().raw_bytes())),
                    e.row().page().get(),
                    e.row().slot()
                )
            })
            .collect::<Vec<_>>()
            .join(",");
        let nodes = tree
            .nodes()
            .iter()
            .map(|n| n.page().get().to_string())
            .collect::<Vec<_>>()
            .join(",");
        let depth = tree
            .nodes()
            .iter()
            .map(|n| n.depth())
            .max()
            .ok_or("missing index root")?;
        indexes.push(format!(
            "{{\"name\":{},\"metadata\":{metadata},\"entries\":[{entries}],\"nodes\":[{nodes}],\"depth\":{depth}}}",
            quote(std::str::from_utf8(logical.name().raw_bytes())?)
        ));
    }
    Ok(format!(
        "{{\"fields\":[{fields}],\"count\":{},\"declared_count\":{},\"locators\":[{locators}],\"stream\":{},\"indexes\":[{}]}}",
        rows.len(),
        table.row_count(),
        quote(
            output
                .file_name()
                .and_then(|s| s.to_str())
                .ok_or("stream filename")?
        ),
        indexes.join(",")
    ))
}
pub(super) fn snapshot(path: &Path, output: &Path) -> Result<()> {
    let mut b = budget();
    let mut db = DatabaseReader::open(path, &mut b)?;
    let mut tables = Vec::new();
    let stem = output
        .file_stem()
        .and_then(|s| s.to_str())
        .ok_or("receipt filename")?;
    for name in [b"Items".as_slice(), b"Notes"] {
        let table = definition(&mut db, name, &mut b)?;
        let name = std::str::from_utf8(name)?;
        let stream = output.with_file_name(format!("{stem}.{name}.rows.bin"));
        tables.push(format!(
            "{}:{}",
            quote(name),
            table_snapshot(&mut db, &table, &stream, &mut b)?
        ));
    }
    fs::write(
        output,
        format!(
            "{{\"pages\":{},\"tables\":{{{}}}}}\n",
            db.geometry().page_count(),
            tables.join(",")
        ),
    )?;
    Ok(())
}
