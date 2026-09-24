//! Finite EXP-0223 public index-tree mutation candidates and reader receipts.
use jet3::{
    ColumnOrdinal, ColumnSpec, ColumnType, DatabaseReader, FieldUpdate, IndexColumnSpec, IndexKind,
    IndexSpec, ResourceBudget, ResourceLimits, RowDelete, RowLocator, RowUpdate, RowValue,
    TableDefinition, TableRows, TableSpec,
};
use std::{collections::BTreeMap, fs, num::NonZeroU8, path::Path};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
const MEMO: &[u8; 4096] = &[b'n'; 4096];

#[derive(Clone)]
struct Row {
    id: i32,
    value: i32,
    text: Vec<u8>,
    binary: Vec<u8>,
}
impl Row {
    fn new(id: i32, deep: bool) -> Self {
        Self {
            id,
            value: id.wrapping_mul(17).wrapping_add(3),
            text: vec![b'x'; if deep { 1 } else { 80 }],
            binary: vec![0x11; 8],
        }
    }
    fn values(&self, deep: bool) -> Vec<RowValue<'_>> {
        if deep {
            vec![RowValue::Long(self.id), RowValue::Long(self.value)]
        } else {
            vec![
                RowValue::Long(self.id),
                RowValue::Text(&self.text),
                RowValue::Binary(&self.binary),
            ]
        }
    }
    fn json(&self, deep: bool) -> Result<String> {
        Ok(if deep {
            format!("[{},{}]", self.id, self.value)
        } else {
            format!(
                "[{},\"{}\",\"{}\"]",
                self.id,
                std::str::from_utf8(&self.text)?,
                hex(&self.binary)
            )
        })
    }
}
fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}
fn definition(
    db: &mut DatabaseReader<jet3::FileSource>,
    b: &mut ResourceBudget,
) -> Result<TableDefinition> {
    let root = {
        let mut catalog = db.catalog(b)?;
        let mut root = None;
        while let Some(row) = catalog.next_record()? {
            if row.name().raw_bytes() == b"Items" {
                root = row.table_definition();
            }
        }
        root.ok_or("Items definition missing")?
    };
    Ok(db.table_definition(root, b)?)
}
fn locate(path: &Path, id: i32) -> Result<(RowLocator, ColumnOrdinal)> {
    let mut b = budget();
    let mut db = DatabaseReader::open(path, &mut b)?;
    let table = definition(&mut db, &mut b)?;
    let mut rows = db.rows(&table, &mut b)?;
    let mut found = None;
    while let Some(row) = rows.next_row()? {
        if row
            .field(table.columns()[0].ordinal())
            .and_then(|f| f.raw_bytes())
            == Some(id.to_le_bytes().as_slice())
            && found.replace(row.locator()).is_some()
        {
            return Err("duplicate Id".into());
        }
    }
    found
        .map(|row| (row, table.columns()[0].ordinal()))
        .ok_or_else(|| "Id absent".into())
}
fn create(path: &Path, model: &BTreeMap<i32, Row>, descending: bool, deep: bool) -> Result<()> {
    let width = NonZeroU8::new(80).ok_or("column width")?;
    let columns = if deep {
        vec![
            ColumnSpec::new(b"Id", ColumnType::Long),
            ColumnSpec::new(b"Value", ColumnType::Long),
        ]
    } else {
        vec![
            ColumnSpec::new(b"Id", ColumnType::Long),
            ColumnSpec::new(b"Text", ColumnType::Text { max_len: width }),
            ColumnSpec::new(b"Bytes", ColumnType::Binary { max_len: width }),
        ]
    };
    let keys = [if descending {
        IndexColumnSpec::descending(0)
    } else {
        IndexColumnSpec::ascending(0)
    }];
    let indexes = [IndexSpec {
        name: b"ById",
        fields: &keys,
        kind: if descending {
            IndexKind::Unique
        } else {
            IndexKind::Primary
        },
    }];
    let values = model
        .values()
        .map(|row| row.values(deep))
        .collect::<Vec<_>>();
    let rows = values.iter().map(Vec::as_slice).collect::<Vec<_>>();
    jet3::create_database(
        path,
        &jet3::DatabaseSpec {
            tables: &[
                TableRows {
                    table: TableSpec {
                        validation: jet3::TableValidation::NONE,
                        name: b"Items",
                        columns: &columns,
                        indexes: &indexes,
                    },
                    rows: &rows,
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
fn save(
    directory: &Path,
    case: &str,
    phase: &str,
    source: &Path,
    model: &BTreeMap<i32, Row>,
    deep: bool,
    eof: bool,
) -> Result<()> {
    let descending = case == "descending";
    let path = directory.join(format!("{case}-{phase}.mdb"));
    fs::copy(source, &path)?;
    let mut b = budget();
    let mut db = DatabaseReader::open(&path, &mut b)?;
    let table = definition(&mut db, &mut b)?;
    let mut actual = BTreeMap::new();
    let mut locators = BTreeMap::new();
    {
        let mut cursor = db.rows(&table, &mut b)?;
        while let Some(row) = cursor.next_row()? {
            let raw = row
                .field(table.columns()[0].ordinal())
                .and_then(|f| f.raw_bytes())
                .ok_or("Id missing")?;
            let id = i32::from_le_bytes(raw.try_into()?);
            let mut read = Row::new(id, deep);
            if deep {
                read.value = i32::from_le_bytes(
                    row.field(table.columns()[1].ordinal())
                        .and_then(|f| f.raw_bytes())
                        .ok_or("Value missing")?
                        .try_into()?,
                );
            } else {
                read.text = row
                    .field(table.columns()[1].ordinal())
                    .and_then(|f| f.raw_bytes())
                    .ok_or("Text missing")?
                    .to_vec();
                read.binary = row
                    .field(table.columns()[2].ordinal())
                    .and_then(|f| f.raw_bytes())
                    .ok_or("Bytes missing")?
                    .to_vec();
            }
            if actual.insert(id, read.json(deep)?).is_some() {
                return Err("duplicate read Id".into());
            }
            locators.insert(id, row.locator());
        }
    }
    let expected = model
        .iter()
        .map(|(id, row)| Ok((*id, row.json(deep)?)))
        .collect::<Result<BTreeMap<_, _>>>()?;
    if actual != expected {
        return Err("reader rows differ from operation model".into());
    }
    let tree = db.index_tree(&table, 0, &mut b)?;
    let depth = tree
        .nodes()
        .iter()
        .map(|n| n.depth())
        .max()
        .ok_or("missing root")?;
    let wanted_depth = if model.len() <= 200 {
        1
    } else if model.len() <= 27800 {
        2
    } else {
        3
    };
    if tree.entries().len() != model.len() || depth != wanted_depth {
        return Err(format!(
            "tree count/depth: {case}/{phase} count={} depth={depth}",
            tree.entries().len()
        )
        .into());
    }
    let mut expected_keys = model
        .keys()
        .map(|id| {
            let mut key = vec![0x7f];
            let mut raw = id.to_be_bytes();
            raw[0] ^= 0x80;
            key.extend(raw);
            if descending {
                for b in &mut key {
                    *b ^= 0xff;
                }
            }
            (key, locators[id])
        })
        .collect::<Vec<_>>();
    expected_keys.sort_by(|a, b| a.0.cmp(&b.0));
    if !tree
        .entries()
        .iter()
        .zip(&expected_keys)
        .all(|(entry, (key, locator))| entry.key().raw_bytes() == key && entry.row() == *locator)
    {
        return Err("complete index key/locator mismatch".into());
    }
    fs::write(
        directory.join(format!("{case}-{phase}.rows.json")),
        format!(
            "[{}]\n",
            expected.into_values().collect::<Vec<_>>().join(",")
        ),
    )?;
    let nodes = tree
        .nodes()
        .iter()
        .map(|n| n.page().get().to_string())
        .collect::<Vec<_>>()
        .join(",");
    fs::write(
        directory.join(format!("{case}-{phase}.layout.json")),
        format!(
            "{{\"rows\":{},\"depth\":{depth},\"nodes\":[{nodes}],\"pages\":{},\"data_eof_insert\":{eof}}}\n",
            model.len(),
            db.geometry().page_count()
        ),
    )?;
    Ok(())
}
fn insert(path: &Path, model: &mut BTreeMap<i32, Row>, row: Row, deep: bool) -> Result<bool> {
    let pages = fs::metadata(path)?.len() / 2048;
    let locator = jet3::insert_row(path, b"Items", &row.values(deep), &mut budget())?;
    if model.insert(row.id, row).is_some() {
        return Err("recipe duplicate".into());
    }
    Ok(locator.page().get() == pages)
}
fn key_update(path: &Path, model: &mut BTreeMap<i32, Row>, id: i32, next: i32) -> Result<()> {
    let (locator, column) = locate(path, id)?;
    jet3::update_field(
        path,
        FieldUpdate {
            table: b"Items",
            row: locator,
            column,
            value: RowValue::Long(next),
        },
        &mut budget(),
    )?;
    let mut row = model.remove(&id).ok_or("model key")?;
    row.id = next;
    if model.insert(next, row).is_some() {
        return Err("recipe duplicate".into());
    }
    Ok(())
}
fn delete(path: &Path, model: &mut BTreeMap<i32, Row>, id: i32) -> Result<()> {
    jet3::delete_row(
        path,
        RowDelete {
            table: b"Items",
            row: locate(path, id)?.0,
        },
        &mut budget(),
    )?;
    model.remove(&id).ok_or("model delete")?;
    Ok(())
}
fn run(directory: &Path, case: &str, count: i32, descending: bool, deep: bool) -> Result<()> {
    let path = directory.join(format!("{case}-working.mdb"));
    let mut model = (0..count)
        .map(|id| (id, Row::new(id, deep)))
        .collect::<BTreeMap<_, _>>();
    create(&path, &model, descending, deep)?;
    save(directory, case, "original", &path, &model, deep, false)?;
    if case == "empty" || case == "tombstone-empty" {
        let deletes: &[(i32, &str)] = if case == "tombstone-empty" {
            &[(2, "delete-tail"), (0, "delete-first"), (1, "empty")]
        } else {
            &[(0, "empty")]
        };
        for &(id, phase) in deletes {
            delete(&path, &mut model, id)?;
            save(directory, case, phase, &path, &model, deep, false)?;
        }
        let eof = insert(&path, &mut model, Row::new(-2, deep), deep)?;
        save(directory, case, "regrown", &path, &model, deep, eof)?;
    } else {
        let added = if deep { 27800 } else { -1 };
        let eof = insert(&path, &mut model, Row::new(added, deep), deep)?;
        if !deep && !eof {
            return Err("split did not also append a data page".into());
        }
        save(directory, case, "split", &path, &model, deep, eof)?;
        key_update(&path, &mut model, added, 1000000)?;
        save(directory, case, "reordered", &path, &model, deep, false)?;
        if !deep {
            for (phase, text, binary) in [
                ("grown", vec![b'g'; 80], vec![0xab; 60]),
                ("shrunk", vec![b's'], vec![1]),
            ] {
                let mut row = model.get(&1000000).ok_or("model row")?.clone();
                row.text = text;
                row.binary = binary;
                jet3::update_row(
                    &path,
                    RowUpdate {
                        table: b"Items",
                        row: locate(&path, row.id)?.0,
                        values: &row.values(deep),
                    },
                    &mut budget(),
                )?;
                model.insert(row.id, row);
                save(directory, case, phase, &path, &model, deep, false)?;
            }
        }
        delete(&path, &mut model, 1000000)?;
        save(directory, case, "collapsed", &path, &model, deep, false)?;
        let eof = insert(&path, &mut model, Row::new(-2, deep), deep)?;
        save(directory, case, "regrown", &path, &model, deep, eof)?;
    }
    fs::remove_file(path)?;
    Ok(())
}
fn continue_dao(source: &Path, directory: &Path, case: &str) -> Result<()> {
    let deep = case == "deep";
    let mut b = budget();
    let mut db = DatabaseReader::open(source, &mut b)?;
    let table = definition(&mut db, &mut b)?;
    let mut model = BTreeMap::new();
    {
        let mut cursor = db.rows(&table, &mut b)?;
        while let Some(row) = cursor.next_row()? {
            let id = i32::from_le_bytes(
                row.field(table.columns()[0].ordinal())
                    .and_then(|f| f.raw_bytes())
                    .ok_or("Id missing")?
                    .try_into()?,
            );
            let mut value = Row::new(id, deep);
            if deep {
                value.value = i32::from_le_bytes(
                    row.field(table.columns()[1].ordinal())
                        .and_then(|f| f.raw_bytes())
                        .ok_or("Value missing")?
                        .try_into()?,
                );
            } else {
                value.text = row
                    .field(table.columns()[1].ordinal())
                    .and_then(|f| f.raw_bytes())
                    .ok_or("Text missing")?
                    .to_vec();
                value.binary = row
                    .field(table.columns()[2].ordinal())
                    .and_then(|f| f.raw_bytes())
                    .ok_or("Bytes missing")?
                    .to_vec();
            }
            if model.insert(id, value).is_some() {
                return Err("duplicate source key".into());
            }
        }
    }
    drop(db);
    fs::create_dir(directory)?;
    let working = directory.join("working.mdb");
    fs::copy(source, &working)?;
    let eof = insert(&working, &mut model, Row::new(1234567, deep), deep)?;
    save(directory, case, "continued", &working, &model, deep, eof)?;
    fs::remove_file(working)?;
    Ok(())
}

fn main() -> Result<()> {
    let arguments = std::env::args().skip(1).collect::<Vec<_>>();
    if let [mode, source, directory, case] = arguments.as_slice()
        && mode == "continue"
    {
        return continue_dao(Path::new(source), Path::new(directory), case);
    }
    let [directory] = arguments.as_slice() else {
        return Err("usage: index_tree_mutation_candidate NEW_DIRECTORY | continue SOURCE NEW_DIRECTORY CASE".into());
    };
    let directory = Path::new(directory);
    fs::create_dir(directory)?;
    for (case, count, descending, deep) in [
        ("primary", 200, false, false),
        ("descending", 200, true, false),
        ("empty", 1, false, false),
        ("tombstone-empty", 3, false, true),
        ("deep", 27800, false, true),
    ] {
        run(directory, case, count, descending, deep)?;
    }
    Ok(())
}
