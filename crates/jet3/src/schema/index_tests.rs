use crate::{PAGE_BYTES, *};
use std::{fs, path::PathBuf};
pub(super) type TestResult = Result<(), Box<dyn std::error::Error>>;
pub(super) struct Fixture(pub(super) crate::testkit::TempDir);
impl Fixture {
    pub(super) fn new(rows: &[&[RowValue<'_>]]) -> Result<Self, Box<dyn std::error::Error>> {
        let path = crate::testkit::TempDir::new("schema-index")?;
        let fixture = Self(path);
        create_database(
            fixture.path(),
            &DatabaseSpec {
                tables: &[TableRows {
                    table: TableSpec {
                        validation: crate::TableValidation::NONE,
                        name: b"Items",
                        columns: &[
                            ColumnSpec::new(b"Id", ColumnType::Long),
                            ColumnSpec::new(b"Payload", ColumnType::Memo),
                        ],
                        indexes: &[],
                    },
                    rows,
                }],
                ..DatabaseSpec::default()
            },
            &mut budget(),
        )?;
        Ok(fixture)
    }
    pub(super) fn path(&self) -> PathBuf {
        self.0.join("source.mdb")
    }
    pub(super) fn table(&self) -> Result<TableDefinition, UpdateError> {
        let mut budget = budget();
        let mut database = DatabaseReader::open(self.path(), &mut budget)?;
        crate::write::update::indexed_writable_table(&mut database, b"Items", &mut budget)
    }
    pub(super) fn create(
        &self,
        name: &[u8],
        kind: IndexKind,
        direction: IndexDirection,
    ) -> Result<(), UpdateError> {
        edit_schema(
            self.path(),
            SchemaEdit::CreateIndex {
                table: b"Items",
                index: IndexSpec {
                    name,
                    kind,
                    fields: &[IndexColumnSpec {
                        column: ColumnRef::Name(b"Id"),
                        direction,
                    }],
                },
            },
            &mut budget(),
        )
    }
    pub(super) fn drop_index(&self, index: &[u8]) -> Result<(), UpdateError> {
        edit_schema(
            self.path(),
            SchemaEdit::DropIndex {
                table: b"Items",
                index,
            },
            &mut budget(),
        )
    }
}
pub(super) use crate::testkit::budget;

#[test]
fn index_edits_preserve_rows_payloads_and_unrelated_source_pages() -> TestResult {
    let payload = [b'x'; 6000];
    let values: Vec<_> = (0..400)
        .map(|i| [RowValue::Long(i), RowValue::Memo(&payload)])
        .collect();
    let rows: Vec<_> = values.iter().map(|row| row.as_slice()).collect();
    let fixture = Fixture::new(&rows)?;
    let before = fs::read(fixture.path())?;
    let table = fixture.table()?;
    let mut database = DatabaseReader::open(fixture.path(), &mut budget())?;
    let global = crate::alloc::mutation_map::MapBits::load(
        &mut database,
        crate::alloc::mutation_map::global_locator(),
        &mut budget(),
    )?;
    let allocation: Vec<_> = global
        .spans
        .iter()
        .map(|span| span.page.get() as usize)
        .collect();
    fixture.create(b"ById", IndexKind::Unique, IndexDirection::Descending)?;
    let after = fs::read(fixture.path())?;
    for (number, page) in before.chunks_exact(PAGE_BYTES).enumerate() {
        if number != 1 && number != table.root().get() as usize && !allocation.contains(&number) {
            assert!(
                page == &after[number * PAGE_BYTES..(number + 1) * PAGE_BYTES],
                "source page {number}"
            );
        }
    }
    let table = fixture.table()?;
    let mut database = DatabaseReader::open(fixture.path(), &mut budget())?;
    let tree = database.index_tree(&table, 0, &mut budget())?;
    assert_eq!(tree.entries().len(), 400);
    assert!(tree.nodes().len() > 1);
    assert_eq!(
        table.physical_indexes()[0].fields()[0].direction(),
        IndexDirection::Descending
    );
    fixture.drop_index(b"ById")?;
    let dropped = fixture.table()?;
    assert!(dropped.indexes().is_empty());
    assert!(dropped.physical_indexes().is_empty());
    let after = fs::read(fixture.path())?;
    for (number, page) in before.chunks_exact(PAGE_BYTES).enumerate() {
        if number != 1 && number != table.root().get() as usize && !allocation.contains(&number) {
            assert!(
                page == &after[number * PAGE_BYTES..(number + 1) * PAGE_BYTES],
                "source page {number}"
            );
        }
    }
    Ok(())
}

#[test]
fn shared_alias_rename_and_drop_keep_the_tree_until_the_last_alias() -> TestResult {
    let fixture = Fixture::new(&[&[RowValue::Long(1), RowValue::Null]])?;
    fixture.create(b"Z", IndexKind::Unique, IndexDirection::Ascending)?;
    let first = fixture.table()?;
    fixture.create(b"A", IndexKind::Unique, IndexDirection::Ascending)?;
    let aliases = fixture.table()?;
    assert_eq!(aliases.physical_indexes(), first.physical_indexes());
    assert_eq!(aliases.indexes().len(), 2);
    edit_schema(
        fixture.path(),
        SchemaEdit::RenameIndex {
            table: b"Items",
            index: b"Z",
            name: b"B",
        },
        &mut budget(),
    )?;
    fixture.drop_index(b"A")?;
    let remaining = fixture.table()?;
    assert_eq!(remaining.physical_indexes(), first.physical_indexes());
    assert_eq!(remaining.indexes()[0].name().raw_bytes(), b"B");
    fixture.create(b"Other", IndexKind::Ordinary, IndexDirection::Descending)?;
    fixture.drop_index(b"B")?;
    let compacted = fixture.table()?;
    assert_eq!(compacted.indexes()[0].physical_index(), 0);
    assert_eq!(
        compacted.physical_indexes()[0].fields()[0].direction(),
        IndexDirection::Descending
    );
    Ok(())
}

#[test]
fn unique_required_names_and_resource_refusals_leave_the_source_unchanged() -> TestResult {
    let fixture = Fixture::new(&[
        &[RowValue::Long(7), RowValue::Null],
        &[RowValue::Long(7), RowValue::Null],
        &[RowValue::Null, RowValue::Null],
    ])?;
    let before = fs::read(fixture.path())?;
    for kind in [
        IndexKind::Unique,
        IndexKind::Primary,
        IndexKind::Ordinary.with_null_policy(IndexNullPolicy::Required),
    ] {
        assert!(
            fixture
                .create(b"Key", kind, IndexDirection::Ascending)
                .is_err()
        );
        assert_eq!(fs::read(fixture.path())?, before);
    }
    for name in [b"".as_slice(), b" Leading", b"bad.name", &[b'x'; 64]] {
        assert!(
            fixture
                .create(name, IndexKind::Ordinary, IndexDirection::Ascending)
                .is_err()
        );
        assert_eq!(fs::read(fixture.path())?, before);
    }
    fixture.create(
        b"Key",
        IndexKind::Ordinary.with_null_policy(IndexNullPolicy::IgnoreAllNull),
        IndexDirection::Ascending,
    )?;
    let before = fs::read(fixture.path())?;
    assert!(
        fixture
            .create(b"kEy", IndexKind::Ordinary, IndexDirection::Ascending)
            .is_err()
    );
    assert!(fixture.drop_index(b"Missing").is_err());
    let mut limited = ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(1));
    assert!(
        edit_schema(
            fixture.path(),
            SchemaEdit::DropIndex {
                table: b"Items",
                index: b"Key"
            },
            &mut limited
        )
        .is_err()
    );
    assert_eq!(fs::read(fixture.path())?, before);
    let mut database = DatabaseReader::open(fixture.path(), &mut budget())?;
    assert_eq!(
        database
            .index_tree(&fixture.table()?, 0, &mut budget())?
            .entries()
            .len(),
        2
    );
    Ok(())
}

#[test]
fn definition_chain_growth_shrink_and_logical_capacity() -> TestResult {
    let fixture = Fixture::new(&[])?;
    for ordinal in 0..32 {
        let name = format!("Index{ordinal:02}{}", "x".repeat(56));
        fixture.create(
            name.as_bytes(),
            IndexKind::Ordinary,
            IndexDirection::Ascending,
        )?;
    }
    let table = fixture.table()?;
    assert_eq!(table.indexes().len(), 32);
    assert_eq!(table.physical_indexes().len(), 1);
    assert!(table.pages().len() > 1);
    let before = fs::read(fixture.path())?;
    assert!(
        fixture
            .create(
                b"OneTooMany",
                IndexKind::Ordinary,
                IndexDirection::Ascending
            )
            .is_err()
    );
    assert_eq!(fs::read(fixture.path())?, before);
    for index in table.indexes().iter().take(20) {
        fixture.drop_index(index.name().raw_bytes())?;
    }
    assert_eq!(fixture.table()?.pages().len(), 1);
    Ok(())
}

#[test]
fn table_rename_updates_the_catalog_and_preserves_user_storage() -> TestResult {
    let fixture = Fixture::new(&[&[RowValue::Long(1), RowValue::Memo(&[b'z'; 3000])]])?;
    fixture.create(b"PrimaryKey", IndexKind::Primary, IndexDirection::Ascending)?;
    let table = fixture.table()?;
    let before = fs::read(fixture.path())?;
    edit_schema(
        fixture.path(),
        SchemaEdit::RenameTable {
            table: b"Items",
            name: b"Renamed",
        },
        &mut budget(),
    )?;
    let mut database = DatabaseReader::open(fixture.path(), &mut budget())?;
    let renamed =
        crate::write::update::indexed_writable_table(&mut database, b"Renamed", &mut budget())?;
    assert_eq!(renamed, table);
    let after = fs::read(fixture.path())?;
    for (number, page) in before
        .chunks_exact(PAGE_BYTES)
        .enumerate()
        .skip(table.root().get() as usize)
    {
        assert!(
            page == &after[number * PAGE_BYTES..(number + 1) * PAGE_BYTES],
            "user page {number}"
        );
    }
    Ok(())
}

#[test]
fn create_table_keeps_existing_storage_and_accepts_later_rows() -> TestResult {
    let fixture = Fixture::new(&[&[RowValue::Long(1), RowValue::Memo(&[b'z'; 3000])]])?;
    let old = fixture.table()?;
    let before = fs::read(fixture.path())?;
    let mut database = DatabaseReader::open(fixture.path(), &mut budget())?;
    let mut preserved = old.pages().to_vec();
    for locator in [old.maps().owned(), old.maps().available()]
        .into_iter()
        .chain(
            old.long_value_maps()
                .iter()
                .flat_map(|map| [map.owned(), map.available()]),
        )
    {
        preserved.push(locator.page());
        let map = crate::alloc::mutation_map::MapBits::load(&mut database, locator, &mut budget())?;
        preserved.extend(map.existing_pages(
            database.geometry().page_count(),
            false,
            &mut budget(),
        )?);
        preserved.extend(map.spans.iter().map(|span| span.page));
    }
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::AutoIncrement),
        ColumnSpec::new(
            b"Text",
            ColumnType::Text {
                max_len: std::num::NonZeroU8::new(20).ok_or("width")?,
            },
        )
        .with_required(),
        ColumnSpec::new(b"Note", ColumnType::Memo).with_allow_zero_length(),
    ];
    let indexes = [IndexSpec {
        name: b"PrimaryKey",
        kind: IndexKind::Primary,
        fields: &[IndexColumnSpec::ascending(0)],
    }];
    let table = TableSpec {
        validation: crate::TableValidation::NONE,
        name: b"Added",
        columns: &columns,
        indexes: &indexes,
    };
    edit_schema(
        fixture.path(),
        SchemaEdit::CreateTable { table },
        &mut budget(),
    )?;
    let after = fs::read(fixture.path())?;
    for page in preserved {
        let start = page.get() as usize * PAGE_BYTES;
        assert!(
            before[start..start + PAGE_BYTES] == after[start..start + PAGE_BYTES],
            "prior user page {}",
            page.get()
        );
    }
    insert_row(
        fixture.path(),
        b"Added",
        &[
            RowValue::AutoIncrement,
            RowValue::Text(b"present"),
            RowValue::Memo(&[b'p'; 4000]),
        ],
        &mut budget(),
    )?;
    let mut database = DatabaseReader::open(fixture.path(), &mut budget())?;
    let added =
        crate::write::update::indexed_writable_table(&mut database, b"Added", &mut budget())?;
    assert_eq!(added.row_count(), 1);
    assert_eq!(
        database
            .index_tree(&added, 0, &mut budget())?
            .entries()
            .len(),
        1
    );
    let before = fs::read(fixture.path())?;
    assert!(
        insert_row(
            fixture.path(),
            b"Added",
            &[RowValue::AutoIncrement, RowValue::Null, RowValue::Null],
            &mut budget()
        )
        .is_err()
    );
    assert!(
        edit_schema(
            fixture.path(),
            SchemaEdit::CreateTable { table },
            &mut budget()
        )
        .is_err()
    );
    assert_eq!(fs::read(fixture.path())?, before);
    Ok(())
}

