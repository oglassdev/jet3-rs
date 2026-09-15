use super::*;
use crate::{
    ColumnSpec, ColumnType, IndexColumnSpec, IndexKind, IndexSpec, PAGE_BYTES, PageNumber,
    ResourceLimits, RowDirectoryError, RowValue, SliceSource, TableRows, TableSpec,
    creation::composer::compose_database_with_table_rows,
};

type TestResult<T = ()> = Result<T, Box<dyn std::error::Error>>;

fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}

fn open<'a>(
    bytes: &'a [u8],
    budget: &mut ResourceBudget,
) -> TestResult<DatabaseReader<SliceSource<'a>>> {
    Ok(DatabaseReader::from_source(
        SliceSource::new(bytes, budget.read_budget())?,
        budget,
    )?)
}

fn fixture() -> TestResult<Vec<u8>> {
    let id = ColumnSpec::new(b"Id", ColumnType::Long);
    let index = IndexSpec {
        name: b"PrimaryKey",
        fields: &[IndexColumnSpec::ascending(b"Id")],
        kind: IndexKind::Primary,
    };
    let middle = [b'b'; 512];
    let chained = [b'c'; 4096];
    let requests = [
        TableRows {
            table: TableSpec {
                name: b"Items",
                columns: &[id],
                indexes: &[index],
            },
            rows: &[
                &[RowValue::Long(2)],
                &[RowValue::Long(0)],
                &[RowValue::Long(1)],
            ],
        },
        TableRows {
            table: TableSpec {
                name: b"Notes",
                columns: &[id, ColumnSpec::new(b"Body", ColumnType::Memo)],
                indexes: &[],
            },
            rows: &[
                &[RowValue::Long(1), RowValue::Memo(&[233])],
                &[RowValue::Long(2), RowValue::Memo(&middle)],
                &[RowValue::Long(3), RowValue::Memo(&chained)],
            ],
        },
        TableRows {
            table: TableSpec {
                name: b"Payload",
                columns: &[ColumnSpec::new(b"Data", ColumnType::LongBinary)],
                indexes: &[],
            },
            rows: &[&[RowValue::LongBinary(&[42; 33])]],
        },
    ];
    let plan = compose_database_with_table_rows(&requests, &mut budget())?;
    Ok(plan
        .pages()
        .iter()
        .flat_map(|page| page.image().as_bytes().iter().copied())
        .collect())
}

#[allow(
    clippy::result_large_err,
    reason = "Exercise the allocation-free public error."
)]
fn validate(bytes: &[u8]) -> Result<ValidationReport, ValidationError> {
    let mut budget = budget();
    let mut database = open(bytes, &mut budget).unwrap();
    database.validate(TextCodePage::Windows1252, &mut budget)
}

fn definition(bytes: &[u8], name: &[u8]) -> TestResult<TableDefinition> {
    let mut budget = budget();
    let mut database = open(bytes, &mut budget)?;
    let mut catalog = database.catalog(&mut budget)?;
    let mut root = None;
    while let Some(record) = catalog.next_record()? {
        if record.name().raw_bytes() == name {
            root = record.table_definition();
        }
    }
    drop(catalog);
    Ok(database.table_definition(root.ok_or("missing fixture table")?, &mut budget)?)
}

fn page_start(page: PageNumber) -> usize {
    page.get() as usize * PAGE_BYTES
}

#[test]
fn checks_multiple_tables_index_and_inline_single_and_chained_long_values() -> TestResult {
    let bytes = fixture()?;
    let report = validate(&bytes)?;
    assert_eq!(
        report,
        ValidationReport {
            catalog_objects: 11,
            user_tables: 3,
            skipped_system_objects: 8,
            rows: 7,
            values: 10,
            indexes: 1,
            index_entries: 3,
            long_values: 4,
            long_value_bytes: 4642,
            ..ValidationReport::default()
        }
    );
    assert_eq!(definition(&bytes, b"Notes")?.row_count(), 3);
    Ok(())
}

#[test]
fn rejects_system_definition_kind_for_a_user_catalog_table() -> TestResult {
    let mut bytes = fixture()?;
    let table = definition(&bytes, b"Payload")?;
    let root = page_start(table.root());
    // EXP-0059/0073: system definitions use marker 53 and column constant
    // zero/class 12 for this variable column. No index prefixes precede it.
    bytes[root + 20] = 0x53;
    bytes[root + 43 + 7..root + 43 + 9].fill(0);
    bytes[root + 43 + 13] = 0x12;
    assert_eq!(
        definition(&bytes, b"Payload")?.kind(),
        TableDefinitionKind::System
    );
    assert!(matches!(validate(&bytes), Err(ValidationError::Table {
        table,
        source: TableValidationError::DefinitionKind {
            expected: TableDefinitionKind::User,
            actual: TableDefinitionKind::System,
        },
    }) if table.class() == CatalogObjectClass::User && table.name().raw_bytes() == b"Payload"));
    Ok(())
}

