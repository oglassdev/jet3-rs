//! Repeatable public-API Memo/OLE insertion, replacement, deletion, and reuse.
mod long_value_lifecycle_support;
use jet3::{
    ColumnSpec, ColumnType, DatabaseReader, IndexColumnSpec, IndexKind, IndexSpec, ResourceBudget,
    ResourceLimits, RowDelete, RowLocator, RowUpdate, RowValue, TableRows, TableSpec, TextCodePage,
    ValueKind,
};
use long_value_lifecycle_support::{Result, budget, definition, quote, snapshot};
use std::{fs, path::Path};

#[derive(Clone, Copy)]
struct Case {
    name: &'static str,
    types: &'static [ColumnType],
    generated: bool,
}
const CASES: [Case; 4] = [
    Case {
        name: "memo",
        types: &[ColumnType::Memo],
        generated: false,
    },
    Case {
        name: "ole",
        types: &[ColumnType::LongBinary],
        generated: false,
    },
    Case {
        name: "mixed",
        types: &[
            ColumnType::Memo,
            ColumnType::LongBinary,
            ColumnType::Memo,
            ColumnType::LongBinary,
        ],
        generated: false,
    },
    Case {
        name: "auto-mixed",
        types: &[
            ColumnType::Memo,
            ColumnType::LongBinary,
            ColumnType::Memo,
            ColumnType::LongBinary,
        ],
        generated: true,
    },
];
const NAMES: [&[u8]; 4] = [b"Body", b"Blob", b"ExtraMemo", b"ExtraBlob"];
const LENGTHS: [Option<usize>; 12] = [
    Some(33),
    Some(512),
    Some(2036),
    Some(2037),
    Some(32),
    None,
    Some(1),
    Some(4096),
    Some(33),
    Some(33),
    Some(12),
    Some(2048),
];

