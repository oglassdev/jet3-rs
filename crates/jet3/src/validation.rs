//! Read-only composition of the catalog (`EXP-0058`), definition (`EXP-0059`),
//! row (`EXP-0060`), long-value (`EXP-0061`) and index (`EXP-0062`) readers.
//!
//! Success covers catalogued user tables only. System records are checked by
//! the catalog reader, but system table contents and other object kinds are
//! skipped. Unreferenced pages, allocation slack, relationship constraints,
//! index key ordering/semantics, index completeness, index-to-row key equality,
//! live-row membership of index references and application compatibility are
//! outside this check. Index key bytes with an
//! unsupported encoding remain uninterpreted; traversal still checks their
//! framing and page/slot references. No index key-count prefix is compared
//! with the live row count: `EXP-0219` permits retained counts after deletion.

use std::{fmt, mem::size_of};

use crate::{
    ByteCount, CatalogError, CatalogObjectClass, CatalogRecord, ColumnOrdinal, DatabaseReader,
    Error, IndexKeyEncoding, IndexTreeError, InlineLongValue, LongValue, LongValueChunkValue,
    LongValueError, LongValueReference, ReadAt, ResourceBudget, RowError, RowLocator,
    TableDefinition, TableDefinitionError, TextCodePage, ValueError, ValueKind,
};

/// Counts produced only after every in-scope reader finishes successfully.
/// These are logical objects, not unique physical pages or whole-file coverage.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct ValidationReport {
    /// Active catalog records decoded, including skipped objects.
    pub catalog_objects: u64,
    /// User table definitions and their reachable data checked.
    pub user_tables: u64,
    /// System objects whose contents were not checked beyond catalog metadata.
    pub skipped_system_objects: u64,
    /// Non-system, non-table objects whose contents were not checked.
    pub skipped_other_objects: u64,
    /// Live logical user rows decoded, with declared table counts checked.
    pub rows: u64,
    /// User field values decoded, including nulls.
    pub values: u64,
    /// Physical user indexes fully traversed, counted once per definition.
    pub indexes: u64,
    /// Leaf entries traversed across those indexes.
    pub index_entries: u64,
    /// Entries whose key encoding the index reader leaves unsupported.
    pub uninterpreted_index_entries: u64,
    /// Non-null inline and external Memo/OLE values checked.
    pub long_values: u64,
    /// Raw payload bytes reached through those values, before text decoding.
    /// Shared storage, if referenced more than once, is counted per reference.
    pub long_value_bytes: u64,
}

/// The first failure in a bounded validation. No successful report is returned
/// on failure; earlier objects may already have consumed the caller's budget.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum ValidationError {
    /// Catalog discovery, records or table references failed.
    Catalog(CatalogError),
    /// A user-table check failed; the original catalog identity is retained.
    Table {
        /// Catalog name, identifier and table-definition reference.
        table: CatalogRecord,
        /// Specific failed check.
        source: TableValidationError,
    },
    /// Resource policy rejected validation bookkeeping.
    Resource(Error),
}

/// Context for a failure inside one user table.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum TableValidationError {
    /// Definition or its typed references could not be decoded.
    Definition(TableDefinitionError),
    /// Owned data pages, row layout or overflow traversal failed.
    Rows {
        /// Number of live rows read before the failing attempt. A physical
        /// locator is unavailable when the reader rejects a directory/layout.
        completed_rows: u64,
        /// Row reader failure; some variants carry physical references.
        source: RowError,
    },
    /// The definition's live count disagrees with the row stream.
    RowCount {
        /// Count declared by the definition.
        declared: u32,
        /// Live logical rows returned by the reader.
        actual: u64,
    },
    /// One field value could not be decoded.
    Value {
        /// Logical row containing the field.
        row: RowLocator,
        /// Field ordinal in the definition.
        column: ColumnOrdinal,
        /// Value decoder failure.
        source: ValueError,
    },
    /// A reachable external Memo/OLE chain failed.
    LongValue {
        /// Logical row containing the reference.
        row: RowLocator,
        /// Field ordinal containing the reference.
        column: ColumnOrdinal,
        /// Long-value stream failure.
        source: LongValueError,
    },
    /// A physical index failed supported traversal checks.
    Index {
        /// Zero-based physical index ordinal, not a logical index ordinal.
        index: u16,
        /// Index reader failure, including page/row references.
        source: IndexTreeError,
    },
    /// Resource policy rejected table bookkeeping.
    Resource(Error),
}

