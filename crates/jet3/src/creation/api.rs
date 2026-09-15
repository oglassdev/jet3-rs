//! Creation of a fresh Jet 3 database holding user tables.
//!
//! [`create_database`] composes the complete image in memory, writes every
//! page in physical order to a private file beside the destination, reopens
//! that file through the ordinary reader to check its structure against the
//! request, and only then publishes it. A destination that already exists is
//! left untouched, and a destination that was absent stays absent after any
//! pre-publication failure.
//!
//! The structural reopen is a publication prerequisite, not compatibility
//! evidence. DAO observations in `docs/PROVENANCE.md` cover exact candidates;
//! they do not establish arbitrary schemas, values, or general compatibility.
//! Local and hosted differential results govern the support matrix.

use std::error::Error as StdError;
use std::fmt;
use std::fs::File;
use std::io::{self, Write};
use std::path::Path;

use crate::atomic::atomic_create;
use crate::creation::composer::{
    ComposeError, InitialAutoIncrement, InitialLongIndex, compose_database,
    compose_database_with_table_rows, encode_initial_row, initial_payload_start,
    initial_row_layout,
};
use crate::page_append_plan::PlannedPage;
use crate::{
    CatalogError, CatalogObjectClass, ColumnStorageClass, ColumnStorageKind, ColumnType,
    DatabaseOpenError, DatabaseReader, IndexDefinitionKind, IndexKind, PageNumber, PublishError,
    ResourceBudget, RowError, RowValue, TableDefinitionError, TableSpec,
};

/// A table schema and its initial rows, in caller-specified order.
#[derive(Debug, Clone, Copy)]
pub struct TableRows<'a> {
    /// The schema to create.
    pub table: TableSpec<'a>,
    /// Initial values, one slice per row in schema column order.
    pub rows: &'a [&'a [RowValue<'a>]],
}

/// Structured failure of [`create_database`].
#[derive(Debug)]
pub enum CreateDatabaseError {
    /// The tables could not be composed into a database image; nothing was
    /// written.
    Compose(ComposeError),
    /// The composed image could not be written, checked, or published.
    Publish(PublishError),
}

impl fmt::Display for CreateDatabaseError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Compose(source) => write!(formatter, "database composition failed: {source}"),
            Self::Publish(source) => write!(formatter, "database publication failed: {source}"),
        }
    }
}

impl StdError for CreateDatabaseError {
    fn source(&self) -> Option<&(dyn StdError + 'static)> {
        match self {
            Self::Compose(source) => Some(source),
            Self::Publish(source) => Some(source),
        }
    }
}

/// A structural difference between the written candidate and the request,
/// found when the candidate was reopened before publication.
#[derive(Debug)]
pub enum CandidateCheckError {
    /// Reading a candidate page or charging comparison work failed.
    Read(crate::Error),
    /// The candidate index tree could not be read.
    Index(crate::IndexTreeError),
    /// A candidate index usage-map record could not be located.
    UsageMap(crate::UsageMapError),
    /// A candidate index allocation map could not be traversed.
    Allocation(crate::AllocationMapError),
    /// A candidate allocation inventory is malformed.
    AllocationState(crate::UpdateError),
    /// A candidate long-value field could not be decoded.
    Value(crate::ValueError),
    /// A candidate external payload could not be streamed.
    LongValue(crate::LongValueError),
    /// The candidate rows could not be read.
    Rows(RowError),
    /// Requested rows could not be encoded for comparison.
    RowEncoding(ComposeError),
    /// The candidate could not be opened as a Jet 3 database.
    Open(DatabaseOpenError),
    /// The candidate's catalog could not be read.
    Catalog(CatalogError),
    /// The created table's definition could not be read.
    Definition(TableDefinitionError),
    /// The candidate decodes but does not describe the requested tables.
    Mismatch {
        /// Which structure differed.
        detail: &'static str,
    },
}