fn payload(seed: i32, column: usize, kind: ColumnType, length: Option<usize>) -> Option<Vec<u8>> {
    length.map(|n| {
        (0..n)
            .map(|offset| {
                if kind == ColumnType::Memo {
                    b'A' + ((offset + seed as usize + column) % 26) as u8
                } else {
                    ((offset * 17 + seed as usize * 3 + column) % 256) as u8
                }
            })
            .collect()
    })
}
fn lengths(seed: i32) -> [Option<usize>; 4] {
    std::array::from_fn(|c| LENGTHS[(seed as usize - 1 + 3 * c) % LENGTHS.len()])
}
fn values<'a>(
    case: Case,
    id: i32,
    tag: i32,
    inserting: bool,
    payloads: &'a [Option<Vec<u8>>],
) -> Vec<RowValue<'a>> {
    let mut row = vec![
        if case.generated && inserting {
            RowValue::AutoIncrement
        } else {
            RowValue::Long(id)
        },
        RowValue::Long(tag),
    ];
    row.extend(
        payloads
            .iter()
            .zip(case.types)
            .map(|(bytes, kind)| match bytes {
                None => RowValue::Null,
                Some(bytes) if *kind == ColumnType::Memo => RowValue::Memo(bytes),
                Some(bytes) => RowValue::LongBinary(bytes),
            }),
    );
    row
}
fn locate(path: &Path, id: i32) -> Result<RowLocator> {
    let mut b = budget();
    let mut db = DatabaseReader::open(path, &mut b)?;
    let table = definition(&mut db, b"Items", &mut b)?;
    let mut cursor = db.rows(&table, &mut b)?;
    while let Some(mut row) = cursor.next_row()? {
        if matches!(row.value(table.columns()[0].ordinal(), TextCodePage::Windows1252)?.ok_or("Id")?.kind(), ValueKind::Long(n) if *n == id)
        {
            return Ok(row.locator());
        }
    }
    Err(format!("missing Id {id}").into())
}
fn put(
    path: &Path,
    case: Case,
    id: i32,
    tag: i32,
    seed: i32,
    sizes: [Option<usize>; 4],
    insert: bool,
) -> Result<()> {
    let payloads = case
        .types
        .iter()
        .enumerate()
        .map(|(c, kind)| payload(seed, c, *kind, sizes[c]))
        .collect::<Vec<_>>();
    let row = values(case, id, tag, insert, &payloads);
    if insert {
        jet3::insert_row(path, b"Items", &row, &mut budget())?;
        locate(path, id)?;
    } else {
        jet3::update_row(
            path,
            RowUpdate {
                table: b"Items",
                row: locate(path, id)?,
                values: &row,
            },
            &mut budget(),
        )?;
    }
    Ok(())
}
fn delete(path: &Path, id: i32) -> Result<()> {
    Ok(jet3::delete_row(
        path,
        RowDelete {
            table: b"Items",
            row: locate(path, id)?,
        },
        &mut budget(),
    )?)
}
fn create(path: &Path, case: Case) -> Result<()> {
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
        case.types
            .iter()
            .enumerate()
            .map(|(c, kind)| ColumnSpec::new(NAMES[c], *kind)),
    );
    let id = [IndexColumnSpec::ascending(b"Id")];
    let tag_desc = [IndexColumnSpec::descending(b"Tag")];
    let tag_asc = [IndexColumnSpec::ascending(b"Tag")];
    let indexes = [
        IndexSpec {
            name: b"ById",
            fields: &id,
            kind: IndexKind::Primary,
        },
        IndexSpec {
            name: b"ByTag",
            fields: &tag_desc,
            kind: IndexKind::Ordinary,
        },
        IndexSpec {
            name: b"ByUniqueTag",
            fields: &tag_asc,
            kind: IndexKind::Unique,
        },
    ];
    let items = TableRows {
        table: TableSpec {
            name: b"Items",
            columns: &columns,
            indexes: &indexes,
        },
        rows: &[],
    };
    let note_columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"Body", ColumnType::Memo),
    ];
    let notes = TableRows {
        table: TableSpec {
            name: b"Notes",
            columns: &note_columns,
            indexes: &[],
        },
        rows: &[
            &[RowValue::Long(7), RowValue::Memo(&[b'n'; 4096])],
            &[RowValue::Long(8), RowValue::Null],
        ],
    };
    let requests = if case.generated {
        [notes, items]
    } else {
        [items, notes]
    };
    jet3::create_database_with_table_rows(path, &requests, &mut budget())?;
    Ok(())
}
fn checkpoint(directory: &Path, path: &Path, case: Case, phase: &str) -> Result<()> {
    let output = directory.join(format!("{}-{phase}.mdb", case.name));
    fs::copy(path, &output)?;
    snapshot(
        &output,
        &directory.join(format!("{}-{phase}.snapshot.json", case.name)),
    )
}
fn refusals(directory: &Path, path: &Path, case: Case, reports: &mut Vec<String>) -> Result<()> {
    let original = fs::read(path)?;
    let invalid_payload = if case.name == "ole" {
        "caller-header"
    } else {
        "empty-payload"
    };
    for kind in ["duplicate-later-index", invalid_payload, "chain-budget"] {
        let target = directory.join(format!("{}-refused-{kind}.mdb", case.name));
        fs::write(&target, &original)?;
        let n = if kind == "empty-payload" { 0 } else { 8192 };
        let payloads = case
            .types
            .iter()
            .enumerate()
            .map(|(c, t)| payload(999, c, *t, Some(n)))
            .collect::<Vec<_>>();
        let mut row = values(
            case,
            999,
            if kind == "duplicate-later-index" {
                90
            } else {
                9990
            },
            true,
            &payloads,
        );
        if kind == "caller-header" {
            row[2] = RowValue::LongValue(&[0; 12]);
        }
        let mut b = if kind == "chain-budget" {
            ResourceBudget::new(ResourceLimits::default().with_max_chain_depth(2))
        } else {
            budget()
        };
        let result = jet3::insert_row(&target, b"Items", &row, &mut b);
        let error = result.err().ok_or("expected refusal")?;
        if fs::read(&target)? != original {
            return Err(format!("refusal changed bytes: {kind}").into());
        }
        reports.push(format!(
            "{{\"case\":{},\"kind\":{},\"file\":{},\"error\":{},\"bytes_unchanged\":true}}",
            quote(case.name),
            quote(kind),
            quote(target.file_name().ok_or("name")?.to_str().ok_or("utf8")?),
            quote(&format!("{error:?}"))
        ));
    }
    Ok(())
}
fn generate(directory: &Path, explicit_only: bool) -> Result<()> {
    fs::create_dir(directory)?;
    let mut reports = Vec::new();
    for case in CASES.into_iter().filter(|c| !explicit_only || !c.generated) {
        let path = directory.join(format!("{}-working.mdb", case.name));
        create(&path, case)?;
        checkpoint(directory, &path, case, "initial")?;
        for id in 1..=12 {
            put(&path, case, id, id * 10, id, lengths(id), true)?;
        }
        checkpoint(directory, &path, case, "inserted")?;
        refusals(directory, &path, case, &mut reports)?;
        delete(&path, 1)?;
        put(
            &path,
            case,
            9,
            90,
            91,
            [Some(1), Some(32), None, Some(12)],
            false,
        )?;
        put(
            &path,
            case,
            2,
            -20,
            22,
            [Some(4096), Some(2037), Some(33), Some(1)],
            false,
        )?;
        put(
            &path,
            case,
            6,
            60,
            66,
            [Some(33), Some(32), Some(2036), Some(4096)],
            false,
        )?;
        checkpoint(directory, &path, case, "edited")?;
        let peak = fs::metadata(&path)?.len();
        for id in 2..=12 {
            delete(&path, id)?;
        }
        checkpoint(directory, &path, case, "empty")?;
        for seed in 1..=12 {
            let id = if case.generated {
                12 + seed
            } else {
                100 + seed
            };
            put(&path, case, id, seed * 10, seed, lengths(seed), true)?;
        }
        if fs::metadata(&path)?.len() > peak {
            return Err(format!("{} reinsert grew EOF", case.name).into());
        }
        checkpoint(directory, &path, case, "reinserted")?;
        fs::remove_file(path)?;
    }
    fs::write(
        directory.join("refusals.json"),
        format!("[{}]\n", reports.join(",")),
    )?;
    Ok(())
}
fn continuation(case: Case, source: &Path, directory: &Path) -> Result<()> {
    fs::create_dir(directory)?;
    let path = directory.join(format!("{}-continued.mdb", case.name));
    fs::copy(source, &path)?;
    let first = if case.generated { 13 } else { 101 };
    put(
        &path,
        case,
        if case.generated { 26 } else { 9001 },
        9010,
        901,
        [Some(4096), Some(2036), Some(33), Some(2048)],
        true,
    )?;
    put(
        &path,
        case,
        first + 2,
        -44,
        44,
        [Some(12), Some(2036), Some(2048), None],
        false,
    )?;
    delete(&path, first + 3)?;
    snapshot(
        &path,
        &directory.join(format!("{}-continued.snapshot.json", case.name)),
    )
}
fn main() -> Result<()> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    match args.first().map(String::as_str) {
        Some("inspect") => snapshot(
            Path::new(args.get(1).ok_or("inspect MDB JSON")?),
            Path::new(args.get(2).ok_or("inspect MDB JSON")?),
        ),
        Some("continue") => {
            let case = CASES
                .into_iter()
                .find(|c| Some(c.name) == args.get(1).map(String::as_str))
                .ok_or("continue CASE SOURCE NEW_DIRECTORY")?;
            continuation(
                case,
                Path::new(args.get(2).ok_or("source")?),
                Path::new(args.get(3).ok_or("output")?),
            )
        }
        Some(directory) => generate(
            Path::new(directory),
            args.get(1).is_some_and(|a| a == "--explicit-only"),
        ),
        None => Err("usage: long_value_lifecycle_candidate NEW_DIRECTORY".into()),
    }
}
