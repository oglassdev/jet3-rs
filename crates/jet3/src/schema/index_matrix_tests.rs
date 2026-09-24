//! Index class x null policy through creation and `ReplaceIndex`, over a
//! Required column and an AllowZeroLength column (EXP-0093/0148/0283/0297).
use crate::testkit::index;
use crate::testkit::table;
use crate::*;
use std::{fs, num::NonZeroU8};

use crate::testkit::{TempDir, TestResult};

use crate::testkit::budget;

const KINDS: [IndexKind; 3] = [IndexKind::Primary, IndexKind::Unique, IndexKind::Ordinary];
const POLICIES: [IndexNullPolicy; 3] = [
    IndexNullPolicy::Include,
    IndexNullPolicy::IgnoreAllNull,
    IndexNullPolicy::Required,
];
/// Column 1 is Required without AllowZeroLength; column 2 is the reverse.
const INDEXED: [u16; 2] = [1, 2];
const PROBES: [Option<&[u8]>; 6] = [Some(b"a"), None, None, Some(b""), Some(b""), Some(b"a")];

const TEXT: ColumnType = ColumnType::Text {
    max_len: NonZeroU8::MAX,
};
const COLUMNS: [ColumnSpec<'static>; 3] = [
    ColumnSpec::new(b"Id", ColumnType::Long),
    ColumnSpec::new(b"Code", TEXT).with_required(),
    ColumnSpec::new(b"Note", TEXT).with_allow_zero_length(),
];