#[test]
fn appended_autoincrement_backfills_rows_then_continues_generation() -> TestResult {
    let fixture = Fixture::new(&[
        &[RowValue::Long(90), RowValue::Memo(&[b'a'; 3000])],
        &[RowValue::Long(20), RowValue::Null],
        &[RowValue::Long(50), RowValue::Memo(b"third")],
    ])?;
    fixture.create(b"OldKey", IndexKind::Primary, IndexDirection::Ascending)?;
    edit_schema(
        fixture.path(),
        SchemaEdit::CreateColumn {
            table: b"Items",
            column: ColumnSpec::new(b"Sequence", ColumnType::AutoIncrement),
        },
        &mut budget(),
    )?;
    let definition = fixture.table()?;
    assert!(definition.columns()[2].auto_increment());
    assert_eq!(&definition.raw_header()[16..20], &3_u32.to_le_bytes());
    let mut b = budget();
    let mut database = DatabaseReader::open(fixture.path(), &mut b)?;
    let mut rows = database.rows(&definition, &mut b)?;
    for expected in 1_i32..=3 {
        let row = rows.next_row()?.ok_or("backfilled row")?;
        assert_eq!(
            row.field(definition.columns()[2].ordinal())
                .and_then(|field| field.raw_bytes()),
            Some(expected.to_le_bytes().as_slice())
        );
    }
    drop(rows);
    insert_row(
        fixture.path(),
        b"Items",
        &[RowValue::Long(91), RowValue::Null, RowValue::AutoIncrement],
        &mut budget(),
    )?;
    assert_eq!(&fixture.table()?.raw_header()[16..20], &4_u32.to_le_bytes());
    let before = fs::read(fixture.path())?;
    assert!(
        edit_schema(
            fixture.path(),
            SchemaEdit::CreateColumn {
                table: b"Items",
                column: ColumnSpec::new(b"Second", ColumnType::AutoIncrement)
            },
            &mut budget()
        )
        .is_err()
    );
    assert_eq!(fs::read(fixture.path())?, before);
    Ok(())
}

