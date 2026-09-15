//! Public writer operations and complete reader receipts.
use super::*;

pub(super) fn definition(
    db: &mut DatabaseReader<FileSource>,
    name: &[u8],
    b: &mut ResourceBudget,
) -> Result<TableDefinition> {
    let mut root = None;
    {
        let mut catalog = db.catalog(b)?;
        while let Some(row) = catalog.next_record()? {
            if row.name().raw_bytes() == name {
                root = row.table_definition();
            }
        }
    }
    Ok(db.table_definition(root.ok_or("missing table")?, b)?)
}
pub(super) fn rows(
    db: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    b: &mut ResourceBudget,
) -> Result<BTreeMap<i32, (Row, RowLocator)>> {
    let mut result = BTreeMap::new();
    let mut cursor = db.rows(table, b)?;
    while let Some(mut row) = cursor.next_row()? {
        let locator = row.locator();
        let mut value = Vec::new();
        let mut external = Vec::new();
        for (ordinal, column) in table.columns().iter().enumerate() {
            let field = row
                .value(column.ordinal(), TextCodePage::Windows1252)?
                .ok_or("missing field")?;
            if let ValueKind::LongValue(LongValue::External(reference)) = field.kind() {
                external.push((ordinal, *reference, column.physical_type().raw() == 12));
                value.push(Scalar::Null);
            } else {
                value.push(Scalar::read(field.kind())?);
            }
        }
        for (ordinal, reference, memo) in external {
            let mut bytes = Vec::new();
            let mut stream = cursor.long_value(reference)?;
            while let Some(chunk) = stream.next_chunk()? {
                match chunk.value() {
                    LongValueChunkValue::Text(t) if memo => bytes.extend_from_slice(t.raw_bytes()),
                    LongValueChunkValue::Binary(b) if !memo => bytes.extend_from_slice(b),
                    _ => return Err("payload chunk type".into()),
                }
            }
            value[ordinal] = if memo {
                Scalar::Memo(bytes)
            } else {
                Scalar::Ole(bytes)
            };
        }
        if result.insert(id(&value)?, (value, locator)).is_some() {
            return Err("duplicate Id".into());
        }
    }
    Ok(result)
}
pub(super) fn locate(path: &Path, wanted: i32) -> Result<RowLocator> {
    let mut b = budget();
    let mut db = DatabaseReader::open(path, &mut b)?;
    let table = definition(&mut db, b"Items", &mut b)?;
    let mut cursor = db.rows(&table, &mut b)?;
    while let Some(row) = cursor.next_row()? {
        if row
            .field(table.columns()[0].ordinal())
            .and_then(|f| f.raw_bytes())
            == Some(wanted.to_le_bytes().as_slice())
        {
            return Ok(row.locator());
        }
    }
    Err("Id absent".into())
}
pub(super) fn column_ordinal(path: &Path, column: u16) -> Result<ColumnOrdinal> {
    let mut b = budget();
    let mut db = DatabaseReader::open(path, &mut b)?;
    let table = definition(&mut db, b"Items", &mut b)?;
    table
        .columns()
        .get(usize::from(column))
        .map(|c| c.ordinal())
        .ok_or_else(|| "column absent".into())
}
pub(super) fn apply(
    path: &Path,
    model: &mut BTreeMap<i32, Row>,
    operation: Operation,
) -> Result<()> {
    use Operation::*;
    match operation {
        Insert(row) => {
            jet3::insert_row(path, b"Items", &values(&row), &mut budget())?;
            if model.insert(id(&row)?, row).is_some() {
                return Err("recipe duplicate".into());
            }
        }
        Delete(id) => {
            jet3::delete_row(
                path,
                RowDelete {
                    table: b"Items",
                    row: locate(path, id)?,
                },
                &mut budget(),
            )?;
            model.remove(&id).ok_or("recipe absent")?;
        }
        Replace(old, row) => {
            jet3::update_row(
                path,
                RowUpdate {
                    table: b"Items",
                    row: locate(path, old)?,
                    values: &values(&row),
                },
                &mut budget(),
            )?;
            model.remove(&old).ok_or("recipe absent")?;
            model.insert(id(&row)?, row);
        }
        Field(old, column, value) => {
            let mut row = model.get(&old).ok_or("recipe absent")?.clone();
            let previous = row[usize::from(column)].clone();
            row[usize::from(column)] = value.clone();
            if matches!(
                previous,
                Scalar::Null
                    | Scalar::Binary(_)
                    | Scalar::Text(_)
                    | Scalar::Memo(_)
                    | Scalar::Ole(_)
            ) || matches!(
                value,
                Scalar::Null
                    | Scalar::Binary(_)
                    | Scalar::Text(_)
                    | Scalar::Memo(_)
                    | Scalar::Ole(_)
            ) {
                jet3::update_row(
                    path,
                    RowUpdate {
                        table: b"Items",
                        row: locate(path, old)?,
                        values: &values(&row),
                    },
                    &mut budget(),
                )?;
            } else {
                jet3::update_field(
                    path,
                    FieldUpdate {
                        table: b"Items",
                        row: locate(path, old)?,
                        column: column_ordinal(path, column)?,
                        value: value.value(),
                    },
                    &mut budget(),
                )?;
            }
            model.remove(&old).ok_or("recipe absent")?;
            model.insert(id(&row)?, row);
        }
    }
    Ok(())
}
pub(super) fn create(path: &Path, case: &Case, model: &BTreeMap<i32, Row>) -> Result<()> {
    let index_fields = case.index_fields();
    let values = model.values().map(|r| values(r)).collect::<Vec<_>>();
    let rows = values.iter().map(Vec::as_slice).collect::<Vec<_>>();
    jet3::create_database_with_table_rows(
        path,
        &[
            TableRows {
                table: TableSpec {
                    name: b"Items",
                    columns: &case.columns(),
                    indexes: &case.indexes(&index_fields),
                },
                rows: &rows,
            },
            TableRows {
                table: TableSpec {
                    name: b"Notes",
                    columns: &[
                        ColumnSpec::new(b"Id", ColumnType::Long),
                        ColumnSpec::new(b"Body", ColumnType::Memo),
                    ],
                    indexes: &[],
                },
                rows: &[
                    &[RowValue::Long(7), RowValue::Memo(MEMO)],
                    &[RowValue::Long(8), RowValue::Null],
                ],
            },
        ],
        &mut budget(),
    )?;
    Ok(())
}
pub(super) fn notes(
    db: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    b: &mut ResourceBudget,
) -> Result<String> {
    let mut result = BTreeMap::new();
    let mut cursor = db.rows(table, b)?;
    while let Some(mut row) = cursor.next_row()? {
        let id = match row
            .value(table.columns()[0].ordinal(), TextCodePage::Windows1252)?
            .ok_or("Notes Id")?
            .kind()
        {
            ValueKind::Long(n) => *n,
            _ => return Err("Notes Id type".into()),
        };
        let value = row
            .value(table.columns()[1].ordinal(), TextCodePage::Windows1252)?
            .ok_or("Notes Body")?;
        let mut payload = Vec::new();
        let reference = match value.kind() {
            ValueKind::Null => {
                result.insert(id, format!("[{id},null]"));
                continue;
            }
            ValueKind::LongValue(LongValue::External(reference)) => Some(*reference),
            ValueKind::LongValue(LongValue::Inline {
                value: InlineLongValue::Text(text),
                ..
            }) => {
                payload.extend_from_slice(text.raw_bytes());
                None
            }
            _ => return Err("Notes Body type".into()),
        };
        if let Some(reference) = reference {
            let mut stream = cursor.long_value(reference)?;
            while let Some(chunk) = stream.next_chunk()? {
                match chunk.value() {
                    LongValueChunkValue::Text(text) => payload.extend_from_slice(text.raw_bytes()),
                    _ => return Err("Memo chunk type".into()),
                }
            }
        }
        if id != 7 || payload != MEMO {
            return Err("Notes payload changed".into());
        }
        result.insert(
            id,
            format!("[{id},{}]", quote(std::str::from_utf8(&payload)?)),
        );
    }
    if result.keys().copied().collect::<Vec<_>>() != [7, 8] {
        return Err("Notes inventory".into());
    }
    Ok(format!(
        "[{}]",
        result.into_values().collect::<Vec<_>>().join(",")
    ))
}
pub(super) fn schema(table: &TableDefinition) -> Result<String> {
    Ok(format!(
        "[{}]",
        table
            .columns()
            .iter()
            .map(|c| Ok(format!(
                "[{},{},{}]",
                quote(std::str::from_utf8(c.name().raw_bytes())?),
                c.physical_type().raw(),
                c.size()
            )))
            .collect::<Result<Vec<_>>>()?
            .join(",")
    ))
}
fn classes(table: &TableDefinition) -> String {
    format!(
        "[{}]",
        table
            .columns()
            .iter()
            .map(|c| c.raw_class_flags().to_string())
            .collect::<Vec<_>>()
            .join(",")
    )
}
pub(super) fn save(
    directory: &Path,
    case: &Case,
    phase: &str,
    source: &Path,
    model: &BTreeMap<i32, Row>,
) -> Result<()> {
    let path = directory.join(format!("{}-{phase}.mdb", case.name()));
    fs::copy(source, &path)?;
    let mut b = budget();
    let mut db = DatabaseReader::open(&path, &mut b)?;
    let table = definition(&mut db, b"Items", &mut b)?;
    let actual = rows(&mut db, &table, &mut b)?;
    if actual
        .iter()
        .map(|(id, (r, _))| (*id, r.clone()))
        .collect::<BTreeMap<_, _>>()
        != *model
    {
        return Err(format!("{}/{phase}: reader/model mismatch", case.name()).into());
    }
    let mut indexes = Vec::new();
    for logical in table.indexes() {
        let physical = logical.physical_index();
        let tree = db.index_tree(&table, physical, &mut b)?;
        let records = tree
            .entries()
            .iter()
            .map(|e| {
                format!(
                    "[{}, {}, {}]",
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
            "{{\"name\":{},\"depth\":{depth},\"nodes\":[{nodes}],\"entries\":[{records}]}}",
            quote(std::str::from_utf8(logical.name().raw_bytes())?)
        ));
    }
    let notes_table = definition(&mut db, b"Notes", &mut b)?;
    let memo = notes(&mut db, &notes_table, &mut b)?;
    let row_json = actual
        .values()
        .map(|(r, _)| row_json(r))
        .collect::<Vec<_>>()
        .join(",");
    let locators = actual
        .iter()
        .map(|(id, (_, l))| format!("[{id},{},{}]", l.page().get(), l.slot()))
        .collect::<Vec<_>>()
        .join(",");
    let pages = actual
        .values()
        .map(|(_, l)| l.page().get())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .map(|p| p.to_string())
        .collect::<Vec<_>>()
        .join(",");
    fs::write(
        directory.join(format!("{}-{phase}.snapshot.json", case.name())),
        format!(
            "{{\"items\":[{row_json}],\"notes\":{memo},\"schema\":{{\"Items\":{},\"Notes\":{}}},\"classes\":{{\"Items\":{},\"Notes\":{}}},\"locators\":[{locators}],\"indexes\":[{}],\"pages\":{},\"data_pages\":[{pages}]}}\n",
            schema(&table)?,
            schema(&notes_table)?,
            classes(&table),
            classes(&notes_table),
            indexes.join(","),
            db.geometry().page_count()
        ),
    )?;
    Ok(())
}
