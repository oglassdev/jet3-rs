use super::cascade::*;
use crate::testkit::{create, create_spec};
use crate::testkit::{index, table};
use crate::{
    DatabaseReader, FileSource, PAGE_BYTES, RowValue, WriteError, relationship::mutation::Change, *,
};
use std::{error::Error, fs, path::Path};

use crate::testkit::TempDir;
use crate::testkit::TestResult;
use crate::testkit::budget;
const COLUMNS: &[ColumnSpec<'_>] = &[
    ColumnSpec::new(b"Id", ColumnType::Long),
    ColumnSpec::new(b"Key", ColumnType::Long),
    ColumnSpec::new(b"Body", ColumnType::Memo),
];
const INDEXES: &[IndexSpec<'_>] = &[
    index(
        b"ById",
        &[IndexColumnSpec {
            column: ColumnRef::Ordinal(0),
            direction: IndexDirection::Ascending,
        }],
        IndexKind::Primary,
    ),
    index(
        b"ByKey",
        &[IndexColumnSpec {
            column: ColumnRef::Ordinal(1),
            direction: IndexDirection::Ascending,
        }],
        IndexKind::Unique,
    ),
];
fn fixture_table(name: &'static [u8], parent: bool) -> TableSpec<'static> {
    table(name, COLUMNS, if parent { INDEXES } else { &INDEXES[..1] })
}
fn relation(
    name: &'static [u8],
    parent: usize,
    child: usize,
    updates: bool,
    deletes: bool,
) -> RelationshipSpec<'static> {
    RelationshipSpec {
        unique: false,
        enforce: true,
        join: crate::RelationshipJoin::Inner,
        name,
        parent: TableRef::Ordinal(parent),
        child: TableRef::Ordinal(child),
        cascade_updates: updates,
        cascade_deletes: deletes,
        fields: &[RelationshipField {
            parent: ColumnRef::Ordinal(1),
            child: ColumnRef::Ordinal(1),
        }],
    }
}
fn locator(path: &Path, table: &[u8], id: i32) -> Result<RowLocator, Box<dyn Error>> {
    let mut work = budget();
    let mut db = DatabaseReader::open(path, &mut work)?;
    let definition = crate::write::update::indexed_writable_table(&mut db, table, &mut work)?;
    let mut rows = db.rows(&definition, &mut work)?;
    while let Some(mut row) = rows.next_row()? {
        if crate::row::scalar_values::read_column(&mut row, ColumnOrdinal::new(0))?
            == RowValue::Long(id)
        {
            return Ok(row.locator());
        }
    }
    Err("row absent".into())
}
fn keys(
    path: &Path,
    table: &[u8],
    columns: &[u16],
) -> Result<Vec<Vec<Option<i32>>>, Box<dyn Error>> {
    let mut work = budget();
    let mut db = DatabaseReader::open(path, &mut work)?;
    let definition = crate::write::update::indexed_writable_table(&mut db, table, &mut work)?;
    let mut cursor = db.rows(&definition, &mut work)?;
    let mut result = Vec::new();
    while let Some(mut row) = cursor.next_row()? {
        let mut values = Vec::new();
        for &column in columns {
            values.push(
                match crate::row::scalar_values::read_column(&mut row, ColumnOrdinal::new(column))?
                {
                    RowValue::Long(value) => Some(value),
                    RowValue::Null => None,
                    _ => return Err("unexpected key type".into()),
                },
            );
        }
        result.push(values);
    }
    Ok(result)
}
fn field(path: &Path, table: &[u8], id: i32, column: u16, value: RowValue<'_>) -> TestResult {
    update_field(
        path,
        FieldUpdate {
            table,
            row: locator(path, table, id)?,
            column: ColumnOrdinal::new(column),
            value,
        },
        &mut budget(),
    )?;
    Ok(())
}
fn validate_file(path: &Path) -> TestResult {
    let mut work = budget();
    DatabaseReader::open(path, &mut work)?.validate(TextCodePage::Windows1252, &mut work)?;
    Ok(())
}

