//! Preservation-aware field updates using `EXP-0059` schema, `SRC-0020`/
//! `EXP-0060` row spans, the fixed scalar encodings from `EXP-0061`, and
//! `EXP-0073`/`EXP-0114` relationship catalog endpoints.

use std::convert::Infallible;
use std::error::Error as StdError;
use std::fmt;
use std::path::Path;

use crate::{
    CatalogObjectClass, ColumnOrdinal, ColumnPhysicalType, DatabaseReader, FileSource, PAGE_BYTES,
    PageImage, PageOffset, PublishStage, ResourceBudget, RowLocator, RowValue, TableDefinitionKind,
    row::directory::RowDirectory,
};

/// One existing field to replace. Obtain the ordinal and locator from the reader.
#[derive(Debug, Clone, Copy)]
pub struct FieldUpdate<'a> {
    /// Exact database-encoded user table name.
    pub table: &'a [u8],
    /// Logical row locator from that table's row cursor.
    pub row: RowLocator,
    /// Column ordinal from that table's definition.
    pub column: ColumnOrdinal,
    /// Replacement value matching the column, including null or raw Memo/OLE payloads.
    pub value: RowValue<'a>,
}

/// A rejected request, malformed source, exhausted resource policy, or publication failure.
#[derive(Debug)]
#[non_exhaustive]
pub enum UpdateError {
    /// A requested table schema is invalid.
    Schema(crate::TableSchemaPlanError),
    /// A table definition could not be encoded.
    DefinitionEncoding(crate::TableDefinitionWriteError),
    /// A schema edit could not encode a data page.
    PageImage(crate::PageImageError),
    /// A schema edit could not encode an allocation map.
    MapEncoding(crate::UsageMapWriteError),
    /// Named column properties or their storage are malformed.
    ColumnProperties(crate::ColumnPropertyError),
    /// The named user table, row, or column was not found.
    NotFound(&'static str),
    /// The request needs an unimplemented update capability.
    Unsupported(&'static str),
    /// Source or private bytes did not match the planned update.
    Mismatch(&'static str),
    /// A non-null child key would have no matching parent after the change.
    RelationshipConstraint {
        /// Definition page of the referenced parent table.
        parent: crate::PageNumber,
        /// Definition page of the referencing child table.
        child: crate::PageNumber,
        /// Child value requiring a matching parent.
        value: i32,
    },
    /// A non-Long scalar or composite child key would have no matching parent after the change.
    ScalarRelationshipConstraint {
        /// Definition page of the referenced parent table.
        parent: crate::PageNumber,
        /// Definition page of the referencing child table.
        child: crate::PageNumber,
    },
    /// A null parent key cannot be changed or removed while null child keys exist.
    NullRelationshipConstraint {
        /// Definition page of the referenced parent table.
        parent: crate::PageNumber,
        /// Definition page of the referencing child table.
        child: crate::PageNumber,
    },
    /// The database uses a sort order outside the six observed single-byte
    /// locales (EXP-0309). Reading remains supported.
    UnsupportedSortOrder {
        /// Raw page-zero sort-order marker.
        raw: [u8; 4],
    },
    /// The table or column stores a ValidationRule; Jet expressions are not evaluated.
    ValidationRule {
        /// The column whose rule applies, or `None` for the table rule.
        column: Option<ColumnOrdinal>,
    },
    /// Resource policy or raw input failure.
    Resource(crate::Error),
    /// File operation failed.
    Io(std::io::Error),
    /// Database header failure.
    Open(crate::DatabaseOpenError),
    /// Catalog failure.
    Catalog(crate::CatalogError),
    /// Table definition failure.
    Definition(crate::TableDefinitionError),
    /// Row traversal failure.
    Rows(crate::RowError),
    /// Row directory failure.
    Directory(crate::RowDirectoryError),
    /// Replacement value or fixed layout failed checked row encoding.
    Encoding(crate::RowWriteError),
    /// Existing indexed numeric value failed decoding.
    Value(crate::ValueError),
    /// Existing external long-value storage failed validation.
    LongValue(crate::LongValueError),
    /// Available map row is malformed.
    UsageMap(crate::UsageMapError),
    /// Allocation bitmap is malformed or exhausted its budget.
    Allocation(crate::AllocationMapError),
    /// Index metadata, tree, or row reference is malformed.
    Index(crate::IndexTreeError),
    /// Atomic publication failed; its stage indicates whether publication occurred.
    Publish(crate::PublishError),
}

impl fmt::Display for UpdateError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "field update failed: {self:?}")
    }
}
impl StdError for UpdateError {
    fn source(&self) -> Option<&(dyn StdError + 'static)> {
        match self {
            Self::Schema(source) => Some(source),
            Self::DefinitionEncoding(source) => Some(source),
            Self::PageImage(source) => Some(source),
            Self::MapEncoding(source) => Some(source),
            Self::ColumnProperties(source) => Some(source),
            Self::Resource(source) => Some(source),
            Self::Io(source) => Some(source),
            Self::Open(source) => Some(source),
            Self::Catalog(source) => Some(source),
            Self::Definition(source) => Some(source),
            Self::Rows(source) => Some(source),
            Self::Directory(source) => Some(source),
            Self::Encoding(source) => Some(source),
            Self::Value(source) => Some(source),
            Self::LongValue(source) => Some(source),
            Self::UsageMap(source) => Some(source),
            Self::Allocation(source) => Some(source),
            Self::Publish(source) => Some(source),
            Self::Index(source) => Some(source),
            Self::NotFound(_)
            | Self::Unsupported(_)
            | Self::Mismatch(_)
            | Self::RelationshipConstraint { .. }
            | Self::ScalarRelationshipConstraint { .. }
            | Self::NullRelationshipConstraint { .. }
            | Self::ValidationRule { .. }
            | Self::UnsupportedSortOrder { .. } => None,
        }
    }
}

