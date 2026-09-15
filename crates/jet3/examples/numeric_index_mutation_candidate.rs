//! Finite public numeric/multiple-index mutation candidates and reader receipts.
use jet3::{
    ColumnOrdinal, ColumnRef, ColumnSpec, ColumnType, DatabaseReader, FieldUpdate, FileSource,
    IndexColumnSpec, IndexDirection, IndexKind, IndexNullPolicy, IndexSpec, InlineLongValue,
    LongValue, LongValueChunkValue, ResourceBudget, ResourceLimits, RowDelete, RowLocator,
    RowUpdate, RowValue, TableDefinition, TableRows, TableSpec, TextCodePage, UpdateError,
    ValueKind,
};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::Path,
};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
const MEMO: &[u8; 4096] = &[b'n'; 4096];
const ID_FIELDS: [IndexColumnSpec<'static>; 1] = [IndexColumnSpec {
    column: ColumnRef::Ordinal(0),
    direction: IndexDirection::Ascending,
}];
const PAIR_FIELDS: [IndexColumnSpec<'static>; 2] = [
    IndexColumnSpec {
        column: ColumnRef::Ordinal(1),
        direction: IndexDirection::Ascending,
    },
    IndexColumnSpec {
        column: ColumnRef::Ordinal(2),
        direction: IndexDirection::Descending,
    },
];
const LAST_B: [IndexColumnSpec<'static>; 1] = [PAIR_FIELDS[1]];
const LAST_C: [IndexColumnSpec<'static>; 1] = [IndexColumnSpec {
    column: ColumnRef::Ordinal(3),
    direction: IndexDirection::Descending,
}];

#[derive(Clone, Copy, Debug, PartialEq)]
enum Scalar {
    Null,
    Boolean(bool),
    Byte(u8),
    Integer(i16),
    Long(i32),
    Currency(i64),
    Single(f32),
    Double(f64),
}
impl Scalar {
    fn value(self) -> RowValue<'static> {
        match self {
            Self::Null => RowValue::Null,
            Self::Boolean(n) => RowValue::Boolean(n),
            Self::Byte(n) => RowValue::Byte(n),
            Self::Integer(n) => RowValue::Integer(n),
            Self::Long(n) => RowValue::Long(n),
            Self::Currency(scaled) => RowValue::Currency { scaled },
            Self::Single(n) => RowValue::Single(n),
            Self::Double(n) => RowValue::Double(n),
        }
    }
    fn json(self) -> String {
        match self {
            Self::Null => "null".into(),
            Self::Boolean(n) => n.to_string(),
            Self::Byte(n) => n.to_string(),
            Self::Integer(n) => n.to_string(),
            Self::Long(n) => n.to_string(),
            Self::Currency(n) => n.to_string(),
            Self::Single(n) => n.to_string(),
            Self::Double(n) => n.to_string(),
        }
    }
    fn read(kind: &ValueKind<'_>) -> Result<Self> {
        Ok(match kind {
            ValueKind::Null => Self::Null,
            ValueKind::Boolean(n) => Self::Boolean(*n),
            ValueKind::Byte(n) => Self::Byte(*n),
            ValueKind::Integer(n) => Self::Integer(*n),
            ValueKind::Long(n) => Self::Long(*n),
            ValueKind::Currency(n) => Self::Currency(n.scaled()),
            ValueKind::Single(n) => Self::Single(*n),
            ValueKind::Double(n) => Self::Double(*n),
            _ => return Err("unexpected scalar type".into()),
        })
    }
}
type Row = Vec<Scalar>;
fn row_json(row: &[Scalar]) -> String {
    format!(
        "[{}]",
        row.iter().map(|v| v.json()).collect::<Vec<_>>().join(",")
    )
}
fn values(row: &[Scalar]) -> Vec<RowValue<'static>> {
    row.iter().map(|v| v.value()).collect()
}
fn id(row: &[Scalar]) -> Result<i32> {
    if let Some(Scalar::Long(n)) = row.first() {
        Ok(*n)
    } else {
        Err("Id type".into())
    }
}
fn quote(text: &str) -> String {
    let mut out = String::from("\"");
    for c in text.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            c if c.is_control() => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}
fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}

