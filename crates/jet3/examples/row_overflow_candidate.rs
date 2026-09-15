//! Public Rust mutations of retained native row-overflow lifecycle fixtures.
use jet3::{
    ColumnOrdinal, DatabaseReader, FieldUpdate, FileSource, InlineLongValue, LongValue,
    LongValueChunkValue, ResourceBudget, ResourceLimits, RowDelete, RowLocator, RowUpdate,
    RowValue, TableDefinition, TextCodePage, ValueKind,
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

struct Case {
    name: &'static str,
    mixed: bool,
    native_overflow: bool,
}
impl Case {
    fn name(&self) -> &str {
        self.name
    }
    fn selected(&self) -> [i32; 4] {
        if self.mixed {
            [0, 15, 31, 71]
        } else {
            [0, 27, 55, 71]
        }
    }
    fn row(&self, id: i32, width: usize) -> Row {
        let mut row = vec![
            Scalar::Long(id),
            Scalar::Text(payload(width, id, 1, true)),
            Scalar::Binary(payload(width, id, 2, false)),
        ];
        if self.mixed {
            row.extend([
                Scalar::Memo(payload(80, id, 3, true)),
                Scalar::Ole(payload(80, id, 4, false)),
            ]);
        }
        row
    }
}

fn short(row: &Row, seed: i32, width: usize, salt: usize) -> Row {
    let alphabet = b"aAezZ\xe9\xc9\xc6\xe6\xdf\x8a\x9a";
    let mut result = row.clone();
    result[1] = Scalar::Text(
        (0..width)
            .map(|n| alphabet[(n * 7 + seed as usize * 13 + 31 + salt * 5) % alphabet.len()])
            .collect(),
    );
    result[2] = Scalar::Binary(
        (0..width)
            .map(|n| ((n * 37 + seed as usize * 13 + 62 + salt * 17) % 256) as u8)
            .collect(),
    );
    result
}

fn replacements(
    model: &BTreeMap<i32, Row>,
    selected: &[i32],
    width: usize,
    salt: usize,
) -> Result<Vec<Operation>> {
    selected
        .iter()
        .map(|&id| {
            let seed = if id >= 900 { id - 900 } else { id };
            Ok(Operation::Replace(
                id,
                short(
                    model.get(&id).ok_or("missing selected row")?,
                    seed,
                    width,
                    salt,
                ),
            ))
        })
        .collect()
}

fn operations(case: &Case, phase: &str, model: &BTreeMap<i32, Row>) -> Result<Vec<Operation>> {
    let selected = case.selected();
    let renamed = selected[2] + 900;
    Ok(match phase {
        "original" => vec![],
        "grown" => replacements(model, &selected, 180, 0)?,
        "equal" => replacements(model, &selected[..2], 180, 1)?,
        "keyed" => vec![Operation::Field(selected[2], 0, Scalar::Long(renamed))],
        "shrunken" => replacements(model, &selected[..2], 1, 2)?,
        "filled" => (1000..1013)
            .map(|id| Operation::Insert(case.row(id, 12)))
            .collect(),
        "relocated" => replacements(
            model,
            &[selected[0], selected[1], renamed, selected[3]],
            255,
            3,
        )?,
        "shared" => (2000..2008)
            .map(|id| Operation::Insert(case.row(id, 150)))
            .collect(),
        "deleted" => [selected[0], selected[1], renamed, selected[3]]
            .into_iter()
            .map(Operation::Delete)
            .collect(),
        "released" => (1000..1013)
            .chain(2000..2008)
            .map(Operation::Delete)
            .collect(),
        "reinserted" => (3000..3008)
            .map(|id| Operation::Insert(case.row(id, 255)))
            .collect(),
        _ => return Err("unknown phase".into()),
    })
}

fn refusals(directory: &Path, source: &Path, case: &Case) -> Result<()> {
    let original = fs::read(source)?;
    let mut receipts = Vec::new();
    for name in ["duplicate-insert", "duplicate-replace", "resource"] {
        let before = directory.join(format!("refusal-{name}-before.mdb"));
        let after = directory.join(format!("refusal-{name}-after.mdb"));
        fs::write(&before, &original)?;
        fs::write(&after, &original)?;
        let row = case.row(if name == "resource" { 9999 } else { 0 }, 255);
        let error = match name {
            "duplicate-replace" => jet3::update_row(
                &after,
                RowUpdate {
                    table: b"Items",
                    row: locate(&after, case.selected()[1])?,
                    values: &values(&row),
                },
                &mut budget(),
            ),
            _ => {
                let mut operation = if name == "resource" {
                    ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(0))
                } else {
                    budget()
                };
                jet3::insert_row(&after, b"Items", &values(&row), &mut operation).map(|_| ())
            }
        }
        .err()
        .ok_or("invalid request accepted")?;
        if fs::read(after)? != original {
            return Err("refusal changed the input".into());
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

fn main() -> Result<()> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    let [directory, sources] = args.as_slice() else {
        return Err("usage: row_overflow_candidate NEW_DIRECTORY NATIVE_CAPTURE_DIRECTORY".into());
    };
    let directory = Path::new(directory);
    fs::create_dir(directory)?;
    for case in [
        Case {
            name: "ordinary",
            mixed: false,
            native_overflow: false,
        },
        Case {
            name: "payloads",
            mixed: true,
            native_overflow: false,
        },
        Case {
            name: "payloads-native",
            mixed: true,
            native_overflow: true,
        },
    ] {
        let source = Path::new(sources).join(format!(
            "{}-r1-{}.mdb",
            if case.mixed { "payloads" } else { "ordinary" },
            if case.native_overflow {
                "grown"
            } else {
                "original"
            }
        ));
        let input = directory.join(format!("{}-source.mdb", case.name));
        let working = directory.join(format!("{}-working.mdb", case.name));
        fs::copy(&source, &input)?;
        fs::copy(&source, &working)?;
        let mut operation = budget();
        let mut db = DatabaseReader::open(&source, &mut operation)?;
        let table = definition(&mut db, b"Items", &mut operation)?;
        let mut model = rows(&mut db, &table, &mut operation)?
            .into_iter()
            .map(|(id, (row, _))| (id, row))
            .collect::<BTreeMap<_, _>>();
        drop(db);
        for phase in [
            "original",
            "grown",
            "equal",
            "keyed",
            "shrunken",
            "filled",
            "relocated",
            "shared",
            "deleted",
            "released",
            "reinserted",
        ] {
            for action in operations(&case, phase, &model)? {
                apply(&working, &mut model, action)?;
            }
            save(directory, &case, phase, &working, &model)?;
            if case.name == "payloads" && phase == "grown" {
                refusals(directory, &working, &case)?;
            }
        }
        fs::remove_file(working)?;
    }
    Ok(())
}