macro_rules! conversion {
    ($source:ty, $variant:ident) => {
        impl From<$source> for UpdateError {
            fn from(source: $source) -> Self {
                Self::$variant(source)
            }
        }
    };
}
conversion!(crate::Error, Resource);
conversion!(crate::TableSchemaPlanError, Schema);
conversion!(crate::TableDefinitionWriteError, DefinitionEncoding);
conversion!(crate::PageImageError, PageImage);
conversion!(crate::UsageMapWriteError, MapEncoding);
conversion!(crate::ColumnPropertyError, ColumnProperties);
conversion!(std::io::Error, Io);
conversion!(crate::DatabaseOpenError, Open);
conversion!(crate::CatalogError, Catalog);
conversion!(crate::TableDefinitionError, Definition);
conversion!(crate::RowError, Rows);
conversion!(crate::RowDirectoryError, Directory);
conversion!(crate::RowWriteError, Encoding);
conversion!(crate::ValueError, Value);
conversion!(crate::LongValueError, LongValue);
conversion!(crate::PublishError, Publish);
conversion!(crate::IndexTreeError, Index);

/// Replaces one field in a user table while retaining its logical row locator.
///
/// Fixed fields are patched in place; other edits may compact or relocate the
/// row. Indexes, long-value storage, allocation maps and enforced
/// relationships (including cascades) are updated and checked, and every
/// affected row publishes in one atomic replacement; all other bytes are
/// preserved. Callers must exclude other writers for the whole operation.
/// `budget` bounds planning, copying and verification.
///
/// # Errors
///
/// Returns [`UpdateError`] when the file or request is outside the supported
/// scope, a key or relationship constraint refuses the change, the table
/// stores a validation rule (rules are not evaluated), or `budget` is
/// exhausted; the original file is then unchanged. Publication failures
/// identify their stage. See `docs/plans/V1_SCOPE.md` for the supported scope.
pub fn update_field(
    path: impl AsRef<Path>,
    request: FieldUpdate<'_>,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    update_with_hook(path.as_ref(), request, budget, |_| Ok::<(), Infallible>(()))
}

pub(super) fn update_with_hook<H, HE>(
    path: &Path,
    request: FieldUpdate<'_>,
    budget: &mut ResourceBudget,
    hook: H,
) -> Result<(), UpdateError>
where
    H: FnMut(PublishStage) -> Result<(), HE>,
    HE: StdError + Send + Sync + 'static,
{
    let change =
        crate::relationship::mutation::Change::Field(request.row, request.column, request.value);
    super::driver::apply(path, request.table, change, budget, hook).map(drop)
}

