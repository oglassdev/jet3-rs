use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

use super::{CandidateCheckError, CreateDatabaseError, check_candidate, create_database};
use crate::table_schema_plan::TableSchemaPlanError;
use crate::{
    BootstrapComposeError, ColumnPhysicalType, ColumnSpec, ColumnStorageKind, DatabaseReader,
    IndexDirection, IndexFieldSpec, IndexKind, IndexSpec, PageNumber, PublishStage, ResourceBudget,
    ResourceLimits, TableSpec,
};

static NEXT_TEST_DIRECTORY: AtomicU64 = AtomicU64::new(0);
type TestResult = Result<(), Box<dyn std::error::Error>>;
type Accepts = fn(&BootstrapComposeError) -> bool;

struct TestDirectory {
    path: PathBuf,
}

impl TestDirectory {
    fn create() -> Result<Self, std::io::Error> {
        let sequence = NEXT_TEST_DIRECTORY.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "jet3-create-test-{}-{sequence}",
            std::process::id()
        ));
        fs::create_dir(&path)?;
        Ok(Self { path })
    }

    fn target(&self) -> PathBuf {
        self.path.join("created.mdb")
    }

    fn entries(&self) -> Result<Vec<String>, std::io::Error> {
        let mut names = Vec::new();
        for entry in fs::read_dir(&self.path)? {
            names.push(entry?.file_name().to_string_lossy().into_owned());
        }
        names.sort();
        Ok(names)
    }
}

impl Drop for TestDirectory {
    fn drop(&mut self) {
        let _cleanup_result = fs::remove_dir_all(&self.path);
    }
}

fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}

const ID: ColumnSpec<'static> =
    ColumnSpec::new(b"Id", ColumnPhysicalType::Long, ColumnStorageKind::Fixed, 4);
const CODE: ColumnSpec<'static> = ColumnSpec::new(
    b"Code",
    ColumnPhysicalType::Text,
    ColumnStorageKind::Variable,
    8,
);
const SEQUENCE: ColumnSpec<'static> = ColumnSpec::new(
    b"Sequence",
    ColumnPhysicalType::Long,
    ColumnStorageKind::Fixed,
    4,
);
const NOTE: ColumnSpec<'static> = ColumnSpec::new(
    b"Note",
    ColumnPhysicalType::Memo,
    ColumnStorageKind::Variable,
    0,
);

const fn field(column: u16, direction: IndexDirection) -> IndexFieldSpec {
    IndexFieldSpec { column, direction }
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
            fields: &[field(1, IndexDirection::Ascending)],
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
        name: b"Items",
        columns: &columns,
        indexes: &indexes,
    };
    create_database(&target, &spec, &mut budget())?;
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
        &TableSpec {
            name: b"Items",
            columns: &columns,
            indexes: &unique_indexes,
        },
        &mut budget(),
    )?;

    let ordinary_indexes = [IndexSpec {
        name: b"ById",
        fields: &fields,
        kind: IndexKind::Ordinary,
    }];
    let page_count = fs::metadata(&target)?.len() / crate::PAGE_BYTES as u64;
    let error = check_candidate(
        &target,
        &TableSpec {
            name: b"Items",
            columns: &columns,
            indexes: &ordinary_indexes,
        },
        page_count,
        &mut budget(),
    )
    .err()
    .ok_or("candidate check accepted mismatched index flags")?;
    assert!(matches!(
        error,
        CandidateCheckError::Mismatch {
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
        name: b"Notes",
        columns: &columns,
        indexes: &[],
    };
    create_database(&target, &spec, &mut budget())?;
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
    let indexed_memo = [ID, NOTE];
    let high_byte = [ColumnSpec::new(
        b"Caf\xe9",
        ColumnPhysicalType::Long,
        ColumnStorageKind::Fixed,
        4,
    )];
    let wide_names = (0..70)
        .map(|ordinal| format!("Field{ordinal:05}").into_bytes())
        .collect::<Vec<_>>();
    let wide = wide_names
        .iter()
        .map(|name| ColumnSpec::new(name, ColumnPhysicalType::Long, ColumnStorageKind::Fixed, 4))
        .collect::<Vec<_>>();
    let cases: [(TableSpec<'_>, Accepts); 3] = [
        (
            TableSpec {
                name: b"Mixed",
                columns: &indexed_memo,
                indexes: &by_id,
            },
            |error| matches!(error, BootstrapComposeError::UnobservedMapRowLayout),
        ),
        (
            TableSpec {
                name: b"Accent",
                columns: &high_byte,
                indexes: &[],
            },
            |error| {
                matches!(
                    error,
                    BootstrapComposeError::Schema(TableSchemaPlanError::NameByteUnestablished {
                        byte: 0xe9,
                        ..
                    })
                )
            },
        ),
        (
            TableSpec {
                name: b"Wide",
                columns: &wide,
                indexes: &[],
            },
            |error| {
                matches!(
                    error,
                    BootstrapComposeError::Schema(
                        TableSchemaPlanError::ContinuationPlacementUnestablished {
                            continuations: 1,
                            ..
                        }
                    )
                )
            },
        ),
    ];
    for (spec, accepts) in cases {
        match create_database(&target, &spec, &mut budget()) {
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
        name: b"Alpha",
        columns: &columns,
        indexes: &[],
    };
    match create_database(&target, &spec, &mut budget()) {
        Err(CreateDatabaseError::Publish(error)) => {
            assert_eq!(error.stage(), PublishStage::PrivateCopyCreation);
        }
        other => return Err(format!("unexpected result: {other:?}").into()),
    }
    assert_eq!(fs::read(&target)?, b"keep me");
    assert_eq!(directory.entries()?, ["created.mdb"]);
    Ok(())
}
