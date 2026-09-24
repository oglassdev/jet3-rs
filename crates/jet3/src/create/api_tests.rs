use crate::{
    ColumnRef, ColumnSpec, ColumnType, ComposeError, DatabaseReader, IndexColumnSpec,
    IndexDirection, IndexKind, IndexSpec, PageNumber, PublishStage, TableSpec,
    create::schema_plan::TableSchemaPlanError, definition::column_writer::nz,
};
use std::fs;
use std::path::PathBuf;

use super::{
    api::{CreateDatabaseError, DatabaseSpec, TableRows, create_database},
    check::{ImageCheckError, check_image},
};

pub(super) type TestResult = Result<(), Box<dyn std::error::Error>>;
type Accepts = fn(&ComposeError) -> bool;

pub(super) struct TestDirectory {
    pub(super) path: crate::testkit::TempDir,
}

impl TestDirectory {
    pub(super) fn create() -> Result<Self, std::io::Error> {
        let path = crate::testkit::TempDir::new("create-test")?;
        Ok(Self { path })
    }

    pub(super) fn target(&self) -> PathBuf {
        self.path.join("created.mdb")
    }

    pub(super) fn entries(&self) -> Result<Vec<String>, std::io::Error> {
        let mut names = Vec::new();
        for entry in fs::read_dir(&self.path)? {
            names.push(entry?.file_name().to_string_lossy().into_owned());
        }
        names.sort();
        Ok(names)
    }
}

pub(super) use crate::testkit::budget;

pub(super) const ID: ColumnSpec<'static> = ColumnSpec::new(b"Id", ColumnType::Long);
pub(super) const CODE: ColumnSpec<'static> =
    ColumnSpec::new(b"Code", ColumnType::Text { max_len: nz(8) });
pub(super) const SEQUENCE: ColumnSpec<'static> = ColumnSpec::new(b"Sequence", ColumnType::Long);
pub(super) const NOTE: ColumnSpec<'static> = ColumnSpec::new(b"Note", ColumnType::Memo);

pub(super) const fn field(column: u16, direction: IndexDirection) -> IndexColumnSpec<'static> {
    IndexColumnSpec {
        column: ColumnRef::Ordinal(column),
        direction,
    }
}

#[test]
fn a_mixed_table_with_three_indexes_is_created_and_reopens() -> TestResult {
    let directory = TestDirectory::create()?;
    let target = directory.target();
    let columns = [ID, CODE, SEQUENCE];
    let indexes = [
        IndexSpec {
            name: b"PrimaryKey",
            fields: &[field(0, IndexDirection::Ascending)],
            kind: IndexKind::Primary,
        },
        IndexSpec {
            name: b"ByCode",
            fields: &[IndexColumnSpec::ascending(b"Code")],
            kind: IndexKind::Unique,
        },
        IndexSpec {
            name: b"BySequence",
            fields: &[
                field(1, IndexDirection::Descending),
                field(2, IndexDirection::Ascending),
            ],
            kind: IndexKind::Ordinary,
        },
    ];
    let spec = TableSpec {
        validation: crate::TableValidation::NONE,
        name: b"Items",
        columns: &columns,
        indexes: &indexes,
    };
    create_database(
        &target,
        &DatabaseSpec {
            tables: &[TableRows::empty(spec)],
            ..DatabaseSpec::default()
        },
        &mut budget(),
    )?;
    assert_eq!(directory.entries()?, ["created.mdb"]);
    assert_eq!(fs::metadata(&target)?.len(), 26 * crate::PAGE_BYTES as u64);

    let mut budget = budget();
    let mut database = DatabaseReader::open(&target, &mut budget)?;
    let definition = database.table_definition(PageNumber::new(20), &mut budget)?;
    assert_eq!(definition.columns().len(), 3);
    assert_eq!(definition.physical_indexes().len(), 3);
    assert_eq!(definition.physical_indexes()[1].raw_flags(), 0x01);
    assert_eq!(
        definition
            .indexes()
            .iter()
            .map(|index| index.name().raw_bytes())
            .collect::<Vec<_>>(),
        [b"ByCode".as_slice(), b"BySequence", b"PrimaryKey"]
    );
    for ordinal in 0..3 {
        assert!(
            database
                .index_tree(&definition, ordinal, &mut budget)?
                .entries()
                .is_empty()
        );
    }
    Ok(())
}

