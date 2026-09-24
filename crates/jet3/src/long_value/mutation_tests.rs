use crate::{PAGE_BYTES, *};
use std::{collections::BTreeMap, fs, path::PathBuf};

pub(super) type TestResult = Result<(), Box<dyn std::error::Error>>;
const COLUMNS: [ColumnSpec<'static>; 4] = [
    ColumnSpec::new(b"Id", ColumnType::Long),
    ColumnSpec::new(b"Tag", ColumnType::Long),
    ColumnSpec::new(b"Memo", ColumnType::Memo),
    ColumnSpec::new(b"Ole", ColumnType::LongBinary),
];
const INDEXES: [IndexSpec<'static>; 3] = [
    IndexSpec {
        name: b"ById",
        kind: IndexKind::Primary,
        fields: &[IndexColumnSpec {
            column: ColumnRef::Name(b"Id"),
            direction: IndexDirection::Ascending,
        }],
    },
    IndexSpec {
        name: b"ByTag",
        kind: IndexKind::Ordinary,
        fields: &[IndexColumnSpec {
            column: ColumnRef::Name(b"Tag"),
            direction: IndexDirection::Descending,
        }],
    },
    IndexSpec {
        name: b"ByPair",
        kind: IndexKind::Unique,
        fields: &[
            IndexColumnSpec {
                column: ColumnRef::Name(b"Tag"),
                direction: IndexDirection::Descending,
            },
            IndexColumnSpec {
                column: ColumnRef::Name(b"Id"),
                direction: IndexDirection::Ascending,
            },
        ],
    },
];

pub(super) use crate::testkit::budget;