#[test]
fn drop_columns_retains_rows_releases_payloads_and_allows_sparse_mutations() -> TestResult {
    let fixture = Fixture::new(&[&[RowValue::Long(7), RowValue::Memo(&[b'm'; 5000])]])?;
    let before_table = fixture.table()?;
    let mut b = budget();
    let mut database = DatabaseReader::open(fixture.path(), &mut b)?;
    let map = crate::alloc::mutation_map::MapBits::load(
        &mut database,
        before_table.long_value_maps()[0].owned(),
        &mut b,
    )?;
    let payload_pages = map.existing_pages(database.geometry().page_count(), false, &mut b)?;
    let mut rows = database.rows(&before_table, &mut b)?;
    let row = rows.next_row()?.ok_or("row")?;
    let locator = row.locator();
    let raw = row.raw_bytes().to_vec();
    drop(rows);
    edit_schema(
        fixture.path(),
        SchemaEdit::DropColumn {
            table: b"Items",
            column: b"Payload",
        },
        &mut budget(),
    )?;
    let table = fixture.table()?;
    assert_eq!(table.storage_column_count(), 2);
    assert_eq!(table.columns().len(), 1);
    let mut database = DatabaseReader::open(fixture.path(), &mut b)?;
    let global = crate::alloc::mutation_map::MapBits::load(
        &mut database,
        crate::alloc::mutation_map::global_locator(),
        &mut b,
    )?;
    for page in payload_pages {
        assert!(global.contains(page)?);
    }
    let mut rows = database.rows(&table, &mut b)?;
    let row = rows.next_row()?.ok_or("retained row")?;
    assert_eq!(row.locator(), locator);
    assert_eq!(row.raw_bytes(), raw);
    drop(rows);
    edit_schema(
        fixture.path(),
        SchemaEdit::CreateColumn {
            table: b"Items",
            column: ColumnSpec::new(b"Next", ColumnType::Long),
        },
        &mut budget(),
    )?;
    let table = fixture.table()?;
    assert_eq!(table.columns()[1].storage_ordinal(), 2);
    insert_row(
        fixture.path(),
        b"Items",
        &[RowValue::Long(8), RowValue::Long(20)],
        &mut budget(),
    )?;
    edit_schema(
        fixture.path(),
        SchemaEdit::CreateIndex {
            table: b"Items",
            index: IndexSpec {
                name: b"ByNext",
                fields: &[IndexColumnSpec::ascending(1)],
                kind: IndexKind::Ordinary,
            },
        },
        &mut budget(),
    )?;
    let table = fixture.table()?;
    assert_eq!(table.physical_indexes()[0].fields()[0].column().get(), 1);
    let before = fs::read(fixture.path())?;
    assert!(
        edit_schema(
            fixture.path(),
            SchemaEdit::DropColumn {
                table: b"Items",
                column: b"Next"
            },
            &mut budget()
        )
        .is_err()
    );
    assert_eq!(fs::read(fixture.path())?, before);
    Ok(())
}

