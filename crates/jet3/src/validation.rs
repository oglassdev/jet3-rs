//! Read-only composition of the catalog (`EXP-0058`), definition (`EXP-0059`),
//! row (`EXP-0060`), long-value (`EXP-0061`) and index (`EXP-0062`) readers.
//!
//! Success covers catalogued allocation roles and user/system table contents.
//! Non-table object contents are skipped.
//! Unreferenced file pages, allocation slack, unsupported relationship forms,
//! and application compatibility are outside this check. Index references must
//! name distinct live logical rows. Supported scalar schemas additionally check
//! key values, null policies, uniqueness, complete row coverage and branch bounds.
//! Unsupported key schemas remain explicitly uninterpreted; their framing and
//! live-row membership are still checked. No index key-count prefix is compared
//! with the live row count: `EXP-0219` permits retained counts after deletion.
//! Catalogued allocation roles must be disjoint from incompatible owners and
//! globally free pages. Row storage and every live payload fragment must
//! be uniquely reachable through the owning table or column. Enforced, non-cascading
//! single ascending scalar relationships check reciprocal records and non-null child
//! keys against their parent. Other forms are counted as uninterpreted. Complete
//! endpoint inventory is checked only when every central record is interpreted.

use std::fmt;

use crate::resource::reserve;

use crate::{
    CatalogError, CatalogObjectClass, CatalogRecord, ColumnOrdinal, DatabaseReader, Error,
    IndexTreeError, InlineLongValue, LongValue, LongValueChunkValue, LongValueError,
    LongValueReference, ReadAt, ResourceBudget, RowError, RowLocator, TableDefinition,
    TableDefinitionError, TableDefinitionKind, TextCodePage, ValueError, ValueKind,
};

/// Counts produced only after every in-scope reader finishes successfully.
/// These are logical objects, not unique physical pages or whole-file coverage.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
#[non_exhaustive]
pub struct ValidationReport {
    /// Active catalog records decoded, including skipped objects.
    pub catalog_objects: u64,
    /// User table definitions and their reachable data checked.
    pub user_tables: u64,
    /// System table definitions and their reachable data checked.
    pub system_tables: u64,
    /// System objects without a table definition whose contents were not checked.
    pub skipped_system_objects: u64,
    /// Non-system, non-table objects whose contents were not checked.
    pub skipped_other_objects: u64,
    /// Live logical user and system rows decoded, with declared counts checked.
    pub rows: u64,
    /// User and system field values decoded, including nulls.
    pub values: u64,
    /// Physical user and system indexes traversed, counted once per definition.
    pub indexes: u64,
    /// Leaf entries traversed across those indexes.
    pub index_entries: u64,
    /// Physical indexes whose complete keys match the live row values.
    pub indexes_with_verified_keys: u64,
    /// Physical indexes with a key schema outside the admitted scalar encodings.
    pub uninterpreted_indexes: u64,
    /// Entries in indexes whose key schema remains uninterpreted.
    pub uninterpreted_index_entries: u64,
    /// Central relationship catalog rows decoded, including uninterpreted forms.
    pub relationship_catalog_rows: u64,
    /// Enforced single-column scalar relationships whose metadata and key inclusion agree.
    pub relationships_with_verified_keys: u64,
    /// Central rows whose relationship form or key schema is not interpreted.
    pub uninterpreted_relationship_rows: u64,
    /// Every logical endpoint exactly matches the interpreted central catalog.
    pub relationship_inventory_checked: bool,
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
    /// Catalog property values could not be collected.
    ColumnProperties(crate::ColumnPropertyError),
    /// A table check failed; the original catalog identity is retained.
    Table {
        /// Catalog name, identifier and table-definition reference.
        table: CatalogRecord,
        /// Specific failed check.
        source: TableValidationError,
    },
    /// Catalogued allocation maps or shared page ownership are inconsistent.
    Storage(StorageValidationError),
    /// Relationship metadata, endpoint resolution or key inclusion failed.
    Relationships(RelationshipValidationError),
    /// Resource policy rejected validation bookkeeping.
    Resource(Error),
}

/// Context for a failure inside one user or system table.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum TableValidationError {
    /// Named column properties or their payload storage could not be read.
    ColumnProperties(crate::ColumnPropertyError),
    /// A stored null violates the column's Required property.
    RequiredValue {
        /// Logical row containing the null.
        row: RowLocator,
        /// Required field ordinal.
        column: ColumnOrdinal,
    },
    /// Definition or its typed references could not be decoded.
    Definition(TableDefinitionError),
    /// The definition kind disagrees with its catalog record's class.
    DefinitionKind {
        /// Kind required by the catalog classification.
        expected: TableDefinitionKind,
        /// Kind decoded from the definition header.
        actual: TableDefinitionKind,
    },
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
    /// Leaf membership, supported key semantics or branch bounds disagree with the table.
    IndexContents {
        /// Zero-based physical index ordinal.
        index: u16,
        /// Failed consistency check.
        detail: &'static str,
    },
    /// Physical row or Memo/OLE storage is shared, missing, or unreferenced.
    Storage(StorageValidationError),
    /// Resource policy rejected table bookkeeping.
    Resource(Error),
}

