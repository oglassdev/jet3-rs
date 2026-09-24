//! Public-library Items/Notes lifecycle candidates for the practical v1 milestone.
use jet3::{
    ByteCount, ColumnSpec, ColumnType, DatabaseReader, FileSource, IndexColumnSpec, IndexKind,
    IndexSpec, InlineLongValue, LongValue, LongValueChunkValue, ResourceBudget, ResourceLimits,
    RowDelete, RowLocator, RowUpdate, RowValue, TableDefinition, TableRows, TableSpec,
    TextCodePage, UpdateError, ValueKind,
};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    num::NonZeroU8,
    path::Path,
};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
const MEMO: &[u8; 4096] = &[b'n'; 4096];
const REPLACEMENTS: &[i32] = &[0, 1, 20, 21, 99, 199, 201, 219];
const DELETIONS: &[i32] = &[0, 1, 20, 21, 99, 100, 199, 219];

#[derive(Clone, Debug, PartialEq, Eq)]
struct Item {
    id: i32,
    name: String,
    price: Option<i64>,
    active: bool,
}
impl Item {
    fn initial(id: i32) -> Self {
        Self {
            id,
            name: format!("item-{id:04}-{}", "x".repeat(70)),
            price: (id % 3 != 0).then_some(i64::from(id) * 10003 - 123456),
            active: id % 2 == 0,
        }
    }
    fn replaced(id: i32) -> Self {
        Self {
            id,
            name: if id % 2 == 0 {
                format!("renamed-{id:04}")
            } else {
                format!("edit-{id:04}-{}", "y".repeat(70))
            },
            price: if id % 3 == 0 {
                Some(7654321 + i64::from(id))
            } else {
                None
            },
            active: id % 2 != 0,
        }
    }
    fn values(&self) -> [RowValue<'_>; 4] {
        [
            RowValue::Long(self.id),
            RowValue::Text(self.name.as_bytes()),
            self.price
                .map_or(RowValue::Null, |scaled| RowValue::Currency { scaled }),
            RowValue::Boolean(self.active),
        ]
    }
    fn json(&self) -> String {
        format!(
            "[{},{},{},{}]",
            self.id,
            quote(&self.name),
            self.price.map_or("null".into(), |n| n.to_string()),
            self.active
        )
    }
}
fn quote(text: &str) -> String {
    format!(
        "\"{}\"",
        text.replace('\\', "\\\\")
            .replace('"', "\\\"")
            .replace('\n', "\\n")
            .replace('\r', "\\r")
            .replace('\t', "\\t")
    )
}
fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}
fn definition(
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
fn items(
    db: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    b: &mut ResourceBudget,
) -> Result<BTreeMap<i32, (Item, RowLocator)>> {
    let mut output = BTreeMap::new();
    let mut cursor = db.rows(table, b)?;
    while let Some(mut row) = cursor.next_row()? {
        let id = match row
            .value(table.columns()[0].ordinal(), TextCodePage::Windows1252)?
            .ok_or("Id field")?
            .kind()
        {
            ValueKind::Long(n) => *n,
            _ => return Err("Id type".into()),
        };
        let name = match row
            .value(table.columns()[1].ordinal(), TextCodePage::Windows1252)?
            .ok_or("Name field")?
            .kind()
        {
            ValueKind::Text(text) => std::str::from_utf8(text.raw_bytes())?.to_owned(),
            _ => return Err("Name type".into()),
        };
        let price = match row
            .value(table.columns()[2].ordinal(), TextCodePage::Windows1252)?
            .ok_or("Price field")?
            .kind()
        {
            ValueKind::Null => None,
            ValueKind::Currency(n) => Some(n.scaled()),
            _ => return Err("Price type".into()),
        };
        let active = match row
            .value(table.columns()[3].ordinal(), TextCodePage::Windows1252)?
            .ok_or("Active field")?
            .kind()
        {
            ValueKind::Boolean(n) => *n,
            _ => return Err("Active type".into()),
        };
        if output
            .insert(
                id,
                (
                    Item {
                        id,
                        name,
                        price,
                        active,
                    },
                    row.locator(),
                ),
            )
            .is_some()
        {
            return Err("duplicate read Id".into());
        }
    }
    Ok(output)
}
fn locate(path: &Path, id: i32) -> Result<RowLocator> {
    let mut b = budget();
    let mut db = DatabaseReader::open(path, &mut b)?;
    let table = definition(&mut db, b"Items", &mut b)?;
    items(&mut db, &table, &mut b)?
        .get(&id)
        .map(|(_, row)| *row)
        .ok_or_else(|| "Id absent".into())
}
fn create(path: &Path) -> Result<()> {
    let width = NonZeroU8::new(80).ok_or("width")?;
    jet3::create_database(
        path,
        &jet3::DatabaseSpec {
            tables: &[
                TableRows {
                    table: TableSpec {
                        validation: jet3::TableValidation::NONE,
                        name: b"Items",
                        columns: &[
                            ColumnSpec::new(b"Id", ColumnType::Long),
                            ColumnSpec::new(b"Name", ColumnType::Text { max_len: width }),
                            ColumnSpec::new(b"Price", ColumnType::Currency),
                            ColumnSpec::new(b"Active", ColumnType::Boolean),
                        ],
                        indexes: &[IndexSpec {
                            name: b"ById",
                            fields: &[IndexColumnSpec::ascending(0)],
                            kind: IndexKind::Primary,
                        }],
                    },
                    rows: &[],
                },
                TableRows {
                    table: TableSpec {
                        validation: jet3::TableValidation::NONE,
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
            ..jet3::DatabaseSpec::default()
        },
        &mut budget(),
    )?;
    Ok(())
}
fn notes(
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
        return Err("Notes row inventory".into());
    }
    Ok(format!(
        "[{}]",
        result.into_values().collect::<Vec<_>>().join(",")
    ))
}
fn schema(table: &TableDefinition) -> Result<String> {
    Ok(format!(
        "[{}]",
        table
            .columns()
            .iter()
            .map(|column| Ok(format!(
                "[{},{},{}]",
                quote(std::str::from_utf8(column.name().raw_bytes())?),
                column.physical_type().raw(),
                column.size()
            )))
            .collect::<Result<Vec<_>>>()?
            .join(",")
    ))
}
fn key(id: i32) -> Vec<u8> {
    let mut bytes = vec![0x7f];
    bytes.extend(((id as u32) ^ 0x80000000).to_be_bytes());
    bytes
}
fn save(
    directory: &Path,
    case: &str,
    phase: &str,
    source: &Path,
    model: &BTreeMap<i32, Item>,
) -> Result<()> {
    let path = directory.join(format!("{case}-{phase}.mdb"));
    fs::copy(source, &path)?;
    let mut b = budget();
    let mut db = DatabaseReader::open(&path, &mut b)?;
    let table = definition(&mut db, b"Items", &mut b)?;
    let read = items(&mut db, &table, &mut b)?;
    let actual = read
        .iter()
        .map(|(id, (row, _))| (*id, row.clone()))
        .collect::<BTreeMap<_, _>>();
    if actual != *model {
        return Err(format!("{case}/{phase}: model differs").into());
    }
    let tree = db.index_tree(&table, 0, &mut b)?;
    let expected = model
        .keys()
        .map(|id| (key(*id), read[id].1))
        .collect::<Vec<_>>();
    if tree.entries().len() != expected.len()
        || !tree
            .entries()
            .iter()
            .zip(&expected)
            .all(|(e, (key, row))| e.key().raw_bytes() == key && e.row() == *row)
    {
        return Err("complete index key/row mismatch".into());
    }
    let rows = format!(
        "[{}]",
        actual
            .values()
            .map(Item::json)
            .collect::<Vec<_>>()
            .join(",")
    );
    let seeks = (-1..=260)
        .map(|query| {
            let hit = tree
                .entries()
                .iter()
                .find(|entry| entry.key().raw_bytes() == key(query));
            let found = hit
                .map(|entry| {
                    read.values()
                        .find(|(_, locator)| *locator == entry.row())
                        .ok_or("index row absent")
                })
                .transpose()?;
            Ok(format!(
                "{{\"query\":{query},\"row\":{}}}",
                found.map_or("null".into(), |(row, _)| row.json())
            ))
        })
        .collect::<Result<Vec<_>>>()?
        .join(",");
    let notes_table = definition(&mut db, b"Notes", &mut b)?;
    let notes = notes(&mut db, &notes_table, &mut b)?;
    let pages = read
        .values()
        .map(|(_, locator)| locator.page().get())
        .collect::<BTreeSet<_>>();
    let list = |values: Vec<u64>| {
        values
            .iter()
            .map(u64::to_string)
            .collect::<Vec<_>>()
            .join(",")
    };
    let depth = tree
        .nodes()
        .iter()
        .map(|n| n.depth())
        .max()
        .ok_or("missing index root")?;
    if case == "lifecycle" && phase == "loaded" && (pages.len() < 2 || depth != 2) {
        return Err("data/leaf boundaries not crossed".into());
    }
    fs::write(
        directory.join(format!("{case}-{phase}.snapshot.json")),
        format!(
            "{{\"items\":{rows},\"notes\":{notes},\"schema\":{{\"Items\":{},\"Notes\":{}}},\"traversal\":{rows},\"seek\":[{seeks}],\"layout\":{{\"pages\":{},\"data_pages\":[{}],\"index_pages\":[{}],\"depth\":{depth}}}}}\n",
            schema(&table)?,
            schema(&notes_table)?,
            db.geometry().page_count(),
            list(pages.into_iter().collect()),
            list(tree.nodes().iter().map(|n| n.page().get()).collect())
        ),
    )?;
    Ok(())
}
fn insert(path: &Path, model: &mut BTreeMap<i32, Item>, row: Item) -> Result<RowLocator> {
    let locator = jet3::insert_row(path, b"Items", &row.values(), &mut budget())?;
    if model.insert(row.id, row).is_some() {
        return Err("duplicate recipe Id".into());
    }
    Ok(locator)
}
fn delete(path: &Path, model: &mut BTreeMap<i32, Item>, id: i32) -> Result<()> {
    jet3::delete_row(
        path,
        RowDelete {
            table: b"Items",
            row: locate(path, id)?,
        },
        &mut budget(),
    )?;
    model.remove(&id).ok_or("missing recipe Id")?;
    Ok(())
}
fn refusals(directory: &Path, source: &Path) -> Result<()> {
    let mut receipts = Vec::new();
    for case in ["duplicate", "wrong-value", "malformed-source", "resource"] {
        let before = directory.join(format!("refusal-{case}-before.mdb"));
        fs::copy(source, &before)?;
        if case == "malformed-source" {
            let mut b = budget();
            let mut db = DatabaseReader::open(&before, &mut b)?;
            let table = definition(&mut db, b"Items", &mut b)?;
            let mut bytes = fs::read(&before)?;
            let offset = table.physical_indexes()[0].root().get() as usize * 2048 + 4;
            bytes[offset..offset + 4].fill(0);
            fs::write(&before, bytes)?;
        }
        let after = directory.join(format!("refusal-{case}-after.mdb"));
        fs::copy(&before, &after)?;
        let error = match case {
            "wrong-value" => jet3::update_row(
                &after,
                RowUpdate {
                    table: b"Items",
                    row: locate(&after, 37)?,
                    values: &[
                        RowValue::Long(37),
                        RowValue::Text(b"wrong"),
                        RowValue::Text(b"not Currency"),
                        RowValue::Boolean(false),
                    ],
                },
                &mut budget(),
            )
            .err()
            .ok_or("refusal was accepted")?,
            "resource" => jet3::insert_row(
                &after,
                b"Items",
                &Item::initial(1000).values(),
                &mut ResourceBudget::new(
                    ResourceLimits::default().with_max_encoded_bytes(ByteCount::new(0)),
                ),
            )
            .err()
            .ok_or("refusal was accepted")?,
            _ => jet3::insert_row(
                &after,
                b"Items",
                &Item::initial(if case == "duplicate" { 37 } else { 1000 }).values(),
                &mut budget(),
            )
            .err()
            .ok_or("refusal was accepted")?,
        };
        let expected = match case {
            "duplicate" => matches!(error, UpdateError::Unsupported("duplicate unique key")),
            "wrong-value" => matches!(
                error,
                UpdateError::Encoding(jet3::RowWriteError::TypeMismatch { .. })
            ),
            "malformed-source" => matches!(
                error,
                UpdateError::Mismatch("mapped index page kind or owner")
            ),
            _ => {
                let mut cause: &dyn std::error::Error = &error;
                while let Some(next) = cause.source() {
                    cause = next;
                }
                matches!(
                    cause.downcast_ref::<jet3::Error>(),
                    Some(jet3::Error::ResourceLimitExceeded { .. })
                )
            }
        };
        if !expected
            || matches!(error, UpdateError::Publish(_))
            || fs::read(&before)? != fs::read(&after)?
        {
            return Err(format!("refusal {case}: {error}").into());
        }
        receipts.push(format!(
            "{{\"name\":{},\"error\":{},\"preserved\":true,\"expected_error\":true}}",
            quote(case),
            quote(&format!("{error:?}"))
        ));
    }
    fs::write(
        directory.join("refusals.json"),
        format!("[{}]\n", receipts.join(",")),
    )?;
    Ok(())
}
fn main() -> Result<()> {
    let directory = std::env::args()
        .nth(1)
        .ok_or("usage: practical_lifecycle_candidate NEW_DIRECTORY")?;
    let directory = Path::new(&directory);
    fs::create_dir(directory)?;
    let path = directory.join("working.mdb");
    let mut model = BTreeMap::new();
    create(&path)?;
    save(directory, "lifecycle", "empty", &path, &model)?;
    for id in 0..220 {
        insert(&path, &mut model, Item::initial(id))?;
    }
    save(directory, "lifecycle", "loaded", &path, &model)?;
    refusals(directory, &path)?;
    for &id in REPLACEMENTS {
        let row = Item::replaced(id);
        jet3::update_row(
            &path,
            RowUpdate {
                table: b"Items",
                row: locate(&path, id)?,
                values: &row.values(),
            },
            &mut budget(),
        )?;
        model.insert(id, row);
    }
    save(directory, "lifecycle", "replaced", &path, &model)?;
    for &id in DELETIONS {
        delete(&path, &mut model, id)?;
    }
    save(directory, "lifecycle", "deleted", &path, &model)?;
    for id in 220..260 {
        insert(&path, &mut model, Item::initial(id))?;
    }
    save(directory, "lifecycle", "reinserted", &path, &model)?;
    fs::remove_file(&path)?;
    model.clear();
    create(&path)?;
    for id in 0..3 {
        insert(&path, &mut model, Item::initial(id))?;
    }
    let released = locate(&path, 0)?.page();
    let bytes = fs::metadata(&path)?.len();
    save(directory, "reuse", "loaded", &path, &model)?;
    for id in [2, 0, 1] {
        delete(&path, &mut model, id)?;
    }
    save(directory, "reuse", "empty", &path, &model)?;
    let inserted = insert(&path, &mut model, Item::initial(42))?;
    if inserted.page() != released || fs::metadata(&path)?.len() != bytes {
        return Err("released page was not reused without EOF growth".into());
    }
    save(directory, "reuse", "reinserted", &path, &model)?;
    fs::remove_file(path)?;
    Ok(())
}