pub(crate) fn plan(
    database: &mut DatabaseReader<crate::FileSource>,
    definition: &crate::TableDefinition,
    request: FieldUpdate<'_>,
    check_relationships: bool,
    budget: &mut ResourceBudget,
) -> Result<crate::write::page_edits::PageEdits, UpdateError> {
    let graph = crate::row::mutation_graph::RowGraph::load(
        database,
        definition,
        Some(request.row),
        budget,
    )?;
    if graph.selected.len() > 2 {
        return Err(UpdateError::Unsupported(
            "mutation of multi-hop overflow chain",
        ));
    }
    let storage = *graph.selected.last().ok_or(UpdateError::NotFound("row"))?;
    let column = definition
        .columns()
        .get(usize::from(request.column.get()))
        .ok_or(UpdateError::NotFound("column"))?;
    if column.auto_increment() {
        return Err(UpdateError::Unsupported("AutoIncrement column"));
    }
    let rewrite = if matches!(request.value, RowValue::Null)
        || column.physical_type() == ColumnPhysicalType::Boolean
        || matches!(column.storage(), crate::ColumnStorageClass::Variable { .. })
    {
        true
    } else {
        let mut rows = database.rows(definition, budget)?;
        let mut rewrite = false;
        while let Some(row) = rows.next_row()? {
            if row.locator() == request.row {
                rewrite = row.present_fixed_field_range(request.column).is_none();
                break;
            }
        }
        rewrite
    };
    if rewrite {
        return crate::write::field_update::plan(
            database,
            definition,
            graph,
            request,
            check_relationships,
            budget,
        );
    }
    let mut replacement = [0; u8::MAX as usize];
    let width = crate::row::writer::encode_present_fixed_field(
        request.column.get(),
        column.into(),
        request.value,
        &mut replacement,
        budget,
    )?;
    if check_relationships {
        crate::relationship::mutation::check(
            database,
            definition,
            request.table,
            crate::relationship::mutation::Change::Field(
                request.row,
                request.column,
                request.value,
            ),
            budget,
        )?;
    }
    let index_change =
        crate::index::mutation::plan_field_update(database, definition, request, budget)?;
    let mut original_page = [0; PAGE_BYTES];
    database.read_raw_page(storage.page(), &mut original_page, budget)?;
    let directory =
        RowDirectory::validate(storage.page(), definition.root(), &original_page, budget)?;
    let entry = directory.entry(&original_page, storage.slot())?;
    let mut before = [0; u8::MAX as usize];
    let relative = {
        let mut rows = database.rows(definition, budget)?;
        let mut found = None;
        while let Some(row) = rows.next_row()? {
            if row.locator() != request.row {
                continue;
            }
            if row.storage_locator() != storage {
                return Err(UpdateError::Mismatch("overflow storage locator"));
            }
            let range = row
                .present_fixed_field_range(request.column)
                .ok_or(UpdateError::Unsupported("null or variable field"))?;
            let bytes = row
                .field(request.column)
                .and_then(|field| field.raw_bytes())
                .filter(|bytes| bytes.len() == width && range.len() == width)
                .ok_or(UpdateError::Mismatch("fixed field width"))?;
            before[..width].copy_from_slice(bytes);
            found = Some(range);
            break;
        }
        found.ok_or(UpdateError::NotFound("row"))?
    };
    let row_range = entry.range();
    if relative.end > row_range.len() {
        return Err(UpdateError::Mismatch("field outside row"));
    }
    let start = row_range
        .start
        .checked_add(relative.start)
        .ok_or(UpdateError::Mismatch("field offset"))?;
    let end = start
        .checked_add(width)
        .ok_or(UpdateError::Mismatch("field end"))?;
    if original_page.get(start..end) != Some(&before[..width]) {
        return Err(UpdateError::Mismatch("source changed during planning"));
    }
    let mut patched = PageImage::from_bytes(original_page);
    patched.write_at(PageOffset::new(start as u64), &replacement[..width], budget)?;
    let field_change = crate::write::update_pages::PageChange {
        page: storage.page(),
        before: &original_page,
        after: patched.as_bytes(),
    };
    let mut edits = crate::write::page_edits::PageEdits::new(database.geometry().page_count());
    edits.replace(field_change, budget)?;
    if let Some(index) = index_change {
        index.stage(database, definition, &mut edits, budget)?;
    }
    Ok(edits)
}

#[cfg(all(test, any(unix, windows)))]
pub(crate) fn writable_table(
    database: &mut DatabaseReader<FileSource>,
    table: &[u8],
    budget: &mut ResourceBudget,
) -> Result<crate::TableDefinition, UpdateError> {
    guarded_table(database, table, false, budget)
}

pub(crate) fn indexed_writable_table(
    database: &mut DatabaseReader<FileSource>,
    table: &[u8],
    budget: &mut ResourceBudget,
) -> Result<crate::TableDefinition, UpdateError> {
    guarded_table(database, table, true, budget)
}

pub(super) fn guarded_table(
    database: &mut DatabaseReader<FileSource>,
    table: &[u8],
    allow_indexes: bool,
    budget: &mut ResourceBudget,
) -> Result<crate::TableDefinition, UpdateError> {
    let mut root = None;
    {
        let mut catalog = database.catalog(budget)?;
        while let Some(record) = catalog.next_record()? {
            if record.class() == CatalogObjectClass::User && record.name().raw_bytes() == table {
                if root.is_some() {
                    return Err(UpdateError::Mismatch("ambiguous table name"));
                }
                root = record.table_definition();
            }
        }
    }
    let definition =
        database.table_definition(root.ok_or(UpdateError::NotFound("table"))?, budget)?;
    if definition.kind() != TableDefinitionKind::User {
        return Err(UpdateError::Unsupported("non-user table"));
    }
    if !allow_indexes
        && (!definition.indexes().is_empty() || !definition.physical_indexes().is_empty())
    {
        return Err(UpdateError::Unsupported(
            "table has indexes or relationships",
        ));
    }
    Ok(definition)
}

/// Refuses writes to databases whose sort order Rust cannot maintain (EXP-0299).
pub(crate) fn require_writable_sort_order<S: crate::ReadAt>(
    database: &DatabaseReader<S>,
) -> Result<(), UpdateError> {
    match database.header().sort_order() {
        order if order.code_page().is_some() => Ok(()),
        order => Err(UpdateError::UnsupportedSortOrder {
            raw: order.raw_marker(),
        }),
    }
}