fn create_indexed(
    path: &std::path::Path,
    kind: IndexKind,
    column: u16,
    rows: &[&[RowValue<'_>]],
) -> Result<(), WriteError> {
    let indexes = [IndexSpec {
        name: b"Key",
        kind,
        fields: &[IndexColumnSpec::ascending(column)],
    }];
    crate::testkit::create(
        path,
        &[TableRows {
            table: table(b"T", &COLUMNS, &indexes),
            rows,
        }],
    )
}

fn replace(path: &std::path::Path, kind: IndexKind, column: u16) -> Result<(), WriteError> {
    edit_schema(
        path,
        SchemaEdit::ReplaceIndex {
            table: b"T",
            index: b"Key",
            replacement: index(b"Key", &[IndexColumnSpec::ascending(column)], kind),
        },
        &mut budget(),
    )
}

fn row(id: i32, column: u16, probe: Option<&[u8]>) -> [RowValue<'_>; 3] {
    let value = probe.map_or(RowValue::Null, RowValue::Text);
    let mut values = [
        RowValue::Long(id),
        RowValue::Text(b"c"),
        RowValue::Text(b"n"),
    ];
    values[usize::from(column)] = value;
    values
}

/// Expected acceptance of each probe insert, and whether a null probe gets a key.
fn expected(kind: IndexKind, column: u16) -> ([bool; 6], bool) {
    let required_column = column == 1;
    let nulls = !required_column && kind.null_policy() != IndexNullPolicy::Required;
    let empties = !required_column;
    let unique = kind.is_unique();
    (
        [true, nulls, nulls, empties, empties && !unique, !unique],
        kind.null_policy() == IndexNullPolicy::Include,
    )
}

struct Observed {
    accepted: Vec<bool>,
    entries: usize,
    rows: u32,
    flags: u8,
    primary: bool,
}

fn probe(path: &std::path::Path, column: u16) -> Result<Observed, Box<dyn std::error::Error>> {
    let mut accepted = Vec::new();
    for (id, value) in (1..).zip(PROBES) {
        let before = fs::read(path)?;
        let ok = insert_row(path, b"T", &row(id, column, value), &mut budget()).is_ok();
        if !ok {
            assert_eq!(fs::read(path)?, before, "refused insert changed the file");
        }
        accepted.push(ok);
    }
    let mut database = DatabaseReader::open(path, &mut budget())?;
    let table = crate::write::update::indexed_writable_table(&mut database, b"T", &mut budget())?;
    let physical = table.physical_indexes().first().ok_or("physical index")?;
    let logical = table.indexes().first().ok_or("logical index")?;
    Ok(Observed {
        accepted,
        entries: database
            .index_tree(&table, 0, &mut budget())?
            .entries()
            .len(),
        rows: table.row_count(),
        flags: physical.raw_flags(),
        primary: matches!(logical.kind(), IndexDefinitionKind::Primary),
    })
}

#[test]
fn creation_and_replacement_agree_across_the_option_matrix() -> TestResult {
    for class in KINDS {
        for policy in POLICIES {
            let kind = class.with_null_policy(policy);
            for column in INDEXED {
                let label = format!("{kind:?} on column {column}");
                let created = TempDir::new("index-matrix")?;
                let replaced = TempDir::new("index-matrix")?;
                create_indexed(&replaced.target(), IndexKind::Ordinary, column, &[])?;
                let before = fs::read(replaced.target())?;
                if kind.is_primary() && policy != IndexNullPolicy::Required {
                    assert!(
                        create_indexed(&created.target(), kind, column, &[]).is_err(),
                        "{label}"
                    );
                    assert!(
                        replace(&replaced.target(), kind, column).is_err(),
                        "{label}"
                    );
                    assert_eq!(fs::read(replaced.target())?, before, "{label}");
                    continue;
                }
                create_indexed(&created.target(), kind, column, &[])?;
                replace(&replaced.target(), kind, column)?;
                let (accepted, null_keys) = expected(kind, column);
                let nulls = accepted[1..3].iter().filter(|ok| **ok).count();
                let pair = [
                    probe(&created.target(), column)?,
                    probe(&replaced.target(), column)?,
                ];
                assert_eq!(pair[0].flags, pair[1].flags, "{label}");
                for observed in pair {
                    assert_eq!(observed.accepted, accepted, "{label}");
                    let rows = accepted.iter().filter(|ok| **ok).count();
                    assert_eq!(observed.rows as usize, rows, "{label}");
                    let omitted = if null_keys { 0 } else { nulls };
                    assert_eq!(observed.entries, rows - omitted, "{label}");
                    assert_eq!(observed.primary, kind.is_primary(), "{label}");
                }
            }
        }
    }
    Ok(())
}

#[test]
fn replacement_over_existing_rows_checks_every_option() -> TestResult {
    let rows: [[RowValue<'_>; 3]; 4] = [
        row(1, 2, Some(b"a")),
        row(2, 2, None),
        row(3, 2, Some(b"")),
        row(4, 2, Some(b"")),
    ];
    let rows: Vec<&[RowValue<'_>]> = rows.iter().map(|row| row.as_slice()).collect();
    for class in KINDS {
        for policy in POLICIES {
            let kind = class.with_null_policy(policy);
            let dir = TempDir::new("index-matrix")?;
            create_indexed(&dir.target(), IndexKind::Ordinary, 2, &rows)?;
            let before = fs::read(dir.target())?;
            let fits = !kind.is_unique() && policy != IndexNullPolicy::Required;
            assert_eq!(replace(&dir.target(), kind, 2).is_ok(), fits, "{kind:?}");
            if !fits {
                assert_eq!(fs::read(dir.target())?, before, "{kind:?}");
            }
        }
        // The Required column has no nulls or empties, so every valid option fits.
        let dir = TempDir::new("index-matrix")?;
        let code: Vec<[RowValue<'_>; 3]> = [b"x", b"y"]
            .into_iter()
            .zip(1..)
            .map(|(code, id)| row(id, 1, Some(code.as_slice())))
            .collect();
        let code: Vec<&[RowValue<'_>]> = code.iter().map(|row| row.as_slice()).collect();
        create_indexed(&dir.target(), IndexKind::Ordinary, 1, &code)?;
        replace(&dir.target(), class, 1)?;
    }
    Ok(())
}