impl fmt::Display for CandidateCheckError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Read(source) => write!(formatter, "candidate page comparison failed: {source}"),
            Self::Index(source) => write!(formatter, "candidate index scan failed: {source}"),
            Self::UsageMap(source) => write!(formatter, "candidate usage map failed: {source}"),
            Self::Allocation(source) => {
                write!(formatter, "candidate allocation map failed: {source}")
            }
            Self::AllocationState(source) => {
                write!(formatter, "candidate allocation state failed: {source}")
            }
            Self::Value(source) => write!(formatter, "candidate value failed: {source}"),
            Self::LongValue(source) => write!(formatter, "candidate long value failed: {source}"),
            Self::Rows(source) => write!(formatter, "candidate row scan failed: {source}"),
            Self::RowEncoding(source) => {
                write!(formatter, "candidate row comparison failed: {source}")
            }
            Self::Open(source) => write!(formatter, "candidate did not open: {source}"),
            Self::Catalog(source) => write!(formatter, "candidate catalog failed: {source}"),
            Self::Definition(source) => {
                write!(formatter, "candidate table definition failed: {source}")
            }
            Self::Mismatch { detail } => {
                write!(formatter, "candidate does not match the request: {detail}")
            }
        }
    }
}

impl StdError for CandidateCheckError {
    fn source(&self) -> Option<&(dyn StdError + 'static)> {
        match self {
            Self::Read(source) => Some(source),
            Self::Index(source) => Some(source),
            Self::UsageMap(source) => Some(source),
            Self::Allocation(source) => Some(source),
            Self::AllocationState(source) => Some(source),
            Self::Value(source) => Some(source),
            Self::LongValue(source) => Some(source),
            Self::Rows(source) => Some(source),
            Self::RowEncoding(source) => Some(source),
            Self::Open(source) => Some(source),
            Self::Catalog(source) => Some(source),
            Self::Definition(source) => Some(source),
            Self::Mismatch { .. } => None,
        }
    }
}