impl fmt::Display for ValidationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Catalog(source) => write!(f, "validation catalog: {source}"),
            Self::ColumnProperties(source) => write!(f, "validation properties: {source}"),
            Self::Table { table, source } => {
                f.write_str("validation table ")?;
                if let Some(name) = table.name().decoded_ascii() {
                    write!(f, "{name:?}")?;
                } else {
                    write!(f, "{:?}", table.name().raw_bytes())?;
                }
                write!(f, " (id {}): {source}", table.id().get())
            }
            Self::Storage(source) => write!(f, "validation allocations: {source}"),
            Self::Relationships(source) => write!(f, "validation relationships: {source}"),
            Self::Resource(source) => write!(f, "validation resources: {source}"),
        }
    }
}

impl std::error::Error for ValidationError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        Some(match self {
            Self::Catalog(source) => source,
            Self::ColumnProperties(source) => source,
            Self::Table { source, .. } => source,
            Self::Storage(source) => source,
            Self::Relationships(source) => source,
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
            Self::ColumnProperties(source) => Some(source),
            Self::Definition(source) => Some(source),
            Self::Rows { source, .. } => Some(source),
            Self::Value { source, .. } => Some(source),
            Self::LongValue { source, .. } => Some(source),
            Self::Index { source, .. } => Some(source),
            Self::Storage(source) => Some(source),
            Self::Resource(source) => Some(source),
            Self::DefinitionKind { .. }
            | Self::RowCount { .. }
            | Self::IndexContents { .. }
            | Self::RequiredValue { .. } => None,
        }
    }
}

impl<S: ReadAt> DatabaseReader<S> {
    /// Checks the catalog and reachable user/system table data without writing.
    ///
    /// Pass the same budget used to open the reader to bound the entire
    /// operation. All readers, pending records and traversal scratch share it.
    /// Values are streamed; relationship checks retain both endpoint definitions
    /// and sorted parent keys. Exclude concurrent writes to the source.
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
        let mut roots = Vec::new();
        let catalog_root;
        {
            let mut catalog = self.catalog(budget).map_err(ValidationError::Catalog)?;
            catalog_root = catalog.root();
            while let Some(record) = catalog.next_record().map_err(ValidationError::Catalog)? {
                add(&mut report.catalog_objects, 1).map_err(ValidationError::Resource)?;
                if let Some(root) = record.table_definition() {
                    reserve(&mut roots, 1, catalog.budget_mut())
                        .map_err(ValidationError::Resource)?;
                    roots.push(root);
                }
                if let Some(root) = record.table_definition() {
                    reserve(&mut tables, 1, catalog.budget_mut())
                        .map_err(ValidationError::Resource)?;
                    tables.push((record, root));
                } else if record.class() == CatalogObjectClass::System {
                    add(&mut report.skipped_system_objects, 1)
                        .map_err(ValidationError::Resource)?;
                } else {
                    add(&mut report.skipped_other_objects, 1).map_err(ValidationError::Resource)?;
                }
            }
        }
        let mut properties =
            crate::column_property_values::Properties::load(self, catalog_root, &roots, budget)
                .map_err(ValidationError::ColumnProperties)?;
        for (table, root) in tables {
            let expected = match table.class() {
                CatalogObjectClass::User => TableDefinitionKind::User,
                CatalogObjectClass::System => TableDefinitionKind::System,
            };
            let result = self
                .table_definition(root, budget)
                .map_err(TableValidationError::Definition)
                .and_then(|definition| {
                    if definition.kind() != expected {
                        return Err(TableValidationError::DefinitionKind {
                            expected,
                            actual: definition.kind(),
                        });
                    }
                    let empty_payload_column = if table.class() == CatalogObjectClass::System
                        && table.name().raw_bytes() == b"MSysObjects"
                    {
                        // EXP-0091: the catalog can retain an empty owned LvProp page.
                        definition
                            .columns()
                            .iter()
                            .find(|column| column.name().raw_bytes() == b"LvProp")
                            .map(|column| column.ordinal())
                    } else {
                        None
                    };
                    let options = if expected == TableDefinitionKind::User {
                        properties
                            .options(self, &definition, budget)
                            .map_err(TableValidationError::ColumnProperties)?
                    } else {
                        [crate::column_property_reader::ColumnOptions::default(); 255]
                    };
                    validate_table(
                        self,
                        &definition,
                        code_page,
                        budget,
                        &mut report,
                        empty_payload_column,
                        &options,
                    )
                });
            if let Err(source) = result {
                return Err(ValidationError::Table { table, source });
            }
            let count = match table.class() {
                CatalogObjectClass::User => &mut report.user_tables,
                CatalogObjectClass::System => &mut report.system_tables,
            };
            add(count, 1).map_err(ValidationError::Resource)?;
        }
        let mut allocation =
            storage::AllocationState::new(self, budget).map_err(ValidationError::Storage)?;
        for root in roots {
            let definition = self.table_definition(root, budget).map_err(|source| {
                ValidationError::Storage(StorageValidationError::Definition { root, source })
            })?;
            allocation
                .table(self, &definition, budget)
                .map_err(ValidationError::Storage)?;
        }
        let relationships = crate::relationship_catalog::validate(self, budget)
            .map_err(|source| ValidationError::Relationships(source.into()))?;
        report.relationship_catalog_rows = relationships.catalog_rows;
        report.relationships_with_verified_keys = relationships.verified;
        report.uninterpreted_relationship_rows = relationships.uninterpreted;
        report.relationship_inventory_checked = relationships.inventory_checked;
        Ok(report)
    }
}