#[test]
fn cascade_actions_are_independent_and_preserve_refused_inputs() -> TestResult {
    for (updates, deletes) in [(false, false), (true, false), (false, true), (true, true)] {
        let directory = TempDir::new("create")?;
        let path = directory.join("actions.mdb");
        let parents = [&[
            RowValue::Long(1),
            RowValue::Long(10),
            RowValue::Memo(b"parent"),
        ][..]];
        let children = [&[
            RowValue::Long(2),
            RowValue::Long(10),
            RowValue::Memo(b"child"),
        ][..]];
        create_spec(
            &path,
            &DatabaseSpec {
                tables: &[
                    TableRows {
                        table: fixture_table(b"Parent", true),
                        rows: &parents,
                    },
                    TableRows {
                        table: fixture_table(b"Child", false),
                        rows: &children,
                    },
                ],
                relationships: &[relation(b"ParentChild", 0, 1, updates, deletes)],
                ..DatabaseSpec::default()
            },
        )?;
        for value in [10, 11] {
            let before = fs::read(&path)?;
            let result = field(&path, b"Parent", 1, 1, RowValue::Long(value));
            assert_eq!(result.is_ok(), updates);
            if !updates {
                assert_eq!(fs::read(&path)?, before);
            }
            assert_eq!(
                keys(&path, b"Child", &[1])?,
                vec![vec![Some(if updates { value } else { 10 })]]
            );
        }
        let before = fs::read(&path)?;
        let result = delete_row(
            &path,
            RowDelete {
                table: b"Parent",
                row: locator(&path, b"Parent", 1)?,
            },
            &mut budget(),
        );
        assert_eq!(result.is_ok(), deletes);
        if deletes {
            assert!(keys(&path, b"Parent", &[0])?.is_empty());
            assert!(keys(&path, b"Child", &[0])?.is_empty());
        } else {
            assert_eq!(fs::read(&path)?, before);
        }
        validate_file(&path)?;
    }
    Ok(())
}

#[test]
fn cascade_chain_changes_every_level_and_rolls_back_the_whole_publication() -> TestResult {
    let directory = TempDir::new("create")?;
    let path = directory.join("chain.mdb");
    let payload = [b'p'; 8192];
    let root = [
        RowValue::Long(1),
        RowValue::Long(10),
        RowValue::Memo(&payload),
    ];
    let middle = [
        RowValue::Long(11),
        RowValue::Long(10),
        RowValue::Memo(&payload),
    ];
    let leaf = [
        RowValue::Long(101),
        RowValue::Long(10),
        RowValue::Memo(&payload),
    ];
    create_spec(
        &path,
        &DatabaseSpec {
            tables: &[
                TableRows {
                    table: fixture_table(b"Root", true),
                    rows: &[&root],
                },
                TableRows {
                    table: fixture_table(b"Middle", true),
                    rows: &[&middle],
                },
                TableRows {
                    table: fixture_table(b"Leaf", false),
                    rows: &[&leaf],
                },
            ],
            relationships: &[
                relation(b"RootMiddle", 0, 1, true, true),
                relation(b"MiddleLeaf", 1, 2, true, true),
            ],
            ..DatabaseSpec::default()
        },
    )?;
    let original = fs::read(&path)?;
    let selected = locator(&path, b"Root", 1)?;
    let mut work = budget();
    let mut db = DatabaseReader::open(&path, &mut work)?;
    let definition = crate::write::update::indexed_writable_table(&mut db, b"Root", &mut work)?;
    let plan = prepare(
        &mut db,
        &definition,
        b"Root",
        Change::Field(selected, ColumnOrdinal::new(1), RowValue::Long(11)),
        &mut work,
    )?
    .ok_or("cascade plan")?;
    let result = plan.publish(&path, db, &mut work, |stage| {
        if stage == PublishStage::PrePublish {
            Err(std::io::Error::other("injected"))
        } else {
            Ok(())
        }
    });
    assert!(
        matches!(result, Err(WriteError::Publish(error)) if error.stage() == PublishStage::PrePublish)
    );
    assert_eq!(fs::read(&path)?, original);
    assert_eq!(fs::read_dir(&*directory)?.count(), 1);
    field(&path, b"Root", 1, 1, RowValue::Long(11))?;
    for name in [b"Root".as_slice(), b"Middle", b"Leaf"] {
        assert_eq!(keys(&path, name, &[1])?, vec![vec![Some(11)]]);
    }
    validate_file(&path)?;
    delete_row(
        &path,
        RowDelete {
            table: b"Root",
            row: selected,
        },
        &mut budget(),
    )?;
    for name in [b"Root".as_slice(), b"Middle", b"Leaf"] {
        assert!(keys(&path, name, &[0])?.is_empty());
    }
    validate_file(&path)
}