#[test]
fn drop_table_removes_catalog_grants_and_releases_its_storage() -> TestResult {
    let fixture = Fixture::new(&[&[RowValue::Long(7), RowValue::Memo(&[b'm'; 5000])]])?;
    fixture.create(b"PrimaryKey", IndexKind::Primary, IndexDirection::Ascending)?;
    let table = fixture.table()?;
    edit_schema(
        fixture.path(),
        SchemaEdit::DropTable { table: b"Items" },
        &mut budget(),
    )?;
    let mut b = budget();
    let mut database = DatabaseReader::open(fixture.path(), &mut b)?;
    assert!(crate::write::update::indexed_writable_table(&mut database, b"Items", &mut b).is_err());
    let global = crate::alloc::mutation_map::MapBits::load(
        &mut database,
        crate::alloc::mutation_map::global_locator(),
        &mut b,
    )?;
    assert!(global.contains(table.root())?);
    let before = fs::read(fixture.path())?;
    assert!(
        edit_schema(
            fixture.path(),
            SchemaEdit::DropTable { table: b"Items" },
            &mut budget()
        )
        .is_err()
    );
    assert_eq!(fs::read(fixture.path())?, before);
    Ok(())
}

#[test]
fn field_rewrite_keeps_absent_appended_fixed_fields_null() -> TestResult {
    let fixture = Fixture::new(&[&[RowValue::Long(7), RowValue::Memo(b"old")]])?;
    let names = [b"A".as_slice(), b"B", b"C"];
    for name in names {
        edit_schema(
            fixture.path(),
            SchemaEdit::CreateColumn {
                table: b"Items",
                column: ColumnSpec::new(name, ColumnType::Long),
            },
            &mut budget(),
        )?;
    }
    let mut b = budget();
    let mut database = DatabaseReader::open(fixture.path(), &mut b)?;
    let table = fixture.table()?;
    let mut rows = database.rows(&table, &mut b)?;
    let locator = rows.next_row()?.ok_or("old row")?.locator();
    drop(rows);
    update_field(
        fixture.path(),
        FieldUpdate {
            table: b"Items",
            row: locator,
            column: ColumnOrdinal::new(0),
            value: RowValue::Null,
        },
        &mut budget(),
    )?;
    let mut database = DatabaseReader::open(fixture.path(), &mut b)?;
    let mut rows = database.rows(&table, &mut b)?;
    let row = rows.next_row()?.ok_or("rewritten row")?;
    for column in [0, 2, 3, 4] {
        assert!(
            row.field(ColumnOrdinal::new(column))
                .ok_or("field")?
                .is_null()
        );
    }
    assert_eq!(row.locator(), locator);
    Ok(())
}