impl fmt::Display for ValidationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Catalog(source) => write!(f, "validation catalog: {source}"),
            Self::Table { table, source } => {
                f.write_str("validation table ")?;
                if let Some(name) = table.name().decoded_ascii() {
                    write!(f, "{name:?}")?;
                } else {
                    write!(f, "{:?}", table.name().raw_bytes())?;
                }
                write!(f, " (id {}): {source}", table.id().get())
            }
            Self::Resource(source) => write!(f, "validation resources: {source}"),
        }
    }
}

impl std::error::Error for ValidationError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        Some(match self {
            Self::Catalog(source) => source,
            Self::Table { source, .. } => source,
            Self::Resource(source) => source,
        })
    }
}

impl fmt::Display for TableValidationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{self:?}")
    }
}

impl std::error::Error for TableValidationError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Definition(source) => Some(source),
            Self::Rows { source, .. } => Some(source),
            Self::Value { source, .. } => Some(source),
            Self::LongValue { source, .. } => Some(source),
            Self::Index { source, .. } => Some(source),
            Self::Resource(source) => Some(source),
            Self::RowCount { .. } => None,
        }
    }
}

impl<S: ReadAt> DatabaseReader<S> {
    /// Checks the catalog and reachable user-table data without writing.
    ///
    /// Pass the same budget used to open the reader to bound the entire
    /// operation. All readers, pending records and traversal scratch share it.
    /// Values are streamed; one table definition and one physical index tree
    /// are retained at a time. Exclude concurrent writes to the source.
    ///
    /// `code_page` controls text decoding, not index collation. See the
    /// [module coverage limits](crate::validation) before interpreting success.
    #[allow(
        clippy::result_large_err,
        reason = "Keep the catalog identity and source inline so budget failures need no allocation."
    )]
    pub fn validate(
        &mut self,
        code_page: TextCodePage,
        budget: &mut ResourceBudget,
    ) -> Result<ValidationReport, ValidationError> {
        let mut report = ValidationReport::default();
        let mut tables = Vec::new();
        {
            let mut catalog = self.catalog(budget).map_err(ValidationError::Catalog)?;
            while let Some(record) = catalog.next_record().map_err(ValidationError::Catalog)? {
                add(&mut report.catalog_objects, 1).map_err(ValidationError::Resource)?;
                if record.class() == CatalogObjectClass::System {
                    add(&mut report.skipped_system_objects, 1)
                        .map_err(ValidationError::Resource)?;
                } else if let Some(root) = record.table_definition() {
                    reserve(&mut tables, 1, catalog.budget_mut())
                        .map_err(ValidationError::Resource)?;
                    tables.push((record, root));
                } else {
                    add(&mut report.skipped_other_objects, 1).map_err(ValidationError::Resource)?;
                }
            }
        }
        for (table, root) in tables {
            let result = self
                .table_definition(root, budget)
                .map_err(TableValidationError::Definition)
                .and_then(|definition| {
                    validate_table(self, &definition, code_page, budget, &mut report)
                });
            if let Err(source) = result {
                return Err(ValidationError::Table { table, source });
            }
            add(&mut report.user_tables, 1).map_err(ValidationError::Resource)?;
        }
        Ok(report)
    }
}

fn validate_table<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    definition: &TableDefinition,
    code_page: TextCodePage,
    budget: &mut ResourceBudget,
    report: &mut ValidationReport,
) -> Result<(), TableValidationError> {
    validate_rows(database, definition, code_page, budget, report)?;
    for (index, _) in (0_u16..).zip(definition.physical_indexes()) {
        let tree = database
            .index_tree(definition, index, budget)
            .map_err(|source| TableValidationError::Index { index, source })?;
        budget
            .charge_items(tree.entries().len() as u64)
            .map_err(TableValidationError::Resource)?;
        for entry in tree.entries() {
            if entry.key().encoding() == IndexKeyEncoding::Unsupported {
                add(&mut report.uninterpreted_index_entries, 1)
                    .map_err(TableValidationError::Resource)?;
            }
        }
        add(&mut report.index_entries, tree.entries().len() as u64)
            .map_err(TableValidationError::Resource)?;
        add(&mut report.indexes, 1).map_err(TableValidationError::Resource)?;
    }
    Ok(())
}

