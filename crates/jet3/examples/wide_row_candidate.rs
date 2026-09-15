//! Wide variable-row creation, shrink/regrow, indexed mutations and native inputs.
use jet3::{
    ColumnOrdinal, ColumnRef, ColumnSpec, ColumnType, DatabaseReader, FieldUpdate, FileSource,
    IndexColumnSpec, IndexDirection, IndexKind, IndexSpec, InlineLongValue, LongValue,
    LongValueChunkValue, ResourceBudget, ResourceLimits, RowDelete, RowLocator, RowUpdate,
    RowValue, TableDefinition, TableRows, TableSpec, TextCodePage, ValueKind,
};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::Path,
};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
const MEMO: &[u8; 4096] = &[b'n'; 4096];
const CASES: [(&str, usize, usize, bool); 8] = [
    ("vars2", 2, 5, false),
    ("vars3", 3, 5, false),
    ("vars8", 8, 5, false),
    ("vars32", 32, 5, false),
    ("vars254", 254, 5, false),
    ("fixed260", 2, 260, false),
    ("fixed767", 2, 767, false),
    ("mixed", 4, 5, true),
];
#[path = "wide_row_support/model.rs"]
mod model;
use model::*;
#[path = "wide_row_support/io.rs"]
mod io;
use io::*;
#[path = "wide_row_support/create.rs"]
mod create;
use create::create;