#[test]
fn candidate_check_rejects_an_index_kind_mismatch() -> TestResult {
    let directory = TestDirectory::create()?;
    let target = directory.target();
    let columns = [ID];
    let fields = [field(0, IndexDirection::Ascending)];
    let unique_indexes = [IndexSpec {
        name: b"ById",
        fields: &fields,
        kind: IndexKind::Unique,
    }];
    create_database(
        &target,
        &DatabaseSpec {
            tables: &[TableRows::empty(TableSpec {
                validation: crate::TableValidation::NONE,
                name: b"Items",
                columns: &columns,
                indexes: &unique_indexes,
            })],
            ..DatabaseSpec::default()
        },
        &mut budget(),
    )?;

    let ordinary_indexes = [IndexSpec {
        name: b"ById",
        fields: &fields,
        kind: IndexKind::Ordinary,
    }];
    let page_count = fs::metadata(&target)?.len() / crate::PAGE_BYTES as u64;
    let error = check_image(
        &target,
        &[TableSpec {
            validation: crate::TableValidation::NONE,
            name: b"Items",
            columns: &columns,
            indexes: &ordinary_indexes,
        }],
        page_count,
        &mut budget(),
    )
    .err()
    .ok_or("candidate check accepted mismatched index flags")?;
    assert!(matches!(
        error,
        ImageCheckError::Mismatch {
            detail: "index kind"
        }
    ));
    Ok(())
}

#[test]
fn a_memo_table_is_created_and_reopens() -> TestResult {
    let directory = TestDirectory::create()?;
    let target = directory.target();
    let columns = [ID, NOTE];
    let spec = TableSpec {
        validation: crate::TableValidation::NONE,
        name: b"Notes",
        columns: &columns,
        indexes: &[],
    };
    create_database(
        &target,
        &DatabaseSpec {
            tables: &[TableRows::empty(spec)],
            ..DatabaseSpec::default()
        },
        &mut budget(),
    )?;
    assert_eq!(fs::metadata(&target)?.len(), 23 * crate::PAGE_BYTES as u64);
    let mut budget = budget();
    let mut database = DatabaseReader::open(&target, &mut budget)?;
    let definition = database.table_definition(PageNumber::new(20), &mut budget)?;
    assert_eq!(definition.long_value_maps().len(), 1);
    Ok(())
}

