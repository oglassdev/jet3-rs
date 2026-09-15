//! Public bulk creation and bounded mutations across allocation-map boundaries.
use jet3::{
    ByteCount, ColumnSpec, ColumnType, DatabaseReader, FileSource, IndexColumnSpec, IndexKind,
    IndexSpec, InlineLongValue, LongValue, LongValueChunkValue, ResourceBudget, ResourceLimits,
    RowDelete, RowLocator, RowUpdate, RowValue, TableDefinition, TableRows, TableSpec,
    TextCodePage, UpdateError, ValueKind,
};
use std::{
    collections::BTreeMap,
    fs,
    io::{BufWriter, Write},
    path::Path,
};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
#[path = "allocation_support/reader.rs"]
mod reader;

#[derive(Clone, Copy)]
struct Case {
    name: &'static str,
    count: i32,
    payload: bool,
}
const CASES: [Case; 4] = [
    Case {
        name: "rows-inline",
        count: 1000,
        payload: false,
    },
    Case {
        name: "rows-slot",
        count: 16350,
        payload: false,
    },
    Case {
        name: "payload-inline",
        count: 512,
        payload: true,
    },
    Case {
        name: "payload-slot",
        count: 8200,
        payload: true,
    },
];
fn budget() -> ResourceBudget {
    ResourceBudget::new(
        ResourceLimits::default()
            .with_max_allocation_bytes(ByteCount::new(2 * 1024 * 1024 * 1024))
            .with_max_total_work_units(4_000_000_000),
    )
}
fn quote(value: &str) -> String {
    let mut result = String::from("\"");
    for c in value.chars() {
        match c {
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
fn feed(output: &mut Vec<u8>, value: Option<&[u8]>) {
    output.extend_from_slice(&value.map_or(-1, |v| v.len() as i32).to_le_bytes());
    if let Some(value) = value {
        output.extend_from_slice(value);
    }
}
struct ModelRow {
    id: i32,
    tag: Option<i32>,
    cells: Vec<Option<Vec<u8>>>,
}
impl ModelRow {
    fn new(case: Case, id: i32, seed: i32) -> Self {
        let cells = if case.payload {
            vec![
                if seed == 0 {
                    None
                } else {
                    Some(vec![b'A' + (seed % 26) as u8; 1800])
                },
                if seed == 1 {
                    None
                } else {
                    Some((0..1800).map(|n| ((n * 37 + seed) % 256) as u8).collect())
                },
            ]
        } else {
            (0..4)
                .map(|c| Some(vec![b'A' + ((seed + 3 * c) % 26) as u8; 255]))
                .collect()
        };
        Self {
            id,
            tag: if seed % 13 == 0 {
                None
            } else {
                Some(seed % 17 - 8)
            },
            cells,
        }
    }
    fn values(&self, case: Case) -> Vec<RowValue<'_>> {
        let mut result = vec![RowValue::Long(self.id)];
        if case.payload {
            result.push(self.tag.map_or(RowValue::Null, RowValue::Long));
        }
        result.extend(self.cells.iter().enumerate().map(|(c, value)| match value {
            None => RowValue::Null,
            Some(bytes) if !case.payload => RowValue::Text(bytes),
            Some(bytes) if c == 0 => RowValue::Memo(bytes),
            Some(bytes) => RowValue::LongBinary(bytes),
        }));
        result
    }
    fn canonical(&self, case: Case) -> Vec<u8> {
        let mut result = Vec::new();
        feed(&mut result, Some(&self.id.to_le_bytes()));
        if case.payload {
            feed(
                &mut result,
                self.tag
                    .as_ref()
                    .map(|v| v.to_le_bytes())
                    .as_ref()
                    .map(|v| v.as_slice()),
            );
        }
        for cell in &self.cells {
            feed(&mut result, cell.as_deref());
        }
        result
    }
}
fn columns(case: Case) -> Vec<ColumnSpec<'static>> {
    if case.payload {
        vec![
            ColumnSpec::new(b"Id", ColumnType::Long),
            ColumnSpec::new(b"Tag", ColumnType::Long),
            ColumnSpec::new(b"Body", ColumnType::Memo),
            ColumnSpec::new(b"Blob", ColumnType::LongBinary),
        ]
    } else {
        std::iter::once(ColumnSpec::new(b"Id", ColumnType::Long))
            .chain(
                [b"Pad0", b"Pad1", b"Pad2", b"Pad3"]
                    .into_iter()
                    .map(|name| {
                        ColumnSpec::new(
                            name,
                            ColumnType::FixedText {
                                len: std::num::NonZeroU8::MAX,
                            },
                        )
                    }),
            )
            .collect()
    }
}
fn create(path: &Path, case: Case) -> Result<()> {
    let columns = columns(case);
    let by_id = [IndexColumnSpec::ascending(b"Id")];
    let by_tag = [IndexColumnSpec::descending(b"Tag")];
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
    ];
    let owned = (0..case.count)
        .map(|id| ModelRow::new(case, id, id))
        .collect::<Vec<_>>();
    let values = owned.iter().map(|r| r.values(case)).collect::<Vec<_>>();
    let rows = values.iter().map(Vec::as_slice).collect::<Vec<_>>();
    jet3::create_database_with_table_rows(
        path,
        &[
            TableRows {
                table: TableSpec {
                    name: b"Items",
                    columns: &columns,
                    indexes: &indexes[..if case.payload { 2 } else { 1 }],
                },
                rows: &rows,
            },
            TableRows {
                table: TableSpec {
                    name: b"Notes",
                    columns: &[
                        ColumnSpec::new(b"Id", ColumnType::Long),
                        ColumnSpec::new(
                            b"Body",
                            ColumnType::Text {
                                max_len: std::num::NonZeroU8::new(64).ok_or("Notes width")?,
                            },
                        ),
                    ],
                    indexes: &[],
                },
                rows: &[&[RowValue::Long(7), RowValue::Text(b"allocation-control")]],
            },
        ],
        &mut budget(),
    )?;
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
        while let Some(row) = catalog.next_record()? {
            if row.name().raw_bytes() == name {
                root = row.table_definition();
            }
        }
    }
    Ok(db.table_definition(root.ok_or("missing table")?, b)?)
}
fn locate(path: &Path, wanted: i32) -> Result<RowLocator> {
    let mut b = budget();
    let mut db = DatabaseReader::open(path, &mut b)?;
    let table = definition(&mut db, b"Items", &mut b)?;
    let mut rows = db.rows(&table, &mut b)?;
    while let Some(row) = rows.next_row()? {
        if row
            .field(table.columns()[0].ordinal())
            .and_then(|f| f.raw_bytes())
            == Some(wanted.to_le_bytes().as_slice())
        {
            return Ok(row.locator());
        }
    }
    Err("mutation Id absent".into())
}
fn mutate(path: &Path, case: Case, continuation: bool) -> Result<()> {
    let (insert, old, replacement, deleted) = if continuation {
        (300000, 6, 300001, 200000)
    } else {
        (100000, 3, 100001, 2)
    };
    let row = ModelRow::new(case, insert, insert);
    jet3::insert_row(path, b"Items", &row.values(case), &mut budget())?;
    let row = ModelRow::new(case, replacement, replacement);
    jet3::update_row(
        path,
        RowUpdate {
            table: b"Items",
            row: locate(path, old)?,
            values: &row.values(case),
        },
        &mut budget(),
    )?;
    jet3::delete_row(
        path,
        RowDelete {
            table: b"Items",
            row: locate(path, deleted)?,
        },
        &mut budget(),
    )?;
    if continuation && case.name == "rows-inline" {
        for id in 400000..400064 {
            let row = ModelRow::new(case, id, id);
            jet3::insert_row(path, b"Items", &row.values(case), &mut budget())?;
        }
    }
    Ok(())
}
fn refusal(source: &Path, after: &Path, case: Case) -> Result<()> {
    fs::copy(source, after)?;
    let row = ModelRow::new(case, 999999, 999999);
    let error = jet3::insert_row(after, b"Items", &row.values(case), &mut budget())
        .err()
        .ok_or("damaged map accepted")?;
    if !matches!(
        error,
        UpdateError::Mismatch(_)
            | UpdateError::Allocation(_)
            | UpdateError::UsageMap(_)
            | UpdateError::Definition(_)
            | UpdateError::Rows(_)
            | UpdateError::Index(_)
            | UpdateError::LongValue(_)
    ) {
        return Err(format!("wrong refusal category: {error:?}").into());
    }
    if fs::read(source)? != fs::read(after)? {
        return Err("refusal changed input bytes".into());
    }
    println!(
        "{{\"error\":{},\"preserved\":true}}",
        quote(&format!("{error:?}"))
    );
    Ok(())
}
fn find(name: &str) -> Result<Case> {
    CASES
        .into_iter()
        .find(|c| c.name == name)
        .ok_or_else(|| "unknown case".into())
}
fn main() -> Result<()> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    match args.as_slice() {
        [mode,name,id,seed] if mode=="model" => {let case=find(name)?;println!("{}",hex(&ModelRow::new(case,id.parse()?,seed.parse()?).canonical(case)));}
        [mode,source,output] if mode=="inspect"=>reader::snapshot(Path::new(source),Path::new(output))?,
        [mode,source,after,name] if mode=="refuse"=>refusal(Path::new(source),Path::new(after),find(name)?)?,
        [mode,source,directory,name] if mode=="continue"=>{
            let case=find(name)?;let directory=Path::new(directory);fs::create_dir(directory)?;
            let output=directory.join(format!("{}-continued.mdb",case.name));fs::copy(source,&output)?;mutate(&output,case,true)?;
            reader::snapshot(&output,&directory.join(format!("{}-continued.snapshot.json",case.name)))?;
        }
        [directory]=>{
            let directory=Path::new(directory);fs::create_dir(directory)?;
            for case in CASES {
                let original=directory.join(format!("{}-original.mdb",case.name));create(&original,case)?;
                reader::snapshot(&original,&directory.join(format!("{}-original.snapshot.json",case.name)))?;
                let changed=directory.join(format!("{}-mutated.mdb",case.name));fs::copy(&original,&changed)?;mutate(&changed,case,false)?;
                reader::snapshot(&changed,&directory.join(format!("{}-mutated.snapshot.json",case.name)))?;
            }
        }
        _=>return Err("usage: allocation_candidate NEW_DIRECTORY | inspect MDB OUTPUT.json | continue SOURCE NEW_DIRECTORY CASE | refuse SOURCE AFTER CASE | model CASE ID SEED".into()),
    }
    Ok(())
}
