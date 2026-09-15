//! Finite index-count and ten-component public writer lifecycle candidates.
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
#[allow(dead_code)]
#[path = "numeric_index_mutation_support/mod.rs"]
mod scalar;
use scalar::{Operation, Row, Scalar, budget, hex, id, quote, row_json, values};
#[path = "index_capacity_support/io.rs"]
mod io;
use io::*;

const CASES: [(&str, usize, usize, bool); 5] = [
    ("indexes4", 4, 3, false),
    ("indexes13", 13, 9, false),
    ("indexes14", 14, 10, false),
    ("indexes32", 32, 10, false),
    ("mixed10", 4, 10, true),
];
const COLUMN_NAMES: [&[u8]; 11] = [
    b"Id", b"A", b"B", b"C", b"D", b"E", b"F", b"G", b"H", b"I", b"J",
];
struct Case {
    name: &'static str,
    count: usize,
    width: usize,
    mixed: bool,
    names: Vec<Vec<u8>>,
}
impl Case {
    fn new(spec: (&'static str, usize, usize, bool)) -> Self {
        let (name, count, width, mixed) = spec;
        let names = (0..count)
            .map(|i| {
                if i == 0 {
                    b"ById".to_vec()
                } else {
                    format!("K{i:02}").into_bytes()
                }
            })
            .collect();
        Self {
            name,
            count,
            width,
            mixed,
            names,
        }
    }
    fn name(&self) -> &str {
        self.name
    }
    fn columns(&self) -> Vec<ColumnSpec<'static>> {
        let types = if self.mixed {
            vec![
                ColumnType::Long,
                ColumnType::Long,
                ColumnType::Byte,
                ColumnType::Integer,
                ColumnType::Currency,
                ColumnType::Single,
                ColumnType::Double,
                ColumnType::DateTime,
                ColumnType::Boolean,
                ColumnType::Guid,
                ColumnType::Text {
                    max_len: std::num::NonZeroU8::MAX,
                },
            ]
        } else {
            vec![ColumnType::Long; 11]
        };
        COLUMN_NAMES
            .into_iter()
            .zip(types)
            .map(|(n, t)| ColumnSpec::new(n, t))
            .collect()
    }
    fn index_fields(&self) -> Vec<Vec<IndexColumnSpec<'static>>> {
        (0..self.count)
            .map(|i| {
                if i == 0 {
                    return vec![IndexColumnSpec {
                        column: ColumnRef::Ordinal(0),
                        direction: IndexDirection::Ascending,
                    }];
                }
                (0..self.width)
                    .map(|c| IndexColumnSpec {
                        column: ColumnRef::Ordinal((1 + (c + i - 1) % self.width) as u16),
                        direction: if (i >> (c % 5)) & 1 == 0 {
                            IndexDirection::Ascending
                        } else {
                            IndexDirection::Descending
                        },
                    })
                    .collect()
            })
            .collect()
    }
    fn indexes<'a>(&'a self, fields: &'a [Vec<IndexColumnSpec<'static>>]) -> Vec<IndexSpec<'a>> {
        self.names
            .iter()
            .zip(fields)
            .enumerate()
            .map(|(i, (name, fields))| IndexSpec {
                name,
                fields,
                kind: if i == 0 {
                    IndexKind::Primary
                } else if i + 1 == self.count {
                    IndexKind::Unique.with_null_policy(IndexNullPolicy::IgnoreAllNull)
                } else if i % 2 == 0 {
                    IndexKind::Ordinary.with_null_policy(IndexNullPolicy::IgnoreAllNull)
                } else {
                    IndexKind::Ordinary
                },
            })
            .collect()
    }
    fn row(&self, id: i32) -> Row {
        use Scalar::*;
        if self.mixed {
            let alphabet = b"aA\xe9\xc9\xc6\xe6\xdf\x8a\x9a";
            let text = (0..(240 + id as usize % 16))
                .map(|n| alphabet[(n * 7 + id as usize) % alphabet.len()])
                .collect();
            vec![
                Long(id),
                if id % 11 == 0 { Null } else { Long(id * 2 + 1) },
                Byte((id % 251) as u8),
                Integer((id % 30000 - 15000) as i16),
                Currency(i64::from(id) * 10001),
                Single((id % 1024) as f32 + 0.5),
                Double(f64::from(id) + 0.25),
                DateTime(36526.0 + f64::from(id % 1000) / 4.0),
                Boolean(id % 2 == 0),
                if id % 13 == 0 {
                    Null
                } else {
                    Guid(std::array::from_fn(|n| {
                        ((n * 37 + id as usize) % 256) as u8
                    }))
                },
                Text(text),
            ]
        } else {
            std::iter::once(Long(id))
                .chain((1..=10).map(|c| {
                    if id == 0 || (c == 1 && id % 11 == 0) || (c == self.width && id % 13 == 0) {
                        Null
                    } else {
                        Long(id * (c as i32 + 1) + c as i32)
                    }
                }))
                .collect()
        }
    }
    fn stages(&self) -> Vec<(&'static str, Vec<Operation>)> {
        use Operation::*;
        let mut replacement = self.row(42);
        replacement[0] = Scalar::Long(3);
        vec![
            ("original", vec![]),
            (
                "edited",
                vec![
                    Insert(self.row(40)),
                    Field(1, 1, Scalar::Null),
                    Replace(3, replacement),
                    Field(7, 0, Scalar::Long(99)),
                    Delete(0),
                ],
            ),
            (
                "regrown",
                vec![Delete(2), Insert(self.row(1000)), Insert(self.row(1001))],
            ),
        ]
    }
}
fn refusals(directory: &Path, source: &Path, case: &Case) -> Result<()> {
    let mut receipts = Vec::new();
    for name in ["later-insert", "later-replace"] {
        let before = directory.join(format!("refusal-{name}-before.mdb"));
        let after = directory.join(format!("refusal-{name}-after.mdb"));
        fs::copy(source, &before)?;
        fs::copy(source, &after)?;
        let mut duplicate = case.row(8);
        duplicate[0] = Scalar::Long(if name == "later-insert" { 500 } else { 9 });
        let error = if name == "later-insert" {
            jet3::insert_row(&after, b"Items", &values(&duplicate), &mut budget()).err()
        } else {
            jet3::update_row(
                &after,
                RowUpdate {
                    table: b"Items",
                    row: locate(&after, 9)?,
                    values: &values(&duplicate),
                },
                &mut budget(),
            )
            .err()
        }
        .ok_or("late unique index accepted duplicate")?;
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
fn continuation(source: &Path, directory: &Path, case: &Case) -> Result<()> {
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
    for op in [
        Operation::Insert(case.row(1234567)),
        Operation::Field(1234567, 0, Scalar::Long(1234568)),
        Operation::Delete(9001),
    ] {
        apply(&working, &mut model, op)?;
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
        let spec = CASES
            .into_iter()
            .find(|c| c.0 == name)
            .ok_or("unknown case")?;
        return continuation(Path::new(source), Path::new(directory), &Case::new(spec));
    }
    let [directory] = args.as_slice() else {
        return Err(
            "usage: index_capacity_candidate NEW_DIRECTORY | continue SOURCE NEW_DIRECTORY CASE"
                .into(),
        );
    };
    let directory = Path::new(directory);
    fs::create_dir(directory)?;
    for spec in CASES {
        let case = Case::new(spec);
        let working = directory.join(format!("{}-working.mdb", case.name));
        let mut model = (0..24)
            .map(|id| (id, case.row(id)))
            .collect::<BTreeMap<_, _>>();
        create(&working, &case, &model)?;
        for (phase, operations) in case.stages() {
            for operation in operations {
                apply(&working, &mut model, operation)?;
            }
            save(directory, &case, phase, &working, &model)?;
            if case.name == "indexes13" && phase == "edited" {
                refusals(directory, &working, &case)?;
            }
        }
        fs::remove_file(working)?;
    }
    Ok(())
}