#[test]
fn unsupported_layouts_are_refused_before_anything_is_written() -> TestResult {
    let directory = TestDirectory::create()?;
    let target = directory.target();
    let by_id = [IndexSpec {
        name: b"ById",
        fields: &[field(0, IndexDirection::Ascending)],
        kind: IndexKind::Ordinary,
    }];
    let indexed_memo = [NOTE];
    let undefined_byte = [ColumnSpec::new(b"Caf\x81", ColumnType::Long)];
    let cases: [(TableSpec<'_>, Accepts); 2] = [
        (
            TableSpec {
                validation: crate::TableValidation::NONE,
                name: b"Mixed",
                columns: &indexed_memo,
                indexes: &by_id,
            },
            |error| {
                matches!(
                    error,
                    ComposeError::Schema(TableSchemaPlanError::Definition(_))
                )
            },
        ),
        (
            TableSpec {
                validation: crate::TableValidation::NONE,
                name: b"Accent",
                columns: &undefined_byte,
                indexes: &[],
            },
            |error| {
                matches!(
                    error,
                    ComposeError::Schema(TableSchemaPlanError::NameByteUnestablished {
                        byte: 0x81,
                        ..
                    })
                )
            },
        ),
    ];
    for (spec, accepts) in cases {
        match create_database(
            &target,
            &DatabaseSpec {
                tables: &[TableRows::empty(spec)],
                ..DatabaseSpec::default()
            },
            &mut budget(),
        ) {
            Err(CreateDatabaseError::Compose(error)) if accepts(&error) => {}
            other => return Err(format!("unexpected result: {other:?}").into()),
        }
        assert!(directory.entries()?.is_empty());
    }
    Ok(())
}

#[test]
fn an_existing_destination_is_refused_and_left_unchanged() -> TestResult {
    let directory = TestDirectory::create()?;
    let target = directory.target();
    fs::write(&target, b"keep me")?;
    let columns = [ID];
    let spec = TableSpec {
        validation: crate::TableValidation::NONE,
        name: b"Alpha",
        columns: &columns,
        indexes: &[],
    };
    match create_database(
        &target,
        &DatabaseSpec {
            tables: &[TableRows::empty(spec)],
            ..DatabaseSpec::default()
        },
        &mut budget(),
    ) {
        Err(CreateDatabaseError::Publish(error)) => {
            assert_eq!(error.stage(), PublishStage::PrivateCopyCreation);
        }
        other => return Err(format!("unexpected result: {other:?}").into()),
    }
    assert_eq!(fs::read(&target)?, b"keep me");
    assert_eq!(directory.entries()?, ["created.mdb"]);
    Ok(())
}

#[test]
fn a_definition_spanning_one_continuation_is_created_and_reopens() -> TestResult {
    // EXP-0107's compact construction: 70 ten-byte-named Long columns take a
    // root, map page, LvProp page, and one continuation at page 23.
    let directory = TestDirectory::create()?;
    let target = directory.target();
    let names = (0..70)
        .map(|ordinal| format!("Field{ordinal:05}").into_bytes())
        .collect::<Vec<_>>();
    let columns = names
        .iter()
        .map(|name| ColumnSpec::new(name, ColumnType::Long))
        .collect::<Vec<_>>();
    let spec = TableSpec {
        validation: crate::TableValidation::NONE,
        name: b"Wide",
        columns: &columns,
        indexes: &[],
    };
    create_database(
        &target,
        &DatabaseSpec {
            tables: &[TableRows::empty(spec)],
            ..DatabaseSpec::default()
        },
        &mut budget(),
    )?;
    assert_eq!(fs::metadata(&target)?.len(), 24 * crate::PAGE_BYTES as u64);
    let mut budget = budget();
    let mut database = DatabaseReader::open(&target, &mut budget)?;
    let definition = database.table_definition(PageNumber::new(20), &mut budget)?;
    assert_eq!(definition.columns().len(), 70);
    Ok(())
}

#[test]
fn case_folded_duplicates_are_refused_before_writing() -> TestResult {
    let directory = TestDirectory::create()?;
    let target = directory.target();
    let table = |name: &'static [u8]| TableSpec {
        validation: crate::TableValidation::NONE,
        name,
        columns: &[ID],
        indexes: &[],
    };
    let duplicate = [table(b"Alpha"), table(b"ALPHA")];
    match create_database(
        &target,
        &DatabaseSpec {
            tables: &duplicate.map(TableRows::empty),
            ..DatabaseSpec::default()
        },
        &mut budget(),
    ) {
        Err(CreateDatabaseError::Compose(ComposeError::DuplicateTableName {
            first: 0,
            second: 1,
        })) => {}
        other => return Err(format!("unexpected result: {other:?}").into()),
    }
    assert!(directory.entries()?.is_empty());
    Ok(())
}

#[test]
fn two_tables_are_created_in_order_and_reopen() -> TestResult {
    let directory = TestDirectory::create()?;
    let target = directory.target();
    let indexes = [IndexSpec {
        name: b"PrimaryKey",
        fields: &[IndexColumnSpec::ascending(b"Id")],
        kind: IndexKind::Primary,
    }];
    let tables = [
        TableSpec {
            validation: crate::TableValidation::NONE,
            name: b"Alpha",
            columns: &[ID],
            indexes: &[],
        },
        TableSpec {
            validation: crate::TableValidation::NONE,
            name: b"Gamma",
            columns: &[ID, CODE],
            indexes: &indexes,
        },
    ];
    create_database(
        &target,
        &DatabaseSpec {
            tables: &tables.map(TableRows::empty),
            ..DatabaseSpec::default()
        },
        &mut budget(),
    )?;
    assert_eq!(fs::metadata(&target)?.len(), 27 * crate::PAGE_BYTES as u64);
    let mut budget = budget();
    let mut database = DatabaseReader::open(&target, &mut budget)?;
    let gamma = database.table_definition(PageNumber::new(23), &mut budget)?;
    assert_eq!(gamma.columns().len(), 2);
    assert_eq!(gamma.physical_indexes()[0].root(), PageNumber::new(26));
    Ok(())
}
