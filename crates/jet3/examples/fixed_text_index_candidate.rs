//! Fixed Text index creation, field/full-row changes and native input continuations.
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
#[path = "wide_row_support/model.rs"]
mod model;
use model::*;
#[path = "wide_row_support/io.rs"]
mod io;
use io::*;
#[path = "wide_row_support/create.rs"]
mod create;
use create::create;

const CASES: [(&str, u8); 4] = [
    ("fixed1", 1),
    ("fixed8", 8),
    ("fixed32", 32),
    ("fixed255", 255),
];
struct Case {
    name: &'static str,
    width: std::num::NonZeroU8,
}
impl Case {
    fn name(&self) -> &str {
        self.name
    }
    fn columns(&self) -> Vec<ColumnSpec<'static>> {
        vec![
            ColumnSpec::new(b"Id", ColumnType::Long),
            ColumnSpec::new(b"Code", ColumnType::FixedText { len: self.width }),
            ColumnSpec::new(b"Group", ColumnType::Long),
            ColumnSpec::new(b"Body", ColumnType::Memo),
            ColumnSpec::new(b"Blob", ColumnType::LongBinary),
        ]
    }
    fn index_fields(&self) -> Vec<Vec<IndexColumnSpec<'static>>> {
        vec![
            vec![IndexColumnSpec::ascending(0)],
            vec![IndexColumnSpec::ascending(1)],
            vec![IndexColumnSpec::descending(1)],
            vec![
                IndexColumnSpec::descending(1),
                IndexColumnSpec::ascending(2),
            ],
        ]
    }
    fn indexes<'a>(&self, fields: &'a [Vec<IndexColumnSpec<'static>>]) -> Vec<IndexSpec<'a>> {
        fields
            .iter()
            .zip([
                (b"ById".as_slice(), IndexKind::Primary),
                (b"ByCode", IndexKind::Ordinary),
                (
                    b"ByCodeDesc",
                    IndexKind::Ordinary.with_null_policy(jet3::IndexNullPolicy::IgnoreAllNull),
                ),
                (b"ByPair", IndexKind::Ordinary),
            ])
            .map(|(fields, (name, kind))| IndexSpec { name, fields, kind })
            .collect()
    }
    fn code(&self, seed: i32) -> Vec<u8> {
        if seed % 11 == 0 {
            let mut bytes = vec![b' '; usize::from(self.width.get())];
            bytes[0] = if (seed / 11) % 2 == 0 { b'A' } else { b'a' };
            bytes
        } else {
            payload(usize::from(self.width.get()), seed, 1, true)
        }
    }
    fn row(&self, id: i32) -> Row {
        vec![
            Scalar::Long(id),
            if id % 13 == 0 {
                Scalar::Null
            } else {
                Scalar::Text(self.code(id))
            },
            if id % 7 == 0 {
                Scalar::Null
            } else {
                Scalar::Long(id % 5)
            },
            Scalar::Memo(payload(80, id, 3, true)),
            Scalar::Ole(payload(80, id, 4, false)),
        ]
    }
    fn stages(&self) -> Vec<(&'static str, Vec<Operation>)> {
        let mut clear = self.row(2);
        clear[1] = Scalar::Null;
        let mut present = self.row(13);
        present[1] = Scalar::Text(self.code(101));
        let mut edited = vec![
            Operation::Field(1, 1, Scalar::Text(self.code(100))),
            Operation::Replace(2, clear),
            Operation::Replace(13, present),
            Operation::Field(7, 0, Scalar::Long(700)),
            Operation::Delete(3),
        ];
        edited.extend((1000..1012).map(|id| Operation::Insert(self.row(id))));
        let mut regrown = (20..60).map(Operation::Delete).collect::<Vec<_>>();
        regrown.extend((2000..2040).map(|id| Operation::Insert(self.row(id))));
        vec![
            ("original", vec![]),
            ("edited", edited),
            ("regrown", regrown),
        ]
    }
}
fn mutate(path: &Path, model: &mut BTreeMap<i32, Row>, operation: Operation) -> Result<()> {
    if let Operation::Field(id, 1, Scalar::Text(value)) = operation {
        jet3::update_field(
            path,
            FieldUpdate {
                table: b"Items",
                row: locate(path, id)?,
                column: column_ordinal(path, 1)?,
                value: RowValue::Text(&value),
            },
            &mut budget(),
        )?;
        model.get_mut(&id).ok_or("fixed-field row absent")?[1] = Scalar::Text(value);
        Ok(())
    } else {
        apply(path, model, operation)
    }
}
fn refusals(directory: &Path, source: &Path, case: &Case) -> Result<()> {
    let original = fs::read(source)?;
    let mut receipts = Vec::new();
    for name in ["width", "duplicate", "resource"] {
        let before = directory.join(format!("refusal-{name}-before.mdb"));
        let after = directory.join(format!("refusal-{name}-after.mdb"));
        fs::write(&before, &original)?;
        fs::write(&after, &original)?;
        let error = if name == "width" {
            let short = vec![b'x'; usize::from(case.width.get()) - 1];
            jet3::update_field(
                &after,
                FieldUpdate {
                    table: b"Items",
                    row: locate(&after, 1)?,
                    column: column_ordinal(&after, 1)?,
                    value: RowValue::Text(&short),
                },
                &mut budget(),
            )
        } else {
            let row = case.row(if name == "duplicate" { 1 } else { 9999 });
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
    let case = |name: &str| -> Result<Case> {
        let &(name, width) = CASES
            .iter()
            .find(|(n, _)| *n == name)
            .ok_or("unknown case")?;
        Ok(Case {
            name,
            width: std::num::NonZeroU8::new(width).ok_or("zero width")?,
        })
    };
    if let [mode, source, directory, name] = args.as_slice()
        && mode == "continue"
    {
        return continuation(Path::new(source), Path::new(directory), &case(name)?);
    }
    let [directory] = args.as_slice() else {
        return Err(
            "usage: fixed_text_index_candidate NEW_DIRECTORY | continue SOURCE NEW_DIRECTORY CASE"
                .into(),
        );
    };
    let directory = Path::new(directory);
    fs::create_dir(directory)?;
    for (name, _) in CASES {
        let case = case(name)?;
        let working = directory.join(format!("{name}-working.mdb"));
        let mut model = (0..96)
            .map(|id| (id, case.row(id)))
            .collect::<BTreeMap<_, _>>();
        create(&working, &case, &model)?;
        for (phase, operations) in case.stages() {
            for operation in operations {
                mutate(&working, &mut model, operation)?;
            }
            save(directory, &case, phase, &working, &model)?;
            if name == "fixed8" && phase == "original" {
                refusals(directory, &working, &case)?;
            }
        }
        fs::remove_file(working)?;
    }
    Ok(())
}
