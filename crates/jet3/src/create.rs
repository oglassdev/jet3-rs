//! Creation of a fresh Jet 3 database holding empty user tables.
//!
//! [`create_database`] composes the complete image in memory, writes every
//! page in physical order to a private file beside the destination, reopens
//! that file through the ordinary reader to check its structure against the
//! request, and only then publishes it. A destination that already exists is
//! left untouched, and a destination that was absent stays absent after any
//! pre-publication failure.
//!
//! The structural reopen is a publication prerequisite, not compatibility
//! evidence. Only a recorded DAO differential can establish that Microsoft
//! Access or DAO consume a created database; none has been run for schemas
//! other than the exact `EXP-0091`, `EXP-0107`, and `EXP-0110` constructions.
//! `EXP-0110` observed DAO read one composed four-table image built from the
//! `EXP-0087` later-create pattern; it does not establish other table counts,
//! orders, names, or schemas.

use std::error::Error as StdError;
use std::fmt;
use std::fs::File;
use std::io::{self, Write};
use std::path::Path;

use crate::atomic::atomic_create;
use crate::bootstrap_composer::{ComposeError, compose_database};
use crate::page_append_plan::PlannedPage;
use crate::{
    CatalogError, CatalogObjectClass, ColumnStorageClass, ColumnStorageKind, ColumnType,
    DatabaseOpenError, DatabaseReader, IndexDefinitionKind, IndexKind, PageNumber, PublishError,
    ResourceBudget, TableDefinitionError, TableSpec,
};

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
/// anything is written: more than four tables, two tables whose names differ
/// only by ASCII case, more than three indexes on the first table or more than
/// one on a later table, more than one Memo or LongBinary column on a table,
/// an index together with such a column, a definition longer than two pages,
/// a definition longer than one page together with an index or on a later
/// table, or a name byte above `0x7E`.
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
        |candidate| check_candidate(candidate, tables, page_count, budget),
    )
    .map_err(CreateDatabaseError::Publish)
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

/// Reopens the candidate through the reader and checks its geometry, catalog
/// rows, columns, and indexes against `tables`.
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
    for (spec, root) in tables.iter().zip(roots) {
        let root = root.ok_or(mismatch("catalog row"))?;
        check_table(&mut database, spec, root, budget)?;
    }
    Ok(())
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
        let (logical_kind, physical_flags) = match requested.kind {
            IndexKind::Primary => (IndexDefinitionKind::Primary, 0x09),
            IndexKind::Unique => (IndexDefinitionKind::Ordinary, 0x01),
            IndexKind::Ordinary => (IndexDefinitionKind::Ordinary, 0x00),
        };
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

#[cfg(all(test, unix))]
#[path = "create_tests.rs"]
mod tests;