#[derive(Clone, Copy, PartialEq)]
enum Case {
    Integral,
    Wide,
    Deep,
}
impl Case {
    fn name(self) -> &'static str {
        match self {
            Self::Integral => "integral",
            Self::Wide => "wide",
            Self::Deep => "deep",
        }
    }
    fn count(self) -> i32 {
        match self {
            Self::Integral => 195,
            Self::Wide => 80,
            Self::Deep => 5673,
        }
    }
    fn columns(self) -> Vec<ColumnSpec<'static>> {
        let rest = match self {
            Self::Integral => vec![ColumnType::Byte, ColumnType::Integer, ColumnType::Boolean],
            Self::Wide => vec![ColumnType::Currency, ColumnType::Double, ColumnType::Single],
            Self::Deep => vec![ColumnType::Currency, ColumnType::Double],
        };
        [ColumnType::Long]
            .into_iter()
            .chain(rest)
            .zip([b"Id".as_slice(), b"A", b"B", b"C"])
            .map(|(kind, name)| ColumnSpec::new(name, kind))
            .collect()
    }
    fn indexes(self) -> [IndexSpec<'static>; 3] {
        [
            IndexSpec {
                name: b"ById",
                kind: IndexKind::Primary,
                fields: &ID_FIELDS,
            },
            IndexSpec {
                name: b"ByPair",
                kind: if self == Self::Integral {
                    IndexKind::Ordinary.with_null_policy(IndexNullPolicy::IgnoreAllNull)
                } else {
                    IndexKind::Unique
                },
                fields: &PAIR_FIELDS,
            },
            IndexSpec {
                name: b"ByLast",
                kind: if self == Self::Wide {
                    IndexKind::Unique.with_null_policy(IndexNullPolicy::IgnoreAllNull)
                } else {
                    IndexKind::Ordinary
                },
                fields: if self == Self::Deep { &LAST_B } else { &LAST_C },
            },
        ]
    }
    fn row(self, id: i32) -> Row {
        use Scalar::*;
        match self {
            Self::Integral => vec![
                Long(id),
                if id % 11 == 0 {
                    Null
                } else {
                    Byte((id % 7) as u8)
                },
                if id % 13 == 0 {
                    Null
                } else {
                    Integer((id % 5 - 2) as i16)
                },
                Boolean(id % 2 == 0),
            ],
            Self::Wide => vec![
                Long(id),
                if id % 17 == 0 {
                    Null
                } else {
                    Currency(i64::from(id) * 10001)
                },
                if id % 19 == 0 {
                    Null
                } else {
                    Double(f64::from(id) + 0.5)
                },
                Null,
            ],
            Self::Deep => match id {
                0..=2 => vec![Long(id), Null, Null],
                3 => vec![Long(id), Null, Double(1.5)],
                4 => vec![Long(id), Currency(-100000), Null],
                _ => vec![
                    Long(id),
                    Currency(i64::from(id)),
                    Double(f64::from(id) + 0.5),
                ],
            },
        }
    }
    fn stages(self) -> Vec<(&'static str, Vec<Operation>)> {
        use Operation::*;
        use Scalar::*;
        let (inserted, edits, removed, regrown) = match self {
            Self::Integral => (
                (195..325).collect::<Vec<_>>(),
                vec![
                    Field(0, 1, Byte(250)),
                    Field(1, 2, Null),
                    Replace(2, vec![Long(2), Null, Integer(i16::MIN), Boolean(false)]),
                    Replace(3, vec![Long(3), Null, Null, Boolean(true)]),
                    Field(4, 3, Boolean(false)),
                    Field(324, 0, Long(999)),
                ],
                (0..195).collect::<Vec<_>>(),
                (1000..1195).collect::<Vec<_>>(),
            ),
            Self::Wide => (
                (80..92).collect(),
                vec![
                    Field(2, 3, Single(-1.5)),
                    Replace(3, vec![Long(3), Currency(30003), Double(3.5), Single(2.25)]),
                    Field(4, 1, Null),
                    Replace(5, vec![Long(5), Null, Null, Null]),
                    Field(6, 2, Null),
                    Field(7, 0, Long(99)),
                ],
                vec![0, 17, 80],
                vec![120, 121, 122],
            ),
            Self::Deep => (
                vec![5673],
                vec![
                    Field(5673, 1, Currency(-50000)),
                    Replace(6, vec![Long(6), Null, Null]),
                    Field(5, 2, Null),
                    Field(7, 2, Double(-1.25)),
                ],
                vec![5673],
                vec![6000],
            ),
        };
        vec![
            ("original", vec![]),
            (
                "grown",
                inserted.into_iter().map(|n| Insert(self.row(n))).collect(),
            ),
            ("edited", edits),
            ("collapsed", removed.into_iter().map(Delete).collect()),
            (
                "regrown",
                regrown.into_iter().map(|n| Insert(self.row(n))).collect(),
            ),
        ]
    }
}
enum Operation {
    Insert(Row),
    Field(i32, u16, Scalar),
    Replace(i32, Row),
    Delete(i32),
}
fn definition(
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
fn rows(
    db: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    b: &mut ResourceBudget,
) -> Result<BTreeMap<i32, (Row, RowLocator)>> {
    let mut result = BTreeMap::new();
    let mut cursor = db.rows(table, b)?;
    while let Some(mut row) = cursor.next_row()? {
        let mut value = Vec::new();
        for column in table.columns() {
            value.push(Scalar::read(
                row.value(column.ordinal(), TextCodePage::Windows1252)?
                    .ok_or("missing scalar")?
                    .kind(),
            )?);
        }
        if result.insert(id(&value)?, (value, row.locator())).is_some() {
            return Err("duplicate Id".into());
        }
    }
    Ok(result)
}
fn locate(path: &Path, wanted: i32) -> Result<RowLocator> {
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
fn column_ordinal(path: &Path, column: u16) -> Result<ColumnOrdinal> {
    let mut b = budget();
    let mut db = DatabaseReader::open(path, &mut b)?;
    let table = definition(&mut db, b"Items", &mut b)?;
    table
        .columns()
        .get(usize::from(column))
        .map(|c| c.ordinal())
        .ok_or_else(|| "column absent".into())
}
fn apply(path: &Path, model: &mut BTreeMap<i32, Row>, operation: Operation) -> Result<()> {
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
            let previous = row[usize::from(column)];
            row[usize::from(column)] = value;
            if matches!(previous, Scalar::Null | Scalar::Boolean(_))
                || matches!(value, Scalar::Null | Scalar::Boolean(_))
            {
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
fn create(path: &Path, case: Case, model: &BTreeMap<i32, Row>) -> Result<()> {
    let values = model.values().map(|r| values(r)).collect::<Vec<_>>();
    let rows = values.iter().map(Vec::as_slice).collect::<Vec<_>>();
    jet3::create_database_with_table_rows(
        path,
        &[
            TableRows {
                table: TableSpec {
                    name: b"Items",
                    columns: &case.columns(),
                    indexes: &case.indexes(),
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
        return Err("Notes inventory".into());
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
fn save(
    directory: &Path,
    case: Case,
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
            "{{\"items\":[{row_json}],\"notes\":{memo},\"schema\":{{\"Items\":{},\"Notes\":{}}},\"locators\":[{locators}],\"indexes\":[{}],\"pages\":{},\"data_pages\":[{pages}]}}\n",
            schema(&table)?,
            schema(&notes_table)?,
            indexes.join(","),
            db.geometry().page_count()
        ),
    )?;
    Ok(())
}
fn refusals(directory: &Path, source: &Path) -> Result<()> {
    let mut receipts = Vec::new();
    for name in ["later-insert", "later-replace"] {
        let before = directory.join(format!("refusal-{name}-before.mdb"));
        let after = directory.join(format!("refusal-{name}-after.mdb"));
        fs::copy(source, &before)?;
        fs::copy(source, &after)?;
        let error = if name == "later-insert" {
            let mut row = Case::Wide.row(500);
            row[3] = Scalar::Single(-1.5);
            jet3::insert_row(&after, b"Items", &values(&row), &mut budget()).err()
        } else {
            let mut row = Case::Wide.row(81);
            row[3] = Scalar::Single(2.25);
            jet3::update_row(
                &after,
                RowUpdate {
                    table: b"Items",
                    row: locate(&after, 81)?,
                    values: &values(&row),
                },
                &mut budget(),
            )
            .err()
        }
        .ok_or("duplicate in third index accepted")?;
        if !matches!(error, UpdateError::Unsupported("duplicate unique key"))
            || fs::read(&before)? != fs::read(&after)?
        {
            return Err(format!("refusal {name}: {error}").into());
        }
        receipts.push(format!(
            "{{\"name\":{},\"error\":{},\"preserved\":true}}",
            quote(name),
            quote(&format!("{error:?}"))
        ));
    }
    fs::write(
        directory.join("refusals.json"),
        format!("[{}]\n", receipts.join(",")),
    )?;
    Ok(())
}
fn continuation(source: &Path, directory: &Path, case: Case) -> Result<()> {
    fs::create_dir(directory)?;
    let mut b = budget();
    let mut db = DatabaseReader::open(source, &mut b)?;
    let table = definition(&mut db, b"Items", &mut b)?;
    let mut model = rows(&mut db, &table, &mut b)?
        .into_iter()
        .map(|(id, (r, _))| (id, r))
        .collect();
    drop(db);
    let working = directory.join("working.mdb");
    fs::copy(source, &working)?;
    for operation in [
        Operation::Insert(case.row(1234567)),
        Operation::Field(1234567, 0, Scalar::Long(1234568)),
        Operation::Delete(9001),
    ] {
        apply(&working, &mut model, operation)?;
    }
    save(directory, case, "continued", &working, &model)?;
    fs::remove_file(working)?;
    Ok(())
}
fn main() -> Result<()> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    if let [mode, source, directory, name] = args.as_slice()
        && mode == "continue"
    {
        let case = match name.as_str() {
            "integral" => Case::Integral,
            "wide" => Case::Wide,
            _ => return Err("unsupported continuation case".into()),
        };
        return continuation(Path::new(source), Path::new(directory), case);
    }
    let [directory] = args.as_slice() else {
        return Err("usage: numeric_index_mutation_candidate NEW_DIRECTORY | continue SOURCE NEW_DIRECTORY CASE".into());
    };
    let directory = Path::new(directory);
    fs::create_dir(directory)?;
    for case in [Case::Integral, Case::Wide, Case::Deep] {
        let working = directory.join(format!("{}-working.mdb", case.name()));
        let mut model = (0..case.count())
            .map(|id| (id, case.row(id)))
            .collect::<BTreeMap<_, _>>();
        create(&working, case, &model)?;
        for (phase, operations) in case.stages() {
            for operation in operations {
                apply(&working, &mut model, operation)?;
            }
            save(directory, case, phase, &working, &model)?;
            if case == Case::Wide && phase == "edited" {
                refusals(directory, &working)?;
            }
        }
        fs::remove_file(working)?;
    }
    Ok(())
}
