//! Reproducible Text/Memo empty-value and OLE-null lifecycle candidates.
use jet3::{
    ColumnOrdinal, ColumnSpec, ColumnType, DatabaseReader, FieldUpdate, FileSource,
    IndexColumnSpec, IndexKind, IndexSpec, InlineLongValue, LongValue, LongValueChunkValue,
    ResourceBudget, ResourceLimits, RowDelete, RowLocator, RowUpdate, RowValue, TableDefinition,
    TableRows, TableSpec, TextCodePage, ValueKind,
};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::Path,
};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
const MEMO: &[u8; 4096] = &[b'n'; 4096];
const CASES: [&str; 4] = [
    "mixed-first",
    "mixed-later",
    "property-chain",
    "unique-text",
];
#[path = "wide_row_support/model.rs"]
mod model;
use model::*;
#[path = "wide_row_support/io.rs"]
mod io;
use io::*;

struct Case {
    name: String,
    names: Vec<String>,
}
impl Case {
    fn new(name: &str) -> Result<Self> {
        if !CASES.contains(&name) {
            return Err("unknown case".into());
        }
        Ok(Self {
            name: name.into(),
            names: (0..26)
                .map(|i| format!("Field{i:02}_{}", "x".repeat(56)))
                .collect(),
        })
    }
    fn name(&self) -> &str {
        &self.name
    }
    fn columns(&self) -> Vec<ColumnSpec<'_>> {
        let width = std::num::NonZeroU8::new(8).unwrap_or(std::num::NonZeroU8::MIN);
        let mut result = vec![
            ColumnSpec::new(b"Id", ColumnType::Long),
            ColumnSpec::new(b"Code", ColumnType::Text { max_len: width }).with_allow_zero_length(),
            ColumnSpec::new(b"Fixed", ColumnType::FixedText { len: width }),
            ColumnSpec::new(b"Body Memo", ColumnType::Memo).with_allow_zero_length(),
            ColumnSpec::new(b"OtherText", ColumnType::Text { max_len: width }),
            ColumnSpec::new(b"OtherMemo", ColumnType::Memo),
            ColumnSpec::new(b"Blob", ColumnType::LongBinary),
            ColumnSpec::new(b"Group", ColumnType::Long),
            ColumnSpec::new(b"Extra_Memo", ColumnType::Memo).with_allow_zero_length(),
        ];
        if self.name == "property-chain" {
            result.extend(self.names.iter().enumerate().map(|(i, name)| {
                let field = ColumnSpec::new(name.as_bytes(), ColumnType::Text { max_len: width });
                if i % 2 == 0 {
                    field.with_allow_zero_length()
                } else {
                    field
                }
            }));
        }
        result
    }
    fn row(&self, id: i32) -> Row {
        let code = if id % 13 == 0 {
            Scalar::Null
        } else if id == 1 || (self.name != "unique-text" && id % 7 == 1) {
            Scalar::Text(vec![])
        } else {
            Scalar::Text(format!("{id:08}").into_bytes())
        };
        let body = match id % 5 {
            0 => Scalar::Null,
            n => Scalar::Memo(payload([0, 0, 1, 33, 4096][n as usize], id, 3, true)),
        };
        let blob = match id % 5 {
            0 => Scalar::Null,
            n => Scalar::Ole(payload([0, 0, 1, 33, 4096][n as usize], id, 6, false)),
        };
        let mut row = vec![
            Scalar::Long(id),
            code,
            Scalar::Text(b"        ".to_vec()),
            body,
            Scalar::Text(b"kept".to_vec()),
            Scalar::Memo(payload(32, id, 5, true)),
            blob,
            Scalar::Long(id % 5),
            Scalar::Memo(vec![]),
        ];
        if self.name == "property-chain" {
            row.extend((0..26).map(|i| {
                Scalar::Text(if i % 2 == 0 {
                    vec![]
                } else {
                    b"value".to_vec()
                })
            }));
        }
        row
    }
    fn stages(&self) -> Vec<(&'static str, Vec<Operation>)> {
        let mut changed = self.row(4);
        changed[1] = Scalar::Null;
        changed[3] = Scalar::Memo(vec![]);
        changed[6] = Scalar::Ole(vec![]);
        let mut edited = vec![
            Operation::Field(1, 1, Scalar::Text(b"changed ".to_vec())),
            Operation::Field(2, 1, Scalar::Text(vec![])),
            Operation::Field(3, 3, Scalar::Memo(payload(4096, 3, 3, true))),
            Operation::Replace(4, changed),
            Operation::Field(7, 0, Scalar::Long(700)),
            Operation::Delete(5),
        ];
        edited.extend((1000..1008).map(|id| Operation::Insert(self.row(id))));
        let mut regrown = (20..40).map(Operation::Delete).collect::<Vec<_>>();
        regrown.extend((2000..2020).map(|id| Operation::Insert(self.row(id))));
        vec![
            ("original", vec![]),
            ("edited", edited),
            ("regrown", regrown),
        ]
    }
}
fn normalize(model: &mut BTreeMap<i32, Row>) {
    for row in model.values_mut() {
        for value in row {
            if matches!(value, Scalar::Ole(bytes) if bytes.is_empty()) {
                *value = Scalar::Null;
            }
        }
    }
}
fn mutate(path: &Path, model: &mut BTreeMap<i32, Row>, operation: Operation) -> Result<()> {
    apply(path, model, operation)?;
    normalize(model);
    Ok(())
}
fn create(path: &Path, case: &Case, model: &BTreeMap<i32, Row>) -> Result<()> {
    let columns = case.columns();
    let fields = [
        vec![IndexColumnSpec::ascending(0)],
        vec![IndexColumnSpec::ascending(1)],
        vec![
            IndexColumnSpec::descending(1),
            IndexColumnSpec::ascending(7),
        ],
    ];
    let indexes = [
        IndexSpec {
            name: b"ById",
            fields: &fields[0],
            kind: IndexKind::Primary,
        },
        IndexSpec {
            name: b"ByCode",
            fields: &fields[1],
            kind: if case.name == "unique-text" {
                IndexKind::Unique
            } else {
                IndexKind::Ordinary
            },
        },
        IndexSpec {
            name: b"ByPair",
            fields: &fields[2],
            kind: IndexKind::Ordinary,
        },
    ];
    let values = model.values().map(|r| values(r)).collect::<Vec<_>>();
    let rows = values.iter().map(Vec::as_slice).collect::<Vec<_>>();
    let items = TableRows {
        table: TableSpec {
            validation: jet3::TableValidation::NONE,
            name: b"Items",
            columns: &columns,
            indexes: &indexes,
        },
        rows: &rows,
    };
    let notes = TableRows {
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
    };
    let tables = if case.name == "mixed-later" {
        [notes, items]
    } else {
        [items, notes]
    };
    jet3::create_database_with_table_rows(path, &tables, &mut budget())?;
    Ok(())
}
fn refusals(
    directory: &Path,
    case: &Case,
    source: &Path,
    receipts: &mut Vec<String>,
) -> Result<()> {
    let names: &[&str] = match case.name() {
        "mixed-later" => &["disabled-text-insert", "disabled-memo-row", "resource"],
        "unique-text" => &["unique-empty"],
        _ => &[],
    };
    let original = fs::read(source)?;
    for &name in names {
        let before = directory.join(format!("refusal-{name}-before.mdb"));
        let after = directory.join(format!("refusal-{name}-after.mdb"));
        fs::write(before, &original)?;
        fs::write(&after, &original)?;
        let error = if name == "disabled-memo-row" {
            let mut row = case.row(4);
            row[5] = Scalar::Memo(vec![]);
            jet3::update_row(
                &after,
                RowUpdate {
                    table: b"Items",
                    row: locate(&after, 4)?,
                    values: &values(&row),
                },
                &mut budget(),
            )
        } else {
            let mut row = case.row(9999);
            if name == "disabled-text-insert" {
                row[4] = Scalar::Text(vec![]);
            }
            if name == "unique-empty" {
                row[1] = Scalar::Text(vec![]);
            }
            let mut work = if name == "resource" {
                ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(0))
            } else {
                budget()
            };
            jet3::insert_row(&after, b"Items", &values(&row), &mut work).map(|_| ())
        }
        .err()
        .ok_or("invalid request accepted")?;
        if fs::read(&after)? != original {
            return Err("refusal changed image".into());
        }
        receipts.push(format!(
            "{{\"name\":{},\"case\":{},\"error\":{},\"preserved\":true}}",
            quote(name),
            quote(case.name()),
            quote(&format!("{error:?}"))
        ));
    }
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
    for operation in [
        Operation::Insert(case.row(1234567)),
        Operation::Field(1234567, 0, Scalar::Long(1234568)),
        Operation::Delete(9001),
    ] {
        mutate(&working, &mut model, operation)?;
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
        return continuation(Path::new(source), Path::new(directory), &Case::new(name)?);
    }
    let [directory] = args.as_slice() else {
        return Err(
            "usage: empty_value_candidate NEW_DIRECTORY | continue SOURCE NEW_DIRECTORY CASE"
                .into(),
        );
    };
    let directory = Path::new(directory);
    fs::create_dir(directory)?;
    let mut receipts = Vec::new();
    for name in CASES {
        let case = Case::new(name)?;
        let working = directory.join(format!("{name}-working.mdb"));
        let mut model = (0..48)
            .map(|id| (id, case.row(id)))
            .collect::<BTreeMap<_, _>>();
        create(&working, &case, &model)?;
        normalize(&mut model);
        for (phase, operations) in case.stages() {
            for operation in operations {
                mutate(&working, &mut model, operation)?;
            }
            save(directory, &case, phase, &working, &model)?;
            if phase == "original" {
                refusals(directory, &case, &working, &mut receipts)?;
            }
        }
        fs::remove_file(working)?;
    }
    fs::write(
        directory.join("refusals.json"),
        format!("[{}]\n", receipts.join(",")),
    )?;
    Ok(())
}