/// Creates the database at `path` holding `tables`, created in order, each
/// empty.
///
/// After successful composition, `path` must not exist; an existing entry
/// fails with an `AlreadyExists` I/O error at
/// [`crate::PublishStage::PrivateCopyCreation`] and is left unchanged.
/// Composition, the page writes, and the structural reopen are all charged to
/// `budget`.
///
/// Unsupported layouts fail with [`CreateDatabaseError::Compose`] before
/// anything is written: creation-counter overflow, exhausted map-reference capacity,
/// two tables whose names differ only by ASCII case, more than 32 indexes
/// on a table, or a
/// name byte above `0x7E`. EXP-0249 bounds table/column names to 64 bytes and
/// index names to 63; it establishes the 16-bit creation counter carry.
/// Table definitions use linked pages within the same
/// allocation and resource limits, including indexed and later tables.
pub fn create_database(
    path: impl AsRef<Path>,
    tables: &[TableSpec<'_>],
    budget: &mut ResourceBudget,
) -> Result<(), CreateDatabaseError> {
    let pages = compose_database(tables, budget)
        .map_err(CreateDatabaseError::Compose)?
        .into_pages();
    let page_count = pages.len() as u64;
    budget
        .charge_work_units(page_count.saturating_mul(crate::PAGE_BYTES as u64))
        .map_err(|error| CreateDatabaseError::Compose(ComposeError::Encoding(error)))?;
    atomic_create(
        path,
        |file| write_pages(file, &pages),
        |candidate| {
            check_long_value_written_pages(candidate, tables, &pages, budget)?;
            check_candidate(candidate, tables, page_count, budget)
        },
    )
    .map_err(CreateDatabaseError::Publish)
}

/// Creates one table containing initial rows in caller order.
///
/// Rows are packed in caller order into data pages within the allocation-map
/// and resource limits. Each row must fit one page; table definitions may span linked
/// pages. Pages with a slot and room for an all-null row are marked available;
/// this construction
/// policy has not been established as DAO's allocation policy.
/// Each table accepts up to 32 indexes. Each
/// index has one to ten supported scalar columns (including a generated AutoIncrement
/// column), with each field ascending or descending. Multiple populated indexes
/// use separate roots/maps and independent trees.
/// Uncompressed branch/leaf trees grow within the allocation-map and
/// resource limits. Unique indexes reject repeated fully present keys while
/// allowing repeated null-bearing keys. The index null policy includes keys,
/// omits all-null keys, or requires every component; primary indexes require
/// every component. Supported components are Boolean, Byte, Integer, Long,
/// Currency, Single, Double, DateTime, Binary, fixed/variable Text and GUID. Nonfinite
/// floating values and Boolean nulls are refused. Empty Binary
/// saves as null. Text keys use the English-US/CP1252 collation from EXP-0248;
/// case, ligature expansion and trailing ASCII spaces affect key equality without
/// changing stored row bytes. EXP-0243/0245 establish Date fractions, floating
/// negative zero and Binary keys; EXP-0248 establishes GUID display-order keys.
/// Keys longer than 255 bytes retain a prefix plus a checksum; distinct values
/// colliding after shortening are duplicate keys, matching DAO uniqueness.
/// One AutoIncrement column accepts [`RowValue::AutoIncrement`] or an explicit Long.
/// Generation starts at 1 independently per table and wraps through signed Long
/// boundaries. Each inserted row advances the persisted allocation state before
/// applying an explicit ID using the unsigned comparison established by EXP-0237.
/// Null IDs are refused. EXP-0239 compares explicit and wrapping initial IDs.
/// Memo and LongBinary columns accept typed payloads or null alongside
/// scalar indexes and generated IDs; the long-value columns themselves cannot
/// be indexed. Every long-value column has its own owned/available map pair,
/// in column order after the table and index maps across packed map pages.
/// Its physical capacity bounds the column count. EXP-0236 compares multiple
/// long-value columns with numeric indexes and native continuations.
/// Raw `RowValue::LongValue` headers are refused.
/// [`crate::ColumnSpec::with_allow_zero_length`] enables present-empty Text or
/// Memo independently per column, including later and indexed tables. Empty
/// OLE payloads are stored as null. Fixed Text retains its exact-width contract.
/// EXP-0266 supplies the named property and empty-value construction facts;
/// candidate compatibility is bounded by the differential outcomes recorded there.
/// The candidate policy stores up to 32 bytes inline, up to 2,036 on one LVAL
/// page, and larger payloads in 2,032-byte chained fragments, one per page.
/// These are construction choices, not established DAO allocation thresholds.
/// Values use the database-code-page and physical representations of [`RowValue`].
/// The existing-destination and atomic publication guarantees of
/// [`create_database`] apply. Composition and the structural row comparison
/// are charged to `budget`. Unsupported schemas and rows fail before writing.
///
/// DAO observations cover only the exact candidates recorded in the provenance
/// ledger. Construction bounds alone do not establish general compatibility or
/// hosted write-differential coverage.
pub fn create_database_with_rows(
    path: impl AsRef<Path>,
    table: &TableSpec<'_>,
    rows: &[&[RowValue<'_>]],
    budget: &mut ResourceBudget,
) -> Result<(), CreateDatabaseError> {
    create_database_with_table_rows(
        path,
        &[TableRows {
            table: *table,
            rows,
        }],
        budget,
    )
}

/// Creates tables and their initial rows in one atomic publication.
///
/// Each table retains the bounds described by [`create_database_with_rows`]
/// and [`create_database`]. Tables, their LVAL pages and their row pages are
/// placed sequentially in input order within the map-reference and resource limits.
/// An empty request creates an empty database. Relationships are not included.
/// Every table and row is checked before publication; existing destinations
/// remain untouched. This candidate construction has no general DAO guarantee.
pub fn create_database_with_table_rows(
    path: impl AsRef<Path>,
    requests: &[TableRows<'_>],
    budget: &mut ResourceBudget,
) -> Result<(), CreateDatabaseError> {
    let pages = compose_database_with_table_rows(requests, budget)
        .map_err(CreateDatabaseError::Compose)?
        .into_pages();
    budget
        .charge_allocation(crate::ByteCount::new(
            (requests.len() * std::mem::size_of::<TableSpec<'_>>()) as u64,
        ))
        .map_err(|error| CreateDatabaseError::Compose(error.into()))?;
    let mut tables = Vec::new();
    tables.try_reserve_exact(requests.len()).map_err(|_| {
        CreateDatabaseError::Compose(ComposeError::Encoding(crate::Error::Io {
            operation: "reserve initial table schemas",
            kind: io::ErrorKind::OutOfMemory,
        }))
    })?;
    tables.extend(requests.iter().map(|request| request.table));
    let page_count = pages.len() as u64;
    budget
        .charge_work_units(page_count.saturating_mul(crate::PAGE_BYTES as u64))
        .map_err(|error| CreateDatabaseError::Compose(ComposeError::Encoding(error)))?;
    atomic_create(
        path,
        |file| write_pages(file, &pages),
        |candidate| {
            check_long_value_written_pages(candidate, &tables, &pages, budget)?;
            check_candidate(candidate, &tables, page_count, budget)?;
            check_initial_tables(candidate, &tables, requests, budget)
        },
    )
    .map_err(CreateDatabaseError::Publish)
}

#[cfg(test)]
fn check_initial_rows(
    candidate: &Path,
    table: &TableSpec<'_>,
    rows: &[&[RowValue<'_>]],
    budget: &mut ResourceBudget,
) -> Result<(), CandidateCheckError> {
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

fn check_initial_tables(
    candidate: &Path,
    tables: &[TableSpec<'_>],
    requests: &[TableRows<'_>],
    budget: &mut ResourceBudget,
) -> Result<(), CandidateCheckError> {
    let mut database =
        DatabaseReader::open(candidate, budget).map_err(CandidateCheckError::Open)?;
    let roots = candidate_table_roots(&mut database, tables, budget)?;
    for (position, (request, root)) in requests.iter().zip(roots).enumerate() {
        let root = root.ok_or(CandidateCheckError::Mismatch {
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
) -> Result<(), CandidateCheckError> {
    let table = &request.table;
    let rows = request.rows;
    let layout = initial_row_layout(table, budget).map_err(CandidateCheckError::RowEncoding)?;
    let definition = database
        .table_definition(root, budget)
        .map_err(CandidateCheckError::Definition)?;
    let mut generated =
        InitialAutoIncrement::new(table, rows, budget).map_err(CandidateCheckError::RowEncoding)?;
    if let Some(generated) = generated {
        let mut raw = [0_u8; crate::PAGE_BYTES];
        database
            .read_raw_page(root, &mut raw, budget)
            .map_err(|error| CandidateCheckError::RowEncoding(ComposeError::Encoding(error)))?;
        if !generated.matches(&raw) {
            return Err(CandidateCheckError::Mismatch {
                detail: "initial AutoIncrement state",
            });
        }
    }
    let mut expected_indexes = InitialLongIndex::for_table(table, rows.len(), budget)
        .map_err(CandidateCheckError::RowEncoding)?;
    let mut next_payload = initial_payload_start(table, root, first_create)
        .map_err(CandidateCheckError::RowEncoding)?;
    let long_columns = table
        .columns
        .iter()
        .filter(|column| column.column_type().is_long_value())
        .count();
    budget
        .charge_allocation(crate::ByteCount::new(
            (long_columns * size_of::<(crate::LongValueReference, &[u8])>()) as u64,
        ))
        .map_err(CandidateCheckError::Read)?;
    let mut external = Vec::new();
    external.try_reserve_exact(long_columns).map_err(|_| {
        CandidateCheckError::Read(crate::Error::Io {
            operation: "reserve initial long-value verification",
            kind: io::ErrorKind::OutOfMemory,
        })
    })?;
    let mut encoded = [0_u8; crate::PAGE_BYTES];
    let mut cursor = database
        .rows(&definition, budget)
        .map_err(CandidateCheckError::Rows)?;
    for (ordinal, row) in rows.iter().enumerate() {
        let mut lowered = [RowValue::Null; u8::MAX as usize];
        let row = if let Some(generated) = generated.as_mut() {
            generated
                .lower(row, ordinal, &mut lowered, cursor.owned.budget_mut())
                .map_err(CandidateCheckError::RowEncoding)?;
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
        .map_err(CandidateCheckError::RowEncoding)?
        .get() as usize;
        let mut actual = cursor
            .next_row()
            .map_err(CandidateCheckError::Rows)?
            .ok_or(CandidateCheckError::Mismatch {
                detail: "initial row count",
            })?;
        if actual.raw_bytes() != &encoded[..length] {
            return Err(CandidateCheckError::Mismatch {
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
                .map_err(CandidateCheckError::Value)?;
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
                .map_err(|error| CandidateCheckError::RowEncoding(ComposeError::Encoding(error)))?;
            let mut stream = cursor
                .long_value(*reference)
                .map_err(CandidateCheckError::LongValue)?;
            let mut remaining = *expected;
            while let Some(chunk) = stream
                .next_chunk()
                .map_err(CandidateCheckError::LongValue)?
            {
                let bytes = match chunk.value() {
                    crate::LongValueChunkValue::Text(text) => text.raw_bytes(),
                    crate::LongValueChunkValue::Binary(bytes) => bytes,
                };
                remaining = remaining
                    .strip_prefix(bytes)
                    .ok_or(CandidateCheckError::Mismatch {
                        detail: "initial long-value payload",
                    })?;
            }
            if !remaining.is_empty() {
                return Err(CandidateCheckError::Mismatch {
                    detail: "initial long-value length",
                });
            }
        }
        for index in &mut expected_indexes {
            index
                .push(row, locator, cursor.owned.budget_mut())
                .map_err(CandidateCheckError::RowEncoding)?;
        }
    }
    if cursor
        .next_row()
        .map_err(CandidateCheckError::Rows)?
        .is_some()
    {
        return Err(CandidateCheckError::Mismatch {
            detail: "initial row count",
        });
    }
    drop(cursor);
    for (ordinal, mut expected) in expected_indexes.into_iter().enumerate() {
        expected
            .sort(budget)
            .map_err(CandidateCheckError::RowEncoding)?;
        let physical =
            definition
                .physical_indexes()
                .get(ordinal)
                .ok_or(CandidateCheckError::Mismatch {
                    detail: "initial index count",
                })?;
        if physical.distinct_key_count() != expected.distinct_count() {
            return Err(CandidateCheckError::Mismatch {
                detail: "initial index distinct count",
            });
        }
        let actual = database
            .index_tree(&definition, ordinal as u16, budget)
            .map_err(CandidateCheckError::Index)?;
        check_initial_index_map(database, physical.usage_map(), &actual, budget)?;
        if !expected
            .matches(&actual, budget)
            .map_err(CandidateCheckError::RowEncoding)?
        {
            return Err(CandidateCheckError::Mismatch {
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
) -> Result<(), CandidateCheckError> {
    let map = crate::mutation_map::MapBits::load(
        database,
        crate::MapRowLocator::new(location.page(), location.row()),
        budget,
    )
    .map_err(CandidateCheckError::AllocationState)?;
    let pages = map
        .existing_pages(database.geometry().page_count(), false, budget)
        .map_err(CandidateCheckError::AllocationState)?;
    let count = pages.len();
    for page in pages {
        budget
            .charge_work_units(tree.nodes().len() as u64)
            .map_err(CandidateCheckError::Read)?;
        if !tree.nodes().iter().any(|node| node.page() == page) {
            return Err(CandidateCheckError::Mismatch {
                detail: "initial index map pages",
            });
        }
    }
    if count != tree.nodes().len() {
        return Err(CandidateCheckError::Mismatch {
            detail: "initial index map pages",
        });
    }
    Ok(())
}

/// Writes every page in physical order and sets the exact final length.
fn write_pages(file: &mut File, pages: &[PlannedPage]) -> Result<(), io::Error> {
    for (slot, page) in pages.iter().enumerate() {
        if page.number().get() != slot as u64 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "planned pages are not in physical order",
            ));
        }
        file.write_all(page.image().as_bytes())?;
    }
    file.set_len(pages.len() as u64 * crate::PAGE_BYTES as u64)?;
    file.flush()
}

/// Checks the complete written image when long-value column maps or column
/// properties are present, including maps whose membership row traversal
/// does not otherwise visit.
fn check_long_value_written_pages(
    candidate: &Path,
    tables: &[TableSpec<'_>],
    pages: &[PlannedPage],
    budget: &mut ResourceBudget,
) -> Result<(), CandidateCheckError> {
    if !tables.iter().any(|table| {
        table
            .columns
            .iter()
            .any(|column| column.column_type().is_long_value() || column.allow_zero_length())
    }) {
        return Ok(());
    }
    let mut database =
        DatabaseReader::open(candidate, budget).map_err(CandidateCheckError::Open)?;
    let mut bytes = [0_u8; crate::PAGE_BYTES];
    for page in pages {
        database
            .read_raw_page(page.number(), &mut bytes, budget)
            .map_err(CandidateCheckError::Read)?;
        budget
            .charge_work_units(crate::PAGE_BYTES as u64)
            .map_err(CandidateCheckError::Read)?;
        if &bytes != page.image().as_bytes() {
            return Err(CandidateCheckError::Mismatch {
                detail: "long-value written page",
            });
        }
    }
    Ok(())
}

fn check_candidate(
    candidate: &Path,
    tables: &[TableSpec<'_>],
    page_count: u64,
    budget: &mut ResourceBudget,
) -> Result<(), CandidateCheckError> {
    let mismatch = |detail: &'static str| CandidateCheckError::Mismatch { detail };
    let mut database =
        DatabaseReader::open(candidate, budget).map_err(CandidateCheckError::Open)?;
    if database.geometry().page_count() != page_count {
        return Err(mismatch("page count"));
    }
    let roots = candidate_table_roots(&mut database, tables, budget)?;
    for (spec, root) in tables.iter().zip(roots) {
        let root = root.ok_or(mismatch("catalog row"))?;
        check_table(&mut database, spec, root, budget)?;
    }
    Ok(())
}

fn candidate_table_roots(
    database: &mut DatabaseReader<crate::FileSource>,
    tables: &[TableSpec<'_>],
    budget: &mut ResourceBudget,
) -> Result<Vec<Option<PageNumber>>, CandidateCheckError> {
    let mismatch = |detail: &'static str| CandidateCheckError::Mismatch { detail };
    let mut roots: Vec<Option<PageNumber>> = vec![None; tables.len()];
    let mut user_rows = 0_usize;
    {
        let mut catalog = database
            .catalog(budget)
            .map_err(CandidateCheckError::Catalog)?;
        while let Some(record) = catalog
            .next_record()
            .map_err(CandidateCheckError::Catalog)?
        {
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
) -> Result<(), CandidateCheckError> {
    let mismatch = |detail: &'static str| CandidateCheckError::Mismatch { detail };
    let definition = database
        .table_definition(root, budget)
        .map_err(CandidateCheckError::Definition)?;
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

#[cfg(all(test, any(unix, windows)))]
#[path = "tests.rs"]
mod tests;

#[path = "api_relationship.rs"]
mod relationship;
pub use relationship::{create_database_with_relationship, create_database_with_relationship_rows};
