use super::*;
use crate::{PageImageError, RowValue, RowWriteError, create_database_with_rows};

fn scalar_table() -> TableSpec<'static> {
    TableSpec {
        name: b"Items",
        columns: &[ID, CODE],
        indexes: &[],
    }
}

#[test]
fn scalar_rows_publish_and_match_requested_values() -> TestResult {
    let directory = TestDirectory::create()?;
    let target = directory.target();
    let rows: &[&[RowValue<'_>]] = &[
        &[RowValue::Long(1), RowValue::Text(b"one")],
        &[RowValue::Long(-2), RowValue::Text(b"two")],
        &[RowValue::Null, RowValue::Null],
    ];
    create_database_with_rows(&target, &scalar_table(), rows, &mut budget())?;
    super::super::check_initial_rows(&target, &scalar_table(), rows, &mut budget())?;
    let bytes = fs::read(&target)?;
    assert_eq!(bytes.len(), 24 * crate::PAGE_BYTES);
    assert_eq!(
        &bytes[20 * crate::PAGE_BYTES + 12..20 * crate::PAGE_BYTES + 16],
        &3_u32.to_le_bytes()
    );
    assert_eq!(directory.entries()?, ["created.mdb"]);
    Ok(())
}

#[test]
fn empty_initial_rows_do_not_allocate_a_data_page() -> TestResult {
    let directory = TestDirectory::create()?;
    create_database_with_rows(directory.target(), &scalar_table(), &[], &mut budget())?;
    assert_eq!(
        fs::metadata(directory.target())?.len(),
        23 * crate::PAGE_BYTES as u64
    );
    Ok(())
}

#[test]
fn rows_that_exceed_the_page_or_have_wrong_types_leave_no_file() -> TestResult {
    let directory = TestDirectory::create()?;
    let row = [RowValue::Long(1), RowValue::Text(b"12345678")];
    let rows = vec![row.as_slice(); 256];
    assert!(matches!(
        create_database_with_rows(directory.target(), &scalar_table(), &rows, &mut budget()),
        Err(CreateDatabaseError::Compose(ComposeError::Page(
            PageImageError::PageFull { .. }
        )))
    ));
    assert!(matches!(
        create_database_with_rows(
            directory.target(),
            &scalar_table(),
            &[&[RowValue::Byte(1), RowValue::Null]],
            &mut budget()
        ),
        Err(CreateDatabaseError::Compose(ComposeError::Row(
            RowWriteError::TypeMismatch { ordinal: 0, .. }
        )))
    ));
    assert!(matches!(
        create_database_with_rows(directory.target(), &scalar_table(), &[&[]], &mut budget()),
        Err(CreateDatabaseError::Compose(ComposeError::Row(
            RowWriteError::ValueCountMismatch { .. }
        )))
    ));
    assert!(directory.entries()?.is_empty());
    Ok(())
}

#[test]
fn unsupported_initial_row_schemas_leave_no_file() -> TestResult {
    let directory = TestDirectory::create()?;
    for column_type in [
        ColumnType::AutoIncrement,
        ColumnType::Memo,
        ColumnType::LongBinary,
    ] {
        let columns = [ColumnSpec::new(b"Value", column_type)];
        let table = TableSpec {
            name: b"Items",
            columns: &columns,
            indexes: &[],
        };
        assert!(matches!(
            create_database_with_rows(
                directory.target(),
                &table,
                &[&[RowValue::Null]],
                &mut budget()
            ),
            Err(CreateDatabaseError::Compose(
                ComposeError::UnsupportedInitialRowSchema
            ))
        ));
    }
    let indexes = [IndexSpec {
        name: b"ById",
        fields: &[IndexColumnSpec::ascending(b"Id")],
        kind: IndexKind::Ordinary,
    }];
    let table = TableSpec {
        name: b"Items",
        columns: &[ID],
        indexes: &indexes,
    };
    assert!(matches!(
        create_database_with_rows(
            directory.target(),
            &table,
            &[&[RowValue::Long(1)]],
            &mut budget()
        ),
        Err(CreateDatabaseError::Compose(
            ComposeError::UnsupportedInitialRowSchema
        ))
    ));
    assert!(directory.entries()?.is_empty());
    Ok(())
}

#[test]
fn initial_row_check_detects_wrong_values_and_counts() -> TestResult {
    let directory = TestDirectory::create()?;
    let rows: &[&[RowValue<'_>]] = &[&[RowValue::Long(1), RowValue::Text(b"one")]];
    create_database_with_rows(directory.target(), &scalar_table(), rows, &mut budget())?;
    for (rows, detail) in [
        (
            &[&[RowValue::Long(2), RowValue::Text(b"one")][..]][..],
            "initial row value",
        ),
        (&[][..], "initial row count"),
        (&[rows[0], rows[0]][..], "initial row count"),
    ] {
        assert!(
            matches!(super::super::check_initial_rows(&directory.target(), &scalar_table(), rows, &mut budget()),
            Err(CandidateCheckError::Mismatch { detail: actual }) if actual == detail)
        );
    }
    Ok(())
}

#[test]
fn initial_rows_preserve_existing_destination_and_enforce_budget() -> TestResult {
    let directory = TestDirectory::create()?;
    fs::write(directory.target(), b"keep me")?;
    let rows: &[&[RowValue<'_>]] = &[&[RowValue::Long(1), RowValue::Null]];
    assert!(matches!(
        create_database_with_rows(directory.target(), &scalar_table(), rows, &mut budget()),
        Err(CreateDatabaseError::Publish(_))
    ));
    assert_eq!(fs::read(directory.target())?, b"keep me");
    let mut limited = ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(0));
    assert!(matches!(
        create_database_with_rows(
            directory.path.join("limited.mdb"),
            &scalar_table(),
            rows,
            &mut limited
        ),
        Err(CreateDatabaseError::Compose(_))
    ));
    assert_eq!(directory.entries()?, ["created.mdb"]);
    Ok(())
}

#[test]
fn initial_rows_leave_one_available_row_slot() -> TestResult {
    let directory = TestDirectory::create()?;
    let table = TableSpec {
        name: b"Bits",
        columns: &[ColumnSpec::new(b"Bit", ColumnType::Boolean)],
        indexes: &[],
    };
    let row = [RowValue::Boolean(true)];
    let rows = vec![row.as_slice(); 256];
    create_database_with_rows(directory.target(), &table, &rows[..255], &mut budget())?;
    assert!(matches!(
        create_database_with_rows(
            directory.path.join("overflow.mdb"),
            &table,
            &rows,
            &mut budget()
        ),
        Err(CreateDatabaseError::Compose(ComposeError::Page(
            PageImageError::RowSlotsExhausted { maximum: 256 }
        )))
    ));
    assert_eq!(directory.entries()?, ["created.mdb"]);
    Ok(())
}

#[test]
fn initial_rows_refuse_a_continued_definition() -> TestResult {
    let directory = TestDirectory::create()?;
    let names = (0..70)
        .map(|ordinal| format!("Field{ordinal:05}").into_bytes())
        .collect::<Vec<_>>();
    let columns = names
        .iter()
        .map(|name| ColumnSpec::new(name, ColumnType::Long))
        .collect::<Vec<_>>();
    let table = TableSpec {
        name: b"Wide",
        columns: &columns,
        indexes: &[],
    };
    assert!(matches!(
        create_database_with_rows(directory.target(), &table, &[], &mut budget()),
        Err(CreateDatabaseError::Compose(
            ComposeError::UnsupportedInitialRowSchema
        ))
    ));
    assert!(directory.entries()?.is_empty());
    Ok(())
}

#[test]
fn initial_rows_require_space_for_a_minimal_row() -> TestResult {
    let directory = TestDirectory::create()?;
    let table = TableSpec {
        name: b"Numbers",
        columns: &[ID],
        indexes: &[],
    };
    let row = [RowValue::Long(1)];
    let rows = vec![row.as_slice(); 254];
    create_database_with_rows(directory.target(), &table, &rows[..253], &mut budget())?;
    assert!(matches!(
        create_database_with_rows(
            directory.path.join("full.mdb"),
            &table,
            &rows,
            &mut budget()
        ),
        Err(CreateDatabaseError::Compose(ComposeError::Page(
            PageImageError::PageFull { .. }
        )))
    ));
    assert_eq!(directory.entries()?, ["created.mdb"]);
    Ok(())
}
