//! Preservation-aware field updates using `EXP-0059` schema, `SRC-0020`/
//! `EXP-0060` row spans, the fixed scalar encodings from `EXP-0061`, and
//! `EXP-0073`/`EXP-0114` relationship catalog endpoints.

use std::convert::Infallible;
use std::error::Error as StdError;
use std::fmt;
use std::path::Path;

use crate::row_directory::RowDirectory;
use crate::{
    CatalogObjectClass, ColumnOrdinal, ColumnPhysicalType, DatabaseReader, FileSource, PAGE_BYTES,
    PageImage, PageOffset, PublishStage, ResourceBudget, RowLocator, RowValue, TableDefinitionKind,
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
    /// Replacement value matching the present fixed-width column.
    pub value: RowValue<'a>,
}

/// A rejected request, malformed source, exhausted resource policy, or publication failure.
#[derive(Debug)]
#[non_exhaustive]
pub enum UpdateError {
    /// The named user table, row, or column was not found.
    NotFound(&'static str),
    /// The request needs an unimplemented update capability.
    Unsupported(&'static str),
    /// Source or private bytes did not match the planned update.
    Mismatch(&'static str),
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
            Self::Resource(source) => Some(source),
            Self::Io(source) => Some(source),
            Self::Open(source) => Some(source),
            Self::Catalog(source) => Some(source),
            Self::Definition(source) => Some(source),
            Self::Rows(source) => Some(source),
            Self::Directory(source) => Some(source),
            Self::Encoding(source) => Some(source),
            Self::UsageMap(source) => Some(source),
            Self::Allocation(source) => Some(source),
            Self::Publish(source) => Some(source),
            Self::Index(source) => Some(source),
            Self::NotFound(_) | Self::Unsupported(_) | Self::Mismatch(_) => None,
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
conversion!(std::io::Error, Io);
conversion!(crate::DatabaseOpenError, Open);
conversion!(crate::CatalogError, Catalog);
conversion!(crate::TableDefinitionError, Definition);
conversion!(crate::RowError, Rows);
conversion!(crate::RowDirectoryError, Directory);
conversion!(crate::RowWriteError, Encoding);
conversion!(crate::PublishError, Publish);
conversion!(crate::IndexTreeError, Index);

/// Replaces one present fixed field in a relationship-free user table.
///
/// Indexed tables are supported when the column is absent from every physical
/// index. A key update additionally supports one unique/primary Long index whose
/// entire tree is one uncompressed root leaf with present keys. It preserves
/// row and distinct-key counts, allocation maps, leaf header/bitmap and unused
/// bytes. Other key updates are refused. This bounded key-update construction
/// has not yet been validated by DAO.
///
/// Supports Byte, Integer, Long, Currency, Single, Double, DateTime, GUID and
/// exact-width fixed Text. Null transitions, Boolean presence bits, AutoIncrement,
/// variable fields and hidden/overflow rows remain unsupported. A missing or
/// unreadable relationship catalog and unresolved non-ASCII relationship endpoint
/// names are also refused.
/// Only the requested field and, for a supported key update, the occupied leaf
/// entry area change. Opaque pages and unused space remain unchanged.
/// Locators remain valid only while the source is unchanged: callers must exclude
/// external writers for this entire operation, as required by [`crate::atomic_update`].
/// Publication is Unix-only. Any pre-publication failure preserves the original;
/// a post-publication sync failure is distinguished by the publication error stage.
/// The same budget covers planning, copying, patching and streaming verification.
/// Structural verification is not a DAO compatibility claim.
pub fn update_field(
    path: impl AsRef<Path>,
    request: FieldUpdate<'_>,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    update_with_hook(path.as_ref(), request, budget, |_| Ok::<(), Infallible>(()))
}

fn update_with_hook<H, HE>(
    path: &Path,
    request: FieldUpdate<'_>,
    budget: &mut ResourceBudget,
    hook: H,
) -> Result<(), UpdateError>
where
    H: FnMut(PublishStage) -> Result<(), HE>,
    HE: StdError + Send + Sync + 'static,
{
    if matches!(request.value, RowValue::Null) {
        return Err(UpdateError::Unsupported("null replacement"));
    }
    let mut database = DatabaseReader::open(path, budget)?;
    let definition = guarded_table(&mut database, request.table, true, budget)?;
    let column = definition
        .columns()
        .get(usize::from(request.column.get()))
        .ok_or(UpdateError::NotFound("column"))?;
    if column.auto_increment() || column.physical_type() == ColumnPhysicalType::Boolean {
        return Err(UpdateError::Unsupported("AutoIncrement or Boolean column"));
    }
    let mut replacement = [0; u8::MAX as usize];
    let width = crate::row_writer::encode_present_fixed_field(
        request.column.get(),
        column.into(),
        request.value,
        &mut replacement,
        budget,
    )?;
    let index_change = crate::update_index_key::plan(&mut database, &definition, request, budget)?;
    let mut original_page = [0; PAGE_BYTES];
    database.read_raw_page(request.row.page(), &mut original_page, budget)?;
    let directory = RowDirectory::validate(
        request.row.page(),
        definition.root(),
        &original_page,
        budget,
    )?;
    let entry = directory.entry(&original_page, request.row.slot())?;
    if entry.hidden() || entry.overflow() {
        return Err(UpdateError::Unsupported("hidden or overflow row"));
    }
    let mut before = [0; u8::MAX as usize];
    let relative = {
        let mut rows = database.rows(&definition, budget)?;
        let mut found = None;
        while let Some(row) = rows.next_row()? {
            if row.locator() != request.row {
                continue;
            }
            if row.storage_locator() != request.row {
                return Err(UpdateError::Unsupported("overflow row"));
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
    let field_change = crate::update_pages::PageChange {
        page: request.row.page(),
        before: &original_page,
        after: patched.as_bytes(),
    };
    if let Some(index) = index_change {
        crate::update_pages::publish_changes(
            path,
            database.into_source(),
            &[
                field_change,
                crate::update_pages::PageChange {
                    page: index.page,
                    before: &index.before,
                    after: index.after.as_bytes(),
                },
            ],
            budget,
            hook,
        )
    } else {
        crate::update_pages::publish_changes(
            path,
            database.into_source(),
            &[field_change],
            budget,
            hook,
        )
    }
}

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

fn guarded_table(
    database: &mut DatabaseReader<FileSource>,
    table: &[u8],
    allow_indexes: bool,
    budget: &mut ResourceBudget,
) -> Result<crate::TableDefinition, UpdateError> {
    let mut root = None;
    let mut relationship_root = None;
    {
        let mut catalog = database.catalog(budget)?;
        while let Some(record) = catalog.next_record()? {
            if record.class() == CatalogObjectClass::System
                && record.name().raw_bytes() == b"MSysRelationships"
            {
                if relationship_root.is_some() {
                    return Err(UpdateError::Mismatch("ambiguous relationship catalog"));
                }
                relationship_root = record.table_definition();
            }
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
    if allow_indexes {
        // EXP-0059/0062: the typed decoder validates every logical selector,
        // physical key field and complete logical-to-physical coverage.
        for index in definition.indexes() {
            budget.charge_items(1)?;
            if matches!(index.kind(), crate::IndexDefinitionKind::Relationship(_)) {
                return Err(UpdateError::Unsupported("relationship index"));
            }
        }
    } else if !definition.indexes().is_empty() || !definition.physical_indexes().is_empty() {
        return Err(UpdateError::Unsupported(
            "table has indexes or relationships",
        ));
    }
    reject_catalog_relationships(
        database,
        relationship_root.ok_or(UpdateError::Unsupported("missing relationship catalog"))?,
        table,
        budget,
    )?;
    Ok(definition)
}

// EXP-0073/0114 source these endpoint columns independently of user indexes.
fn reject_catalog_relationships(
    database: &mut DatabaseReader<FileSource>,
    root: crate::PageNumber,
    table: &[u8],
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let definition = database.table_definition(root, budget)?;
    if definition.kind() != TableDefinitionKind::System {
        return Err(UpdateError::Unsupported("relationship catalog kind"));
    }
    let mut endpoints = [None; 2];
    for (position, name) in [b"szObject".as_slice(), b"szReferencedObject".as_slice()]
        .iter()
        .enumerate()
    {
        for column in definition
            .columns()
            .iter()
            .filter(|column| column.name().raw_bytes() == *name)
        {
            if endpoints[position].is_some() || column.physical_type() != ColumnPhysicalType::Text {
                return Err(UpdateError::Unsupported("relationship endpoint schema"));
            }
            endpoints[position] = Some(column.ordinal());
        }
    }
    let [Some(object), Some(referenced)] = endpoints else {
        return Err(UpdateError::Unsupported("missing relationship endpoint"));
    };
    let mut rows = database.rows(&definition, budget)?;
    while let Some(row) = rows.next_row()? {
        for ordinal in [object, referenced] {
            let name = row
                .field(ordinal)
                .and_then(|field| field.raw_bytes())
                .ok_or(UpdateError::Unsupported("null relationship endpoint"))?;
            if name.is_empty() || !name.is_ascii() || !table.is_ascii() {
                return Err(UpdateError::Unsupported(
                    "unresolved relationship endpoint name",
                ));
            }
            if name.eq_ignore_ascii_case(table) {
                return Err(UpdateError::Unsupported(
                    "table appears in relationship catalog",
                ));
            }
        }
    }
    Ok(())
}

#[cfg(all(test, unix))]
#[path = "update_tests.rs"]
mod tests;