struct Case {
    name: &'static str,
    variables: usize,
    fixed: usize,
    mixed: bool,
    names: Vec<Vec<u8>>,
}
impl Case {
    fn new((name, variables, fixed, mixed): (&'static str, usize, usize, bool)) -> Self {
        let fixed_columns = (fixed - 5).div_ceil(255);
        let names = std::iter::once(b"Id".to_vec())
            .chain((0..fixed_columns).map(|i| format!("F{i:03}").into_bytes()))
            .chain((0..variables).map(|i| format!("V{i:03}").into_bytes()))
            .collect();
        Self {
            name,
            variables,
            fixed,
            mixed,
            names,
        }
    }
    fn name(&self) -> &str {
        self.name
    }
    fn first_variable(&self) -> usize {
        1 + (self.fixed - 5).div_ceil(255)
    }
    fn columns(&self) -> Vec<ColumnSpec<'_>> {
        self.names
            .iter()
            .enumerate()
            .map(|(i, name)| {
                let kind = if i == 0 {
                    ColumnType::Long
                } else if i < self.first_variable() {
                    ColumnType::FixedText {
                        len: std::num::NonZeroU8::new(
                            (self.fixed - 5 - 255 * (i - 1)).min(255) as u8
                        )
                        .unwrap_or(std::num::NonZeroU8::MIN),
                    }
                } else {
                    let v = i - self.first_variable();
                    if self.mixed && v == 2 {
                        ColumnType::Memo
                    } else if self.mixed && v == 3 {
                        ColumnType::LongBinary
                    } else if self.mixed || v.is_multiple_of(2) {
                        ColumnType::Text {
                            max_len: std::num::NonZeroU8::MAX,
                        }
                    } else {
                        ColumnType::Binary {
                            max_len: std::num::NonZeroU8::MAX,
                        }
                    }
                };
                ColumnSpec::new(name, kind)
            })
            .collect()
    }
    fn index_fields(&self) -> Vec<Vec<IndexColumnSpec<'static>>> {
        let mut fields = vec![vec![IndexColumnSpec {
            column: ColumnRef::Ordinal(0),
            direction: IndexDirection::Ascending,
        }]];
        if self.variables <= 8 {
            fields.push(vec![IndexColumnSpec {
                column: ColumnRef::Ordinal(self.first_variable() as u16),
                direction: IndexDirection::Descending,
            }]);
        }
        fields
    }
    fn indexes<'a>(&self, fields: &'a [Vec<IndexColumnSpec<'static>>]) -> Vec<IndexSpec<'a>> {
        fields
            .iter()
            .enumerate()
            .map(|(i, fields)| IndexSpec {
                name: if i == 0 { b"ById" } else { b"ByText" },
                fields,
                kind: if i == 0 {
                    IndexKind::Primary
                } else {
                    IndexKind::Ordinary
                },
            })
            .collect()
    }
    fn maximum(&self) -> usize {
        match self.variables {
            2 => 510,
            3 => 765,
            8 => 1988,
            32 => 1600,
            254 => 1400,
            _ => 510,
        }
    }
    fn row(&self, id: i32) -> Row {
        self.sized_row(id, self.maximum(), false)
    }
    fn sized_row(&self, id: i32, total: usize, sparse: bool) -> Row {
        let mut row = vec![Scalar::Long(id)];
        for i in 1..self.first_variable() {
            row.push(Scalar::Text(payload(
                (self.fixed - 5 - 255 * (i - 1)).min(255),
                id,
                i,
                true,
            )));
        }
        let vars = if self.mixed { 2 } else { self.variables };
        for i in 0..vars {
            let width = total / vars + usize::from(i < total % vars);
            let text = self.mixed || i.is_multiple_of(2);
            row.push(if sparse && i + 1 != vars {
                Scalar::Null
            } else {
                let p = payload(
                    if sparse { 1 } else { width },
                    id,
                    i + self.first_variable(),
                    text,
                );
                if text {
                    Scalar::Text(p)
                } else {
                    Scalar::Binary(p)
                }
            });
        }
        if self.mixed {
            row.push(Scalar::Memo(payload(80, id, 30, true)));
            row.push(Scalar::Ole(payload(80, id, 31, false)));
        }
        row
    }
    fn inserted(&self, id: i32) -> Row {
        let widths = [240, 249, 495, 751];
        self.sized_row(
            id,
            widths[(id - 1000) as usize]
                .min(self.maximum())
                .max(self.variables),
            false,
        )
    }
    fn stages(&self) -> Vec<(&'static str, Vec<Operation>)> {
        let mut regrown = vec![Operation::Replace(0, self.row(0))];
        regrown.extend((1000..1004).map(|id| Operation::Insert(self.inserted(id))));
        regrown.extend([
            Operation::Delete(1),
            Operation::Field(7, 0, Scalar::Long(99)),
            Operation::Delete(2),
        ]);
        vec![
            ("original", vec![]),
            (
                "sparse",
                vec![Operation::Replace(0, self.sized_row(0, 1, true))],
            ),
            ("regrown", regrown),
        ]
    }
}
fn refusals(directory: &Path, source: &Path, case: &Case) -> Result<()> {
    let locator = locate(source, 0)?;
    let original = fs::read(source)?;
    let base = locator.page().get() as usize * 2048;
    let slot = usize::from(locator.slot());
    let word = |n| u16::from_le_bytes([original[n], original[n + 1]]) as usize & 0x1fff;
    let start = base + word(base + 10 + 2 * slot);
    let end = base
        + if slot == 0 {
            2048
        } else {
            word(base + 8 + 2 * slot)
        };
    let null = usize::from(original[start]).div_ceil(8);
    let count = end - null - 1;
    let jumps = (end - start - 1) / 256;
    let jump = count - jumps;
    let low = jump - case.variables - 1;
    let mut receipts = Vec::new();
    for (name, offset, value) in [
        ("jump-ordinal", jump, 254),
        ("end-low", low, original[low] ^ 1),
        ("variable-count", count, 255),
    ] {
        let mut damaged = original.clone();
        damaged[offset] = value;
        let before = directory.join(format!("refusal-{name}-before.mdb"));
        let after = directory.join(format!("refusal-{name}-after.mdb"));
        fs::write(&before, &damaged)?;
        fs::write(&after, &damaged)?;
        let error = jet3::update_row(
            &after,
            RowUpdate {
                table: b"Items",
                row: locator,
                values: &values(&case.row(0)),
            },
            &mut budget(),
        )
        .err()
        .ok_or("malformed row accepted")?;
        if fs::read(&after)? != damaged {
            return Err("corruption refusal changed complete image".into());
        }
        receipts.push(format!(
            "{{\"name\":{},\"offset\":{offset},\"value\":{value},\"error\":{},\"preserved\":true}}",
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
        let case = CASES
            .into_iter()
            .find(|c| c.0 == name)
            .ok_or("unknown case")?;
        return continuation(Path::new(source), Path::new(directory), &Case::new(case));
    }
    let [directory] = args.as_slice() else {
        return Err(
            "usage: wide_row_candidate NEW_DIRECTORY | continue SOURCE NEW_DIRECTORY CASE".into(),
        );
    };
    let directory = Path::new(directory);
    fs::create_dir(directory)?;
    for spec in CASES {
        let case = Case::new(spec);
        let working = directory.join(format!("{}-working.mdb", case.name));
        let mut model = (0..16)
            .map(|id| (id, case.row(id)))
            .collect::<BTreeMap<_, _>>();
        create(&working, &case, &model)?;
        for (phase, operations) in case.stages() {
            for operation in operations {
                apply(&working, &mut model, operation)?;
            }
            save(directory, &case, phase, &working, &model)?;
            if case.name == "vars3" && phase == "original" {
                refusals(directory, &working, &case)?;
            }
        }
        fs::remove_file(working)?;
    }
    Ok(())
}