pub(super) struct Fixture {
    directory: crate::testkit::TempDir,
}
impl Fixture {
    pub(super) fn new(indexes: bool) -> Result<Self, Box<dyn std::error::Error>> {
        Self::with_auto(indexes, false)
    }
    pub(super) fn with_auto(indexes: bool, auto: bool) -> Result<Self, Box<dyn std::error::Error>> {
        let directory = crate::testkit::TempDir::new("lval-mutation")?;
        let result = Self { directory };
        let mut columns = COLUMNS;
        if auto {
            columns[0] = ColumnSpec::new(b"Id", ColumnType::AutoIncrement);
        }
        let first = [
            if auto {
                RowValue::AutoIncrement
            } else {
                RowValue::Long(1)
            },
            RowValue::Long(1),
            RowValue::Memo(&[b'a'; 33]),
            RowValue::LongBinary(&[0x11; 33]),
        ];
        let second = [
            if auto {
                RowValue::AutoIncrement
            } else {
                RowValue::Long(2)
            },
            RowValue::Long(1),
            RowValue::Memo(&[b'b'; 33]),
            RowValue::LongBinary(&[0x22; 4096]),
        ];
        let note = [RowValue::Long(9), RowValue::Memo(&[b'z'; 4096])];
        create_database_with_table_rows(
            result.path(),
            &[
                TableRows {
                    table: TableSpec {
                        validation: crate::TableValidation::NONE,
                        name: b"Rows",
                        columns: &columns,
                        indexes: if indexes { &INDEXES } else { &[] },
                    },
                    rows: &[&first, &second],
                },
                TableRows {
                    table: TableSpec {
                        validation: crate::TableValidation::NONE,
                        name: b"Notes",
                        columns: &[COLUMNS[0], COLUMNS[2]],
                        indexes: &[],
                    },
                    rows: &[&note],
                },
            ],
            &mut budget(),
        )?;
        Ok(result)
    }
    pub(super) fn path(&self) -> PathBuf {
        self.directory.join("data.mdb")
    }
    pub(super) fn definition(&self) -> Result<TableDefinition, Box<dyn std::error::Error>> {
        let mut b = budget();
        let mut db = DatabaseReader::open(self.path(), &mut b)?;
        Ok(crate::write::update::indexed_writable_table(
            &mut db, b"Rows", &mut b,
        )?)
    }
    pub(super) fn snapshot(&self) -> Result<BTreeMap<i32, Sample>, Box<dyn std::error::Error>> {
        let table = self.definition()?;
        let mut b = budget();
        let mut db = DatabaseReader::open(self.path(), &mut b)?;
        let mut cursor = db.rows(&table, &mut b)?;
        let mut result = BTreeMap::new();
        while let Some(mut row) = cursor.next_row()? {
            let mut sample = Sample {
                locator: row.locator(),
                tag: None,
                payloads: [None, None],
            };
            let id = match row
                .value(ColumnOrdinal::new(0), TextCodePage::Windows1252)?
                .ok_or("id")?
                .kind()
            {
                ValueKind::Long(id) => *id,
                _ => return Err("id type".into()),
            };
            sample.tag = match row
                .value(ColumnOrdinal::new(1), TextCodePage::Windows1252)?
                .ok_or("tag")?
                .kind()
            {
                ValueKind::Long(tag) => Some(*tag),
                ValueKind::Null => None,
                _ => return Err("tag type".into()),
            };
            let mut pending = Vec::new();
            for column in 0..2 {
                let value = row
                    .value(
                        ColumnOrdinal::new(column as u16 + 2),
                        TextCodePage::Windows1252,
                    )?
                    .ok_or("payload")?;
                match value.kind() {
                    ValueKind::Null => (),
                    ValueKind::LongValue(LongValue::Inline { value, .. }) => {
                        sample.payloads[column] = Some(match value {
                            InlineLongValue::Text(text) => text.raw_bytes().to_vec(),
                            InlineLongValue::Binary(bytes) => bytes.to_vec(),
                        });
                    }
                    ValueKind::LongValue(LongValue::External(reference)) => {
                        pending.push((column, *reference))
                    }
                    _ => return Err("payload type".into()),
                }
            }
            for (column, reference) in pending {
                let mut stream = cursor.long_value(reference)?;
                let mut payload = Vec::new();
                while let Some(chunk) = stream.next_chunk()? {
                    payload.extend_from_slice(match chunk.value() {
                        LongValueChunkValue::Text(text) => text.raw_bytes(),
                        LongValueChunkValue::Binary(bytes) => bytes,
                    });
                }
                sample.payloads[column] = Some(payload);
            }
            assert!(result.insert(id, sample).is_none());
        }
        assert_eq!(result.len(), table.row_count() as usize);
        Ok(result)
    }
    pub(super) fn validate(&self) -> TestResult {
        let mut b = budget();
        let mut db = DatabaseReader::open(self.path(), &mut b)?;
        let table = crate::write::update::indexed_writable_table(&mut db, b"Rows", &mut b)?;
        crate::long_value::mutation::LongValues::load(&mut db, &table, None, &mut b)?;
        if !table.physical_indexes().is_empty() {
            crate::index::mutation::load(&mut db, &table, &mut b)?;
        }
        db.validate(TextCodePage::Windows1252, &mut b)?;
        Ok(())
    }
}

#[derive(Debug, PartialEq, Eq)]
pub(super) struct Sample {
    pub(super) locator: RowLocator,
    tag: Option<i32>,
    pub(super) payloads: [Option<Vec<u8>>; 2],
}