#[test]
fn sparse_fixed_offsets_are_checked_against_actual_minimum_row_size() -> TestResult {
    let fixture = Fixture::new(&[])?;
    let width = std::num::NonZeroU8::new(200).ok_or("width")?;
    for ordinal in 0..9 {
        let name = format!("Fixed{ordinal}");
        edit_schema(
            fixture.path(),
            SchemaEdit::CreateColumn {
                table: b"Items",
                column: ColumnSpec::new(name.as_bytes(), ColumnType::FixedText { len: width }),
            },
            &mut budget(),
        )?;
    }
    for ordinal in [1, 3, 5, 7] {
        let name = format!("Fixed{ordinal}");
        edit_schema(
            fixture.path(),
            SchemaEdit::DropColumn {
                table: b"Items",
                column: name.as_bytes(),
            },
            &mut budget(),
        )?;
    }
    let before = fs::read(fixture.path())?;
    assert!(
        edit_schema(
            fixture.path(),
            SchemaEdit::CreateColumn {
                table: b"Items",
                column: ColumnSpec::new(
                    b"TooWide",
                    ColumnType::FixedText {
                        len: std::num::NonZeroU8::new(255).ok_or("width")?
                    }
                )
            },
            &mut budget()
        )
        .is_err()
    );
    assert_eq!(fs::read(fixture.path())?, before);
    Ok(())
}