#[test]
fn cascade_shared_foreign_key_checks_other_parents_before_writing() -> TestResult {
    let directory = TempDir::new("create")?;
    let path = directory.join("shared.mdb");
    let first = [RowValue::Long(1), RowValue::Long(10), RowValue::Memo(b"a")];
    let other = [RowValue::Long(2), RowValue::Long(30), RowValue::Memo(b"b")];
    create_spec(
        &path,
        &DatabaseSpec {
            tables: &[
                TableRows {
                    table: fixture_table(b"Left", true),
                    rows: &[&first],
                },
                TableRows {
                    table: fixture_table(b"Right", true),
                    rows: &[&first, &other],
                },
                TableRows {
                    table: fixture_table(b"Child", false),
                    rows: &[&first],
                },
            ],
            relationships: &[
                relation(b"LeftChild", 0, 2, true, true),
                relation(b"RightChild", 1, 2, false, false),
            ],
            ..DatabaseSpec::default()
        },
    )?;
    let original = fs::read(&path)?;
    assert!(field(&path, b"Left", 1, 1, RowValue::Long(11)).is_err());
    assert_eq!(fs::read(&path)?, original);
    field(&path, b"Left", 1, 1, RowValue::Long(30))?;
    assert_eq!(keys(&path, b"Child", &[1])?, vec![vec![Some(30)]]);
    let original = fs::read(&path)?;
    assert!(field(&path, b"Right", 2, 1, RowValue::Long(30)).is_err());
    assert_eq!(fs::read(&path)?, original);
    validate_file(&path)
}

#[test]
fn cascade_self_replacement_preserves_the_explicit_foreign_key() -> TestResult {
    for foreign in [10, 30, 99] {
        let directory = TempDir::new("create")?;
        let path = directory.join("self.mdb");
        let columns = [
            ColumnSpec::new(b"Id", ColumnType::Long),
            ColumnSpec::new(b"Key", ColumnType::Long),
            ColumnSpec::new(b"Foreign", ColumnType::Long),
        ];
        let rows = [
            [RowValue::Long(1), RowValue::Long(10), RowValue::Long(10)],
            [RowValue::Long(2), RowValue::Long(20), RowValue::Long(10)],
            [RowValue::Long(3), RowValue::Long(30), RowValue::Null],
        ];
        let relation = RelationshipSpec {
            fields: &[RelationshipField {
                parent: ColumnRef::Ordinal(1),
                child: ColumnRef::Ordinal(2),
            }],
            ..relation(b"SelfRel", 0, 0, true, true)
        };
        create_spec(
            &path,
            &DatabaseSpec {
                tables: &[TableRows {
                    table: table(b"Node", &columns, INDEXES),
                    rows: &[&rows[0], &rows[1], &rows[2]],
                }],
                relationships: &[relation],
                ..DatabaseSpec::default()
            },
        )?;
        let before = fs::read(&path)?;
        let result = update_row(
            &path,
            RowUpdate {
                table: b"Node",
                row: locator(&path, b"Node", 1)?,
                values: &[
                    RowValue::Long(1),
                    RowValue::Long(11),
                    RowValue::Long(foreign),
                ],
            },
            &mut budget(),
        );
        assert_eq!(result.is_ok(), foreign == 30);
        if foreign == 30 {
            assert_eq!(
                keys(&path, b"Node", &[1, 2])?,
                vec![
                    vec![Some(11), Some(30)],
                    vec![Some(20), Some(11)],
                    vec![Some(30), None]
                ]
            );
        } else {
            assert_eq!(fs::read(&path)?, before);
        }
        validate_file(&path)?;
    }
    Ok(())
}