#[test]
fn complete_payload_lifecycles_preserve_neighbors_and_reuse_released_storage() -> TestResult {
    for indexed in [false, true] {
        let fixture = Fixture::new(indexed)?;
        let initial = fixture.snapshot()?;
        let before = fs::read(fixture.path())?;
        let table = fixture.definition()?;
        let root = table.root().get() as usize;
        let notes_start = (root + 1..before.len() / PAGE_BYTES)
            .find(|page| before[*page * PAGE_BYTES] == 2 && before[*page * PAGE_BYTES + 20] == 0x4e)
            .ok_or("Notes root")?;
        let notes = before[notes_start * PAGE_BYTES..].to_vec();
        let values = [
            RowValue::Long(3),
            RowValue::Null,
            RowValue::Memo(&[b'c'; 512]),
            RowValue::LongBinary(&[0x33; 512]),
        ];
        let third = insert_row(fixture.path(), b"Rows", &values, &mut budget())?;
        let inserted = fixture.snapshot()?;
        assert_eq!(inserted[&1], initial[&1]);
        assert_eq!(inserted[&2], initial[&2]);
        assert_eq!(
            inserted[&3].payloads,
            [Some(vec![b'c'; 512]), Some(vec![0x33; 512])]
        );
        delete_row(
            fixture.path(),
            RowDelete {
                table: b"Rows",
                row: initial[&1].locator,
            },
            &mut budget(),
        )?;
        assert_eq!(fixture.snapshot()?[&3], inserted[&3]);
        let grown = [
            RowValue::Long(30),
            RowValue::Long(4),
            RowValue::Memo(&[b'd'; 8192]),
            RowValue::LongBinary(&[0x44; 2037]),
        ];
        update_row(
            fixture.path(),
            RowUpdate {
                table: b"Rows",
                row: third,
                values: &grown,
            },
            &mut budget(),
        )?;
        let changed = fixture.snapshot()?;
        assert_eq!(changed[&2], initial[&2]);
        assert_eq!(changed[&30].locator, third);
        assert_eq!(
            changed[&30].payloads,
            [Some(vec![b'd'; 8192]), Some(vec![0x44; 2037])]
        );
        let peak = fs::metadata(fixture.path())?.len();
        for row in changed.values() {
            delete_row(
                fixture.path(),
                RowDelete {
                    table: b"Rows",
                    row: row.locator,
                },
                &mut budget(),
            )?;
        }
        assert!(fixture.snapshot()?.is_empty());
        insert_row(fixture.path(), b"Rows", &grown, &mut budget())?;
        assert_eq!(fs::metadata(fixture.path())?.len(), peak);
        update_row(
            fixture.path(),
            RowUpdate {
                table: b"Rows",
                row: fixture.snapshot()?[&30].locator,
                values: &[
                    RowValue::Long(30),
                    RowValue::Null,
                    RowValue::Memo(b"x"),
                    RowValue::Null,
                ],
            },
            &mut budget(),
        )?;
        assert_eq!(
            fixture.snapshot()?[&30].payloads,
            [Some(b"x".to_vec()), None]
        );
        let after = fs::read(fixture.path())?;
        assert_eq!(
            &after[notes_start * PAGE_BYTES..notes_start * PAGE_BYTES + notes.len()],
            &notes
        );
        fixture.validate()?;
    }
    Ok(())
}

fn map_offset(bytes: &[u8], locator: MapRowLocator, member: PageNumber) -> usize {
    let start = locator.page().get() as usize * PAGE_BYTES;
    let word = start + 10 + 2 * usize::from(locator.row());
    let offset = usize::from(u16::from_le_bytes([bytes[word], bytes[word + 1]]) & 0x1fff);
    start + offset + 5 + member.get() as usize / 8
}

#[test]
fn malformed_long_value_ownership_and_chains_preserve_exact_input() -> TestResult {
    let fixture = Fixture::new(true)?;
    let table = fixture.definition()?;
    let original = fs::read(fixture.path())?;
    let mut b = budget();
    let mut db = DatabaseReader::open(fixture.path(), &mut b)?;
    let mut rows = db.rows(&table, &mut b)?;
    let mut first = rows.next_row()?.ok_or("first")?;
    let locator = first.locator();
    let reference = match first
        .value(ColumnOrdinal::new(2), TextCodePage::Windows1252)?
        .ok_or("memo")?
        .kind()
    {
        ValueKind::LongValue(LongValue::External(reference)) => *reference,
        _ => return Err("external".into()),
    };
    drop(rows);
    drop(db);
    let target = reference.target().page();
    let page = target.get() as usize * PAGE_BYTES;
    let maps = table.long_value_maps();
    for case in 0..6 {
        let mut damaged = original.clone();
        match case {
            0 => damaged[page + 4] ^= 1,
            1 => {
                damaged[map_offset(&original, MapRowLocator::new(PageNumber::new(1), 0), target)] |=
                    1 << (target.get() % 8)
            }
            2 => {
                damaged[map_offset(&original, maps[0].owned(), target)] &=
                    !(1 << (target.get() % 8))
            }
            3 => damaged[map_offset(&original, maps[1].owned(), target)] |= 1 << (target.get() % 8),
            4 => damaged[page + 2] ^= 1,
            5 => damaged[page + 11] |= 0x20,
            _ => unreachable!(),
        }
        fs::write(fixture.path(), &damaged)?;
        assert!(
            delete_row(
                fixture.path(),
                RowDelete {
                    table: b"Rows",
                    row: locator
                },
                &mut budget()
            )
            .is_err(),
            "case {case}"
        );
        assert_eq!(fs::read(fixture.path())?, damaged, "case {case}");
    }
    Ok(())
}