#[test]
fn reports_table_and_stream_position_for_corrupt_row_and_live_count() -> TestResult {
    let original = fixture()?;
    let table = definition(&original, b"Items")?;
    let mut budget = budget();
    let mut database = open(&original, &mut budget)?;
    let mut cursor = database.rows(&table, &mut budget)?;
    let row = cursor.next_row()?.ok_or("missing row")?;
    let locator = row.storage_locator();
    let row_length = row.raw_bytes().len();
    // EXP-0060: slot zero ends at the page boundary; byte zero is column count.
    assert_eq!(locator.slot(), 0);
    let mut changed = original.clone();
    changed[page_start(locator.page()) + PAGE_BYTES - row_length] = 0;
    assert!(
        matches!(validate(&changed), Err(ValidationError::Table { table, source:
        TableValidationError::Rows { completed_rows: 0, source: RowError::ColumnCountMismatch { .. } }
    }) if table.name().raw_bytes() == b"Items")
    );
    // EXP-0073: declared live row count in the definition header.
    let mut changed = original;
    let count = page_start(table.root()) + 12;
    changed[count..count + 4].copy_from_slice(&4_u32.to_le_bytes());
    assert!(matches!(
        validate(&changed),
        Err(ValidationError::Table {
            source: TableValidationError::RowCount {
                declared: 4,
                actual: 3
            },
            ..
        })
    ));
    Ok(())
}

#[test]
fn reports_index_ordinal_for_corrupt_node_and_leaf_row_reference() -> TestResult {
    let original = fixture()?;
    let table = definition(&original, b"Items")?;
    let root = page_start(table.physical_indexes()[0].root());
    // EXP-0062: node owner at byte 4; uncompressed Long leaf records occupy
    // nine bytes from byte 248, ending in the referenced row slot.
    let mut changed = original.clone();
    changed[root + 4..root + 8].fill(0);
    assert!(
        matches!(validate(&changed), Err(ValidationError::Table { table, source:
        TableValidationError::Index { index: 0, source: IndexTreeError::UnexpectedOwner { .. } }
    }) if table.name().raw_bytes() == b"Items")
    );
    let mut changed = original;
    changed[root + 248 + 8] = 255;
    assert!(matches!(
        validate(&changed),
        Err(ValidationError::Table {
            source: TableValidationError::Index {
                index: 0,
                source: IndexTreeError::RowDirectory {
                    source: RowDirectoryError::MissingRow { row: 255, .. },
                    ..
                }
            },
            ..
        })
    ));
    Ok(())
}

#[test]
fn uninterpreted_key_bytes_are_reported_without_claiming_semantic_validity() -> TestResult {
    let mut bytes = fixture()?;
    let table = definition(&bytes, b"Items")?;
    let root = page_start(table.physical_indexes()[0].root());
    // EXP-0062: byte 248 starts the first uncompressed key. Unknown key
    // markers remain lossless in the existing index reader.
    bytes[root + 248] = 0x12;
    let report = validate(&bytes)?;
    assert_eq!(report.index_entries, 3);
    assert_eq!(report.uninterpreted_index_entries, 1);
    Ok(())
}

#[test]
fn reports_source_row_and_column_for_reachable_long_value_failure() -> TestResult {
    let original = fixture()?;
    let table = definition(&original, b"Notes")?;
    let mut budget = budget();
    let mut database = open(&original, &mut budget)?;
    let mut cursor = database.rows(&table, &mut budget)?;
    cursor.next_row()?.ok_or("missing inline row")?;
    let mut row = cursor.next_row()?.ok_or("missing external row")?;
    let source_row = row.locator();
    let column = table.columns()[1].ordinal();
    let value = row
        .value(column, TextCodePage::Windows1252)?
        .ok_or("missing value")?;
    let ValueKind::LongValue(LongValue::External(reference)) = value.kind() else {
        panic!("expected external")
    };
    let target = reference.target();
    // EXP-0061: external value pages carry the four-byte LVAL owner marker.
    let mut changed = original.clone();
    changed[page_start(target.page()) + 4] ^= 1;
    assert!(
        matches!(validate(&changed), Err(ValidationError::Table { table, source:
        TableValidationError::LongValue { row, column: actual, source: LongValueError::InvalidOwner { .. } }
    }) if table.name().raw_bytes() == b"Notes" && row == source_row && actual == column)
    );
    Ok(())
}

#[test]
fn catalog_references_and_one_cumulative_budget_are_enforced() -> TestResult {
    let original = fixture()?;
    let table = definition(&original, b"Items")?;
    let mut changed = original.clone();
    // EXP-0058: catalog table identifiers must point to table-definition pages.
    changed[page_start(table.root())] = 4;
    assert!(matches!(
        validate(&changed),
        Err(ValidationError::Catalog(
            CatalogError::UnexpectedTableDefinitionReference { .. }
        ))
    ));
    let mut measured = budget();
    let mut database = open(&original, &mut measured)?;
    database.validate(TextCodePage::Windows1252, &mut measured)?;
    let mut limited = ResourceBudget::new(
        ResourceLimits::default().with_max_total_work_units(measured.total_work_units() - 1),
    );
    let mut database = open(&original, &mut limited)?;
    let error = database
        .validate(TextCodePage::Windows1252, &mut limited)
        .unwrap_err();
    let mut source: &dyn std::error::Error = &error;
    while let Some(next) = source.source() {
        source = next;
    }
    assert!(matches!(
        source.downcast_ref::<Error>(),
        Some(Error::ResourceLimitExceeded { .. })
    ));
    assert!(limited.total_work_units() < measured.total_work_units());
    Ok(())
}
