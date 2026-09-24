//! Structural checks of a written creation candidate against its request,
//! run before publication. A passing check is a publication prerequisite, not
//! compatibility evidence.
use crate::{
    CatalogError, CatalogObjectClass, ColumnStorageClass, ColumnStorageKind, ColumnType,
    DatabaseOpenError, DatabaseReader, IndexDefinitionKind, PageNumber, ResourceBudget, RowError,
    RowValue, TableDefinitionError, TableSpec,
    create::{
        TableRows,
        composer::{
            ComposeError, InitialAutoIncrement, InitialScalarIndex, encode_initial_row,
            initial_payload_start, initial_row_layout,
        },
        page_append_plan::PlannedPage,
    },
};

use std::io;
use std::path::Path;

/// A structural difference between the written candidate and the request,
/// found when the candidate was reopened before publication.
#[derive(Debug, thiserror::Error)]
pub(crate) enum ImageCheckError {
    /// Reading a candidate page or charging comparison work failed.
    #[error("candidate page comparison failed: {0}")]
    Read(#[source] crate::Error),
    /// The candidate index tree could not be read.
    #[error("candidate index scan failed: {0}")]
    Index(#[source] crate::IndexTreeError),
    /// The candidate fails the catalogued allocation and user-table validator.
    #[error("candidate validation failed: {0}")]
    Validation(#[source] Box<crate::ValidationError>),
    /// A candidate allocation inventory is malformed.
    #[error("candidate allocation state failed: {0}")]
    AllocationState(#[source] Box<crate::WriteError>),
    /// A candidate long-value field could not be decoded.
    #[error("candidate value failed: {0}")]
    Value(#[source] crate::ValueError),
    /// A candidate external payload could not be streamed.
    #[error("candidate long value failed: {0}")]
    LongValue(#[source] crate::LongValueError),
    /// The candidate rows could not be read.
    #[error("candidate row scan failed: {0}")]
    Rows(#[source] RowError),
    /// Requested rows could not be encoded for comparison.
    #[error("candidate row comparison failed: {0}")]
    RowEncoding(#[source] ComposeError),
    /// The candidate could not be opened as a Jet 3 database.
    #[error("candidate did not open: {0}")]
    Open(#[source] DatabaseOpenError),
    /// The candidate's catalog could not be read.
    #[error("candidate catalog failed: {0}")]
    Catalog(#[source] CatalogError),
    /// The created table's definition could not be read.
    #[error("candidate table definition failed: {0}")]
    Definition(#[source] TableDefinitionError),
    /// The candidate decodes but does not describe the requested tables.
    #[error("candidate does not match the request: {detail}")]
    Mismatch {
        /// Which structure differed.
        detail: &'static str,
    },
}

#[cfg(test)]
pub(super) fn check_initial_rows(
    candidate: &Path,
    table: &TableSpec<'_>,
    rows: &[&[RowValue<'_>]],
    budget: &mut ResourceBudget,
) -> Result<(), ImageCheckError> {
    check_initial_tables(
        candidate,
        std::slice::from_ref(table),
        &[TableRows {
            table: *table,
            rows,
        }],
        budget,
    )
}

pub(super) fn check_initial_tables(
    candidate: &Path,
    tables: &[TableSpec<'_>],
    requests: &[TableRows<'_>],
    budget: &mut ResourceBudget,
) -> Result<(), ImageCheckError> {
    let mut database = DatabaseReader::open(candidate, budget).map_err(ImageCheckError::Open)?;
    let roots = image_table_roots(&mut database, tables, budget)?;
    for (position, (request, root)) in requests.iter().zip(roots).enumerate() {
        let root = root.ok_or(ImageCheckError::Mismatch {
            detail: "catalog row",
        })?;
        check_initial_table_rows(&mut database, request, root, position == 0, budget)?;
    }
    Ok(())
}

fn check_initial_table_rows(
    database: &mut DatabaseReader<crate::FileSource>,
    request: &TableRows<'_>,
    root: PageNumber,
    first_create: bool,
    budget: &mut ResourceBudget,
) -> Result<(), ImageCheckError> {
    let next_payload = initial_payload_start(&request.table, root, first_create, budget)
        .map_err(ImageCheckError::RowEncoding)?;
    check_initial_table_rows_from(database, request, root, next_payload, budget)
}

pub(super) fn check_initial_table_rows_from(
    database: &mut DatabaseReader<crate::FileSource>,
    request: &TableRows<'_>,
    root: PageNumber,
    mut next_payload: u64,
    budget: &mut ResourceBudget,
) -> Result<(), ImageCheckError> {
    let table = &request.table;
    let rows = request.rows;
    let layout = initial_row_layout(table, budget).map_err(ImageCheckError::RowEncoding)?;
    let definition = database
        .table_definition(root, budget)
        .map_err(ImageCheckError::Definition)?;
    let mut generated =
        InitialAutoIncrement::new(table, rows, budget).map_err(ImageCheckError::RowEncoding)?;
    if let Some(generated) = generated {
        let mut raw = [0_u8; crate::PAGE_BYTES];
        database
            .read_raw_page(root, &mut raw, budget)
            .map_err(|error| ImageCheckError::RowEncoding(ComposeError::Encoding(error)))?;
        if !generated.matches(&raw) {
            return Err(ImageCheckError::Mismatch {
                detail: "initial AutoIncrement state",
            });
        }
    }
    let mut expected_indexes = InitialScalarIndex::for_table(table, rows.len(), budget)
        .map_err(ImageCheckError::RowEncoding)?;
    let long_columns = table
        .columns
        .iter()
        .filter(|column| column.column_type().is_long_value())
        .count();
    budget
        .charge_allocation(crate::ByteCount::new(
            (long_columns * size_of::<(crate::LongValueReference, &[u8])>()) as u64,
        ))
        .map_err(ImageCheckError::Read)?;
    let mut external = Vec::new();
    external.try_reserve_exact(long_columns).map_err(|_| {
        ImageCheckError::Read(crate::Error::Io {
            operation: "reserve initial long-value verification",
            kind: io::ErrorKind::OutOfMemory,
        })
    })?;
    let mut encoded = [0_u8; crate::PAGE_BYTES];
    let mut cursor = database
        .rows(&definition, budget)
        .map_err(ImageCheckError::Rows)?;
    for (ordinal, row) in rows.iter().enumerate() {
        let mut lowered = [RowValue::Null; u8::MAX as usize];
        let row = if let Some(generated) = generated.as_mut() {
            generated
                .lower(row, ordinal, &mut lowered, cursor.owned.budget_mut())
                .map_err(ImageCheckError::RowEncoding)?;
            &lowered[..row.len()]
        } else {
            *row
        };
        let length = encode_initial_row(
            &layout,
            table.columns,
            row,
            ordinal,
            &mut next_payload,
            &mut encoded,
            cursor.owned.budget_mut(),
        )
        .map_err(ImageCheckError::RowEncoding)?
        .get() as usize;
        let mut actual =
            cursor
                .next_row()
                .map_err(ImageCheckError::Rows)?
                .ok_or(ImageCheckError::Mismatch {
                    detail: "initial row count",
                })?;
        if actual.raw_bytes() != &encoded[..length] {
            return Err(ImageCheckError::Mismatch {
                detail: "initial row value",
            });
        }
        let locator = actual.locator();
        external.clear();
        for (column, value) in row.iter().enumerate() {
            let payload = match value {
                RowValue::Memo(payload) | RowValue::LongBinary(payload) => *payload,
                _ => continue,
            };
            let decoded = actual
                .value(
                    crate::ColumnOrdinal::new(column as u16),
                    crate::TextCodePage::Windows1252,
                )
                .map_err(ImageCheckError::Value)?;
            if let Some(decoded) = decoded
                && let crate::ValueKind::LongValue(crate::LongValue::External(reference)) =
                    decoded.kind()
            {
                external.push((*reference, payload));
            }
        }
        for (reference, expected) in &external {
            cursor
                .owned
                .budget_mut()
                .charge_work_units(expected.len() as u64)
                .map_err(|error| ImageCheckError::RowEncoding(ComposeError::Encoding(error)))?;
            let mut stream = cursor
                .long_value(*reference)
                .map_err(ImageCheckError::LongValue)?;
            let mut remaining = *expected;
            while let Some(chunk) = stream.next_chunk().map_err(ImageCheckError::LongValue)? {
                let bytes = chunk.value().raw_bytes();
                remaining = remaining
                    .strip_prefix(bytes)
                    .ok_or(ImageCheckError::Mismatch {
                        detail: "initial long-value payload",
                    })?;
            }
            if !remaining.is_empty() {
                return Err(ImageCheckError::Mismatch {
                    detail: "initial long-value length",
                });
            }
        }
        for index in &mut expected_indexes {
            index
                .push(row, locator, cursor.owned.budget_mut())
                .map_err(ImageCheckError::RowEncoding)?;
        }
    }
    if cursor.next_row().map_err(ImageCheckError::Rows)?.is_some() {
        return Err(ImageCheckError::Mismatch {
            detail: "initial row count",
        });
    }
    drop(cursor);
    for (ordinal, mut expected) in expected_indexes.into_iter().enumerate() {
        expected
            .sort(budget)
            .map_err(ImageCheckError::RowEncoding)?;
        let physical =
            definition
                .physical_indexes()
                .get(ordinal)
                .ok_or(ImageCheckError::Mismatch {
                    detail: "initial index count",
                })?;
        if physical.distinct_key_count() != expected.distinct_count() {
            return Err(ImageCheckError::Mismatch {
                detail: "initial index distinct count",
            });
        }
        let actual = database
            .index_tree(&definition, ordinal as u16, budget)
            .map_err(ImageCheckError::Index)?;
        check_initial_index_map(database, physical.usage_map(), &actual, budget)?;
        if !expected
            .matches(&actual, budget)
            .map_err(ImageCheckError::RowEncoding)?
        {
            return Err(ImageCheckError::Mismatch {
                detail: "initial index entries",
            });
        }
    }
    Ok(())
}

fn check_initial_index_map(
    database: &mut DatabaseReader<crate::FileSource>,
    location: crate::IndexUsageMapReference,
    tree: &crate::IndexTree,
    budget: &mut ResourceBudget,
) -> Result<(), ImageCheckError> {
    let map = crate::alloc::mutation_map::MapBits::load(
        database,
        crate::MapRowLocator::new(location.page(), location.row()),
        budget,
    )
    .map_err(|error| ImageCheckError::AllocationState(Box::new(error)))?;
    let pages = map
        .existing_pages(database.geometry().page_count(), false, budget)
        .map_err(|error| ImageCheckError::AllocationState(Box::new(error)))?;
    let count = pages.len();
    for page in pages {
        budget
            .charge_work_units(tree.nodes().len() as u64)
            .map_err(ImageCheckError::Read)?;
        if !tree.nodes().iter().any(|node| node.page() == page) {
            return Err(ImageCheckError::Mismatch {
                detail: "initial index map pages",
            });
        }
    }
    if count != tree.nodes().len() {
        return Err(ImageCheckError::Mismatch {
            detail: "initial index map pages",
        });
    }
    Ok(())
}

/// Checks the complete written image when long-value column maps or column
/// properties are present, including maps whose membership row traversal
/// does not otherwise visit.
pub(super) fn check_long_value_written_pages(
    candidate: &Path,
    tables: &[TableSpec<'_>],
    pages: &[PlannedPage],
    budget: &mut ResourceBudget,
) -> Result<(), ImageCheckError> {
    if !tables.iter().any(|table| {
        table.columns.iter().any(|column| {
            column.column_type().is_long_value()
                || crate::properties::column::has_zero_length_property(column.physical_type())
                || column.required()
        })
    }) {
        return Ok(());
    }
    let mut database = DatabaseReader::open(candidate, budget).map_err(ImageCheckError::Open)?;
    let mut bytes = [0_u8; crate::PAGE_BYTES];
    for page in pages {
        database
            .read_raw_page(page.number(), &mut bytes, budget)
            .map_err(ImageCheckError::Read)?;
        budget
            .charge_work_units(crate::PAGE_BYTES as u64)
            .map_err(ImageCheckError::Read)?;
        if &bytes != page.image().as_bytes() {
            return Err(ImageCheckError::Mismatch {
                detail: "long-value written page",
            });
        }
    }
    Ok(())
}

pub(super) fn check_image(
    candidate: &Path,
    tables: &[TableSpec<'_>],
    page_count: u64,
    budget: &mut ResourceBudget,
) -> Result<(), ImageCheckError> {
    let mismatch = |detail: &'static str| ImageCheckError::Mismatch { detail };
    let mut database = DatabaseReader::open(candidate, budget).map_err(ImageCheckError::Open)?;
    if database.geometry().page_count() != page_count {
        return Err(mismatch("page count"));
    }
    let roots = image_table_roots(&mut database, tables, budget)?;
    for (spec, root) in tables.iter().zip(roots) {
        let root = root.ok_or(mismatch("catalog row"))?;
        check_table(&mut database, spec, root, budget)?;
    }
    Ok(())
}

pub(super) fn image_table_roots(
    database: &mut DatabaseReader<crate::FileSource>,
    tables: &[TableSpec<'_>],
    budget: &mut ResourceBudget,
) -> Result<Vec<Option<PageNumber>>, ImageCheckError> {
    let mismatch = |detail: &'static str| ImageCheckError::Mismatch { detail };
    let mut roots: Vec<Option<PageNumber>> = vec![None; tables.len()];
    let mut user_rows = 0_usize;
    {
        let mut catalog = database.catalog(budget).map_err(ImageCheckError::Catalog)?;
        while let Some(record) = catalog.next_record().map_err(ImageCheckError::Catalog)? {
            if record.class() != CatalogObjectClass::User {
                continue;
            }
            user_rows += 1;
            let position = tables
                .iter()
                .position(|table| table.name == record.name().raw_bytes())
                .ok_or(mismatch("catalog row"))?;
            if roots[position].is_some() {
                return Err(mismatch("catalog row"));
            }
            roots[position] = Some(record.table_definition().ok_or(mismatch("catalog row"))?);
        }
    }
    if user_rows != tables.len() {
        return Err(mismatch("catalog row"));
    }
    Ok(roots)
}

/// Checks one table's definition at `root` against `spec`.
fn check_table(
    database: &mut DatabaseReader<crate::FileSource>,
    spec: &TableSpec<'_>,
    root: PageNumber,
    budget: &mut ResourceBudget,
) -> Result<(), ImageCheckError> {
    let mismatch = |detail: &'static str| ImageCheckError::Mismatch { detail };
    let definition = database
        .table_definition(root, budget)
        .map_err(ImageCheckError::Definition)?;
    check_columns(&definition, spec)?;
    if definition.physical_indexes().len() != spec.indexes.len()
        || definition.indexes().len() != spec.indexes.len()
    {
        return Err(mismatch("index count"));
    }
    for logical in definition.indexes() {
        let physical = usize::from(logical.physical_index());
        let requested = spec
            .indexes
            .get(physical)
            .ok_or(mismatch("index reference"))?;
        if logical.name().raw_bytes() != requested.name {
            return Err(mismatch("index name"));
        }
        let physical_definition = &definition.physical_indexes()[physical];
        let logical_kind = if requested.kind.is_primary() {
            IndexDefinitionKind::Primary
        } else {
            IndexDefinitionKind::Ordinary
        };
        let physical_flags = requested.kind.flags().raw();
        if logical.kind() != logical_kind || physical_definition.raw_flags() != physical_flags {
            return Err(mismatch("index kind"));
        }
        let fields = physical_definition.fields();
        if fields.len() != requested.fields.len()
            || fields.iter().zip(requested.fields).any(|(field, wanted)| {
                wanted.column.resolve(spec.columns) != Some(field.column().get())
                    || field.direction() != wanted.direction
            })
        {
            return Err(mismatch("index fields"));
        }
    }
    Ok(())
}

pub(super) fn check_columns(
    definition: &crate::TableDefinition,
    spec: &TableSpec<'_>,
) -> Result<(), ImageCheckError> {
    let mismatch = |detail| ImageCheckError::Mismatch { detail };
    if definition.columns().len() != spec.columns.len() {
        return Err(mismatch("column count"));
    }
    for (column, requested) in definition.columns().iter().zip(spec.columns) {
        let storage_matches = matches!(
            (column.storage(), requested.storage()),
            (ColumnStorageClass::Fixed { .. }, ColumnStorageKind::Fixed)
                | (
                    ColumnStorageClass::Variable { .. },
                    ColumnStorageKind::Variable
                )
        );
        if column.name().raw_bytes() != requested.name()
            || column.physical_type() != requested.physical_type()
            || column.size() != requested.size()
            || column.auto_increment() != (requested.column_type() == ColumnType::AutoIncrement)
            || !storage_matches
        {
            return Err(mismatch("column"));
        }
    }
    Ok(())
}