#[test]
fn aliases_unreferenced_fragments_and_broken_chains_are_refused() -> TestResult {
    let fixture = Fixture::new(true)?;
    let table = fixture.definition()?;
    let original = fs::read(fixture.path())?;
    let mut b = budget();
    let mut db = DatabaseReader::open(fixture.path(), &mut b)?;
    let mut rows = db.rows(&table, &mut b)?;
    let mut headers = Vec::new();
    while let Some(mut row) = rows.next_row()? {
        let locator = row.locator();
        let raw = row.raw_bytes().to_vec();
        for ordinal in [2, 3] {
            let reference = match row
                .value(ColumnOrdinal::new(ordinal), TextCodePage::Windows1252)?
                .ok_or("value")?
                .kind()
            {
                ValueKind::LongValue(LongValue::External(reference)) => *reference,
                _ => return Err("external".into()),
            };
            let header = reference.raw_header();
            let offset = raw
                .windows(header.len())
                .position(|v| v == header)
                .ok_or("header")?;
            let directory =
                locator.page().get() as usize * PAGE_BYTES + 10 + 2 * usize::from(locator.slot());
            let start = usize::from(
                u16::from_le_bytes([original[directory], original[directory + 1]]) & 0x1fff,
            );
            headers.push((
                locator,
                reference,
                locator.page().get() as usize * PAGE_BYTES + start + offset,
            ));
        }
    }
    drop(rows);
    drop(db);
    for case in 0..5 {
        let mut damaged = original.clone();
        match case {
            0 | 1 => {
                let other = headers[if case == 0 { 2 } else { 1 }].1.raw_header();
                damaged[headers[0].2 + 4..headers[0].2 + 8].copy_from_slice(&other[4..8]);
            }
            2 => {
                let target = headers[0].1.target();
                let start = target.page().get() as usize * PAGE_BYTES;
                let source: [u8; PAGE_BYTES] = original[start..start + PAGE_BYTES].try_into()?;
                let (after, _) = crate::row::insert_page::append(
                    target.page(),
                    super::mutation::OWNER,
                    &source,
                    b"unreferenced",
                    &mut budget(),
                )?
                .ok_or("append")?;
                damaged[start..start + PAGE_BYTES].copy_from_slice(after.as_bytes());
            }
            3 | 4 => {
                let reference = headers[3].1;
                let page = reference.target().page().get() as usize * PAGE_BYTES;
                let start = usize::from(
                    u16::from_le_bytes([damaged[page + 10], damaged[page + 11]]) & 0x1fff,
                );
                let mut pointer: [u8; 4] = reference.raw_header()[4..8].try_into()?;
                if case == 4 {
                    pointer[0] = 255;
                }
                damaged[page + start..page + start + 4].copy_from_slice(&pointer);
            }
            _ => unreachable!(),
        }
        fs::write(fixture.path(), &damaged)?;
        let result = delete_row(
            fixture.path(),
            RowDelete {
                table: b"Rows",
                row: headers[0].0,
            },
            &mut budget(),
        );
        match case {
            0 => assert!(matches!(
                result,
                Err(UpdateError::Mismatch("aliased long-value fragment"))
            )),
            1 => assert!(matches!(
                result,
                Err(UpdateError::Mismatch(
                    "long-value reference has wrong column owner"
                ))
            )),
            2 => assert!(matches!(
                result,
                Err(UpdateError::Mismatch(
                    "unreferenced live long-value fragment"
                ))
            )),
            3 => assert!(matches!(
                result,
                Err(UpdateError::LongValue(LongValueError::Cycle { .. }))
            )),
            4 => assert!(matches!(
                result,
                Err(UpdateError::LongValue(LongValueError::MissingRow { .. }))
            )),
            _ => unreachable!(),
        }
        assert_eq!(fs::read(fixture.path())?, damaged);
    }
    Ok(())
}

