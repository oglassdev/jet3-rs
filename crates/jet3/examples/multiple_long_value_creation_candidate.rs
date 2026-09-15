//! Repeatable creation candidates with independent Memo/OLE ownership and numeric indexes.
use jet3::{
    ColumnSpec, ColumnType, ComposeError, CreateDatabaseError, DatabaseReader, FileSource,
    IndexColumnSpec, IndexKind, IndexSpec, InlineLongValue, LongValue, LongValueChunkValue,
    PageImageError, ResourceBudget, ResourceLimits, RowValue, TableDefinition, TableRows,
    TextCodePage, ValueKind,
};
use std::{collections::BTreeMap, fs, path::Path};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
const NAMES: [&[u8]; 6] = [
    b"Body",
    b"Blob",
    b"ExtraMemo",
    b"ExtraBlob",
    b"LastMemo",
    b"LastBlob",
];
const LENGTHS: [usize; 9] = [1, 32, 33, 512, 2036, 2037, 2048, 4064, 4096];
const NOTES: &[u8; 4096] = &[b'n'; 4096];
#[derive(Clone, Copy)]
struct Case {
    name: &'static str,
    count: i32,
    columns: usize,
    indexes: usize,
    generated: bool,
    later: bool,
}
const CASES: [Case; 6] = [
    Case {
        name: "first-mixed",
        count: 205,
        columns: 4,
        indexes: 3,
        generated: false,
        later: false,
    },
    Case {
        name: "later-generated",
        count: 213,
        columns: 4,
        indexes: 3,
        generated: true,
        later: true,
    },
    Case {
        name: "capacity-six-zero",
        count: 12,
        columns: 6,
        indexes: 0,
        generated: false,
        later: false,
    },
    Case {
        name: "capacity-six-one",
        count: 12,
        columns: 6,
        indexes: 1,
        generated: false,
        later: true,
    },
    Case {
        name: "capacity-five-two",
        count: 12,
        columns: 5,
        indexes: 2,
        generated: false,
        later: false,
    },
    Case {
        name: "capacity-five-three",
        count: 12,
        columns: 5,
        indexes: 3,
        generated: false,
        later: true,
    },
];
fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}
fn quote(value: &str) -> String {
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
fn payload(id: i32, column: usize) -> Option<Vec<u8>> {
    if id == 10 || (id > 10 && (id as usize + column).is_multiple_of(7)) {
        return None;
    }
    let length = if id <= 9 {
        LENGTHS[(id as usize - 1 + column) % LENGTHS.len()]
    } else {
        1 + (id as usize + 3 * column) % 17
    };
    Some(
        (0..length)
            .map(|offset| {
                if column.is_multiple_of(2) {
                    b'A' + ((offset + id as usize + column) % 26) as u8
                } else {
                    ((offset * 17 + id as usize * 3 + column) % 256) as u8
                }
            })
            .collect(),
    )
}
fn schema_columns(case: Case) -> Vec<ColumnSpec<'static>> {
    let mut columns = vec![
        ColumnSpec::new(
            b"Id",
            if case.generated {
                ColumnType::AutoIncrement
            } else {
                ColumnType::Long
            },
        ),
        ColumnSpec::new(b"Tag", ColumnType::Long),
    ];
    columns.extend(
        NAMES[..case.columns]
            .iter()
            .enumerate()
            .map(|(column, name)| {
                ColumnSpec::new(
                    name,
                    if column.is_multiple_of(2) {
                        ColumnType::Memo
                    } else {
                        ColumnType::LongBinary
                    },
                )
            }),
    );
    columns
}
fn create(path: &Path, case: Case, extra_column: bool) -> Result<()> {
    let mut columns = schema_columns(case);
    if extra_column {
        columns.push(ColumnSpec::new(b"OverflowMemo", ColumnType::Memo));
    }
    let by_id = [IndexColumnSpec::ascending(b"Id")];
    let by_tag = [IndexColumnSpec::descending(b"Tag")];
    let by_pair = [
        IndexColumnSpec::ascending(b"Tag"),
        IndexColumnSpec::descending(b"Id"),
    ];
    let indexes = [
        IndexSpec {
            name: b"ById",
            fields: &by_id,
            kind: IndexKind::Primary,
        },
        IndexSpec {
            name: b"ByTag",
            fields: &by_tag,
            kind: IndexKind::Ordinary,
        },
        IndexSpec {
            name: b"ByPair",
            fields: &by_pair,
            kind: IndexKind::Unique,
        },
    ];
    let owned = (1..=case.count)
        .map(|id| {
            (0..case.columns)
                .map(|c| payload(id, c))
                .collect::<Vec<_>>()
        })
        .collect::<Vec<_>>();
    let values = owned
        .iter()
        .enumerate()
        .map(|(position, payloads)| {
            let id = position as i32 + 1;
            let mut row = vec![
                if case.generated {
                    RowValue::AutoIncrement
                } else {
                    RowValue::Long(id)
                },
                if id % 37 == 0 {
                    RowValue::Null
                } else {
                    RowValue::Long(id % 11 - 5)
                },
            ];
            row.extend(
                payloads
                    .iter()
                    .enumerate()
                    .map(|(column, bytes)| match bytes {
                        None => RowValue::Null,
                        Some(bytes) if column.is_multiple_of(2) => RowValue::Memo(bytes),
                        Some(bytes) => RowValue::LongBinary(bytes),
                    }),
            );
            if extra_column {
                row.push(RowValue::Memo(b"overflow"));
            }
            row
        })
        .collect::<Vec<_>>();
    let rows = values.iter().map(Vec::as_slice).collect::<Vec<_>>();
    let items = TableRows {
        table: jet3::TableSpec {
            name: b"Items",
            columns: &columns,
            indexes: &indexes[..case.indexes],
        },
        rows: &rows,
    };
    let note_columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"Body", ColumnType::Memo),
    ];
    let notes = TableRows {
        table: jet3::TableSpec {
            name: b"Notes",
            columns: &note_columns,
            indexes: &[],
        },
        rows: &[
            &[RowValue::Long(7), RowValue::Memo(NOTES)],
            &[RowValue::Long(8), RowValue::Null],
        ],
    };
    let requests = if case.later {
        [notes, items]
    } else {
        [items, notes]
    };
    let result = jet3::create_database_with_table_rows(path, &requests, &mut budget());
    if extra_column {
        if !matches!(
            result,
            Err(CreateDatabaseError::Compose(ComposeError::Page(
                PageImageError::PageFull { .. }
            )))
        ) || path.exists()
        {
            return Err(format!("capacity refusal: {result:?}").into());
        }
    } else {
        result?;
    }
    Ok(())
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
fn snapshot(path: &Path, output: &Path) -> Result<()> {
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
fn main() -> Result<()> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    if args.first().is_some_and(|s| s == "inspect") {
        return snapshot(
            Path::new(args.get(1).ok_or("inspect MDB OUTPUT.json")?),
            Path::new(args.get(2).ok_or("inspect MDB OUTPUT.json")?),
        );
    }
    let directory = Path::new(
        args.first()
            .ok_or("usage: multiple_long_value_creation_candidate NEW_DIRECTORY")?,
    );
    fs::create_dir(directory)?;
    let mut refusals = Vec::new();
    for case in CASES {
        let path = directory.join(format!("{}.mdb", case.name));
        create(&path, case, false)?;
        snapshot(
            &path,
            &directory.join(format!("{}.snapshot.json", case.name)),
        )?;
        if case.name.starts_with("capacity") {
            create(
                &directory.join(format!("{}-overflow.mdb", case.name)),
                case,
                true,
            )?;
            refusals.push(format!(
                "{{\"case\":{},\"error\":\"PageFull\",\"destination_absent\":true}}",
                quote(case.name)
            ));
        }
    }
    fs::write(
        directory.join("refusals.json"),
        format!("[{}]\n", refusals.join(",")),
    )?;
    Ok(())
}