fn validate_table<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    definition: &TableDefinition,
    code_page: TextCodePage,
    budget: &mut ResourceBudget,
    report: &mut ValidationReport,
    empty_payload_column: Option<ColumnOrdinal>,
    options: &[crate::column_property_reader::ColumnOptions; 255],
) -> Result<(), TableValidationError> {
    let mut payloads = storage::PayloadInventory::new(database, definition, budget)
        .map_err(TableValidationError::Storage)?;
    let mut rows = validate_rows(
        database,
        definition,
        code_page,
        budget,
        report,
        &mut payloads,
        options,
    )?;
    payloads
        .finish(database, empty_payload_column, budget)
        .map_err(TableValidationError::Storage)?;
    budget
        .charge_work_units(
            (rows.len() as u64).saturating_mul(u64::from(rows.len().max(1).ilog2()) + 1),
        )
        .map_err(TableValidationError::Resource)?;
    rows.sort_unstable_by_key(|row| index::key(*row));
    for (index, physical) in (0_u16..).zip(definition.physical_indexes()) {
        let tree = database
            .index_tree(definition, index, budget)
            .map_err(|source| TableValidationError::Index { index, source })?;
        index::validate(database, definition, index, &tree, &rows, budget, report)?;
        storage::index(database, physical, &tree, budget).map_err(TableValidationError::Storage)?;
        add(&mut report.index_entries, tree.entries().len() as u64)
            .map_err(TableValidationError::Resource)?;
        add(&mut report.indexes, 1).map_err(TableValidationError::Resource)?;
    }
    crate::row_mutation_graph::RowGraph::load(database, definition, None, budget).map_err(
        |source| TableValidationError::Storage(storage::shared(source, definition.maps().owned())),
    )?;
    Ok(())
}

fn validate_rows<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    definition: &TableDefinition,
    code_page: TextCodePage,
    budget: &mut ResourceBudget,
    report: &mut ValidationReport,
    payloads: &mut storage::PayloadInventory,
    options: &[crate::column_property_reader::ColumnOptions; 255],
) -> Result<Vec<RowLocator>, TableValidationError> {
    let mut locators = Vec::new();
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
        for (column, option) in definition.columns().iter().zip(options) {
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
            if option.required && matches!(value.kind(), ValueKind::Null) {
                return Err(TableValidationError::RequiredValue {
                    row: locator,
                    column: ordinal,
                });
            }
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
                let fragment = chunk.locator();
                payloads
                    .reached(fragment, column, stream.budget_mut())
                    .map_err(TableValidationError::Storage)?;
            }
        }
        if !definition.physical_indexes().is_empty() {
            reserve(&mut locators, 1, cursor.owned.budget_mut())
                .map_err(TableValidationError::Resource)?;
            locators.push(locator);
        }
        add(&mut count, 1).map_err(TableValidationError::Resource)?;
    }
    if count != u64::from(definition.row_count()) {
        return Err(TableValidationError::RowCount {
            declared: definition.row_count(),
            actual: count,
        });
    }
    add(&mut report.rows, count).map_err(TableValidationError::Resource)?;
    Ok(locators)
}

fn add(count: &mut u64, amount: u64) -> Result<(), Error> {
    *count = count.checked_add(amount).ok_or(Error::Arithmetic {
        operation: "count validated objects",
    })?;
    Ok(())
}

#[cfg(test)]
#[path = "validation_tests.rs"]
mod tests;

#[path = "validation_index.rs"]
mod index;

#[path = "validation_storage.rs"]
mod storage;
pub use storage::StorageValidationError;

#[path = "validation_relationship_error.rs"]
mod relationship_error;
pub use relationship_error::RelationshipValidationError;