fn validate_rows<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    definition: &TableDefinition,
    code_page: TextCodePage,
    budget: &mut ResourceBudget,
    report: &mut ValidationReport,
) -> Result<(), TableValidationError> {
    let mut pending: Vec<(ColumnOrdinal, LongValueReference)> = Vec::new();
    reserve(&mut pending, definition.long_value_maps().len(), budget)
        .map_err(TableValidationError::Resource)?;
    let mut cursor =
        database
            .rows(definition, budget)
            .map_err(|source| TableValidationError::Rows {
                completed_rows: 0,
                source,
            })?;
    let mut count = 0;
    loop {
        cursor
            .owned
            .budget_mut()
            .charge_items(definition.columns().len() as u64)
            .map_err(TableValidationError::Resource)?;
        let Some(mut row) = cursor
            .next_row()
            .map_err(|source| TableValidationError::Rows {
                completed_rows: count,
                source,
            })?
        else {
            break;
        };
        let locator = row.locator();
        pending.clear();
        for column in definition.columns() {
            let ordinal = column.ordinal();
            let value = row
                .value(ordinal, code_page)
                .map_err(|source| TableValidationError::Value {
                    row: locator,
                    column: ordinal,
                    source,
                })?
                .ok_or(TableValidationError::Resource(Error::Arithmetic {
                    operation: "access defined validation field",
                }))?;
            if let ValueKind::LongValue(value) = value.kind() {
                add(&mut report.long_values, 1).map_err(TableValidationError::Resource)?;
                match value {
                    LongValue::Inline { value, .. } => {
                        let bytes = match value {
                            InlineLongValue::Text(text) => text.raw_bytes().len(),
                            InlineLongValue::Binary(bytes) => bytes.len(),
                        };
                        add(&mut report.long_value_bytes, bytes as u64)
                            .map_err(TableValidationError::Resource)?;
                    }
                    LongValue::External(reference) => pending.push((ordinal, *reference)),
                }
            }
            add(&mut report.values, 1).map_err(TableValidationError::Resource)?;
        }
        for &(column, reference) in &pending {
            let context = |source| TableValidationError::LongValue {
                row: locator,
                column,
                source,
            };
            let mut stream = cursor.long_value(reference).map_err(context)?;
            while let Some(chunk) = stream.next_chunk().map_err(context)? {
                let bytes = match chunk.value() {
                    LongValueChunkValue::Text(text) => text.raw_bytes().len(),
                    LongValueChunkValue::Binary(bytes) => bytes.len(),
                };
                add(&mut report.long_value_bytes, bytes as u64)
                    .map_err(TableValidationError::Resource)?;
            }
        }
        add(&mut count, 1).map_err(TableValidationError::Resource)?;
    }
    if count != u64::from(definition.row_count()) {
        return Err(TableValidationError::RowCount {
            declared: definition.row_count(),
            actual: count,
        });
    }
    add(&mut report.rows, count).map_err(TableValidationError::Resource)
}

fn add(count: &mut u64, amount: u64) -> Result<(), Error> {
    *count = count.checked_add(amount).ok_or(Error::Arithmetic {
        operation: "count validated objects",
    })?;
    Ok(())
}

fn reserve<T>(
    items: &mut Vec<T>,
    additional: usize,
    budget: &mut ResourceBudget,
) -> Result<(), Error> {
    let needed = items
        .len()
        .checked_add(additional)
        .ok_or(Error::Arithmetic {
            operation: "size validation scratch",
        })?;
    if needed <= items.capacity() {
        return Ok(());
    }
    let capacity = needed.max(items.capacity().saturating_mul(2));
    let bytes = (capacity - items.capacity())
        .checked_mul(size_of::<T>())
        .ok_or(Error::Arithmetic {
            operation: "size validation scratch",
        })?;
    budget.charge_allocation(ByteCount::from_usize(bytes)?)?;
    budget.charge_work_units(items.len() as u64)?;
    items
        .try_reserve_exact(capacity - items.len())
        .map_err(|_| Error::Io {
            operation: "reserve validation scratch",
            kind: std::io::ErrorKind::OutOfMemory,
        })
}

#[cfg(test)]
#[path = "validation_tests.rs"]
mod tests;