#[test]
fn rejected_payloads_duplicates_and_chain_budget_leave_no_private_publication() -> TestResult {
    let fixture = Fixture::new(true)?;
    let original = fs::read(fixture.path())?;
    for value in [
        RowValue::Memo(b""),
        RowValue::LongValue(&[0; 12]),
        RowValue::LongBinary(b"wrong"),
    ] {
        assert!(
            insert_row(
                fixture.path(),
                b"Rows",
                &[RowValue::Long(3), RowValue::Null, value, RowValue::Null],
                &mut budget()
            )
            .is_err()
        );
        assert_eq!(fs::read(fixture.path())?, original);
    }
    assert!(
        insert_row(
            fixture.path(),
            b"Rows",
            &[
                RowValue::Long(1),
                RowValue::Long(55),
                RowValue::Memo(&[b'q'; 8192]),
                RowValue::LongBinary(&[0x88; 4096]),
            ],
            &mut budget()
        )
        .is_err()
    );
    assert_eq!(fs::read(fixture.path())?, original);
    let mut limited = ResourceBudget::new(ResourceLimits::default().with_max_chain_depth(2));
    assert!(
        insert_row(
            fixture.path(),
            b"Rows",
            &[
                RowValue::Long(3),
                RowValue::Null,
                RowValue::Memo(&[b'q'; 8192]),
                RowValue::Null,
            ],
            &mut limited
        )
        .is_err()
    );
    assert_eq!(fs::read(fixture.path())?, original);
    assert_eq!(fs::read_dir(&fixture.directory)?.count(), 1);
    Ok(())
}

#[test]
fn autonumber_payload_mutations_generate_retain_and_wrap_state() -> TestResult {
    for indexed in [false, true] {
        let fixture = Fixture::with_auto(indexed, true)?;
        let state = || -> Result<i32, Box<dyn std::error::Error>> {
            let table = fixture.definition()?;
            Ok(i32::from_le_bytes(table.raw_header()[16..20].try_into()?))
        };
        assert_eq!(state()?, 2);
        for (value, id, expected_state) in [
            (RowValue::AutoIncrement, 3, 3),
            (RowValue::Long(100), 100, 100),
            (RowValue::Long(0), 0, 101),
            (RowValue::Long(-5), -5, -5),
            (RowValue::AutoIncrement, -4, -4),
        ] {
            insert_row(
                fixture.path(),
                b"Rows",
                &[
                    value,
                    RowValue::Long(5),
                    RowValue::Memo(&[b'x'; 4096]),
                    RowValue::LongBinary(&[0x55; 33]),
                ],
                &mut budget(),
            )?;
            assert!(fixture.snapshot()?.contains_key(&id));
            assert_eq!(state()?, expected_state);
        }
        let row = fixture.snapshot()?[&-4].locator;
        update_row(
            fixture.path(),
            RowUpdate {
                table: b"Rows",
                row,
                values: &[
                    RowValue::AutoIncrement,
                    RowValue::Null,
                    RowValue::Memo(b"updated"),
                    RowValue::LongBinary(&[0xaa; 8192]),
                ],
            },
            &mut budget(),
        )?;
        assert_eq!(state()?, -4);
        assert_eq!(
            fixture.snapshot()?[&-4].payloads,
            [Some(b"updated".to_vec()), Some(vec![0xaa; 8192])]
        );
        let before = fs::read(fixture.path())?;
        assert!(
            update_row(
                fixture.path(),
                RowUpdate {
                    table: b"Rows",
                    row,
                    values: &[
                        RowValue::Long(777),
                        RowValue::Null,
                        RowValue::Null,
                        RowValue::Null,
                    ]
                },
                &mut budget()
            )
            .is_err()
        );
        assert_eq!(fs::read(fixture.path())?, before);
        if indexed {
            assert!(
                insert_row(
                    fixture.path(),
                    b"Rows",
                    &[
                        RowValue::Long(100),
                        RowValue::Long(5),
                        RowValue::Memo(&[b'q'; 8192]),
                        RowValue::Null,
                    ],
                    &mut budget()
                )
                .is_err()
            );
            assert_eq!(fs::read(fixture.path())?, before);
        }
        for sample in fixture.snapshot()?.values() {
            delete_row(
                fixture.path(),
                RowDelete {
                    table: b"Rows",
                    row: sample.locator,
                },
                &mut budget(),
            )?;
        }
        assert_eq!(state()?, -4);
        for (value, id, expected_state) in [
            (RowValue::Long(-1), -1, -1),
            (RowValue::AutoIncrement, 0, 0),
            (RowValue::Long(i32::MAX), i32::MAX, i32::MAX),
            (RowValue::AutoIncrement, i32::MIN, i32::MIN),
            (RowValue::Long(10), 10, i32::MIN + 1),
        ] {
            insert_row(
                fixture.path(),
                b"Rows",
                &[
                    value,
                    RowValue::Long(7),
                    RowValue::Memo(b"boundary"),
                    RowValue::Null,
                ],
                &mut budget(),
            )?;
            assert!(fixture.snapshot()?.contains_key(&id));
            assert_eq!(state()?, expected_state);
        }
        fixture.validate()?;
    }
    Ok(())
}