#[test]
fn cascade_composite_null_tuples_match_exactly_and_update_each_row_once() -> TestResult {
    let directory = TempDir::new("create")?;
    let path = directory.join("nulls.mdb");
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"First", ColumnType::Long),
        ColumnSpec::new(b"Second", ColumnType::Long),
        ColumnSpec::new(b"Body", ColumnType::Memo),
    ];
    let indexes = [
        INDEXES[0],
        index(
            b"Tuple",
            &[
                IndexColumnSpec {
                    column: ColumnRef::Ordinal(1),
                    direction: IndexDirection::Ascending,
                },
                IndexColumnSpec {
                    column: ColumnRef::Ordinal(2),
                    direction: IndexDirection::Ascending,
                },
            ],
            IndexKind::Unique,
        ),
    ];
    let payload = [b'n'; 4096];
    let input = [
        [
            RowValue::Long(1),
            RowValue::Long(10),
            RowValue::Null,
            RowValue::Memo(&payload),
        ],
        [
            RowValue::Long(2),
            RowValue::Null,
            RowValue::Long(20),
            RowValue::Memo(&payload),
        ],
        [
            RowValue::Long(3),
            RowValue::Null,
            RowValue::Null,
            RowValue::Memo(&payload),
        ],
    ];
    let references = [&input[0][..], &input[1], &input[2]];
    let relationship = RelationshipSpec {
        fields: &[
            RelationshipField {
                parent: ColumnRef::Ordinal(1),
                child: ColumnRef::Ordinal(1),
            },
            RelationshipField {
                parent: ColumnRef::Ordinal(2),
                child: ColumnRef::Ordinal(2),
            },
        ],
        ..relation(b"Pair", 0, 1, true, true)
    };
    create_spec(
        &path,
        &DatabaseSpec {
            tables: &[
                TableRows {
                    table: table(b"Parent", &columns, &indexes),
                    rows: &references,
                },
                TableRows {
                    table: table(b"Child", &columns, &indexes[..1]),
                    rows: &references,
                },
            ],
            relationships: &[relationship],
            ..DatabaseSpec::default()
        },
    )?;
    let descriptors = |path: &Path| -> Result<Vec<Vec<u8>>, Box<dyn Error>> {
        let mut work = budget();
        let mut db = DatabaseReader::open(path, &mut work)?;
        let table = crate::write::update::indexed_writable_table(&mut db, b"Child", &mut work)?;
        let mut rows = db.rows(&table, &mut work)?;
        let mut saved = Vec::new();
        while let Some(row) = rows.next_row()? {
            saved.push(
                row.field(ColumnOrdinal::new(3))
                    .and_then(|field| field.raw_bytes())
                    .ok_or("payload descriptor")?
                    .to_vec(),
            );
        }
        Ok(saved)
    };
    let before = descriptors(&path)?;
    field(&path, b"Parent", 1, 1, RowValue::Long(11))?;
    assert_eq!(
        keys(&path, b"Child", &[1, 2])?,
        vec![vec![Some(11), None], vec![None, Some(20)], vec![None, None]]
    );
    update_row(
        &path,
        RowUpdate {
            table: b"Parent",
            row: locator(&path, b"Parent", 3)?,
            values: &[
                RowValue::Long(3),
                RowValue::Long(30),
                RowValue::Long(31),
                RowValue::Memo(&payload),
            ],
        },
        &mut budget(),
    )?;
    assert_eq!(
        keys(&path, b"Child", &[1, 2])?,
        vec![
            vec![Some(11), None],
            vec![None, Some(20)],
            vec![Some(30), Some(31)]
        ]
    );
    assert_eq!(descriptors(&path)?, before);
    delete_row(
        &path,
        RowDelete {
            table: b"Parent",
            row: locator(&path, b"Parent", 2)?,
        },
        &mut budget(),
    )?;
    assert_eq!(
        keys(&path, b"Child", &[0])?,
        vec![vec![Some(1)], vec![Some(3)]]
    );
    validate_file(&path)
}

