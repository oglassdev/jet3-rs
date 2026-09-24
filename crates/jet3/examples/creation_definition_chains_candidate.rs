//! Exact definition-boundary candidates with populated/indexed first and later tables.
use jet3::{
    ColumnSpec, ColumnType, IndexColumnSpec, IndexKind, IndexSpec, ResourceBudget, ResourceLimits,
    RowValue, TableRows, TableSpec,
};
use std::{fs, path::Path};
#[path = "creation_definition_chains_support/snapshot.rs"]
mod snapshot;
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

#[derive(Clone, Copy)]
struct Case {
    name: &'static str,
    length: usize,
    columns: usize,
    count: i32,
    indexes: usize,
    payloads: bool,
    generated: bool,
    later: bool,
}
const CASES: [Case; 6] = [
    Case {
        name: "root-full",
        length: 2048,
        columns: 64,
        count: 3,
        indexes: 0,
        payloads: false,
        generated: false,
        later: false,
    },
    Case {
        name: "root-over",
        length: 2049,
        columns: 64,
        count: 0,
        indexes: 0,
        payloads: false,
        generated: false,
        later: true,
    },
    Case {
        name: "one-full",
        length: 4088,
        columns: 64,
        count: 205,
        indexes: 3,
        payloads: false,
        generated: false,
        later: false,
    },
    Case {
        name: "one-over",
        length: 4089,
        columns: 64,
        count: 17,
        indexes: 3,
        payloads: true,
        generated: false,
        later: true,
    },
    Case {
        name: "two-full",
        length: 6128,
        columns: 96,
        count: 3,
        indexes: 3,
        payloads: true,
        generated: false,
        later: false,
    },
    Case {
        name: "two-over",
        length: 6129,
        columns: 96,
        count: 205,
        indexes: 3,
        payloads: true,
        generated: true,
        later: true,
    },
];

fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}

fn names(case: Case) -> Vec<Vec<u8>> {
    let mut names = (0..case.columns)
        .map(|n| format!("C{n:04}").into_bytes())
        .collect::<Vec<_>>();
    names[0] = b"Id".to_vec();
    names[1] = b"Tag".to_vec();
    if case.payloads {
        names[case.columns - 2] = b"Body".to_vec();
        names[case.columns - 1] = b"Blob".to_vec();
    }
    // EXP-0059 record/name widths; EXP-0077 contributes ten bytes per LVAL map group.
    let base = 45
        + 19 * names.len()
        + names.iter().map(Vec::len).sum::<usize>()
        + if case.indexes == 0 { 0 } else { 219 }
        + if case.payloads { 20 } else { 0 };
    let padded = case.columns - 2 - if case.payloads { 2 } else { 0 };
    for n in 0..case.length - base {
        names[2 + n % padded].push(b'x');
    }
    names
}

fn payload(id: i32, binary: bool) -> Option<Vec<u8>> {
    let length = match id {
        1 => 32,
        2 => 33,
        3 => 2036,
        4 => 2037,
        5 => 4096,
        _ => return None,
    };
    Some(
        (0..length)
            .map(|n| {
                if binary {
                    ((17 * n + id as usize) % 256) as u8
                } else {
                    b'A' + ((n + id as usize) % 26) as u8
                }
            })
            .collect(),
    )
}

fn create(path: &Path, case: Case) -> Result<()> {
    let names = names(case);
    let columns = names
        .iter()
        .enumerate()
        .map(|(n, name)| {
            ColumnSpec::new(
                name,
                if n == 0 && case.generated {
                    ColumnType::AutoIncrement
                } else if case.payloads && n + 2 == case.columns {
                    ColumnType::Memo
                } else if case.payloads && n + 1 == case.columns {
                    ColumnType::LongBinary
                } else if case.payloads && n > 1 {
                    ColumnType::Byte
                } else {
                    ColumnType::Long
                },
            )
        })
        .collect::<Vec<_>>();
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
    let payloads = (1..=case.count)
        .map(|id| [payload(id, false), payload(id, true)])
        .collect::<Vec<_>>();
    let values = (1..=case.count)
        .map(|id| {
            let mut row = (0..case.columns)
                .map(|n| {
                    if n == 0 {
                        if case.generated {
                            RowValue::AutoIncrement
                        } else {
                            RowValue::Long(id)
                        }
                    } else if n == 1 {
                        RowValue::Long(id % 11 - 5)
                    } else if (id as usize + n).is_multiple_of(7) {
                        RowValue::Null
                    } else if case.payloads {
                        RowValue::Byte(((id as usize + n) % 251) as u8)
                    } else {
                        RowValue::Long(id * 1000 + n as i32)
                    }
                })
                .collect::<Vec<_>>();
            if case.payloads {
                row[case.columns - 2] = payloads[id as usize - 1][0]
                    .as_deref()
                    .map_or(RowValue::Null, RowValue::Memo);
                row[case.columns - 1] = payloads[id as usize - 1][1]
                    .as_deref()
                    .map_or(RowValue::Null, RowValue::LongBinary);
            }
            row
        })
        .collect::<Vec<_>>();
    let rows = values.iter().map(Vec::as_slice).collect::<Vec<_>>();
    let items = TableRows {
        table: TableSpec {
            validation: jet3::TableValidation::NONE,
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
    let note_body = [b'n'; 4096];
    let notes = TableRows {
        table: TableSpec {
            validation: jet3::TableValidation::NONE,
            name: b"Notes",
            columns: &note_columns,
            indexes: &[],
        },
        rows: &[
            &[RowValue::Long(7), RowValue::Memo(&note_body)],
            &[RowValue::Long(8), RowValue::Null],
        ],
    };
    jet3::create_database(
        path,
        &jet3::DatabaseSpec {
            tables: &if case.later {
                [notes, items]
            } else {
                [items, notes]
            },
            ..jet3::DatabaseSpec::default()
        },
        &mut budget(),
    )?;
    Ok(())
}

fn main() -> Result<()> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    if args.first().is_some_and(|s| s == "inspect") {
        return snapshot::snapshot(
            Path::new(args.get(1).ok_or("inspect MDB OUTPUT")?),
            Path::new(args.get(2).ok_or("inspect MDB OUTPUT")?),
        );
    }
    let directory = Path::new(
        args.first()
            .ok_or("usage: creation_definition_chains_candidate NEW_DIRECTORY")?,
    );
    fs::create_dir(directory)?;
    for case in CASES {
        let path = directory.join(format!("{}.mdb", case.name));
        create(&path, case)?;
        snapshot::snapshot(
            &path,
            &directory.join(format!("{}.snapshot.json", case.name)),
        )?;
    }
    Ok(())
}