#[test]
fn overflow_rows_preserve_complete_payloads_and_column_ownership() -> TestResult {
    let fixture = Fixture::new(true)?;
    for id in 3..=50 {
        insert_row(
            fixture.path(),
            b"Rows",
            &[
                RowValue::Long(id),
                RowValue::Long(1),
                RowValue::Memo(&[b'a'; 33]),
                RowValue::LongBinary(&[0x11; 33]),
            ],
            &mut budget(),
        )?;
    }
    let initial = fixture.snapshot()?;
    let logical = initial[&1].locator;
    update_row(
        fixture.path(),
        RowUpdate {
            table: b"Rows",
            row: logical,
            values: &[
                RowValue::Long(1),
                RowValue::Long(2),
                RowValue::Memo(&[b'M'; 32]),
                RowValue::LongBinary(&[0xa5; 32]),
            ],
        },
        &mut budget(),
    )?;
    let table = fixture.definition()?;
    {
        let mut work = budget();
        let mut db = DatabaseReader::open(fixture.path(), &mut work)?;
        let graph =
            crate::row::mutation_graph::RowGraph::load(&mut db, &table, Some(logical), &mut work)?;
        assert_eq!(graph.selected.len(), 2);
    }
    let grown = fixture.snapshot()?;
    assert_eq!(grown[&1].locator, logical);
    assert_eq!(
        grown[&1].payloads,
        [Some(vec![b'M'; 32]), Some(vec![0xa5; 32])]
    );
    for (id, row) in &initial {
        if *id != 1 {
            assert_eq!(&grown[id], row);
        }
    }
    fixture.validate()?;
    update_row(
        fixture.path(),
        RowUpdate {
            table: b"Rows",
            row: logical,
            values: &[
                RowValue::Long(1),
                RowValue::Long(3),
                RowValue::Memo(&[b'C'; 8192]),
                RowValue::LongBinary(&[0x77; 4096]),
            ],
        },
        &mut budget(),
    )?;
    let external = fixture.snapshot()?;
    assert_eq!(
        external[&1].payloads,
        [Some(vec![b'C'; 8192]), Some(vec![0x77; 4096])]
    );
    for (id, row) in &initial {
        if *id != 1 {
            assert_eq!(&external[id], row);
        }
    }
    delete_row(
        fixture.path(),
        RowDelete {
            table: b"Rows",
            row: logical,
        },
        &mut budget(),
    )?;
    let remaining = fixture.snapshot()?;
    assert_eq!(remaining.len(), initial.len() - 1);
    for (id, row) in &initial {
        if *id != 1 {
            assert_eq!(&remaining[id], row);
        }
    }
    fixture.validate()?;
    Ok(())
}