#[test]
fn cascade_journal_merges_successive_and_appended_pages_and_rejects_stale_plans() -> TestResult {
    use crate::write::page_edits::PageEdits;
    use crate::write::update_pages::PageChange;
    let directory = TempDir::new("create")?;
    let original_path = directory.join("original.mdb");
    let private_path = directory.join("private.mdb");
    create(&original_path, &[])?;
    fs::copy(&original_path, &private_path)?;
    let original_bytes = fs::read(&original_path)?;
    let mut file = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(&private_path)?;
    let mut work = budget();
    let mut db = DatabaseReader::open(&private_path, &mut work)?;
    let pages = db.geometry().page_count();
    let page = PageNumber::new(1);
    let mut before = [0; PAGE_BYTES];
    db.read_raw_page(page, &mut before, &mut work)?;
    let mut first = before;
    first[100] ^= 1;
    let mut combined = PageEdits::new(pages);
    let mut plan = PageEdits::new(pages);
    plan.replace(
        PageChange {
            page,
            before: &before,
            after: &first,
        },
        &mut work,
    )?;
    let appended = plan.append(PageImage::from_bytes([0; PAGE_BYTES]), &mut work)?;
    plan.apply_private(&mut db, &mut file, &mut combined, &mut work)?;
    let mut db = DatabaseReader::open(&private_path, &mut work)?;
    let mut second = first;
    second[101] ^= 2;
    let mut appended_after = [0; PAGE_BYTES];
    appended_after[100] = 17;
    let mut plan = PageEdits::new(pages + 1);
    plan.replace(
        PageChange {
            page,
            before: &first,
            after: &second,
        },
        &mut work,
    )?;
    plan.replace(
        PageChange {
            page: appended,
            before: &[0; PAGE_BYTES],
            after: &appended_after,
        },
        &mut work,
    )?;
    plan.apply_private(&mut db, &mut file, &mut combined, &mut work)?;
    let mut original = FileSource::open(&original_path, work.read_budget())?;
    let mut candidate = FileSource::open(&private_path, work.read_budget())?;
    combined.verify_private(&mut original, &mut candidate, &mut work)?;
    let expected = fs::read(&private_path)?;
    for (page, stale) in [(page, before), (appended, [0; PAGE_BYTES])] {
        let mut db = DatabaseReader::open(&private_path, &mut work)?;
        let mut plan = PageEdits::new(pages + 1);
        plan.replace(
            PageChange {
                page,
                before: &stale,
                after: &stale,
            },
            &mut work,
        )?;
        assert!(matches!(
            plan.apply_private(&mut db, &mut file, &mut combined, &mut work),
            Err(WriteError::Mismatch(_))
        ));
        assert_eq!(fs::read(&private_path)?, expected);
    }
    let mut tampered = expected;
    tampered[PAGE_BYTES * 2 + 100] ^= 1;
    fs::write(&private_path, tampered)?;
    let mut candidate = FileSource::open(&private_path, work.read_budget())?;
    assert!(
        combined
            .verify_private(&mut original, &mut candidate, &mut work)
            .is_err()
    );
    assert_eq!(fs::read(&original_path)?, original_bytes);
    Ok(())
}

#[test]
fn cascade_autoincrement_marker_requires_an_autonumber_row_replacement() -> TestResult {
    for auto_number in [false, true] {
        let directory = TempDir::new("create")?;
        let path = directory.join("marker.mdb");
        let mut columns = COLUMNS.to_vec();
        if auto_number {
            columns[1] = ColumnSpec::new(b"Key", ColumnType::AutoIncrement);
        }
        let values = [
            RowValue::Long(1),
            RowValue::Long(10),
            RowValue::Memo(b"before"),
        ];
        create_spec(
            &path,
            &DatabaseSpec {
                tables: &[
                    TableRows {
                        table: TableSpec {
                            columns: &columns,
                            ..fixture_table(b"Parent", true)
                        },
                        rows: &[&values],
                    },
                    TableRows {
                        table: fixture_table(b"Child", false),
                        rows: &[&values],
                    },
                ],
                relationships: &[relation(b"ParentChild", 0, 1, true, true)],
                ..DatabaseSpec::default()
            },
        )?;
        let selected = locator(&path, b"Parent", 1)?;
        let before = fs::read(&path)?;
        let result = update_field(
            &path,
            FieldUpdate {
                table: b"Parent",
                row: selected,
                column: ColumnOrdinal::new(1),
                value: RowValue::AutoIncrement,
            },
            &mut budget(),
        );
        assert!(matches!(result, Err(WriteError::Unsupported(_))));
        assert_eq!(fs::read(&path)?, before);
        let result = update_row(
            &path,
            RowUpdate {
                table: b"Parent",
                row: selected,
                values: &[
                    RowValue::Long(1),
                    RowValue::AutoIncrement,
                    RowValue::Memo(b"after"),
                ],
            },
            &mut budget(),
        );
        if auto_number {
            result?;
            assert_eq!(keys(&path, b"Parent", &[1])?, vec![vec![Some(10)]]);
            assert_eq!(keys(&path, b"Child", &[1])?, vec![vec![Some(10)]]);
            validate_file(&path)?;
        } else {
            assert!(matches!(result, Err(WriteError::Unsupported(_))));
            assert_eq!(fs::read(&path)?, before);
        }
    }
    Ok(())
}
