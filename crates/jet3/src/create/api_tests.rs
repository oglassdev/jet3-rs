use crate::WriteError;
use crate::testkit::create;
use crate::testkit::{index, table};
use crate::{
    ColumnRef, ColumnSpec, ColumnType, ComposeError, DatabaseReader, IndexColumnSpec,
    IndexDirection, IndexKind, IndexSpec, PageNumber, PublishStage, RowValue, TableSpec,
    create::schema_plan::TableSchemaPlanError, definition::column_writer::nz,
};
use std::fs;

use super::{
    api::TableRows,
    check::{ImageCheckError, check_image},
};

pub(super) use crate::testkit::TestResult;
type Accepts = fn(&ComposeError) -> bool;

pub(super) use crate::testkit::TempDir;

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
fn candidate_check_rejects_an_index_kind_mismatch() -> TestResult {
    let directory = TempDir::new("create")?;
    let target = directory.target();
    let columns = [ID];
    let fields = [field(0, IndexDirection::Ascending)];
    let unique_indexes = [index(b"ById", &fields, IndexKind::Unique)];
    create(
        &target,
        &[TableRows::empty(table(b"Items", &columns, &unique_indexes))],
    )?;

    let ordinary_indexes = [index(b"ById", &fields, IndexKind::Ordinary)];
    let page_count = fs::metadata(&target)?.len() / crate::PAGE_BYTES as u64;
    let error = check_image(
        &target,
        &[table(b"Items", &columns, &ordinary_indexes)],
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
fn unsupported_layouts_are_refused_before_anything_is_written() -> TestResult {
    let directory = TempDir::new("create")?;
    let target = directory.target();
    let by_id = [IndexSpec {
        name: b"ById",
        fields: &[field(0, IndexDirection::Ascending)],
        kind: IndexKind::Ordinary,
    }];
    let indexed_memo = [NOTE];
    let undefined_byte = [ColumnSpec::new(b"Caf\x81", ColumnType::Long)];
    let cases: [(TableSpec<'_>, Accepts); 2] = [
        (table(b"Mixed", &indexed_memo, &by_id), |error| {
            matches!(
                error,
                ComposeError::Schema(TableSchemaPlanError::Definition(_))
            )
        }),
        (table(b"Accent", &undefined_byte, &[]), |error| {
            matches!(
                error,
                ComposeError::Schema(TableSchemaPlanError::NameByteUnestablished {
                    byte: 0x81,
                    ..
                })
            )
        }),
    ];
    for (spec, accepts) in cases {
        match create(&target, &[TableRows::empty(spec)]) {
            Err(WriteError::Compose(error)) if accepts(&error) => {}
            other => return Err(format!("unexpected result: {other:?}").into()),
        }
        assert!(directory.entries()?.is_empty());
    }
    Ok(())
}

#[test]
fn an_existing_destination_is_refused_and_left_unchanged() -> TestResult {
    let directory = TempDir::new("create")?;
    let target = directory.target();
    fs::write(&target, b"keep me")?;
    let columns = [ID];
    let spec = table(b"Alpha", &columns, &[]);
    // Schema-only and initial-row creation publish through separate paths.
    for rows in [&[][..], &[&[RowValue::Long(1)][..]]] {
        match create(&target, &[TableRows { table: spec, rows }]) {
            Err(WriteError::CreatePublish(error)) => {
                assert_eq!(error.stage(), PublishStage::PrivateCopyCreation);
            }
            other => return Err(format!("unexpected result: {other:?}").into()),
        }
        assert_eq!(fs::read(&target)?, b"keep me");
        assert_eq!(directory.entries()?, ["created.mdb"]);
    }
    Ok(())
}

#[test]
fn two_tables_are_created_in_order_and_reopen() -> TestResult {
    let directory = TempDir::new("create")?;
    let target = directory.target();
    let indexes = [IndexSpec {
        name: b"PrimaryKey",
        fields: &[IndexColumnSpec::ascending(b"Id")],
        kind: IndexKind::Primary,
    }];
    let tables = [
        table(b"Alpha", &[ID], &[]),
        table(b"Gamma", &[ID, CODE], &indexes),
    ];
    create(&target, &tables.map(TableRows::empty))?;
    assert_eq!(fs::metadata(&target)?.len(), 27 * crate::PAGE_BYTES as u64);
    let mut budget = budget();
    let mut database = DatabaseReader::open(&target, &mut budget)?;
    let gamma = database.table_definition(PageNumber::new(23), &mut budget)?;
    assert_eq!(gamma.columns().len(), 2);
    assert_eq!(gamma.physical_indexes()[0].root(), PageNumber::new(26));
    Ok(())
}
